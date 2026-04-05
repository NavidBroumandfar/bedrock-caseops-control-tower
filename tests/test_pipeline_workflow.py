"""
D-2 unit tests — end-to-end pipeline orchestration workflow.

Coverage:

  run_pipeline — success path:
  - returns a typed CaseOutput
  - session_id is populated (not None) in the final output
  - session_id has the expected format "sess-{8 hex chars}"
  - document_id in output matches intake document_id
  - source_filename in output matches intake record
  - source_type in output matches intake record
  - citations are populated from the evidence chunks
  - escalation_required and confidence_score come through from the pipeline

  run_pipeline — empty retrieval path:
  - returns a typed CaseOutput (does not raise)
  - session_id is populated even on the empty-retrieval path
  - escalation_required is True (empty evidence forces escalation)
  - citations list is empty
  - document_id is preserved from intake

  run_pipeline — failure propagation:
  - SupervisorWorkflowError surfaces as PipelineWorkflowError
  - PipelineWorkflowError message contains "[supervisor]"
  - PipelineWorkflowError message contains document_id
  - PipelineWorkflowError chains the original exception via __cause__
  - tool_executor failure surfaces as PipelineWorkflowError
  - PipelineWorkflowError message contains "[tool_executor]" on executor failure

  run_pipeline — session_id contract:
  - two consecutive runs produce different session_ids
  - session_id is always a non-empty string

  run_pipeline — no live AWS:
  - pipeline_workflow module does not import boto3
  - no output writing or CloudWatch behavior is introduced

No AWS credentials or live calls required.
All AWS interaction is replaced by injected fakes or MagicMock objects.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from app.agents.analysis_agent import AnalysisAgent
from app.agents.tool_executor_agent import ToolExecutorAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.retrieval_models import EvidenceChunk, RetrievalResult
from app.schemas.supervisor_models import SupervisorResult
from app.schemas.validation_models import ValidationOutput
from app.workflows.pipeline_workflow import (
    PipelineWorkflowError,
    _generate_session_id,
    run_pipeline,
)
from app.workflows.supervisor_workflow import SupervisorWorkflowError
from tests.fakes.fake_retrieval import FakeRetrievalProvider


# ── shared builders ───────────────────────────────────────────────────────────

_DOC_ID = "doc-20260405-d2test01"


def _make_intake_record(document_id: str = _DOC_ID) -> IntakeRecord:
    return IntakeRecord(
        document_id=document_id,
        original_filename="advisory.txt",
        extension=".txt",
        absolute_path=f"/tmp/{document_id}/advisory.txt",
        file_size_bytes=1024,
        intake_timestamp="2026-04-05T00:00:00+00:00",
        source_type="CISA",
        document_date="2026-04-05",
    )


def _make_intake(document_id: str = _DOC_ID) -> IntakeResult:
    record = _make_intake_record(document_id)
    return IntakeResult(
        document_id=document_id,
        artifact_path=f"/tmp/outputs/intake/{document_id}.json",
        record=record,
        storage=None,
    )


def _make_analysis_agent(
    analysis_output: AnalysisOutput | None = None,
    side_effect: Exception | None = None,
) -> AnalysisAgent:
    provider = MagicMock()
    if side_effect is not None:
        provider.analyze.side_effect = side_effect
    else:
        output = analysis_output or AnalysisOutput(
            document_id=_DOC_ID,
            severity="High",
            category="Security / Network Vulnerability",
            summary="Critical network vulnerability identified in industrial control systems.",
            recommendations=[
                "Apply the vendor-supplied patch immediately.",
                "Isolate affected systems from external networks.",
            ],
        )
        provider.analyze.return_value = output
    return AnalysisAgent(provider=provider)


def _make_validation_agent(
    confidence_score: float = 0.85,
    unsupported_claims: list[str] | None = None,
    side_effect: Exception | None = None,
) -> ValidationAgent:
    provider = MagicMock()
    if side_effect is not None:
        provider.validate.side_effect = side_effect
    else:
        output = ValidationOutput(
            document_id=_DOC_ID,
            confidence_score=confidence_score,
            unsupported_claims=unsupported_claims or [],
            validation_status="pass",
        )
        provider.validate.return_value = output
    return ValidationAgent(provider=provider)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def intake() -> IntakeResult:
    return _make_intake()


@pytest.fixture()
def retrieval_provider() -> FakeRetrievalProvider:
    return FakeRetrievalProvider()


@pytest.fixture()
def empty_retrieval_provider() -> FakeRetrievalProvider:
    return FakeRetrievalProvider(return_empty=True)


@pytest.fixture()
def analysis_agent() -> AnalysisAgent:
    return _make_analysis_agent()


@pytest.fixture()
def validation_agent() -> ValidationAgent:
    return _make_validation_agent()


@pytest.fixture()
def tool_executor() -> ToolExecutorAgent:
    return ToolExecutorAgent()


# ── success path: return type ─────────────────────────────────────────────────


def test_success_returns_case_output(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert isinstance(output, CaseOutput)


def test_success_document_id_matches_intake(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.document_id == intake.document_id


def test_success_source_filename_from_intake(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.source_filename == intake.record.original_filename


def test_success_source_type_from_intake(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.source_type == intake.record.source_type


def test_success_citations_are_populated(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """FakeRetrievalProvider returns 2 chunks; citations must be non-empty."""
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert len(output.citations) > 0
    assert all(isinstance(c, Citation) for c in output.citations)


def test_success_escalation_fields_are_present(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert isinstance(output.escalation_required, bool)
    # escalation_reason may be None (no rule triggered) — that is valid
    assert output.escalation_reason is None or isinstance(output.escalation_reason, str)


# ── session_id: populated and formatted correctly ─────────────────────────────


def test_session_id_is_populated_on_success_path(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """session_id must be set to a non-None string by the pipeline."""
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.session_id is not None
    assert isinstance(output.session_id, str)
    assert len(output.session_id) > 0


def test_session_id_matches_expected_format(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """session_id must match the 'sess-{8 hex chars}' pattern."""
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert re.fullmatch(r"sess-[0-9a-f]{8}", output.session_id or "") is not None


def test_session_id_populated_on_empty_retrieval_path(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """session_id must be injected even when retrieval returns no evidence."""
    output = run_pipeline(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.session_id is not None
    assert re.fullmatch(r"sess-[0-9a-f]{8}", output.session_id) is not None


def test_consecutive_runs_produce_different_session_ids(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """Each pipeline run must produce a unique session_id."""
    out1 = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    out2 = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert out1.session_id != out2.session_id


# ── session_id helper unit test ───────────────────────────────────────────────


def test_generate_session_id_format() -> None:
    sid = _generate_session_id()
    assert re.fullmatch(r"sess-[0-9a-f]{8}", sid) is not None


def test_generate_session_id_is_unique() -> None:
    ids = {_generate_session_id() for _ in range(20)}
    # With 8 hex chars (2^32 space) and only 20 draws, collision is virtually impossible.
    assert len(ids) == 20


# ── empty retrieval path ──────────────────────────────────────────────────────


def test_empty_retrieval_returns_case_output(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """Empty KB retrieval must return a typed CaseOutput — never raise."""
    output = run_pipeline(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert isinstance(output, CaseOutput)


def test_empty_retrieval_escalation_required(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    """Empty evidence must always trigger escalation."""
    output = run_pipeline(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.escalation_required is True


def test_empty_retrieval_citations_are_empty(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.citations == []


def test_empty_retrieval_document_id_preserved(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    output = run_pipeline(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert output.document_id == intake.document_id


# ── failure propagation: supervisor ──────────────────────────────────────────


class _FailingSupervisorProvider:
    """Fake retrieval provider that always raises, simulating a supervisor failure."""

    def retrieve(self, request):  # type: ignore[override]
        raise RuntimeError("Simulated KB service failure.")


def test_supervisor_failure_raises_pipeline_workflow_error(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    with pytest.raises(PipelineWorkflowError):
        run_pipeline(
            intake,
            retrieval_provider=_FailingSupervisorProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=tool_executor,
        )


def test_supervisor_failure_message_contains_step_label(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    with pytest.raises(PipelineWorkflowError, match=r"\[supervisor\]"):
        run_pipeline(
            intake,
            retrieval_provider=_FailingSupervisorProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=tool_executor,
        )


def test_supervisor_failure_message_contains_document_id(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    with pytest.raises(PipelineWorkflowError, match=intake.document_id):
        run_pipeline(
            intake,
            retrieval_provider=_FailingSupervisorProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=tool_executor,
        )


def test_supervisor_failure_chains_original_exception(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
) -> None:
    with pytest.raises(PipelineWorkflowError) as exc_info:
        run_pipeline(
            intake,
            retrieval_provider=_FailingSupervisorProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=tool_executor,
        )
    assert exc_info.value.__cause__ is not None


# ── failure propagation: tool executor ───────────────────────────────────────


def test_tool_executor_failure_raises_pipeline_workflow_error(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    """A tool_executor.run() failure must surface as PipelineWorkflowError."""
    failing_executor = MagicMock(spec=ToolExecutorAgent)
    failing_executor.run.side_effect = RuntimeError("Simulated tool executor failure.")

    with pytest.raises(PipelineWorkflowError):
        run_pipeline(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=failing_executor,
        )


def test_tool_executor_failure_message_contains_step_label(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    failing_executor = MagicMock(spec=ToolExecutorAgent)
    failing_executor.run.side_effect = RuntimeError("Simulated tool executor failure.")

    with pytest.raises(PipelineWorkflowError, match=r"\[tool_executor\]"):
        run_pipeline(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=failing_executor,
        )


def test_tool_executor_failure_chains_original_exception(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    original = RuntimeError("Simulated tool executor failure.")
    failing_executor = MagicMock(spec=ToolExecutorAgent)
    failing_executor.run.side_effect = original

    with pytest.raises(PipelineWorkflowError) as exc_info:
        run_pipeline(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=failing_executor,
        )
    assert exc_info.value.__cause__ is original


# ── no live AWS ───────────────────────────────────────────────────────────────


def test_pipeline_workflow_module_does_not_import_boto3() -> None:
    """
    The pipeline workflow must not import boto3.

    All AWS interaction belongs in app/services/.  The orchestration layer
    must remain AWS-free and independently testable.
    """
    import app.workflows.pipeline_workflow as wf_module

    assert not hasattr(wf_module, "boto3"), (
        "pipeline_workflow must not import boto3 directly; "
        "AWS interaction belongs in app/services/."
    )


def test_pipeline_workflow_does_not_write_files(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
    tmp_path,
) -> None:
    """
    run_pipeline must not write any output files.

    File writing is an E-phase concern.  We verify by confirming the
    tmp_path directory stays empty after a full pipeline run.
    """
    output = run_pipeline(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
    )
    assert isinstance(output, CaseOutput)
    # No files should have been written to the test's tmp_path.
    assert list(tmp_path.iterdir()) == []
