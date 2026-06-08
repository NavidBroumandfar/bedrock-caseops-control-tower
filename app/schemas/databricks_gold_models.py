"""
Pydantic contracts for Databricks Gold export payload consumption.

These models define the downstream Bedrock-side handoff contract only. The
Databricks repo remains responsible for raw ingestion, parsing, extraction,
classification, and Gold record production.
"""

from datetime import datetime
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.intake_models import SourceType

GOLD_EXPORT_SCHEMA_VERSION = "databricks-gold-export.v1"
GOLD_EXPORT_PRODUCER = "databricks-caseops-lakehouse"

_FORBIDDEN_METADATA_KEY_PARTS = (
    "workspace_url",
    "account_id",
    "token",
    "pat",
    "activation_link",
    "credential",
    "secret",
)


class DatabricksGoldLineage(BaseModel):
    """Trace identifiers supplied by the upstream Gold record."""

    gold_record_id: str
    source_document_id: str
    bronze_record_id: str | None = None
    silver_record_id: str | None = None
    transform_version: str | None = None

    @field_validator("*")
    @classmethod
    def nonempty_when_present(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("lineage identifiers must be nonempty when present")
        return value


class DatabricksGoldRecord(BaseModel):
    """
    One AI-ready, traceable Gold record produced upstream by Databricks.

    retrieval_query is intentionally required because the Bedrock retrieval
    workflow uses it as the grounded Knowledge Base query signal.
    """

    gold_record_id: str
    source_document_id: str
    source_filename: str
    source_type: SourceType
    document_date: str
    retrieval_query: str
    document_summary: str | None = None
    classification: str | None = None
    priority: Literal["Critical", "High", "Medium", "Low"] | None = None
    evidence_terms: list[str] = Field(default_factory=list)
    lineage: DatabricksGoldLineage
    custom_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("gold_record_id", "source_document_id", "retrieval_query")
    @classmethod
    def must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value

    @field_validator("source_filename")
    @classmethod
    def source_filename_must_be_basename(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_filename must be nonempty")
        if PurePath(value).name != value:
            raise ValueError("source_filename must be a basename, not a path or URI")
        return value

    @field_validator("document_date")
    @classmethod
    def document_date_must_be_iso_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"document_date must be YYYY-MM-DD, got: {value!r}")
        return value

    @field_validator("evidence_terms")
    @classmethod
    def evidence_terms_must_be_nonempty(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip():
                raise ValueError("evidence_terms cannot contain blank values")
        return values

    @field_validator("custom_metadata")
    @classmethod
    def custom_metadata_must_be_public_safe(
        cls,
        values: dict[str, str],
    ) -> dict[str, str]:
        for key, value in values.items():
            normalized_key = key.lower()
            if any(part in normalized_key for part in _FORBIDDEN_METADATA_KEY_PARTS):
                raise ValueError(f"custom_metadata key is not public-safe: {key!r}")
            if not isinstance(value, str):
                raise ValueError("custom_metadata values must be strings")
        return values

    @model_validator(mode="after")
    def lineage_must_match_record(self) -> "DatabricksGoldRecord":
        if self.lineage.gold_record_id != self.gold_record_id:
            raise ValueError("lineage.gold_record_id must match gold_record_id")
        if self.lineage.source_document_id != self.source_document_id:
            raise ValueError("lineage.source_document_id must match source_document_id")
        return self


class DatabricksGoldExportPayload(BaseModel):
    """Schema-versioned batch payload emitted by the upstream Databricks repo."""

    schema_version: Literal["databricks-gold-export.v1"]
    producer: Literal["databricks-caseops-lakehouse"]
    exported_at: str
    records: list[DatabricksGoldRecord]

    @field_validator("exported_at")
    @classmethod
    def exported_at_must_be_iso_datetime(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"exported_at must be ISO 8601, got: {value!r}")
        return value

    @field_validator("records")
    @classmethod
    def records_must_not_be_empty(
        cls,
        values: list[DatabricksGoldRecord],
    ) -> list[DatabricksGoldRecord]:
        if not values:
            raise ValueError("records must contain at least one Gold record")
        return values

    @model_validator(mode="after")
    def gold_record_ids_must_be_unique(self) -> "DatabricksGoldExportPayload":
        ids = [record.gold_record_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("gold_record_id values must be unique within a payload")
        return self
