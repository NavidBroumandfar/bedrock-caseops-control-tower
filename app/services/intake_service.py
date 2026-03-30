"""
Document intake service.

Single public function: run_intake()

Responsibilities:
  1. Validate the file exists on disk.
  2. Confirm the file extension is in the allowed set.
  3. Confirm the file does not exceed the size limit.
  4. Capture file metadata (name, size, path, timestamp).
  5. Generate a document_id.
  6. Build and validate an IntakeRecord.
  7. Serialize the record as JSON to outputs/intake/.
  8. Return the document_id.

No AWS calls are made here. S3 upload is A-2.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.intake_models import IntakeMetadata, IntakeRecord
from app.utils.id_utils import generate_document_id

# ── intake policy constants ────────────────────────────────────────────────────

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md", ".docx"})
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB

# Default output directory — resolved relative to the project root.
# Override by passing output_dir explicitly (useful in tests).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "intake"


class IntakeError(Exception):
    """Raised for any recoverable intake validation failure."""


def run_intake(
    file_path: str,
    metadata: IntakeMetadata,
    output_dir: Path | None = None,
) -> str:
    """
    Run the local intake pipeline for a single document.

    Returns the assigned document_id on success.
    Raises IntakeError with a descriptive message on failure.
    """
    path = Path(file_path).resolve()
    _validate_file_exists(path)
    _validate_extension(path)
    _validate_file_size(path)

    document_id = generate_document_id()
    record = _build_record(path, document_id, metadata)

    destination = output_dir or DEFAULT_OUTPUT_DIR
    _write_artifact(record, destination)

    return document_id


# ── private helpers ────────────────────────────────────────────────────────────

def _validate_file_exists(path: Path) -> None:
    if not path.exists():
        raise IntakeError(f"File not found: {path}")
    if not path.is_file():
        raise IntakeError(f"Path is not a file: {path}")


def _validate_extension(path: Path) -> None:
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise IntakeError(
            f"Unsupported file type: {ext!r}. Allowed types: {allowed}"
        )


def _validate_file_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        raise IntakeError(
            f"File too large: {size:,} bytes "
            f"(limit is {MAX_FILE_SIZE_BYTES:,} bytes / "
            f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB)"
        )


def _build_record(
    path: Path,
    document_id: str,
    metadata: IntakeMetadata,
) -> IntakeRecord:
    return IntakeRecord(
        document_id=document_id,
        original_filename=path.name,
        extension=path.suffix.lower(),
        absolute_path=str(path),
        file_size_bytes=path.stat().st_size,
        intake_timestamp=datetime.now(timezone.utc).isoformat(),
        source_type=metadata.source_type,
        document_date=metadata.document_date,
        submitter_note=metadata.submitter_note,
    )


def _write_artifact(record: IntakeRecord, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{record.document_id}.json"
    artifact_path.write_text(
        json.dumps(record.model_dump(), indent=2),
        encoding="utf-8",
    )
