"""
Tests for the Databricks Gold export payload consumer adapter.

The adapter is local-only and converts a schema-versioned upstream Gold record
into the existing IntakeResult handoff used by the Bedrock pipeline.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.databricks_gold_models import (
    GOLD_EXPORT_PRODUCER,
    GOLD_EXPORT_SCHEMA_VERSION,
    DatabricksGoldExportPayload,
    DatabricksGoldRecord,
)
from app.schemas.intake_models import IntakeResult
from app.services.databricks_gold_adapter import (
    DatabricksGoldAdapterError,
    consume_databricks_gold_payload,
    consume_databricks_gold_payload_file,
    load_databricks_gold_payload,
)

_FIXTURE = Path("tests/fixtures/databricks_gold/sample_gold_payload.json")


@pytest.fixture()
def gold_payload() -> DatabricksGoldExportPayload:
    return load_databricks_gold_payload(_FIXTURE)


def _payload_dict() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _record_dict() -> dict[str, object]:
    payload = _payload_dict()
    records = payload["records"]
    assert isinstance(records, list)
    first_record = records[0]
    assert isinstance(first_record, dict)
    return first_record


def test_load_databricks_gold_payload_returns_typed_payload() -> None:
    payload = load_databricks_gold_payload(_FIXTURE)

    assert isinstance(payload, DatabricksGoldExportPayload)
    assert payload.schema_version == GOLD_EXPORT_SCHEMA_VERSION
    assert payload.producer == GOLD_EXPORT_PRODUCER
    assert len(payload.records) == 1


def test_gold_record_preserves_upstream_trace_identifiers(
    gold_payload: DatabricksGoldExportPayload,
) -> None:
    record = gold_payload.records[0]

    assert record.gold_record_id == "gold-fda-20260608-001"
    assert record.source_document_id == "src-fda-warning-001"
    assert record.lineage.bronze_record_id == "bronze-fda-001"
    assert record.lineage.silver_record_id == "silver-fda-001"


def test_consume_payload_file_returns_existing_intake_result_contract(
    tmp_path: Path,
) -> None:
    result = consume_databricks_gold_payload_file(_FIXTURE, output_dir=tmp_path)

    assert isinstance(result, IntakeResult)
    assert result.storage is None


def test_consume_payload_maps_gold_record_to_intake_handoff(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    result = consume_databricks_gold_payload(gold_payload, output_dir=tmp_path)
    record = gold_payload.records[0]

    assert result.record.original_filename == record.source_filename
    assert result.record.extension == ".txt"
    assert result.record.source_type == record.source_type
    assert result.record.document_date == record.document_date
    assert result.record.submitter_note == record.retrieval_query


def test_consume_payload_writes_intake_artifact(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    result = consume_databricks_gold_payload(gold_payload, output_dir=tmp_path)

    intake_artifact = Path(result.artifact_path)
    assert intake_artifact.exists()
    assert intake_artifact.name == "intake.json"

    data = json.loads(intake_artifact.read_text(encoding="utf-8"))
    assert data["document_id"] == result.document_id
    assert data["original_filename"] == "fda_warning_letter_gold.txt"
    assert data["submitter_note"] == gold_payload.records[0].retrieval_query


def test_consume_payload_writes_gold_record_snapshot(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    result = consume_databricks_gold_payload(gold_payload, output_dir=tmp_path)

    gold_record_snapshot = Path(result.record.absolute_path)
    assert gold_record_snapshot.exists()
    assert gold_record_snapshot.name == "gold_record.json"
    assert result.record.file_size_bytes == gold_record_snapshot.stat().st_size

    snapshot = json.loads(gold_record_snapshot.read_text(encoding="utf-8"))
    assert snapshot["gold_record_id"] == gold_payload.records[0].gold_record_id
    assert snapshot["lineage"]["source_document_id"] == (
        gold_payload.records[0].source_document_id
    )


def test_consume_payload_generates_caseops_document_id(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    result = consume_databricks_gold_payload(gold_payload, output_dir=tmp_path)

    assert result.document_id.startswith("doc-")
    parts = result.document_id.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8
    assert len(parts[2]) == 8


def test_consume_payload_requires_record_id_for_multi_record_payload(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    second_record = gold_payload.records[0].model_copy(
        update={
            "gold_record_id": "gold-fda-20260608-002",
            "lineage": gold_payload.records[0].lineage.model_copy(
                update={"gold_record_id": "gold-fda-20260608-002"}
            ),
        }
    )
    payload = gold_payload.model_copy(update={"records": [gold_payload.records[0], second_record]})

    with pytest.raises(DatabricksGoldAdapterError, match="gold_record_id is required"):
        consume_databricks_gold_payload(payload, output_dir=tmp_path)


def test_consume_payload_selects_requested_record(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    second_record = gold_payload.records[0].model_copy(
        update={
            "gold_record_id": "gold-fda-20260608-002",
            "source_filename": "second_gold_record.txt",
            "lineage": gold_payload.records[0].lineage.model_copy(
                update={"gold_record_id": "gold-fda-20260608-002"}
            ),
        }
    )
    payload = gold_payload.model_copy(update={"records": [gold_payload.records[0], second_record]})

    result = consume_databricks_gold_payload(
        payload,
        gold_record_id="gold-fda-20260608-002",
        output_dir=tmp_path,
    )

    assert result.record.original_filename == "second_gold_record.txt"


def test_consume_payload_raises_for_missing_record_id(
    gold_payload: DatabricksGoldExportPayload,
    tmp_path: Path,
) -> None:
    with pytest.raises(DatabricksGoldAdapterError, match="was not found"):
        consume_databricks_gold_payload(
            gold_payload,
            gold_record_id="missing-record",
            output_dir=tmp_path,
        )


def test_load_payload_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DatabricksGoldAdapterError, match="not found"):
        load_databricks_gold_payload(tmp_path / "missing.json")


def test_load_payload_raises_for_invalid_json(tmp_path: Path) -> None:
    payload_path = tmp_path / "bad.json"
    payload_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(DatabricksGoldAdapterError, match="not valid JSON"):
        load_databricks_gold_payload(payload_path)


def test_load_payload_raises_for_schema_validation_failure(tmp_path: Path) -> None:
    raw_payload = _payload_dict()
    raw_payload["schema_version"] = "wrong-version"
    payload_path = tmp_path / "wrong_version.json"
    payload_path.write_text(json.dumps(raw_payload), encoding="utf-8")

    with pytest.raises(DatabricksGoldAdapterError, match="schema validation failed"):
        load_databricks_gold_payload(payload_path)


def test_payload_rejects_empty_records() -> None:
    raw_payload = _payload_dict()
    raw_payload["records"] = []

    with pytest.raises(ValidationError, match="records"):
        DatabricksGoldExportPayload.model_validate(raw_payload)


def test_payload_rejects_duplicate_gold_record_ids() -> None:
    raw_payload = _payload_dict()
    record = _record_dict()
    raw_payload["records"] = [record, record]

    with pytest.raises(ValidationError, match="unique"):
        DatabricksGoldExportPayload.model_validate(raw_payload)


def test_record_rejects_path_like_source_filename() -> None:
    raw_record = _record_dict()
    raw_record["source_filename"] = "/tmp/private/source.txt"

    with pytest.raises(ValidationError, match="basename"):
        DatabricksGoldRecord.model_validate(raw_record)


def test_record_rejects_lineage_mismatch() -> None:
    raw_record = _record_dict()
    lineage = raw_record["lineage"]
    assert isinstance(lineage, dict)
    lineage["gold_record_id"] = "different-gold-id"

    with pytest.raises(ValidationError, match="lineage.gold_record_id"):
        DatabricksGoldRecord.model_validate(raw_record)


def test_record_rejects_secret_like_custom_metadata_keys() -> None:
    raw_record = _record_dict()
    raw_record["custom_metadata"] = {"workspace_url": "redacted"}

    with pytest.raises(ValidationError, match="public-safe"):
        DatabricksGoldRecord.model_validate(raw_record)
