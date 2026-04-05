"""
Pydantic models for the supervisor workflow result — D-0.

SupervisorResult — typed output produced by run_supervisor() after one
                   full orchestration cycle through the pipeline.

This model carries the results of:
  intake handoff → retrieval → analysis (optional) → validation (optional)

analysis and validation are Optional because the supervisor skips them when
retrieval returns no evidence.  The Tool Executor (D-1) reads these fields to
determine the escalation path and format the final CaseOutput — that logic
does not belong here.

No final output fields (escalation_required, escalation_reason, CaseOutput)
belong in this model.  Those are D-1 Tool Executor concerns.
"""

from pydantic import BaseModel

from app.schemas.analysis_models import AnalysisOutput
from app.schemas.intake_models import IntakeResult
from app.schemas.retrieval_models import RetrievalResult
from app.schemas.validation_models import ValidationOutput


class SupervisorResult(BaseModel):
    """
    Typed output of the supervisor / planner workflow.

    retrieval   — always present; status reflects "success" or "empty".
    analysis    — None only when retrieval was empty (analysis was not attempted).
    validation  — None only when analysis was skipped (retrieval empty path).

    Downstream callers (D-1 Tool Executor) must check whether analysis and
    validation are None before applying escalation logic.
    """

    document_id: str
    intake: IntakeResult
    retrieval: RetrievalResult
    analysis: AnalysisOutput | None = None
    validation: ValidationOutput | None = None
