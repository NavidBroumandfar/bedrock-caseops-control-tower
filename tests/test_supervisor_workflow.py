"""
D-0 unit tests — supervisor / planner workflow.

Coverage:

  run_supervisor — success path:
  - returns a typed SupervisorResult
  - document_id matches intake.document_id
  - result.intake is the original intake handoff
  - result.retrieval is a typed RetrievalResult
  - result.analysis is a typed AnalysisOutput
  - result.validation is a typed ValidationOutput
  - all four sub-results are typed (no raw dicts or None on success path)

  run_supervisor — empty retrieval path:
  - returns a typed SupervisorResult (does not raise)
  - result.retrieval.retrieval_status is "empty"
  - result.analysis is None (analysis not attempted)
  - result.validation is None (validation not attempted)
  - analysis agent is not called when retrieval is empty
  - validation agent is not called when retrieval is empty

  run_supervisor — downstream failure propagation:
  - retrieval failure surfaces as SupervisorWorkflowError
  - retrieval failure message contains "[retrieval]" step label
  - retrieval failure message contains document_id
  - retrieval failure chains original exception via __cause__
  - analysis failure surfaces as SupervisorWorkflowError (non-retryable path)
  - analysis failure message contains "[analysis]" step label
  - analysis failure chains original exception via __cause__
  - validation failure surfaces as SupervisorWorkflowError (non-retryable path)
  - validation failure message contains "[validation]" step label
  - validation failure chains original exception via __cause__

  run_supervisor — retry policy (BedrockServiceError):
  - analysis retried once on BedrockServiceError, succeeds on second attempt
  - validation retried once on BedrockServiceError, succeeds on second attempt
  - analysis raises SupervisorWorkflowError after max attempts exhausted
  - validation raises SupervisorWorkflowError after max attempts exhausted
  - non-retryable analysis failure (RuntimeError) is not retried
  - non-retryable validation failure (RuntimeError) is not retried
  - SupervisorWorkflowError message contains attempt count on max-attempts failure

  run_supervisor — no live AWS:
  - supervisor module does not import boto3
  - supervisor workflow is independent of tool execution / CaseOutput

  SupervisorResult schema:
  - SupervisorResult requires document_id, intake, retrieval fields
  - analysis and validation default to None
  - SupervisorResult serializes to JSON cleanly

No AWS credentials or live calls required.
All AWS interaction is replaced by injected fakes or MagicMock objects.
"""

import pytest
from unittest.mock import MagicMock

from app.agents.analysis_agent import AnalysisAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.schemas.retrieval_models import EvidenceChunk, RetrievalResult
from app.schemas.supervisor_models import SupervisorResult
from app.schemas.validation_models import ValidationOutput
from app.services.bedrock_service import BedrockServiceError
from app.workflows.supervisor_workflow import (
    SupervisorWorkflowError,
    _MAX_ATTEMPTS,
    run_supervisor,
)
from tests.fakes.fake_retrieval import FakeRetrievalProvider


# ── shared helpers ─────────────────────────────────────────────────────────────


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
            source_id=f"s3://caseops-kb/doc{i + 1}.txt",
            source_label=f"Source Document {i + 1}",
            excerpt=f"Evidence passage {i + 1}.",
            relevance_score=round(0.9 - i * 0.1, 2),
        )
        for i in range(count)
    ]


def _make_analysis_output(document_id: str = _DOC_ID) -> AnalysisOutput:
    return AnalysisOutput(
        document_id=document_id,
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to maintain adequate written procedures for equipment cleaning.",
        recommendations=[
            "Initiate CAPA for cleaning validation gaps.",
            "Notify compliance team within 48 hours.",
        ],
    )


def _make_validation_output(document_id: str = _DOC_ID) -> ValidationOutput:
    return ValidationOutput(
        document_id=document_id,
        confidence_score=0.87,
        unsupported_claims=[],
        validation_status="pass",
    )


# ── fixture factories ──────────────────────────────────────────────────────────


def _make_analysis_agent(
    analysis_output: AnalysisOutput | None = None,
    side_effect: Exception | None = None,
) -> AnalysisAgent:
    """Return an AnalysisAgent backed by a MagicMock provider."""
    provider = MagicMock()
    if side_effect is not None:
        provider.analyze.side_effect = side_effect
    else:
        provider.analyze.return_value = analysis_output or _make_analysis_output()
    return AnalysisAgent(provider=provider)


