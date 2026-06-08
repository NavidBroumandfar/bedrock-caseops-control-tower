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

CaseBriefStatus = Literal["ready_for_supervisor_review"]

RuntimeRequirementStatus = Literal["operator_supplied_at_live_runtime"]

CaseBriefArtifactKind = Literal[
    "intake_artifact",
    "source_artifact",
    "source_document_s3_key",
    "intake_artifact_s3_key",
]


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


class CaseBriefRetrievalRequest(BaseModel):
    """Supervisor-ready retrieval request preview derived from a CaseWorkItem."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    source_type: SourceType
    source_filename: str
    source_document_s3_key: str | None = None
    query_text: str | None = None

    @field_validator("document_id", "source_filename")
    @classmethod
    def request_fields_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value


class CaseBriefRuntimeRequirement(BaseModel):
    """Runtime prerequisite intentionally left for operator/live execution."""

    model_config = ConfigDict(frozen=True)

    name: str
    required_for: str
    status: RuntimeRequirementStatus

    @field_validator("name", "required_for")
    @classmethod
    def requirement_fields_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value


class CaseBriefArtifactReference(BaseModel):
    """Local or registered storage reference included in a case brief."""

    model_config = ConfigDict(frozen=True)

    kind: CaseBriefArtifactKind
    path_or_key: str

    @field_validator("path_or_key")
    @classmethod
    def artifact_reference_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path_or_key must be nonempty")
        return value


class SupervisorCaseBrief(BaseModel):
    """
    Local supervisor-ready case packet derived from a CaseWorkItem.

    The brief does not contain retrieved evidence, analysis, validation, or
    final output. It packages the deterministic context needed before the live
    supervisor pipeline is invoked.
    """

    model_config = ConfigDict(frozen=True)

    case_brief_id: str
    work_item_id: str
    document_id: str
    title: str
    source_type: SourceType
    source_filename: str
    document_date: str
    routing_lane: RoutingLane
    priority_hint: PriorityHint
    readiness_status: CaseBriefStatus
    next_step: NextStep
    retrieval_query_source: RetrievalQuerySource
    expected_retrieval_request: CaseBriefRetrievalRequest
    source_artifacts: list[CaseBriefArtifactReference]
    live_runtime_requirements: list[CaseBriefRuntimeRequirement]
    operator_notes: list[str]
    created_at: str

    @field_validator(
        "case_brief_id",
        "work_item_id",
        "document_id",
        "title",
        "source_filename",
        "document_date",
        "created_at",
    )
    @classmethod
    def brief_fields_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value

    @field_validator("created_at")
    @classmethod
    def brief_created_at_must_be_iso8601(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"created_at must be ISO 8601, got: {value!r}")
        return value

    @field_validator("source_artifacts", "live_runtime_requirements", "operator_notes")
    @classmethod
    def list_fields_must_not_be_empty(cls, value: list[object]) -> list[object]:
        if not value:
            raise ValueError("list field must not be empty")
        return value


class SupervisorCaseBriefResult(BaseModel):
    """Result returned after a SupervisorCaseBrief is written locally."""

    model_config = ConfigDict(frozen=True)

    case_brief: SupervisorCaseBrief
    artifact_path: str

    @field_validator("artifact_path")
    @classmethod
    def brief_artifact_path_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact_path must be nonempty")
        return value
