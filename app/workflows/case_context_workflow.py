"""
Local case/control-tower context workflow.

This workflow starts from IntakeResult and creates a deterministic CaseWorkItem.
It is intentionally pre-retrieval: no Bedrock, Knowledge Base, S3, Databricks,
Delta Share, vector search, agent, or network calls are made here.
"""

import json
from pathlib import Path

from app.schemas.case_context_models import (
    CaseWorkItem,
    CaseWorkItemResult,
    PriorityHint,
    RetrievalQuerySource,
    RoutingLane,
    StorageMode,
)
from app.schemas.intake_models import IntakeResult, SourceType

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CASE_CONTEXT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "case_work_items"

_ROUTING_LANES: dict[SourceType, RoutingLane] = {
    "FDA": "regulatory_review",
    "CISA": "security_review",
    "Incident": "incident_review",
    "Other": "general_review",
}

_EXPEDITE_TERMS = (
    "critical",
    "urgent",
    "immediate",
    "escalate",
    "escalation",
    "ransomware",
    "recall",
)


class CaseContextWorkflowError(Exception):
    """Raised when a local case context cannot be built or written."""


def build_case_work_item(intake: IntakeResult) -> CaseWorkItem:
    """
    Build a deterministic work item from an existing IntakeResult.

    The returned object is a local control-tower context only. It does not
    inspect document contents or perform any retrieval/inference.
    """
    _validate_intake_consistency(intake)
    retrieval_query = intake.record.submitter_note or None
    source_type = _resolve_source_type(intake.record.source_type)

    return CaseWorkItem(
        work_item_id=_build_work_item_id(intake.document_id),
        document_id=intake.document_id,
        source_filename=intake.record.original_filename,
        source_type=source_type,
        document_date=intake.record.document_date,
        intake_artifact_path=intake.artifact_path,
        source_artifact_path=intake.record.absolute_path,
        storage_mode=_storage_mode(intake),
        source_document_s3_key=(
            intake.storage.source_document_key if intake.storage is not None else None
        ),
        intake_artifact_s3_key=(
            intake.storage.intake_artifact_key if intake.storage is not None else None
        ),
        retrieval_query=retrieval_query,
        retrieval_query_source=_retrieval_query_source(retrieval_query),
        routing_lane=_routing_lane(source_type),
        priority_hint=_priority_hint(intake),
        readiness_status="ready_for_grounded_retrieval",
        next_step="run_supervisor_pipeline",
        created_at=intake.record.intake_timestamp,
    )


def run_case_context_workflow(
    intake: IntakeResult,
    *,
    output_dir: Path | None = None,
) -> CaseWorkItemResult:
    """
    Build and write a local CaseWorkItem artifact.

    Artifacts are written to:
      {output_dir or outputs/case_work_items}/{document_id}/work_item.json
    """
    work_item = build_case_work_item(intake)
    artifact_path = _write_case_work_item(
        work_item,
        output_dir or DEFAULT_CASE_CONTEXT_OUTPUT_DIR,
    )
    return CaseWorkItemResult(
        work_item=work_item,
        artifact_path=str(artifact_path),
    )


def _validate_intake_consistency(intake: IntakeResult) -> None:
    if intake.document_id != intake.record.document_id:
        raise CaseContextWorkflowError(
            "IntakeResult.document_id must match IntakeResult.record.document_id "
            f"(got {intake.document_id!r} and {intake.record.document_id!r})"
        )


def _build_work_item_id(document_id: str) -> str:
    return f"work-{document_id}"


def _resolve_source_type(value: str) -> SourceType:
    if value not in _ROUTING_LANES:
        raise CaseContextWorkflowError(f"Unsupported source_type for case context: {value!r}")
    return value  # type: ignore[return-value]


def _storage_mode(intake: IntakeResult) -> StorageMode:
    return "s3" if intake.storage is not None else "local"


def _retrieval_query_source(retrieval_query: str | None) -> RetrievalQuerySource:
    return "submitter_note" if retrieval_query is not None else "provider_fallback"


def _routing_lane(source_type: SourceType) -> RoutingLane:
    return _ROUTING_LANES[source_type]


def _priority_hint(intake: IntakeResult) -> PriorityHint:
    searchable_text = " ".join(
        value
        for value in (
            intake.record.source_type,
            intake.record.original_filename,
            intake.record.submitter_note or "",
        )
        if value
    ).lower()

    if any(term in searchable_text for term in _EXPEDITE_TERMS):
        return "expedite"
    return "standard"


def _write_case_work_item(work_item: CaseWorkItem, output_dir: Path) -> Path:
    artifact_dir = output_dir / work_item.document_id
    artifact_path = artifact_dir / "work_item.json"

    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(work_item.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise CaseContextWorkflowError(
            f"Could not write case work item artifact to {artifact_path}: {exc}"
        ) from exc

    return artifact_path
