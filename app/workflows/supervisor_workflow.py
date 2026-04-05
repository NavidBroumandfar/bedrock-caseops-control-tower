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

Structured logging:
  A logger is accepted as an optional keyword argument.  When omitted, a
  NoOpLogger is used so call sites that do not pass one are unaffected.
  Logging never influences control flow.
"""

from typing import Any, Callable, Union

from app.agents.analysis_agent import AnalysisAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.intake_models import IntakeResult
from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.supervisor_models import SupervisorResult
from app.services.bedrock_service import BedrockServiceError
from app.utils.logging_utils import NoOpLogger, PipelineLogger
from app.workflows.retrieval_workflow import run_retrieval

AnyLogger = Union[PipelineLogger, NoOpLogger]

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
    logger: AnyLogger | None = None,
) -> SupervisorResult:
    """
    Orchestrate retrieval → analysis → validation and return a typed SupervisorResult.

    Dependencies are injected as keyword-only arguments — no service clients are
    constructed here.  The caller is responsible for building and wiring concrete
    providers and agents before invoking the supervisor.

    `logger` is optional.  When omitted a NoOpLogger is used.

    Returns a SupervisorResult on both the success path and the empty-retrieval
    path.  Raises SupervisorWorkflowError if any pipeline step fails.
    """
    _logger: AnyLogger = logger or NoOpLogger()
    document_id = intake.document_id

    # ── step 1: retrieval ─────────────────────────────────────────────────────
    _logger.info(
        agent="supervisor",
        event="retrieval_start",
        document_id=document_id,
    )

    try:
        retrieval = run_retrieval(intake, retrieval_provider)
    except Exception as exc:
        _logger.error(
            agent="supervisor",
            event="retrieval_failed",
            document_id=document_id,
            data={"error": str(exc)},
        )
        raise SupervisorWorkflowError(
            f"[retrieval] Pipeline step failed "
            f"(document_id={document_id!r}): {exc}"
        ) from exc

    chunk_count = len(retrieval.evidence_chunks)
    _logger.info(
        agent="supervisor",
        event="retrieval_complete",
        document_id=document_id,
        data={
            "retrieval_status": retrieval.retrieval_status,
            "chunk_count": chunk_count,
        },
    )

    # ── empty retrieval: return early without calling analysis or validation ──
    #
    # Analysis without grounded evidence is explicitly forbidden — AnalysisAgent
    # raises AnalysisAgentError on empty chunks by design.  Returning a typed
    # SupervisorResult here (rather than raising) lets D-1 apply escalation
    # logic through the same typed-result interface as the success path.
    if retrieval.retrieval_status == "empty":
        _logger.warning(
            agent="supervisor",
            event="retrieval_empty",
            document_id=document_id,
            data={"note": "No evidence chunks returned; routing to empty-retrieval escalation path."},
        )
        return SupervisorResult(
            document_id=document_id,
            intake=intake,
            retrieval=retrieval,
            analysis=None,
            validation=None,
        )

    # ── step 2: analysis (with retry) ─────────────────────────────────────────
    _logger.info(
        agent="supervisor",
        event="analysis_start",
        document_id=document_id,
        data={"chunk_count": chunk_count},
    )

    analysis = _run_with_retry(
        lambda: analysis_agent.run(
            document_id=document_id,
            evidence_chunks=retrieval.evidence_chunks,
        ),
        step="analysis",
        document_id=document_id,
        logger=_logger,
    )

    _logger.info(
        agent="supervisor",
        event="analysis_complete",
        document_id=document_id,
        data={
            "severity": analysis.severity,
            "category": analysis.category,
            "recommendation_count": len(analysis.recommendations),
        },
    )

    # ── step 3: validation (with retry) ───────────────────────────────────────
    _logger.info(
        agent="supervisor",
        event="validation_start",
        document_id=document_id,
    )

    validation = _run_with_retry(
        lambda: validation_agent.run(
            document_id=document_id,
            analysis_output=analysis,
            evidence_chunks=retrieval.evidence_chunks,
        ),
        step="validation",
        document_id=document_id,
        logger=_logger,
    )

    _logger.info(
        agent="supervisor",
        event="validation_complete",
        document_id=document_id,
        data={
            "validation_status": validation.validation_status,
            "confidence_score": validation.confidence_score,
            "unsupported_claim_count": len(validation.unsupported_claims),
        },
    )

    if validation.unsupported_claims:
        _logger.warning(
            agent="supervisor",
            event="validation_unsupported_claims_detected",
            document_id=document_id,
            data={
                "count": len(validation.unsupported_claims),
                "claims": validation.unsupported_claims,
            },
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
    logger: AnyLogger,
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
            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    agent="supervisor",
                    event=f"{step}_retry",
                    document_id=document_id,
                    data={
                        "attempt": attempt,
                        "max_attempts": _MAX_ATTEMPTS,
                        "error": str(exc),
                    },
                )
            if attempt == _MAX_ATTEMPTS:
                logger.error(
                    agent="supervisor",
                    event=f"{step}_failed",
                    document_id=document_id,
                    data={
                        "attempt": attempt,
                        "error": str(exc),
                        "note": "Max retries exhausted.",
                    },
                )
                raise SupervisorWorkflowError(
                    f"[{step}] Pipeline step failed after {_MAX_ATTEMPTS} attempts "
                    f"(document_id={document_id!r}): {exc}"
                ) from exc
            # attempt < _MAX_ATTEMPTS: continue to retry
        except Exception as exc:
            # Non-retryable: wrap and surface immediately without retry.
            logger.error(
                agent="supervisor",
                event=f"{step}_failed",
                document_id=document_id,
                data={"error": str(exc), "note": "Non-retryable failure."},
            )
            raise SupervisorWorkflowError(
                f"[{step}] Pipeline step failed "
                f"(document_id={document_id!r}): {exc}"
            ) from exc
