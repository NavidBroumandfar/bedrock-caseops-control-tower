"""
Unit tests for the local supervisor case brief workflow.

The workflow starts from CaseWorkItem and stops at a deterministic
SupervisorCaseBrief. It makes no Databricks, AWS, Bedrock, S3, retrieval,
vector search, or agent runtime calls.
"""

import json
from pathlib import Path

import pytest

from app.schemas.case_context_models import (
    CaseWorkItem,
    SupervisorCaseBrief,
    SupervisorCaseBriefResult,
)
from app.services.databricks_gold_adapter import consume_databricks_gold_payload_file
from app.workflows.case_brief_workflow import (
    CaseBriefWorkflowError,
    build_supervisor_case_brief,
    load_case_work_item,
    run_supervisor_case_brief_from_file,
    run_supervisor_case_brief_workflow,
)
from app.workflows.case_context_workflow import run_case_context_workflow

_DOC_ID = "doc-20260608-brief1"
_CREATED_AT = "2026-06-08T10:00:00+00:00"
_GOLD_FIXTURE = Path("tests/fixtures/databricks_gold/sample_gold_payload.json")


def _make_work_item(
    *,
    document_id: str = _DOC_ID,
    retrieval_query: str | None = "FDA warning letter quality review.",
    source_document_s3_key: str | None = None,
    intake_artifact_s3_key: str | None = None,
) -> CaseWorkItem:
    return CaseWorkItem(
        work_item_id=f"work-{document_id}",
        document_id=document_id,
        source_filename="warning_letter.txt",
        source_type="FDA",
        document_date="2026-06-08",
        intake_artifact_path=f"/tmp/outputs/intake/{document_id}.json",
        source_artifact_path=f"/tmp/{document_id}/warning_letter.txt",
        storage_mode="s3" if source_document_s3_key else "local",
        source_document_s3_key=source_document_s3_key,
        intake_artifact_s3_key=intake_artifact_s3_key,
        retrieval_query=retrieval_query,
        retrieval_query_source=(
            "submitter_note" if retrieval_query is not None else "provider_fallback"
        ),
        routing_lane="regulatory_review",
        priority_hint="standard",
        readiness_status="ready_for_grounded_retrieval",
        next_step="run_supervisor_pipeline",
        created_at=_CREATED_AT,
    )


def test_build_supervisor_case_brief_returns_typed_brief() -> None:
    brief = build_supervisor_case_brief(_make_work_item())

    assert isinstance(brief, SupervisorCaseBrief)


def test_build_supervisor_case_brief_maps_core_work_item_fields() -> None:
    work_item = _make_work_item()

    brief = build_supervisor_case_brief(work_item)

    assert brief.case_brief_id == f"brief-{work_item.work_item_id}"
    assert brief.work_item_id == work_item.work_item_id
    assert brief.document_id == work_item.document_id
    assert brief.title == "regulatory_review: warning_letter.txt"
    assert brief.source_type == work_item.source_type
    assert brief.source_filename == work_item.source_filename
    assert brief.document_date == work_item.document_date
    assert brief.routing_lane == work_item.routing_lane
    assert brief.priority_hint == work_item.priority_hint
    assert brief.created_at == work_item.created_at


def test_build_supervisor_case_brief_previews_retrieval_request() -> None:
    query = "Critical FDA recall requires immediate escalation review."
    work_item = _make_work_item(retrieval_query=query)

    brief = build_supervisor_case_brief(work_item)

    request = brief.expected_retrieval_request
    assert request.document_id == work_item.document_id
    assert request.source_type == work_item.source_type
    assert request.source_filename == work_item.source_filename
    assert request.source_document_s3_key is None
    assert request.query_text == query
    assert brief.retrieval_query_source == "submitter_note"


def test_build_supervisor_case_brief_preserves_provider_fallback_query_mode() -> None:
    work_item = _make_work_item(retrieval_query=None)

    brief = build_supervisor_case_brief(work_item)

    assert brief.expected_retrieval_request.query_text is None
    assert brief.retrieval_query_source == "provider_fallback"
    assert any("deterministic fallback" in note for note in brief.operator_notes)


def test_build_supervisor_case_brief_includes_local_artifact_references() -> None:
    work_item = _make_work_item()

    brief = build_supervisor_case_brief(work_item)

    references = {artifact.kind: artifact.path_or_key for artifact in brief.source_artifacts}
    assert references["intake_artifact"] == work_item.intake_artifact_path
    assert references["source_artifact"] == work_item.source_artifact_path
    assert "source_document_s3_key" not in references
    assert "intake_artifact_s3_key" not in references


