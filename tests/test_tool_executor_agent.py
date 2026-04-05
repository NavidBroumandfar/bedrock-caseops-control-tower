"""
D-1 unit tests — ToolExecutorAgent.

Coverage:

  run() — success path:
  - returns a typed CaseOutput
  - document_id matches supervisor_result.document_id
  - source_filename matches intake record
  - source_type matches intake record
  - severity, category, summary, recommendations come from AnalysisOutput
  - confidence_score comes from ValidationOutput
  - unsupported_claims come from ValidationOutput
  - validated_by is the expected agent version string
  - timestamp is a non-empty ISO 8601 string
  - citations are populated from evidence chunks

  run() — citation mapping:
  - one citation per evidence chunk
  - citation source_id matches chunk source_id
  - citation source_label matches chunk source_label
  - citation excerpt matches chunk excerpt
  - citation relevance_score matches chunk relevance_score
  - no citations are dropped on success path
  - no citations are fabricated (citation count equals chunk count)

  run() — escalation: critical severity:
  - escalation_required is True when severity is Critical
  - escalation_reason contains "Critical"

  run() — escalation: low confidence:
  - escalation_required is True when confidence_score < 0.60
  - escalation_reason mentions confidence_score
  - confidence exactly at 0.60 does NOT trigger escalation

  run() — escalation: unsupported claims:
  - escalation_required is True when unsupported_claims is non-empty
  - escalation_reason mentions unsupported claims

  run() — escalation: explicit escalation in recommendation:
  - escalation_required is True when recommendation contains "escalate"
  - case-insensitive match ("Escalate", "ESCALATE", "please escalate now")
  - escalation_reason mentions recommendation

  run() — escalation: no trigger:
  - escalation_required is False when no rule fires
  - escalation_reason is None when no rule fires

  run() — escalation: multiple rules:
  - escalation_reason names all triggered rules when multiple fire

  run() — empty-retrieval path:
  - returns a typed CaseOutput (does not raise)
  - escalation_required is True
  - escalation_reason is not None
  - confidence_score is 0.0
  - citations is empty list
  - unsupported_claims is non-empty
  - document_id matches supervisor_result.document_id
  - source_filename from intake is preserved
  - severity is a valid SeverityLevel string

  run() — no live AWS:
  - tool_executor_agent module does not import boto3

No AWS credentials or live calls are required.
"""

import json
from typing import get_args

import pytest

from app.agents.tool_executor_agent import (
    ESCALATION_CONFIDENCE_THRESHOLD,
    ToolExecutorAgent,
    _VALIDATED_BY,
    _determine_escalation,
    _map_chunks_to_citations,
)
from app.schemas.analysis_models import AnalysisOutput, SeverityLevel
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.retrieval_models import EvidenceChunk, RetrievalResult
from app.schemas.supervisor_models import SupervisorResult
from app.schemas.validation_models import ValidationOutput


# ── shared builders ───────────────────────────────────────────────────────────

_DOC_ID = "doc-20260404-a1b2c3d4"


def _make_intake_record(document_id: str = _DOC_ID) -> IntakeRecord:
    return IntakeRecord(
        document_id=document_id,
        original_filename="warning_letter.txt",
        extension=".txt",
        absolute_path=f"/tmp/{document_id}/warning_letter.txt",
        file_size_bytes=2048,
        intake_timestamp="2026-04-04T00:00:00+00:00",
        source_type="FDA",
        document_date="2026-04-04",
    )


def _make_intake(document_id: str = _DOC_ID) -> IntakeResult:
    record = _make_intake_record(document_id)
    return IntakeResult(
        document_id=document_id,
        artifact_path=f"/tmp/outputs/intake/{document_id}.json",
        record=record,
        storage=None,
    )


def _make_chunks(count: int = 2) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id=f"chunk-00{i + 1}",
            text=f"Evidence passage {i + 1} with regulatory content.",
            source_id=f"s3://caseops-kb/doc{i + 1}.txt::chunk-00{i + 1}",
            source_label=f"Source Document {i + 1}",
            excerpt=f"Evidence passage {i + 1}.",
            relevance_score=round(0.9 - i * 0.1, 2),
        )
        for i in range(count)
    ]


