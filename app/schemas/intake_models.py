"""
Pydantic models for the document intake pipeline.

IntakeMetadata — operator-supplied fields required before processing.
IntakeRecord   — the full, serialized artifact written to outputs/intake/.
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