def _make_validation_agent(
    validation_output: ValidationOutput | None = None,
    side_effect: Exception | None = None,
) -> ValidationAgent:
    """Return a ValidationAgent backed by a MagicMock provider."""
    provider = MagicMock()
    if side_effect is not None:
        provider.validate.side_effect = side_effect
    else:
        provider.validate.return_value = validation_output or _make_validation_output()
    return ValidationAgent(provider=provider)


# ── fixtures ───────────────────────────────────────────────────────────────────


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


# ── success path: return type ──────────────────────────────────────────────────


def test_success_returns_supervisor_result(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert isinstance(result, SupervisorResult)


def test_success_document_id_matches_intake(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.document_id == intake.document_id


def test_success_intake_is_preserved(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.intake is intake


# ── success path: typed sub-results ───────────────────────────────────────────


def test_success_retrieval_is_typed(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert isinstance(result.retrieval, RetrievalResult)


def test_success_analysis_is_typed(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert isinstance(result.analysis, AnalysisOutput)


def test_success_validation_is_typed(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert isinstance(result.validation, ValidationOutput)


def test_success_no_sub_result_is_none(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    """On the success path all four sub-results must be populated."""
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.retrieval is not None
    assert result.analysis is not None
    assert result.validation is not None


def test_success_retrieval_document_id_matches(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.retrieval.document_id == intake.document_id


def test_success_analysis_document_id_matches(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.analysis is not None
    assert result.analysis.document_id == intake.document_id


def test_success_validation_document_id_matches(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.validation is not None
    assert result.validation.document_id == intake.document_id


# ── empty retrieval path ───────────────────────────────────────────────────────


def test_empty_retrieval_returns_supervisor_result(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    """Empty retrieval must return a typed SupervisorResult, never raise."""
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert isinstance(result, SupervisorResult)


def test_empty_retrieval_status_is_empty(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.retrieval.retrieval_status == "empty"


def test_empty_retrieval_analysis_is_none(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.analysis is None


def test_empty_retrieval_validation_is_none(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.validation is None


def test_empty_retrieval_document_id_preserved(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert result.document_id == intake.document_id


def test_empty_retrieval_does_not_call_analysis_agent(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
) -> None:
    """Analysis agent must not be called when retrieval is empty — no groundless analysis."""
    provider = MagicMock()
    provider.analyze.side_effect = AssertionError(
        "AnalysisAgent provider must not be called on empty retrieval"
    )
    agent = AnalysisAgent(provider=provider)
    validation_agent = _make_validation_agent()

    # If analysis agent is called, provider.analyze raises AssertionError → test fails.
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=agent,
        validation_agent=validation_agent,
    )
    assert result.analysis is None
    provider.analyze.assert_not_called()


def test_empty_retrieval_does_not_call_validation_agent(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
) -> None:
    """Validation agent must not be called when retrieval is empty."""
    analysis_agent = _make_analysis_agent()
    provider = MagicMock()
    provider.validate.side_effect = AssertionError(
        "ValidationAgent provider must not be called on empty retrieval"
    )
    agent = ValidationAgent(provider=provider)

    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=agent,
    )
    assert result.validation is None
    provider.validate.assert_not_called()


# ── retrieval failure ──────────────────────────────────────────────────────────


class _FailingRetrievalProvider:
    """Fake provider that always raises a retrieval-level error."""

    def retrieve(self, request):  # type: ignore[override]
        raise RuntimeError("Simulated retrieval service failure.")


def test_retrieval_failure_raises_supervisor_workflow_error(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=_FailingRetrievalProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
        )


def test_retrieval_failure_message_contains_step_label(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    with pytest.raises(SupervisorWorkflowError, match=r"\[retrieval\]"):
        run_supervisor(
            intake,
            retrieval_provider=_FailingRetrievalProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
        )


def test_retrieval_failure_message_contains_document_id(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    with pytest.raises(SupervisorWorkflowError, match=intake.document_id):
        run_supervisor(
            intake,
            retrieval_provider=_FailingRetrievalProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
        )


def test_retrieval_failure_chains_original_exception(
    intake: IntakeResult,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    with pytest.raises(SupervisorWorkflowError) as exc_info:
        run_supervisor(
            intake,
            retrieval_provider=_FailingRetrievalProvider(),  # type: ignore[arg-type]
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
        )
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, RuntimeError)


# ── analysis failure ───────────────────────────────────────────────────────────


def test_analysis_failure_raises_supervisor_workflow_error(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    failing_agent = _make_analysis_agent(
        side_effect=RuntimeError("Simulated analysis failure.")
    )
    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=failing_agent,
            validation_agent=validation_agent,
        )


def test_analysis_failure_message_contains_step_label(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    failing_agent = _make_analysis_agent(
        side_effect=RuntimeError("Simulated analysis failure.")
    )
    with pytest.raises(SupervisorWorkflowError, match=r"\[analysis\]"):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=failing_agent,
            validation_agent=validation_agent,
        )


def test_analysis_failure_chains_original_exception(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    original = RuntimeError("Simulated analysis failure.")
    failing_agent = _make_analysis_agent(side_effect=original)
    with pytest.raises(SupervisorWorkflowError) as exc_info:
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=failing_agent,
            validation_agent=validation_agent,
        )
    assert exc_info.value.__cause__ is original


# ── validation failure ─────────────────────────────────────────────────────────


def test_validation_failure_raises_supervisor_workflow_error(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
) -> None:
    failing_agent = _make_validation_agent(
        side_effect=RuntimeError("Simulated validation failure.")
    )
    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=failing_agent,
        )


def test_validation_failure_message_contains_step_label(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
) -> None:
    failing_agent = _make_validation_agent(
        side_effect=RuntimeError("Simulated validation failure.")
    )
    with pytest.raises(SupervisorWorkflowError, match=r"\[validation\]"):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=failing_agent,
        )


def test_validation_failure_chains_original_exception(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
) -> None:
    original = RuntimeError("Simulated validation failure.")
    failing_agent = _make_validation_agent(side_effect=original)
    with pytest.raises(SupervisorWorkflowError) as exc_info:
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=failing_agent,
        )
    assert exc_info.value.__cause__ is original


# ── retry policy ──────────────────────────────────────────────────────────────
#
# BedrockServiceError is the retry-eligible type (parse / transient Converse API
# failures).  RuntimeError and other types are non-retryable and surface immediately.


def test_analysis_retried_once_on_bedrock_service_error(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    """Analysis is retried when the first attempt raises BedrockServiceError."""
    provider = MagicMock()
    provider.analyze.side_effect = [
        BedrockServiceError("Malformed JSON on attempt 1"),
        _make_analysis_output(),
    ]
    agent = AnalysisAgent(provider=provider)

    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=agent,
        validation_agent=validation_agent,
    )

    assert isinstance(result.analysis, AnalysisOutput)
    assert provider.analyze.call_count == 2


def test_validation_retried_once_on_bedrock_service_error(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
) -> None:
    """Validation is retried when the first attempt raises BedrockServiceError."""
    provider = MagicMock()
    provider.validate.side_effect = [
        BedrockServiceError("Missing required key on attempt 1"),
        _make_validation_output(),
    ]
    agent = ValidationAgent(provider=provider)

    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=agent,
    )

    assert isinstance(result.validation, ValidationOutput)
    assert provider.validate.call_count == 2


def test_analysis_raises_after_max_attempts(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    """SupervisorWorkflowError is raised when all analysis attempts are exhausted."""
    provider = MagicMock()
    provider.analyze.side_effect = BedrockServiceError("Persistent parse failure.")
    agent = AnalysisAgent(provider=provider)

    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=agent,
            validation_agent=validation_agent,
        )

    assert provider.analyze.call_count == _MAX_ATTEMPTS


def test_validation_raises_after_max_attempts(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
) -> None:
    """SupervisorWorkflowError is raised when all validation attempts are exhausted."""
    provider = MagicMock()
    provider.validate.side_effect = BedrockServiceError("Persistent parse failure.")
    agent = ValidationAgent(provider=provider)

    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=agent,
        )

    assert provider.validate.call_count == _MAX_ATTEMPTS


def test_analysis_max_attempts_message_contains_attempt_count(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    """Error message must name the attempt count so callers can diagnose exhaustion."""
    provider = MagicMock()
    provider.analyze.side_effect = BedrockServiceError("Parse failure.")
    agent = AnalysisAgent(provider=provider)

    with pytest.raises(SupervisorWorkflowError, match=str(_MAX_ATTEMPTS)):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=agent,
            validation_agent=validation_agent,
        )


def test_non_retryable_analysis_failure_not_retried(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    validation_agent: ValidationAgent,
) -> None:
    """RuntimeError (non-retryable) must surface immediately without any retry."""
    provider = MagicMock()
    provider.analyze.side_effect = RuntimeError("Non-retryable failure.")
    agent = AnalysisAgent(provider=provider)

    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=agent,
            validation_agent=validation_agent,
        )

    assert provider.analyze.call_count == 1


def test_non_retryable_validation_failure_not_retried(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
) -> None:
    """RuntimeError (non-retryable) must surface immediately without any retry."""
    provider = MagicMock()
    provider.validate.side_effect = RuntimeError("Non-retryable failure.")
    agent = ValidationAgent(provider=provider)

    with pytest.raises(SupervisorWorkflowError):
        run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=agent,
        )

    assert provider.validate.call_count == 1


def test_empty_retrieval_unaffected_by_retry_logic(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    """Empty retrieval must still return a clean typed result regardless of retry policy."""
    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    assert isinstance(result, SupervisorResult)
    assert result.analysis is None
    assert result.validation is None


# ── no live AWS ────────────────────────────────────────────────────────────────


def test_supervisor_workflow_module_does_not_import_boto3() -> None:
    """
    The supervisor workflow must not import boto3.

    All AWS interaction belongs in app/services/.  The supervisor is an
    orchestration layer — it must remain AWS-free and independently testable.
    """
    import app.workflows.supervisor_workflow as wf_module

    assert not hasattr(wf_module, "boto3"), (
        "supervisor_workflow must not import boto3 directly; "
        "AWS interaction belongs in app/services/."
    )


def test_supervisor_schema_module_does_not_import_boto3() -> None:
    import app.schemas.supervisor_models as schema_module

    assert not hasattr(schema_module, "boto3")


# ── independence from tool execution / final output ───────────────────────────


def test_supervisor_result_has_no_case_output_field() -> None:
    """SupervisorResult must not carry any CaseOutput or tool-executor fields."""
    intake = _make_intake()
    chunks = _make_chunks()
    retrieval = RetrievalResult(
        document_id=_DOC_ID,
        evidence_chunks=chunks,
        retrieval_status="success",
        retrieved_count=len(chunks),
    )
    analysis = _make_analysis_output()
    validation = _make_validation_output()

    result = SupervisorResult(
        document_id=_DOC_ID,
        intake=intake,
        retrieval=retrieval,
        analysis=analysis,
        validation=validation,
    )

    # Tool executor fields must not exist on SupervisorResult
    assert not hasattr(result, "escalation_required")
    assert not hasattr(result, "escalation_reason")
    assert not hasattr(result, "case_output")
    assert not hasattr(result, "output_path")


def test_supervisor_result_has_no_cloudwatch_fields() -> None:
    """SupervisorResult must not carry CloudWatch or logging fields."""
    intake = _make_intake()
    chunks = _make_chunks()
    retrieval = RetrievalResult(
        document_id=_DOC_ID,
        evidence_chunks=chunks,
        retrieval_status="success",
        retrieved_count=len(chunks),
    )
    result = SupervisorResult(
        document_id=_DOC_ID,
        intake=intake,
        retrieval=retrieval,
    )
    assert not hasattr(result, "log_group")
    assert not hasattr(result, "log_stream")
    assert not hasattr(result, "cloudwatch_event")


# ── SupervisorResult schema ────────────────────────────────────────────────────


def test_supervisor_result_analysis_defaults_to_none() -> None:
    intake = _make_intake()
    chunks = _make_chunks()
    retrieval = RetrievalResult(
        document_id=_DOC_ID,
        evidence_chunks=chunks,
        retrieval_status="success",
        retrieved_count=len(chunks),
    )
    result = SupervisorResult(
        document_id=_DOC_ID,
        intake=intake,
        retrieval=retrieval,
    )
    assert result.analysis is None


def test_supervisor_result_validation_defaults_to_none() -> None:
    intake = _make_intake()
    chunks = _make_chunks()
    retrieval = RetrievalResult(
        document_id=_DOC_ID,
        evidence_chunks=chunks,
        retrieval_status="success",
        retrieved_count=len(chunks),
    )
    result = SupervisorResult(
        document_id=_DOC_ID,
        intake=intake,
        retrieval=retrieval,
    )
    assert result.validation is None


def test_supervisor_result_serializes_to_json(
    intake: IntakeResult,
    retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    """SupervisorResult must round-trip through JSON cleanly."""
    import json

    result = run_supervisor(
        intake,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == _DOC_ID
    assert "intake" in parsed
    assert "retrieval" in parsed
    assert "analysis" in parsed
    assert "validation" in parsed


def test_supervisor_result_empty_path_serializes_to_json(
    intake: IntakeResult,
    empty_retrieval_provider: FakeRetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> None:
    """Empty-path SupervisorResult (analysis=None) must serialize cleanly."""
    import json

    result = run_supervisor(
        intake,
        retrieval_provider=empty_retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
    )
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["analysis"] is None
    assert parsed["validation"] is None