def _make_analysis(
    document_id: str = _DOC_ID,
    severity: SeverityLevel = "High",
    recommendations: list[str] | None = None,
) -> AnalysisOutput:
    return AnalysisOutput(
        document_id=document_id,
        severity=severity,
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to maintain written procedures for equipment cleaning.",
        recommendations=recommendations
        or [
            "Initiate CAPA for cleaning validation gaps.",
            "Notify compliance team within 48 hours.",
        ],
    )


def _make_validation(
    document_id: str = _DOC_ID,
    confidence_score: float = 0.87,
    unsupported_claims: list[str] | None = None,
) -> ValidationOutput:
    return ValidationOutput(
        document_id=document_id,
        confidence_score=confidence_score,
        unsupported_claims=unsupported_claims or [],
        validation_status="pass",
    )


def _make_retrieval(
    document_id: str = _DOC_ID,
    chunks: list[EvidenceChunk] | None = None,
    *,
    empty: bool = False,
) -> RetrievalResult:
    if empty:
        return RetrievalResult(
            document_id=document_id,
            evidence_chunks=[],
            retrieval_status="empty",
            retrieved_count=0,
            warning="No matching chunks found in the knowledge base.",
        )
    evidence = chunks if chunks is not None else _make_chunks()
    return RetrievalResult(
        document_id=document_id,
        evidence_chunks=evidence,
        retrieval_status="success",
        retrieved_count=len(evidence),
    )


def _make_supervisor_result(
    document_id: str = _DOC_ID,
    *,
    severity: SeverityLevel = "High",
    confidence_score: float = 0.87,
    unsupported_claims: list[str] | None = None,
    recommendations: list[str] | None = None,
    chunks: list[EvidenceChunk] | None = None,
    empty_retrieval: bool = False,
) -> SupervisorResult:
    intake = _make_intake(document_id)
    retrieval = _make_retrieval(document_id, chunks=chunks, empty=empty_retrieval)

    if empty_retrieval:
        return SupervisorResult(
            document_id=document_id,
            intake=intake,
            retrieval=retrieval,
            analysis=None,
            validation=None,
        )

    analysis = _make_analysis(document_id, severity=severity, recommendations=recommendations)
    validation = _make_validation(
        document_id,
        confidence_score=confidence_score,
        unsupported_claims=unsupported_claims,
    )
    return SupervisorResult(
        document_id=document_id,
        intake=intake,
        retrieval=retrieval,
        analysis=analysis,
        validation=validation,
    )


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def agent() -> ToolExecutorAgent:
    return ToolExecutorAgent()


@pytest.fixture()
def success_result() -> SupervisorResult:
    return _make_supervisor_result()


@pytest.fixture()
def empty_result() -> SupervisorResult:
    return _make_supervisor_result(empty_retrieval=True)


# ── success path: return type and field mapping ───────────────────────────────


