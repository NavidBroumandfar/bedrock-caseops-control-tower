"""
D-0 supervisor / planner workflow.

Orchestrates retrieval → analysis → validation in sequence and returns a typed
SupervisorResult.  This module owns sequencing, dependency coordination, and
structured-output retry policy for the analysis and validation steps.

Public surface:
  run_supervisor          — orchestrate the pipeline; return SupervisorResult
  SupervisorWorkflowError — raised when any pipeline step fails at this boundary

Architecture contract:
  Input  — IntakeResult (A-3 handoff) + injected provider and agents
  Output — SupervisorResult (D-0 typed handoff for D-1 Tool Executor)

  No boto3 clients or AWS service objects are instantiated here.  All AWS
  interaction stays in app/services/.  Dependencies are injected explicitly
  so this workflow is testable without live AWS calls.

Empty retrieval path:
  When the Knowledge Base returns no chunks, analysis would be groundless
  (AnalysisAgent explicitly rejects empty evidence).  The supervisor returns
  a SupervisorResult with analysis=None and validation=None rather than
  raising — this gives the D-1 Tool Executor a clean, typed result to route
  to the low-confidence escalation path.

Retry policy:
  Analysis and validation steps are each retried up to _MAX_ATTEMPTS times on
  BedrockServiceError — the service-layer type that covers malformed JSON,
  missing required keys, and Pydantic schema validation failures from the model.
  Precondition failures (AnalysisAgentError, ValidationAgentError) and other
  non-recoverable errors are not retried; they surface immediately.
  No backoff or sleep is applied — parse failures are local and immediate.
  Retrieval is not retried here; that is a service-level concern.
"""

from typing import Any, Callable

from app.agents.analysis_agent import AnalysisAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.intake_models import IntakeResult
from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.supervisor_models import SupervisorResult
from app.services.bedrock_service import BedrockServiceError
from app.workflows.retrieval_workflow import run_retrieval

# Total attempts per step (1 initial + 1 retry = 2 attempts, matching the
# architecture's "up to 2 retries for structured output parse failures" intent
# interpreted as 2 total attempts at this phase).
_MAX_ATTEMPTS = 2


class SupervisorWorkflowError(Exception):
    """
    Raised when a pipeline step fails at the supervisor workflow boundary.

    The message always names the step (retrieval / analysis / validation) and
    the document_id so callers can identify the failure without inspecting the
    exception chain.  The original exception is always chained via __cause__.
    """


def run_supervisor(
    intake: IntakeResult,
    *,
    retrieval_provider: RetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
) -> SupervisorResult:
    """
    Orchestrate retrieval → analysis → validation and return a typed SupervisorResult.

    Dependencies are injected as keyword-only arguments — no service clients are
    constructed here.  The caller is responsible for building and wiring concrete
    providers and agents before invoking the supervisor.

    Returns a SupervisorResult on both the success path and the empty-retrieval
    path.  Raises SupervisorWorkflowError if any pipeline step fails.
    """
    document_id = intake.document_id

    # ── step 1: retrieval ─────────────────────────────────────────────────────
    try:
        retrieval = run_retrieval(intake, retrieval_provider)
    except Exception as exc:
        raise SupervisorWorkflowError(
            f"[retrieval] Pipeline step failed "
            f"(document_id={document_id!r}): {exc}"
        ) from exc

    # ── empty retrieval: return early without calling analysis or validation ──
    #
    # Analysis without grounded evidence is explicitly forbidden — AnalysisAgent
    # raises AnalysisAgentError on empty chunks by design.  Returning a typed
    # SupervisorResult here (rather than raising) lets D-1 apply escalation
    # logic through the same typed-result interface as the success path.
    if retrieval.retrieval_status == "empty":
        return SupervisorResult(
            document_id=document_id,
            intake=intake,
            retrieval=retrieval,
            analysis=None,
            validation=None,
        )

    # ── step 2: analysis (with retry) ─────────────────────────────────────────
    analysis = _run_with_retry(
        lambda: analysis_agent.run(
            document_id=document_id,
            evidence_chunks=retrieval.evidence_chunks,
        ),
        step="analysis",
        document_id=document_id,
    )

    # ── step 3: validation (with retry) ───────────────────────────────────────
    validation = _run_with_retry(
        lambda: validation_agent.run(
            document_id=document_id,
            analysis_output=analysis,
            evidence_chunks=retrieval.evidence_chunks,
        ),
        step="validation",
        document_id=document_id,
    )

    return SupervisorResult(
        document_id=document_id,
        intake=intake,
        retrieval=retrieval,
        analysis=analysis,
        validation=validation,
    )


# ── private helpers ────────────────────────────────────────────────────────────


def _run_with_retry(
    call: Callable[[], Any],
    *,
    step: str,
    document_id: str,
) -> Any:
    """
    Invoke `call()` up to _MAX_ATTEMPTS times, retrying on BedrockServiceError.

    BedrockServiceError is the service-layer type for structured-output failures
    (malformed JSON, missing keys, schema validation errors) as well as transient
    Converse API errors.  These are the cases the architecture identifies as
    retry-eligible.

    Precondition failures (AnalysisAgentError, ValidationAgentError) and other
    exceptions are not retried — they indicate a logic error, not a parse
    transient, and repeating the call would not change the outcome.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return call()
        except BedrockServiceError as exc:
            if attempt == _MAX_ATTEMPTS:
                raise SupervisorWorkflowError(
                    f"[{step}] Pipeline step failed after {_MAX_ATTEMPTS} attempts "
                    f"(document_id={document_id!r}): {exc}"
                ) from exc
            # attempt < _MAX_ATTEMPTS: continue to retry
        except Exception as exc:
            # Non-retryable: wrap and surface immediately without retry.
            raise SupervisorWorkflowError(
                f"[{step}] Pipeline step failed "
                f"(document_id={document_id!r}): {exc}"
            ) from exc
