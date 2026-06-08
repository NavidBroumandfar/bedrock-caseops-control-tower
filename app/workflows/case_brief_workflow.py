"""
Local supervisor-ready case brief workflow.

This workflow starts from a CaseWorkItem and creates a deterministic
SupervisorCaseBrief. It is intentionally pre-runtime: no Databricks, AWS, S3,
Bedrock, Knowledge Base, retrieval, vector search, or agent calls are made here.
"""

import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas.case_context_models import (
    CaseBriefArtifactReference,
    CaseBriefRetrievalRequest,
    CaseBriefRuntimeRequirement,
    CaseWorkItem,
    SupervisorCaseBrief,
    SupervisorCaseBriefResult,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CASE_BRIEF_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "case_briefs"


class CaseBriefWorkflowError(Exception):
    """Raised when a local supervisor case brief cannot be built or written."""


def load_case_work_item(work_item_path: str | Path) -> CaseWorkItem:
    """Load and validate a local CaseWorkItem artifact."""
    path = Path(work_item_path).resolve()
    if not path.exists():
        raise CaseBriefWorkflowError(f"Case work item not found: {path}")
    if not path.is_file():
        raise CaseBriefWorkflowError(f"Case work item path is not a file: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaseBriefWorkflowError(f"Case work item is not valid JSON: {exc}") from exc

    try:
        return CaseWorkItem.model_validate(payload)
    except ValidationError as exc:
        raise CaseBriefWorkflowError(f"Case work item schema validation failed: {exc}") from exc


def build_supervisor_case_brief(work_item: CaseWorkItem) -> SupervisorCaseBrief:
    """
    Build a local supervisor-ready case brief from a CaseWorkItem.

    The brief previews the retrieval request and operator/runtime prerequisites
    without invoking the live supervisor pipeline.
    """
    return SupervisorCaseBrief(
        case_brief_id=_build_case_brief_id(work_item.work_item_id),
        work_item_id=work_item.work_item_id,
        document_id=work_item.document_id,
        title=_build_title(work_item),
        source_type=work_item.source_type,
        source_filename=work_item.source_filename,
        document_date=work_item.document_date,
        routing_lane=work_item.routing_lane,
        priority_hint=work_item.priority_hint,
        readiness_status="ready_for_supervisor_review",
        next_step=work_item.next_step,
        retrieval_query_source=work_item.retrieval_query_source,
        expected_retrieval_request=CaseBriefRetrievalRequest(
            document_id=work_item.document_id,
            source_type=work_item.source_type,
            source_filename=work_item.source_filename,
            source_document_s3_key=work_item.source_document_s3_key,
            query_text=work_item.retrieval_query,
        ),
        source_artifacts=_source_artifacts(work_item),
        live_runtime_requirements=_live_runtime_requirements(),
        operator_notes=_operator_notes(work_item),
        created_at=work_item.created_at,
    )


def run_supervisor_case_brief_workflow(
    work_item: CaseWorkItem,
    *,
    output_dir: Path | None = None,
) -> SupervisorCaseBriefResult:
    """
    Build and write a local SupervisorCaseBrief artifact.

    Artifacts are written to:
      {output_dir or outputs/case_briefs}/{document_id}/case_brief.json
    """
    case_brief = build_supervisor_case_brief(work_item)
    artifact_path = _write_case_brief(
        case_brief,
        output_dir or DEFAULT_CASE_BRIEF_OUTPUT_DIR,
    )
    return SupervisorCaseBriefResult(
        case_brief=case_brief,
        artifact_path=str(artifact_path),
    )


def run_supervisor_case_brief_from_file(
    work_item_path: str | Path,
    *,
    output_dir: Path | None = None,
) -> SupervisorCaseBriefResult:
    """Load a CaseWorkItem artifact and write a SupervisorCaseBrief artifact."""
    return run_supervisor_case_brief_workflow(
        load_case_work_item(work_item_path),
        output_dir=output_dir,
    )


def _build_case_brief_id(work_item_id: str) -> str:
    return f"brief-{work_item_id}"


def _build_title(work_item: CaseWorkItem) -> str:
    return f"{work_item.routing_lane}: {work_item.source_filename}"


def _source_artifacts(work_item: CaseWorkItem) -> list[CaseBriefArtifactReference]:
    artifacts = [
        CaseBriefArtifactReference(
            kind="intake_artifact",
            path_or_key=work_item.intake_artifact_path,
        ),
        CaseBriefArtifactReference(
            kind="source_artifact",
            path_or_key=work_item.source_artifact_path,
        ),
    ]
    if work_item.source_document_s3_key is not None:
        artifacts.append(
            CaseBriefArtifactReference(
                kind="source_document_s3_key",
                path_or_key=work_item.source_document_s3_key,
            )
        )
    if work_item.intake_artifact_s3_key is not None:
        artifacts.append(
            CaseBriefArtifactReference(
                kind="intake_artifact_s3_key",
                path_or_key=work_item.intake_artifact_s3_key,
            )
        )
    return artifacts


def _live_runtime_requirements() -> list[CaseBriefRuntimeRequirement]:
    return [
        CaseBriefRuntimeRequirement(
            name="BEDROCK_KB_ID",
            required_for="grounded retrieval",
            status="operator_supplied_at_live_runtime",
        ),
        CaseBriefRuntimeRequirement(
            name="BEDROCK_MODEL_ID",
            required_for="analysis and validation",
            status="operator_supplied_at_live_runtime",
        ),
        CaseBriefRuntimeRequirement(
            name="AWS_REGION",
            required_for="live Bedrock service calls",
            status="operator_supplied_at_live_runtime",
        ),
    ]


def _operator_notes(work_item: CaseWorkItem) -> list[str]:
    notes = [
        "Brief is local-only and has not run retrieval, analysis, validation, or agents.",
        "Invoke the live supervisor pipeline only after operator runtime configuration is present.",
    ]
    if work_item.retrieval_query is None:
        notes.append(
            "Retrieval query is absent; the live retrieval provider will use its deterministic fallback."
        )
    else:
        notes.append("Retrieval query was supplied by the intake handoff.")
    return notes


def _write_case_brief(case_brief: SupervisorCaseBrief, output_dir: Path) -> Path:
    artifact_dir = output_dir / case_brief.document_id
    artifact_path = artifact_dir / "case_brief.json"

    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(case_brief.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise CaseBriefWorkflowError(
            f"Could not write supervisor case brief artifact to {artifact_path}: {exc}"
        ) from exc

    return artifact_path
