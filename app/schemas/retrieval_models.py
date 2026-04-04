"""
Pydantic models for the retrieval contract layer.

EvidenceChunk    — a single retrieved passage with full citation metadata.
RetrievalRequest — typed input to any retrieval implementation.
RetrievalResult  — typed output from any retrieval implementation.

These models define the retrieval contract used from B-0 onward.
No Bedrock-specific fields or AWS client code belong here.
"""

import math
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from app.schemas.intake_models import SourceType


class EvidenceChunk(BaseModel):
    """
    A single retrieved passage plus the citation metadata needed to trace it
    back to its source document.

    text    — full retrieved chunk text, passed as context to the Analysis Agent.
    excerpt — citation-safe snippet preserved in the final CaseOutput; never
              truncated or dropped by intermediate pipeline stages.
    """

    chunk_id: str
    text: str
    source_id: str
    source_label: str
    excerpt: str
    relevance_score: float

    @field_validator("relevance_score")
    @classmethod
    def must_be_finite_float(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"relevance_score must be a finite numeric value, got: {value!r}"
            )
        return value


class RetrievalRequest(BaseModel):
    """
    Typed input to any retrieval implementation.

    Field names align with the A-3 intake handoff contract (IntakeResult /
    IntakeRecord) so retrieval can be driven directly from intake output without
    field remapping.

    source_document_s3_key — present when S3 upload ran; None in local-only mode.
    query_text             — explicit search query; if None the implementation
                             derives its own query from the document context.
    """

    document_id: str
    source_type: SourceType
    source_filename: str
    source_document_s3_key: str | None = None
    query_text: str | None = None


class RetrievalResult(BaseModel):
    """
    Typed output from any retrieval implementation.

    Empty retrieval is representable without exceptions: set retrieval_status
    to "empty", evidence_chunks to [], and retrieved_count to 0.  The
    Supervisor will route empty results to the low-confidence escalation path.

    retrieved_count is validated to always equal len(evidence_chunks).
    warning carries a human-readable message for empty or partial results.
    """

    document_id: str
    evidence_chunks: list[EvidenceChunk]
    retrieval_status: Literal["success", "empty", "error"]
    retrieved_count: int
    warning: str | None = None

    @model_validator(mode="after")
    def check_retrieved_count_consistency(self) -> "RetrievalResult":
        if self.retrieved_count != len(self.evidence_chunks):
            raise ValueError(
                f"retrieved_count ({self.retrieved_count}) must equal "
                f"len(evidence_chunks) ({len(self.evidence_chunks)})"
            )
        return self