def test_success_returns_case_output(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert isinstance(output, CaseOutput)


def test_success_document_id_matches(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.document_id == success_result.document_id


def test_success_source_filename_from_intake(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.source_filename == success_result.intake.record.original_filename


def test_success_source_type_from_intake(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.source_type == success_result.intake.record.source_type


def test_success_severity_from_analysis(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.severity == success_result.analysis.severity  # type: ignore[union-attr]


def test_success_category_from_analysis(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.category == success_result.analysis.category  # type: ignore[union-attr]


def test_success_summary_from_analysis(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.summary == success_result.analysis.summary  # type: ignore[union-attr]


def test_success_recommendations_from_analysis(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.recommendations == success_result.analysis.recommendations  # type: ignore[union-attr]


def test_success_confidence_from_validation(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.confidence_score == success_result.validation.confidence_score  # type: ignore[union-attr]


def test_success_unsupported_claims_from_validation(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.unsupported_claims == success_result.validation.unsupported_claims  # type: ignore[union-attr]


def test_success_validated_by_is_agent_version(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert output.validated_by == _VALIDATED_BY


def test_success_timestamp_is_non_empty_string(agent: ToolExecutorAgent, success_result: SupervisorResult) -> None:
    output = agent.run(success_result)
    assert isinstance(output.timestamp, str)
    assert len(output.timestamp) > 0


# ── citation mapping ──────────────────────────────────────────────────────────


def test_citation_count_equals_chunk_count(agent: ToolExecutorAgent) -> None:
    chunks = _make_chunks(3)
    result = _make_supervisor_result(chunks=chunks)
    output = agent.run(result)
    assert len(output.citations) == 3


def test_citations_source_id_preserved(agent: ToolExecutorAgent) -> None:
    chunks = _make_chunks(2)
    result = _make_supervisor_result(chunks=chunks)
    output = agent.run(result)
    for i, citation in enumerate(output.citations):
        assert citation.source_id == chunks[i].source_id


def test_citations_source_label_preserved(agent: ToolExecutorAgent) -> None:
    chunks = _make_chunks(2)
    result = _make_supervisor_result(chunks=chunks)
    output = agent.run(result)
    for i, citation in enumerate(output.citations):
        assert citation.source_label == chunks[i].source_label


def test_citations_excerpt_preserved(agent: ToolExecutorAgent) -> None:
    chunks = _make_chunks(2)
    result = _make_supervisor_result(chunks=chunks)
    output = agent.run(result)
    for i, citation in enumerate(output.citations):
        assert citation.excerpt == chunks[i].excerpt


def test_citations_relevance_score_preserved(agent: ToolExecutorAgent) -> None:
    chunks = _make_chunks(2)
    result = _make_supervisor_result(chunks=chunks)
    output = agent.run(result)
    for i, citation in enumerate(output.citations):
        assert citation.relevance_score == chunks[i].relevance_score


def test_map_chunks_to_citations_direct() -> None:
    """Unit-test the mapping helper directly."""
    chunks = _make_chunks(2)
    citations = _map_chunks_to_citations(chunks)
    assert len(citations) == 2
    for citation in citations:
        assert isinstance(citation, Citation)


def test_map_chunks_to_citations_empty_input() -> None:
    assert _map_chunks_to_citations([]) == []


# ── escalation: critical severity ────────────────────────────────────────────


def test_critical_severity_triggers_escalation(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(severity="Critical")
    output = agent.run(result)
    assert output.escalation_required is True


def test_critical_severity_escalation_reason_mentions_critical(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(severity="Critical")
    output = agent.run(result)
    assert output.escalation_reason is not None
    assert "Critical" in output.escalation_reason


# ── escalation: low confidence ────────────────────────────────────────────────


def test_low_confidence_triggers_escalation(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(confidence_score=0.55)
    output = agent.run(result)
    assert output.escalation_required is True


def test_low_confidence_escalation_reason_mentions_confidence(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(confidence_score=0.55)
    output = agent.run(result)
    assert output.escalation_reason is not None
    assert "confidence" in output.escalation_reason.lower()


def test_confidence_exactly_at_threshold_does_not_escalate(agent: ToolExecutorAgent) -> None:
    """confidence_score == 0.60 is at the threshold, not below it — should not trigger."""
    result = _make_supervisor_result(
        confidence_score=ESCALATION_CONFIDENCE_THRESHOLD,
        severity="Low",
        unsupported_claims=[],
        recommendations=["Review documentation."],
    )
    output = agent.run(result)
    assert output.escalation_required is False


def test_confidence_just_below_threshold_triggers_escalation(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(confidence_score=0.599)
    output = agent.run(result)
    assert output.escalation_required is True


# ── escalation: unsupported claims ───────────────────────────────────────────


def test_unsupported_claims_trigger_escalation(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(
        unsupported_claims=["Claim about sterilization has no evidence."]
    )
    output = agent.run(result)
    assert output.escalation_required is True


def test_unsupported_claims_escalation_reason_mentions_unsupported(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(
        unsupported_claims=["Claim about sterilization has no evidence."]
    )
    output = agent.run(result)
    assert output.escalation_reason is not None
    assert "unsupported" in output.escalation_reason.lower()


# ── escalation: explicit escalation in recommendation ────────────────────────


@pytest.mark.parametrize("rec_text", [
    "Escalate to the compliance team.",
    "escalate immediately.",
    "ESCALATE — critical finding.",
    "Please escalate now per protocol.",
])
def test_escalation_keyword_in_recommendation_triggers_escalation(
    agent: ToolExecutorAgent,
    rec_text: str,
) -> None:
    result = _make_supervisor_result(
        severity="Low",
        confidence_score=0.85,
        unsupported_claims=[],
        recommendations=[rec_text],
    )
    output = agent.run(result)
    assert output.escalation_required is True


def test_escalation_keyword_reason_mentions_recommendation(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(
        severity="Low",
        confidence_score=0.85,
        unsupported_claims=[],
        recommendations=["Please escalate to compliance."],
    )
    output = agent.run(result)
    assert output.escalation_reason is not None
    assert "recommendation" in output.escalation_reason.lower()


# ── escalation: no trigger ────────────────────────────────────────────────────


def test_no_escalation_rule_fires_returns_false(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(
        severity="Medium",
        confidence_score=0.80,
        unsupported_claims=[],
        recommendations=["Review documentation.", "Schedule follow-up."],
    )
    output = agent.run(result)
    assert output.escalation_required is False


def test_no_escalation_reason_is_none(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(
        severity="Medium",
        confidence_score=0.80,
        unsupported_claims=[],
        recommendations=["Review documentation.", "Schedule follow-up."],
    )
    output = agent.run(result)
    assert output.escalation_reason is None


# ── escalation: multiple rules ────────────────────────────────────────────────


def test_multiple_rules_fire_reason_names_all(agent: ToolExecutorAgent) -> None:
    result = _make_supervisor_result(
        severity="Critical",
        confidence_score=0.40,
        unsupported_claims=["Unsupported claim detected."],
        recommendations=["Escalate to safety board."],
    )
    output = agent.run(result)
    assert output.escalation_required is True
    assert output.escalation_reason is not None
    reason = output.escalation_reason
    assert "Critical" in reason
    assert "confidence" in reason.lower()
    assert "unsupported" in reason.lower()
    assert "recommendation" in reason.lower()


# ── escalation helper: _determine_escalation unit tests ──────────────────────


def test_determine_escalation_no_trigger_returns_false_none() -> None:
    required, reason = _determine_escalation(
        severity="Medium",
        confidence_score=0.80,
        unsupported_claims=[],
        recommendations=["Review documentation."],
    )
    assert required is False
    assert reason is None


def test_determine_escalation_critical_returns_true() -> None:
    required, reason = _determine_escalation(
        severity="Critical",
        confidence_score=0.90,
        unsupported_claims=[],
        recommendations=[],
    )
    assert required is True
    assert reason is not None


# ── empty-retrieval path ──────────────────────────────────────────────────────


def test_empty_retrieval_returns_case_output(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert isinstance(output, CaseOutput)


def test_empty_retrieval_escalation_required(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert output.escalation_required is True


def test_empty_retrieval_escalation_reason_not_none(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert output.escalation_reason is not None
    assert len(output.escalation_reason) > 0


def test_empty_retrieval_confidence_is_zero(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert output.confidence_score == 0.0


def test_empty_retrieval_citations_is_empty(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert output.citations == []


def test_empty_retrieval_unsupported_claims_non_empty(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert len(output.unsupported_claims) > 0


def test_empty_retrieval_document_id_preserved(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert output.document_id == empty_result.document_id


def test_empty_retrieval_source_filename_preserved(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    assert output.source_filename == empty_result.intake.record.original_filename


def test_empty_retrieval_severity_is_valid_level(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    valid_levels = get_args(SeverityLevel)
    assert output.severity in valid_levels


def test_empty_retrieval_output_serializes_to_json(agent: ToolExecutorAgent, empty_result: SupervisorResult) -> None:
    output = agent.run(empty_result)
    raw = output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["escalation_required"] is True
    assert parsed["citations"] == []


# ── no live AWS ───────────────────────────────────────────────────────────────


def test_tool_executor_agent_module_does_not_import_boto3() -> None:
    import app.agents.tool_executor_agent as module

    assert not hasattr(module, "boto3"), (
        "tool_executor_agent must not import boto3; "
        "AWS interaction belongs in app/services/."
    )
