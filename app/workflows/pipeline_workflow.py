"""
D-2 end-to-end pipeline orchestration workflow.

Connects the A-3 intake handoff → D-0 supervisor workflow → D-1 tool executor
into one typed pipeline that produces a final CaseOutput.

Public surface:
  run_pipeline           — orchestrate the full pipeline; return CaseOutput
  PipelineWorkflowError  — raised when any pipeline step fails at this boundary

Architecture contract:
  Input  — IntakeResult (A-3 handoff) + injected dependencies
  Output — CaseOutput with session_id populated

  No boto3 clients or AWS service objects are instantiated here.  All AWS
  interaction stays in app/services/.  Dependencies are injected explicitly
  so this workflow is testable without live AWS calls.

  Output writing and S3 archiving remain E-1 concerns.

Session ID:
  A session_id of the form "sess-{8 hex chars}" is generated at the start of
  each run using uuid4 and injected into the final CaseOutput before returning.
  No session management subsystem is built here.

Structured logging:
  A PipelineLogger is accepted as an optional keyword argument.  When omitted,
  a NoOpLogger is used so callers that do not care about logging are unaffected.
  Logging never influences control flow.

Layering preserved:
  intake layer        → owns registration
  supervisor workflow → owns retrieval → analysis → validation sequencing
  tool executor       → owns output assembly and escalation logic
  pipeline workflow   → owns end-to-end orchestration and session propagation
  persistence/logging → E-phase (logging instrumented here in E-0)
"""

import uuid
from typing import Union

from app.agents.analysis_agent import AnalysisAgent
from app.agents.tool_executor_agent import ToolExecutorAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.intake_models import IntakeResult
from app.schemas.output_models import CaseOutput
from app.schemas.retrieval_contract import RetrievalProvider
from app.utils.logging_utils import NoOpLogger, PipelineLogger
from app.workflows.supervisor_workflow import SupervisorWorkflowError, run_supervisor

# Union type accepted wherever a logger is required, so callers do not need to
# import both PipelineLogger and NoOpLogger.
AnyLogger = Union[PipelineLogger, NoOpLogger]


class PipelineWorkflowError(Exception):
    """
    Raised when any step of the end-to-end pipeline fails at the orchestration boundary.

    The message always names the failing step, the document_id, and the
    session_id so callers can identify the failure without inspecting the
    exception chain.  The original exception is always chained via __cause__.
    """


def run_pipeline(
    intake: IntakeResult,
    *,
    retrieval_provider: RetrievalProvider,
    analysis_agent: AnalysisAgent,
    validation_agent: ValidationAgent,
    tool_executor: ToolExecutorAgent,
    logger: AnyLogger | None = None,
) -> CaseOutput:
    """
    Orchestrate the full pipeline from intake handoff to final CaseOutput.

    Steps:
      1. Generate a session_id for this pipeline run.
      2. Emit session_start log event.
      3. Run the supervisor workflow (retrieval → analysis → validation).
      4. Run the tool executor to assemble the typed CaseOutput.
      5. Inject session_id into the final output and emit completion event.

    Dependencies are injected as keyword-only arguments — no service clients
    are constructed here.  The caller is responsible for building and wiring
    concrete providers and agents before invoking the pipeline.

    `logger` is optional.  When omitted a NoOpLogger is used, so existing
    call sites that do not pass a logger continue to work without modification.

    Returns a typed CaseOutput on both the success path and the empty-retrieval
    path.  Raises PipelineWorkflowError if any pipeline step fails, always
    chaining the original exception via __cause__.
    """
    _logger: AnyLogger = logger or NoOpLogger()

    document_id = intake.document_id
    session_id = _generate_session_id()

    _logger.info(
        agent="pipeline",
        event="session_start",
        document_id=document_id,
        data={
            "session_id": session_id,
            "source_filename": intake.record.original_filename,
            "source_type": intake.record.source_type,
        },
    )

    # ── step 1: supervisor workflow (retrieval → analysis → validation) ────────
    _logger.info(
        agent="pipeline",
        event="intake_handoff_received",
        document_id=document_id,
        data={"session_id": session_id},
    )

    try:
        supervisor_result = run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            logger=_logger,
        )
    except SupervisorWorkflowError as exc:
        _logger.error(
            agent="pipeline",
            event="pipeline_failed",
            document_id=document_id,
            data={"session_id": session_id, "step": "supervisor", "error": str(exc)},
        )
        raise PipelineWorkflowError(
            f"[supervisor] Pipeline failed "
            f"(document_id={document_id!r}, session_id={session_id!r}): {exc}"
        ) from exc
    except Exception as exc:
        _logger.error(
            agent="pipeline",
            event="pipeline_failed",
            document_id=document_id,
            data={"session_id": session_id, "step": "supervisor", "error": str(exc)},
        )
        raise PipelineWorkflowError(
            f"[supervisor] Unexpected pipeline failure "
            f"(document_id={document_id!r}, session_id={session_id!r}): {exc}"
        ) from exc

    # ── step 2: tool executor (output assembly + escalation logic) ─────────────
    try:
        case_output = tool_executor.run(supervisor_result)
    except Exception as exc:
        _logger.error(
            agent="pipeline",
            event="pipeline_failed",
            document_id=document_id,
            data={"session_id": session_id, "step": "tool_executor", "error": str(exc)},
        )
        raise PipelineWorkflowError(
            f"[tool_executor] Pipeline failed "
            f"(document_id={document_id!r}, session_id={session_id!r}): {exc}"
        ) from exc

    # ── step 3: inject session_id and return the final typed output ────────────
    final_output = case_output.model_copy(update={"session_id": session_id})

    if final_output.escalation_required:
        _logger.info(
            agent="pipeline",
            event="escalation_triggered",
            document_id=document_id,
            data={
                "session_id": session_id,
                "escalation_reason": final_output.escalation_reason,
                "severity": final_output.severity,
                "confidence_score": final_output.confidence_score,
            },
        )

    _logger.info(
        agent="pipeline",
        event="output_generation_complete",
        document_id=document_id,
        data={
            "session_id": session_id,
            "severity": final_output.severity,
            "category": final_output.category,
            "confidence_score": final_output.confidence_score,
            "escalation_required": final_output.escalation_required,
            "citation_count": len(final_output.citations),
        },
    )

    return final_output


def _generate_session_id() -> str:
    """Return a short, human-readable session identifier: 'sess-{8 hex chars}'."""
    return f"sess-{uuid.uuid4().hex[:8]}"
