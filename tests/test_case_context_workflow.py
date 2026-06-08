"""
Unit tests for the local case/control-tower context workflow.

The workflow starts from IntakeResult and stops at a deterministic CaseWorkItem.
It makes no Databricks, AWS, Bedrock, S3, retrieval, vector search, or agent
runtime calls.
"""

import json
from pathlib import Path

import pytest

from app.schemas.case_context_models import CaseWorkItem, CaseWorkItemResult
from app.schemas.intake_models import IntakeRecord, IntakeResult, StorageRegistration
from app.services.databricks_gold_adapter import consume_databricks_gold_payload_file
from app.workflows.case_context_workflow import (
    CaseContextWorkflowError,
    build_case_work_item,
    run_case_context_workflow,
)

_DOC_ID = "doc-20260608-context1"
_INTAKE_TIMESTAMP = "2026-06-08T10:00:00+00:00"
_GOLD_FIXTURE = Path("tests/fixtures/databricks_gold/sample_gold_payload.json")


def _make_intake_record(
    *,
    document_id: str = _DOC_ID,
    source_type: str = "FDA",
    original_filename: str = "warning_letter.txt",
    submitter_note: str | None = "FDA warning letter quality review.",
) -> IntakeRecord:
    return IntakeRecord(
        document_id=document_id,
        original_filename=original_filename,
        extension=".txt",
        absolute_path=f"/tmp/{document_id}/{original_filename}",
        file_size_bytes=2048,
        intake_timestamp=_INTAKE_TIMESTAMP,
        source_type=source_type,
        document_date="2026-06-08",
        submitter_note=submitter_note,
    )


def _make_intake_result(
    *,
    record: IntakeRecord | None = None,
    storage: StorageRegistration | None = None,
    document_id: str | None = None,
) -> IntakeResult:
    resolved_record = record or _make_intake_record()
    return IntakeResult(
        document_id=document_id or resolved_record.document_id,
        artifact_path=f"/tmp/outputs/intake/{resolved_record.document_id}.json",
        record=resolved_record,
        storage=storage,
    )


def test_build_case_work_item_returns_typed_work_item() -> None:
    intake = _make_intake_result()

    work_item = build_case_work_item(intake)

    assert isinstance(work_item, CaseWorkItem)


def test_build_case_work_item_maps_core_intake_fields() -> None:
    intake = _make_intake_result()

    work_item = build_case_work_item(intake)

    assert work_item.work_item_id == f"work-{_DOC_ID}"
    assert work_item.document_id == _DOC_ID
    assert work_item.source_filename == "warning_letter.txt"
    assert work_item.source_type == "FDA"
    assert work_item.document_date == "2026-06-08"
    assert work_item.intake_artifact_path == intake.artifact_path
    assert work_item.source_artifact_path == intake.record.absolute_path
    assert work_item.created_at == _INTAKE_TIMESTAMP


def test_build_case_work_item_preserves_submitter_note_as_retrieval_query() -> None:
    note = "Critical FDA recall requires immediate escalation review."
    intake = _make_intake_result(record=_make_intake_record(submitter_note=note))

    work_item = build_case_work_item(intake)

    assert work_item.retrieval_query == note
    assert work_item.retrieval_query_source == "submitter_note"


def test_build_case_work_item_uses_provider_fallback_when_no_submitter_note() -> None:
    intake = _make_intake_result(record=_make_intake_record(submitter_note=None))

    work_item = build_case_work_item(intake)

    assert work_item.retrieval_query is None
    assert work_item.retrieval_query_source == "provider_fallback"


def test_build_case_work_item_local_storage_mode() -> None:
    intake = _make_intake_result(storage=None)

    work_item = build_case_work_item(intake)

    assert work_item.storage_mode == "local"
    assert work_item.source_document_s3_key is None
    assert work_item.intake_artifact_s3_key is None


def test_build_case_work_item_s3_storage_mode_preserves_registered_keys() -> None:
    storage = StorageRegistration(
        bucket_name="caseops-test-bucket",
        source_document_key="documents/doc-20260608-context1/raw/warning_letter.txt",
        intake_artifact_key="artifacts/intake/doc-20260608-context1.json",
    )
    intake = _make_intake_result(storage=storage)

    work_item = build_case_work_item(intake)

    assert work_item.storage_mode == "s3"
    assert work_item.source_document_s3_key == storage.source_document_key
    assert work_item.intake_artifact_s3_key == storage.intake_artifact_key


