"""
Pydantic models for the final output contract — D-1.

Citation   — a single grounded reference preserved from an EvidenceChunk.
CaseOutput — the typed final output produced by the Tool Executor Agent.

These models define the D-1 output contract.  No agent logic, escalation
rules, or AWS service calls belong here.

session_id is included as an optional field (str | None = None) to stay
aligned with the architecture's final CaseOutput design.  The D-1 Tool
Executor does not generate session IDs — that is a D-2 orchestration
concern.  D-2 will populate this field once the orchestration layer
provides a session context.
"""

import math

from pydantic import BaseModel, field_validator

from app.schemas.analysis_models import SeverityLevel


class Citation(BaseModel):
    """
    A grounded reference preserved from a single EvidenceChunk.

    Fields map directly from EvidenceChunk — no invention, no rewriting.
    relevance_score is preserved from the KB retrieval response as-is.
    """

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


class CaseOutput(BaseModel):
    """
    Typed final output of the CaseOps pipeline, produced by the Tool Executor Agent.

    All required fields must be populated by the Tool Executor — it is responsible
    for providing safe placeholder values on degraded paths (e.g. empty retrieval)
    rather than leaving required fields None.

    session_id is optional in D-1 (defaults to None).  The D-2 orchestration layer
    will supply it once a session context is available end-to-end.

    confidence_score is validated in [0.0, 1.0], matching the ValidationOutput contract.
    escalation_reason is None only when escalation_required is False.
    citations may be empty on the empty-retrieval path; on the success path they are
    populated one-to-one from the RetrievalResult evidence chunks.
    """

    document_id: str
    source_filename: str
    source_type: str
    severity: SeverityLevel
    category: str
    summary: str
    recommendations: list[str]
    citations: list[Citation]
    confidence_score: float
    unsupported_claims: list[str]
    escalation_required: bool
    escalation_reason: str | None
    validated_by: str
    session_id: str | None = None
    timestamp: str

    @field_validator("confidence_score")
    @classmethod
    def must_be_in_unit_interval(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"confidence_score must be a finite float, got: {value!r}"
            )
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"confidence_score must be between 0.0 and 1.0 inclusive, got: {value!r}"
            )
        return value
