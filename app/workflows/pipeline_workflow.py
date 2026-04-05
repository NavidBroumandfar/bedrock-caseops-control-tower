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

  No output writing, no CloudWatch logging, no S3 archiving belongs here.
  Those are E-phase concerns.

Session ID:
  A session_id of the form "sess-{8 hex chars}" is generated at the start of
  each run using uuid4 and injected into the final CaseOutput before returning.
  No session management subsystem is built here.

Layering preserved:
  intake layer        → owns registration
  supervisor workflow → owns retrieval → analysis → validation sequencing
  tool executor       → owns output assembly and escalation logic
  pipeline workflow   → owns end-to-end orchestration and session propagation
  persistence/logging → E-phase
"""

import uuid

from app.agents.analysis_agent import AnalysisAgent
from app.agents.tool_executor_agent import ToolExecutorAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.intake_models import IntakeResult
from app.schemas.output_models import CaseOutput
from app.schemas.retrieval_contract import RetrievalProvider
from app.workflows.supervisor_workflow import SupervisorWorkflowError, run_supervisor


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
) -> CaseOutput:
    """
    Orchestrate the full pipeline from intake handoff to final CaseOutput.

    Steps:
      1. Generate a session_id for this pipeline run.
      2. Run the supervisor workflow (retrieval → analysis → validation).
      3. Run the tool executor to assemble the typed CaseOutput.
      4. Inject session_id into the final output.

    Dependencies are injected as keyword-only arguments — no service clients
    are constructed here.  The caller is responsible for building and wiring
    concrete providers and agents before invoking the pipeline.

    Returns a typed CaseOutput on both the success path and the empty-retrieval
    path.  Raises PipelineWorkflowError if any pipeline step fails, always
    chaining the original exception via __cause__.
    """
    document_id = intake.document_id
    session_id = _generate_session_id()

    # ── step 1: supervisor workflow (retrieval → analysis → validation) ────────
    try:
        supervisor_result = run_supervisor(
            intake,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
        )
    except SupervisorWorkflowError as exc:
        raise PipelineWorkflowError(
            f"[supervisor] Pipeline failed "
            f"(document_id={document_id!r}, session_id={session_id!r}): {exc}"
        ) from exc
    except Exception as exc:
        raise PipelineWorkflowError(
            f"[supervisor] Unexpected pipeline failure "
            f"(document_id={document_id!r}, session_id={session_id!r}): {exc}"
        ) from exc

    # ── step 2: tool executor (output assembly + escalation logic) ─────────────
    try:
        case_output = tool_executor.run(supervisor_result)
    except Exception as exc:
        raise PipelineWorkflowError(
            f"[tool_executor] Pipeline failed "
            f"(document_id={document_id!r}, session_id={session_id!r}): {exc}"
        ) from exc

    # ── step 3: inject session_id and return the final typed output ───────────
    return case_output.model_copy(update={"session_id": session_id})


def _generate_session_id() -> str:
    """Return a short, human-readable session identifier: 'sess-{8 hex chars}'."""
    return f"sess-{uuid.uuid4().hex[:8]}"