@pytest.mark.parametrize(
    ("source_type", "expected_lane"),
    [
        ("FDA", "regulatory_review"),
        ("CISA", "security_review"),
        ("Incident", "incident_review"),
        ("Other", "general_review"),
    ],
)
def test_build_case_work_item_routes_by_source_type(
    source_type: str,
    expected_lane: str,
) -> None:
    intake = _make_intake_result(record=_make_intake_record(source_type=source_type))

    work_item = build_case_work_item(intake)

    assert work_item.routing_lane == expected_lane


def test_build_case_work_item_sets_standard_priority_by_default() -> None:
    intake = _make_intake_result(
        record=_make_intake_record(
            original_filename="quality_notice.txt",
            submitter_note="Routine quality review.",
        )
    )

    work_item = build_case_work_item(intake)

    assert work_item.priority_hint == "standard"


def test_build_case_work_item_sets_expedite_priority_from_note() -> None:
    intake = _make_intake_result(
        record=_make_intake_record(
            original_filename="quality_notice.txt",
            submitter_note="Critical issue requires immediate review.",
        )
    )

    work_item = build_case_work_item(intake)

    assert work_item.priority_hint == "expedite"


def test_build_case_work_item_sets_expedite_priority_from_filename() -> None:
    intake = _make_intake_result(
        record=_make_intake_record(
            original_filename="product_recall_notice.txt",
            submitter_note=None,
        )
    )

    work_item = build_case_work_item(intake)

    assert work_item.priority_hint == "expedite"


def test_build_case_work_item_sets_ready_next_step() -> None:
    intake = _make_intake_result()

    work_item = build_case_work_item(intake)

    assert work_item.readiness_status == "ready_for_grounded_retrieval"
    assert work_item.next_step == "run_supervisor_pipeline"


def test_build_case_work_item_rejects_mismatched_intake_document_ids() -> None:
    intake = _make_intake_result(document_id="doc-20260608-different")

    with pytest.raises(CaseContextWorkflowError, match="must match"):
        build_case_work_item(intake)


def test_build_case_work_item_rejects_unknown_source_type() -> None:
    intake = _make_intake_result(record=_make_intake_record(source_type="Unknown"))

    with pytest.raises(CaseContextWorkflowError, match="Unsupported source_type"):
        build_case_work_item(intake)


def test_run_case_context_workflow_returns_result_and_writes_artifact(tmp_path: Path) -> None:
    intake = _make_intake_result()

    result = run_case_context_workflow(intake, output_dir=tmp_path)

    assert isinstance(result, CaseWorkItemResult)
    artifact_path = Path(result.artifact_path)
    assert artifact_path == tmp_path / _DOC_ID / "work_item.json"
    assert artifact_path.exists()

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["work_item_id"] == f"work-{_DOC_ID}"
    assert artifact["document_id"] == _DOC_ID
    assert artifact["next_step"] == "run_supervisor_pipeline"


def test_run_case_context_workflow_raises_when_output_root_is_file(tmp_path: Path) -> None:
    output_root = tmp_path / "not_a_directory"
    output_root.write_text("occupied", encoding="utf-8")

    with pytest.raises(CaseContextWorkflowError, match="Could not write"):
        run_case_context_workflow(_make_intake_result(), output_dir=output_root)


def test_case_context_workflow_accepts_databricks_gold_intake_result(
    tmp_path: Path,
) -> None:
    intake = consume_databricks_gold_payload_file(
        _GOLD_FIXTURE,
        output_dir=tmp_path / "gold_intake",
    )

    result = run_case_context_workflow(
        intake,
        output_dir=tmp_path / "case_work_items",
    )

    assert result.work_item.document_id == intake.document_id
    assert result.work_item.source_filename == intake.record.original_filename
    assert result.work_item.source_type == intake.record.source_type
    assert result.work_item.retrieval_query == intake.record.submitter_note
    assert result.work_item.routing_lane == "regulatory_review"
    assert Path(result.artifact_path).exists()