def test_build_supervisor_case_brief_includes_s3_artifact_references() -> None:
    work_item = _make_work_item(
        source_document_s3_key="documents/doc-20260608-brief1/raw/warning_letter.txt",
        intake_artifact_s3_key="artifacts/intake/doc-20260608-brief1.json",
    )

    brief = build_supervisor_case_brief(work_item)

    references = {artifact.kind: artifact.path_or_key for artifact in brief.source_artifacts}
    assert references["source_document_s3_key"] == work_item.source_document_s3_key
    assert references["intake_artifact_s3_key"] == work_item.intake_artifact_s3_key
    assert brief.expected_retrieval_request.source_document_s3_key == (
        work_item.source_document_s3_key
    )


def test_build_supervisor_case_brief_lists_live_runtime_requirements() -> None:
    brief = build_supervisor_case_brief(_make_work_item())

    requirement_names = {requirement.name for requirement in brief.live_runtime_requirements}
    assert requirement_names == {"BEDROCK_KB_ID", "BEDROCK_MODEL_ID", "AWS_REGION"}
    assert all(
        requirement.status == "operator_supplied_at_live_runtime"
        for requirement in brief.live_runtime_requirements
    )


def test_build_supervisor_case_brief_sets_local_readiness_status() -> None:
    brief = build_supervisor_case_brief(_make_work_item())

    assert brief.readiness_status == "ready_for_supervisor_review"
    assert brief.next_step == "run_supervisor_pipeline"
    assert any("local-only" in note for note in brief.operator_notes)


def test_run_supervisor_case_brief_workflow_writes_artifact(tmp_path: Path) -> None:
    result = run_supervisor_case_brief_workflow(_make_work_item(), output_dir=tmp_path)

    assert isinstance(result, SupervisorCaseBriefResult)
    artifact_path = Path(result.artifact_path)
    assert artifact_path == tmp_path / _DOC_ID / "case_brief.json"
    assert artifact_path.exists()

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["case_brief_id"] == f"brief-work-{_DOC_ID}"
    assert artifact["document_id"] == _DOC_ID
    assert artifact["readiness_status"] == "ready_for_supervisor_review"


def test_run_supervisor_case_brief_workflow_raises_when_output_root_is_file(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "not_a_directory"
    output_root.write_text("occupied", encoding="utf-8")

    with pytest.raises(CaseBriefWorkflowError, match="Could not write"):
        run_supervisor_case_brief_workflow(_make_work_item(), output_dir=output_root)


def test_load_case_work_item_loads_valid_artifact(tmp_path: Path) -> None:
    path = tmp_path / "work_item.json"
    path.write_text(
        json.dumps(_make_work_item().model_dump(mode="json")),
        encoding="utf-8",
    )

    work_item = load_case_work_item(path)

    assert work_item.document_id == _DOC_ID


def test_load_case_work_item_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CaseBriefWorkflowError, match="not found"):
        load_case_work_item(tmp_path / "missing.json")


def test_load_case_work_item_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "work_item.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CaseBriefWorkflowError, match="not valid JSON"):
        load_case_work_item(path)


def test_load_case_work_item_rejects_schema_error(tmp_path: Path) -> None:
    path = tmp_path / "work_item.json"
    path.write_text(json.dumps({"document_id": _DOC_ID}), encoding="utf-8")

    with pytest.raises(CaseBriefWorkflowError, match="schema validation failed"):
        load_case_work_item(path)


def test_run_supervisor_case_brief_from_file_writes_artifact(tmp_path: Path) -> None:
    work_item_path = tmp_path / "work_item.json"
    work_item_path.write_text(
        json.dumps(_make_work_item().model_dump(mode="json")),
        encoding="utf-8",
    )

    result = run_supervisor_case_brief_from_file(
        work_item_path,
        output_dir=tmp_path / "briefs",
    )

    assert result.case_brief.document_id == _DOC_ID
    assert Path(result.artifact_path).exists()


def test_case_brief_workflow_accepts_databricks_gold_work_item(tmp_path: Path) -> None:
    intake = consume_databricks_gold_payload_file(
        _GOLD_FIXTURE,
        output_dir=tmp_path / "gold_intake",
    )
    work_item_result = run_case_context_workflow(
        intake,
        output_dir=tmp_path / "case_work_items",
    )

    brief_result = run_supervisor_case_brief_workflow(
        work_item_result.work_item,
        output_dir=tmp_path / "case_briefs",
    )

    brief = brief_result.case_brief
    assert brief.document_id == intake.document_id
    assert brief.expected_retrieval_request.query_text == intake.record.submitter_note
    assert brief.routing_lane == "regulatory_review"
    assert Path(brief_result.artifact_path).exists()
