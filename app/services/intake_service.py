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
  8. If an S3Service is provided, upload the source document and artifact.
  9. Return a typed IntakeResult capturing the full registration contract.

S3 upload is optional: pass an S3Service instance to enable it.
If not provided, the function runs in local-only mode; IntakeResult.storage is None.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.intake_models import (
    IntakeMetadata,
    IntakeRecord,
    IntakeResult,
    StorageRegistration,
)
from app.services.s3_service import S3Service, StorageError
from app.utils.id_utils import generate_document_id

# ── intake policy constants ────────────────────────────────────────────────────

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md", ".docx"})
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB

# Default output directory — resolved relative to the project root.
# Override by passing output_dir explicitly (useful in tests).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "intake"


class IntakeError(Exception):
    """Raised for any recoverable intake validation or storage failure."""


def run_intake(
    file_path: str,
    metadata: IntakeMetadata,
    output_dir: Path | None = None,
    s3_service: S3Service | None = None,
) -> IntakeResult:
    """
    Run the local intake pipeline for a single document.

    Returns a typed IntakeResult on success.
    Raises IntakeError with a descriptive message on failure.

    If s3_service is provided, the source document and intake artifact are
    uploaded to S3 after the local artifact is written; IntakeResult.storage
    will be populated with the bucket name and both S3 keys.
    """
    path = Path(file_path).resolve()
    _validate_file_exists(path)
    _validate_extension(path)
    _validate_file_size(path)

    document_id = generate_document_id()
    record = _build_record(path, document_id, metadata)

    destination = output_dir or DEFAULT_OUTPUT_DIR
    artifact_path = _write_artifact(record, destination)

    storage: StorageRegistration | None = None
    if s3_service is not None:
        storage = _upload_to_s3(s3_service, path, artifact_path, record)

    return IntakeResult(
        document_id=document_id,
        artifact_path=str(artifact_path),
        record=record,
        storage=storage,
    )


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


def _write_artifact(record: IntakeRecord, output_dir: Path) -> Path:
    """Write the intake record as JSON and return the file path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{record.document_id}.json"
    artifact_path.write_text(
        json.dumps(record.model_dump(), indent=2),
        encoding="utf-8",
    )
    return artifact_path


def _upload_to_s3(
    s3_service: S3Service,
    source_path: Path,
    artifact_path: Path,
    record: IntakeRecord,
) -> StorageRegistration:
    """
    Upload the source document and intake artifact to S3.

    Returns a StorageRegistration with the bucket name and both S3 keys.
    Any StorageError is re-raised as IntakeError so the failure propagates
    through the single public interface.
    """
    try:
        source_key = s3_service.upload_source_document(
            local_path=source_path,
            document_id=record.document_id,
            source_type=record.source_type,
        )
        artifact_key = s3_service.upload_intake_artifact(
            local_path=artifact_path,
            document_id=record.document_id,
            source_type=record.source_type,
        )
    except StorageError as exc:
        raise IntakeError(f"S3 upload failed: {exc}") from exc

    return StorageRegistration(
        bucket_name=s3_service.bucket_name,
        source_document_key=source_key,
        intake_artifact_key=artifact_key,
    )
