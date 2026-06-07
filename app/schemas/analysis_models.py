"""
Pydantic models for the analysis contract layer.

SeverityLevel  — the four-value severity type used across analysis and case output.
AnalysisOutput — typed output of the Analysis Agent; the contract C-1 must satisfy.
GroundedClaim  — a single finding or recommendation with supporting chunk IDs.

These models define what the Analysis Agent produces after consuming retrieved evidence.
No Bedrock Converse calls, prompt logic, or validation/critic logic belong here.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Severity values are pinned here as the single source of truth.
# CaseOutput (Phase D) will import this alias to stay in sync with the analysis layer.
SeverityLevel = Literal["Critical", "High", "Medium", "Low"]
ClaimType = Literal["finding", "recommendation"]


class GroundedClaim(BaseModel):
    """
    A single analysis claim with explicit evidence anchors.

    claim_type distinguishes summary findings from recommendation claims while
    supporting_chunk_ids carries the retrieved EvidenceChunk.chunk_id values that
    support the claim.  The model requires at least one supporting chunk for every
    emitted claim so claim-level grounding is enforceable at the contract boundary.
    """

    claim_id: str
    claim_type: ClaimType
    text: str
    supporting_chunk_ids: list[str]

    @field_validator("claim_id", "text")
    @classmethod
    def required_text_fields_must_be_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim_id and text must be non-empty strings")
        return stripped

    @field_validator("supporting_chunk_ids")
    @classmethod
    def supporting_chunk_ids_must_be_non_empty(
        cls,
        items: list[str],
    ) -> list[str]:
        if not items:
            raise ValueError(
                "supporting_chunk_ids must include at least one evidence chunk ID"
            )

        normalized: list[str] = []
        seen: set[str] = set()
        for i, item in enumerate(items):
            stripped = item.strip()
            if not stripped:
                raise ValueError(
                    f"supporting_chunk_ids[{i}] is empty or whitespace-only"
                )
            if stripped not in seen:
                normalized.append(stripped)
                seen.add(stripped)
        return normalized


class AnalysisOutput(BaseModel):
    """
    Typed output produced by the Analysis Agent after consuming retrieved evidence.

    C-1 will populate this model from a Bedrock Converse response.
    C-2 (Validation / Critic Agent) receives this model alongside the original
    EvidenceChunks to audit for unsupported claims.
    D-phase CaseOutput will incorporate severity, category, summary, and
    recommendations and grounded_claims from this model.

    grounded_claims defaults to [] for backward compatibility with existing
    offline fixtures, but provider-backed Phase 4 analysis must populate it.
    Confidence scores and escalation fields remain downstream concerns.
    """

    document_id: str
    severity: SeverityLevel
    category: str
    summary: str
    recommendations: list[str]
    grounded_claims: list[GroundedClaim] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def summary_must_be_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "summary must be a non-empty, readable string; "
                "got an empty or whitespace-only value"
            )
        return stripped

    @field_validator("recommendations")
    @classmethod
    def recommendations_must_have_no_empty_items(cls, items: list[str]) -> list[str]:
        for i, item in enumerate(items):
            if not item.strip():
                raise ValueError(
                    f"recommendations[{i}] is empty or whitespace-only; "
                    "all recommendation strings must be non-empty"
                )
        return items

    @model_validator(mode="after")
    def grounded_claim_ids_must_be_unique(self) -> "AnalysisOutput":
        claim_ids = [claim.claim_id for claim in self.grounded_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("grounded_claims must not contain duplicate claim_id values")
        return self
