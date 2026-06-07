"""
Lambda invocation contracts for the deployed CaseOps pipeline.

The Lambda handler accepts either inline document content or a source S3 object.
It validates metadata with the same source type and date rules as the CLI
intake path, then materialises the document to /tmp before running the pipeline.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, field_validator, model_validator

from app.schemas.intake_models import IntakeMetadata, SourceType


class LambdaInlineDocument(BaseModel):
    """Inline document payload accepted by the Lambda handler."""

    filename: str = "caseops-input.txt"
    text: str | None = None
    base64_content: str | None = None

    @field_validator("filename")
    @classmethod
    def filename_must_be_simple(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("document.filename must be non-empty")
        if PurePosixPath(name).name != name or "\\" in name:
            raise ValueError("document.filename must not contain path separators")
        return name

    @model_validator(mode="after")
    def exactly_one_content_field(self) -> "LambdaInlineDocument":
        has_text = self.text is not None
        has_base64 = self.base64_content is not None
        if has_text == has_base64:
            raise ValueError("document must include exactly one of text or base64_content")
        return self


class LambdaS3Document(BaseModel):
    """S3 document pointer accepted by the Lambda handler."""

    bucket: str
    key: str
    filename: str | None = None

    @field_validator("bucket", "key")
    @classmethod
    def required_string_must_be_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("S3 bucket and key must be non-empty")
        return stripped

    @field_validator("filename")
    @classmethod
    def optional_filename_must_be_simple(cls, value: str | None) -> str | None:
        if value is None:
            return value
        name = value.strip()
        if not name:
            raise ValueError("s3.filename must be non-empty when provided")
        if PurePosixPath(name).name != name or "\\" in name:
            raise ValueError("s3.filename must not contain path separators")
        return name


class LambdaPipelineRequest(BaseModel):
    """Validated event contract for invoking the CaseOps pipeline on Lambda."""

    source_type: SourceType
    document_date: str
    submitter_note: str | None = None
    document: LambdaInlineDocument | None = None
    s3: LambdaS3Document | None = None
    include_output: bool = True

    @model_validator(mode="after")
    def exactly_one_document_source(self) -> "LambdaPipelineRequest":
        has_inline = self.document is not None
        has_s3 = self.s3 is not None
        if has_inline == has_s3:
            raise ValueError("event must include exactly one of document or s3")
        IntakeMetadata(
            source_type=self.source_type,
            document_date=self.document_date,
            submitter_note=self.submitter_note,
        )
        return self

    def to_intake_metadata(self) -> IntakeMetadata:
        """Return metadata accepted by the existing intake service."""
        return IntakeMetadata(
            source_type=self.source_type,
            document_date=self.document_date,
            submitter_note=self.submitter_note,
        )
