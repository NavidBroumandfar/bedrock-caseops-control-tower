"""
Databricks Gold export payload consumer adapter.

The adapter consumes schema-versioned, local JSON payloads produced by the
upstream Databricks lakehouse and converts one Gold record into the existing
Bedrock CaseOps IntakeResult handoff contract.

It does not call Databricks, S3, Bedrock, or any network service. Direct Delta
Share consumption can be added later behind a separate provider boundary.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from app.schemas.databricks_gold_models import (
    DatabricksGoldExportPayload,
    DatabricksGoldRecord,
)
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.utils.id_utils import generate_document_id

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATABRICKS_GOLD_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "databricks_gold"


class DatabricksGoldAdapterError(Exception):
    """Raised when a Gold export payload cannot be consumed safely."""


def load_databricks_gold_payload(payload_path: str | Path) -> DatabricksGoldExportPayload:
    """
    Load and validate a Databricks Gold export payload from a local JSON file.

    Raises DatabricksGoldAdapterError for file, JSON, or schema failures.
    """
    path = Path(payload_path).resolve()
    if not path.exists():
        raise DatabricksGoldAdapterError(f"Gold export payload not found: {path}")
    if not path.is_file():
        raise DatabricksGoldAdapterError(f"Gold export payload path is not a file: {path}")

    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatabricksGoldAdapterError(f"Gold export payload is not valid JSON: {exc}") from exc

    try:
        return DatabricksGoldExportPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise DatabricksGoldAdapterError(f"Gold export payload schema validation failed: {exc}") from exc


def consume_databricks_gold_payload_file(
    payload_path: str | Path,
    *,
    gold_record_id: str | None = None,
    output_dir: Path | None = None,
) -> IntakeResult:
    """
    Load a local Gold export payload file and convert one Gold record to IntakeResult.

    If gold_record_id is omitted, the payload must contain exactly one record.
    """
    payload = load_databricks_gold_payload(payload_path)
    return consume_databricks_gold_payload(
        payload,
        gold_record_id=gold_record_id,
        output_dir=output_dir,
    )


def consume_databricks_gold_payload(
    payload: DatabricksGoldExportPayload,
    *,
    gold_record_id: str | None = None,
    output_dir: Path | None = None,
) -> IntakeResult:
    """
    Convert one validated Databricks Gold record into the existing intake handoff.

    The adapter writes two local artifacts under output_dir/{document_id}/:
      - gold_record.json: normalized upstream Gold record snapshot
      - intake.json: existing IntakeRecord-compatible handoff artifact
    """
    record = _select_gold_record(payload, gold_record_id)
    document_id = generate_document_id()

    destination = output_dir or DEFAULT_DATABRICKS_GOLD_OUTPUT_DIR
    record_dir = destination / document_id
    record_dir.mkdir(parents=True, exist_ok=True)

    gold_record_path = record_dir / "gold_record.json"
    _write_json(gold_record_path, record.model_dump())

    intake_record = IntakeRecord(
        document_id=document_id,
        original_filename=record.source_filename,
        extension=_source_extension(record.source_filename),
        absolute_path=str(gold_record_path),
        file_size_bytes=gold_record_path.stat().st_size,
        intake_timestamp=datetime.now(timezone.utc).isoformat(),
        source_type=record.source_type,
        document_date=record.document_date,
        submitter_note=record.retrieval_query,
    )

    intake_artifact_path = record_dir / "intake.json"
    _write_json(intake_artifact_path, intake_record.model_dump())

    return IntakeResult(
        document_id=document_id,
        artifact_path=str(intake_artifact_path),
        record=intake_record,
        storage=None,
    )


def _select_gold_record(
    payload: DatabricksGoldExportPayload,
    gold_record_id: str | None,
) -> DatabricksGoldRecord:
    if gold_record_id is None:
        if len(payload.records) != 1:
            raise DatabricksGoldAdapterError(
                "gold_record_id is required when a payload contains multiple records"
            )
        return payload.records[0]

    for record in payload.records:
        if record.gold_record_id == gold_record_id:
            return record

    raise DatabricksGoldAdapterError(
        f"Gold record {gold_record_id!r} was not found in the export payload"
    )


def _source_extension(source_filename: str) -> str:
    extension = Path(source_filename).suffix.lower()
    return extension or ".json"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
