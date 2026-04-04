"""
Pydantic models for the analysis contract layer.

SeverityLevel  — the four-value severity type used across analysis and case output.
AnalysisOutput — typed output of the Analysis Agent; the contract C-1 must satisfy.

These models define what the Analysis Agent produces after consuming retrieved evidence.
No Bedrock Converse calls, prompt logic, or validation/critic logic belong here.
"""

from typing import Literal

from pydantic import BaseModel, field_validator

# Severity values are pinned here as the single source of truth.
# CaseOutput (Phase D) will import this alias to stay in sync with the analysis layer.
SeverityLevel = Literal["Critical", "High", "Medium", "Low"]


class AnalysisOutput(BaseModel):
    """
    Typed output produced by the Analysis Agent after consuming retrieved evidence.

    C-1 will populate this model from a Bedrock Converse response.
    C-2 (Validation / Critic Agent) receives this model alongside the original
    EvidenceChunks to audit for unsupported claims.
    D-phase CaseOutput will incorporate severity, category, summary, and
    recommendations from this model.

    Fields are intentionally minimal for C-0: no citations, confidence scores,
    or escalation fields yet — those belong to later phases.
    """

    document_id: str
    severity: SeverityLevel
    category: str
    summary: str
    recommendations: list[str]

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
