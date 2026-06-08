"""
Cross-repo contract regression tests for sanitized Databricks Gold payloads.

These tests intentionally stop at the Bedrock intake boundary:
Databricks Gold payload JSON -> DatabricksGoldExportPayload -> IntakeResult
-> RetrievalRequest translation.

No Databricks, AWS, Bedrock, S3, Delta Share, vector search, or agent runtime
calls are made here.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.schemas.databricks_gold_models import (
    GOLD_EXPORT_PRODUCER,
    GOLD_EXPORT_SCHEMA_VERSION,
    DatabricksGoldExportPayload,
)
from app.schemas.intake_models import IntakeResult
from app.services.databricks_gold_adapter import consume_databricks_gold_payload_file
from app.workflows.retrieval_workflow import _build_retrieval_request

_FIXTURE_DIR = Path("tests/fixtures/databricks_gold")

_LOCAL_FIXTURE_SAFETY_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
)

_REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "producer",
    "exported_at",
    "records",
}

_REQUIRED_RECORD_KEYS = {
    "gold_record_id",
    "source_document_id",
    "source_filename",
    "source_type",
    "document_date",
    "retrieval_query",
    "lineage",
}

_REQUIRED_LINEAGE_KEYS = {
    "gold_record_id",
    "source_document_id",
}


def _fixture_paths() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("*.json"))


def test_databricks_gold_fixture_directory_contains_contract_fixtures() -> None:
    assert _fixture_paths(), "Expected at least one sanitized Databricks Gold fixture"


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda path: path.name)
def test_sanitized_gold_fixture_matches_export_contract(fixture_path: Path) -> None:
    raw_payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert set(raw_payload).issuperset(_REQUIRED_TOP_LEVEL_KEYS)
    assert raw_payload["schema_version"] == GOLD_EXPORT_SCHEMA_VERSION
    assert raw_payload["producer"] == GOLD_EXPORT_PRODUCER

    payload = DatabricksGoldExportPayload.model_validate(raw_payload)
    assert payload.records

    for raw_record, record in zip(raw_payload["records"], payload.records, strict=True):
        assert set(raw_record).issuperset(_REQUIRED_RECORD_KEYS)
        assert set(raw_record["lineage"]).issuperset(_REQUIRED_LINEAGE_KEYS)
        assert record.lineage.gold_record_id == record.gold_record_id
        assert record.lineage.source_document_id == record.source_document_id


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda path: path.name)
def test_sanitized_gold_fixture_contains_no_private_markers(fixture_path: Path) -> None:
    fixture_text = fixture_path.read_text(encoding="utf-8")

    for pattern in _LOCAL_FIXTURE_SAFETY_PATTERNS:
        assert not pattern.search(fixture_text), (
            f"{fixture_path} contains private marker pattern: {pattern.pattern}"
        )


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda path: path.name)
def test_gold_fixture_consumes_to_existing_intake_result_boundary(
    fixture_path: Path,
    tmp_path: Path,
) -> None:
    raw_payload: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    first_record = raw_payload["records"][0]

    intake = consume_databricks_gold_payload_file(fixture_path, output_dir=tmp_path)

    assert isinstance(intake, IntakeResult)
    assert intake.storage is None
    assert intake.record.original_filename == first_record["source_filename"]
    assert intake.record.source_type == first_record["source_type"]
    assert intake.record.document_date == first_record["document_date"]
    assert intake.record.submitter_note == first_record["retrieval_query"]


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda path: path.name)
def test_gold_intake_preserves_retrieval_query_in_retrieval_request(
    fixture_path: Path,
    tmp_path: Path,
) -> None:
    raw_payload: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    first_record = raw_payload["records"][0]

    intake = consume_databricks_gold_payload_file(fixture_path, output_dir=tmp_path)
    request = _build_retrieval_request(intake)

    assert request.document_id == intake.document_id
    assert request.source_filename == first_record["source_filename"]
    assert request.source_type == first_record["source_type"]
    assert request.source_document_s3_key is None
    assert request.query_text == first_record["retrieval_query"]
