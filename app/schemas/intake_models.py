"""
Pydantic models for the document intake pipeline.

IntakeMetadata      — operator-supplied fields required before processing.
IntakeRecord        — the full, serialized artifact written to outputs/intake/.
StorageRegistration — S3 locations for a registered document (set only when S3 is used).
IntakeResult        — typed handoff contract returned by run_intake().
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

SourceType = Literal["FDA", "CISA", "Incident", "Other"]


class IntakeMetadata(BaseModel):
    """Operator-supplied metadata validated at intake time."""

    source_type: SourceType
    document_date: str          # YYYY-MM-DD
    submitter_note: str | None = None

    @field_validator("document_date")
    @classmethod
    def must_be_iso_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"document_date must be YYYY-MM-DD, got: {value!r}")
        return value


class IntakeRecord(BaseModel):
    """Full intake artifact written to outputs/intake/{document_id}.json."""

    document_id: str
    original_filename: str
    extension: str
    absolute_path: str
    file_size_bytes: int
    intake_timestamp: str       # ISO 8601 UTC
    source_type: str
    document_date: str
    submitter_note: str | None = None


class StorageRegistration(BaseModel):
    """S3 locations written during intake.  Present only when S3 upload ran."""

    bucket_name: str
    source_document_key: str    # documents/{document_id}/raw/{filename}
    intake_artifact_key: str    # artifacts/intake/{document_id}.json


class IntakeResult(BaseModel):
    """
    Typed handoff contract returned by run_intake().

    Answers the four questions the next pipeline phase needs immediately:
      1. What document was registered?      → document_id / record
      2. Where is the local artifact?       → artifact_path
      3. Was S3 used?                       → storage is not None
      4. Where exactly in S3?              → storage.source_document_key / intake_artifact_key
    """

    document_id: str
    artifact_path: str              # absolute local path as a string
    record: IntakeRecord            # the full intake record for downstream use
    storage: StorageRegistration | None = None
