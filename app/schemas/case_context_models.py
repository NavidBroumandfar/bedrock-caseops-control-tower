"""
Pydantic contracts for local case/control-tower context.

These models sit immediately downstream of IntakeResult. They do not represent
retrieval, analysis, validation, or final CaseOutput; they only package an
intake handoff into a deterministic work item that a control-tower queue can
inspect before live runtime work begins.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.intake_models import SourceType

RoutingLane = Literal[
    "regulatory_review",
    "security_review",
    "incident_review",
    "general_review",
]

PriorityHint = Literal["standard", "expedite"]

StorageMode = Literal["local", "s3"]

RetrievalQuerySource = Literal["submitter_note", "provider_fallback"]

ReadinessStatus = Literal["ready_for_grounded_retrieval"]

NextStep = Literal["run_supervisor_pipeline"]


class CaseWorkItem(BaseModel):
    """Deterministic local work item built from an IntakeResult."""

    model_config = ConfigDict(frozen=True)

    work_item_id: str
    document_id: str
    source_filename: str
    source_type: SourceType
    document_date: str
    intake_artifact_path: str
    source_artifact_path: str
    storage_mode: StorageMode
    source_document_s3_key: str | None = None
    intake_artifact_s3_key: str | None = None
    retrieval_query: str | None = None
    retrieval_query_source: RetrievalQuerySource
    routing_lane: RoutingLane
    priority_hint: PriorityHint
    readiness_status: ReadinessStatus
    next_step: NextStep
    created_at: str

    @field_validator(
        "work_item_id",
        "document_id",
        "source_filename",
        "document_date",
        "intake_artifact_path",
        "source_artifact_path",
        "created_at",
    )
    @classmethod
    def must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_iso8601(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"created_at must be ISO 8601, got: {value!r}")
        return value


class CaseWorkItemResult(BaseModel):
    """Result returned after a CaseWorkItem is written locally."""

    model_config = ConfigDict(frozen=True)

    work_item: CaseWorkItem
    artifact_path: str

    @field_validator("artifact_path")
    @classmethod
    def artifact_path_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact_path must be nonempty")
        return value
