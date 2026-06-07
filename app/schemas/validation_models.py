"""
Pydantic models for the validation contract layer — C-2.

ValidationStatus — narrow three-value literal for audit outcomes.
ValidationOutput — typed output of the Validation / Critic Agent.
ClaimValidation — per-claim grounding audit result.

These models define what the Validation Agent produces after auditing an
AnalysisOutput against the original EvidenceChunks.  No Bedrock Converse calls,
prompt logic, or escalation/orchestration logic belong here.

C-2 scope: validation output shape only.
Escalation fields (escalation_required, escalation_reason) live in D-phase CaseOutput.
"""

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# Three-value outcome: pass = fully grounded, warning = partial support,
# fail = unsupported claims detected or confidence below acceptable threshold.
ValidationStatus = Literal["pass", "warning", "fail"]


class ClaimValidation(BaseModel):
    """
    Per-claim validation result produced by the critic.

    claim_id references AnalysisOutput.grounded_claims[*].claim_id.  When a
    claim is supported, supporting_chunk_ids must name the cited evidence chunks
    that actually support it.  Unsupported claims must carry an unsupported_reason
    so downstream escalation can explain the gap.
    """

    claim_id: str
    supported: bool
    supporting_chunk_ids: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None

    @field_validator("claim_id")
    @classmethod
    def claim_id_must_be_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim_id must be a non-empty string")
        return stripped

    @field_validator("supporting_chunk_ids")
    @classmethod
    def chunk_ids_must_not_be_blank(cls, items: list[str]) -> list[str]:
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

    @field_validator("unsupported_reason")
    @classmethod
    def unsupported_reason_must_not_be_blank(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("unsupported_reason must be non-empty when provided")
        return stripped

    @model_validator(mode="after")
    def supported_claims_need_evidence_and_unsupported_claims_need_reason(
        self,
    ) -> "ClaimValidation":
        if self.supported and not self.supporting_chunk_ids:
            raise ValueError(
                "supported claim validations must include at least one supporting chunk ID"
            )
        if not self.supported and self.unsupported_reason is None:
            raise ValueError(
                "unsupported claim validations must include unsupported_reason"
            )
        return self


class ValidationOutput(BaseModel):
    """
    Typed output produced by the Validation / Critic Agent after auditing an AnalysisOutput.

    C-2 populates this model from a Bedrock Converse response via BedrockValidationService.
    D-phase Tool Executor reads confidence_score, unsupported_claims, and validation_status
    to determine escalation_required.

    confidence_score — model's overall grounding confidence (0.0 = no support, 1.0 = fully grounded).
    unsupported_claims — specific claims the model found without evidence backing; may be empty.
    validation_status — coarse outcome label; must align with confidence and unsupported_claims.
    claim_validations — optional per-claim grounding audit results.
    warning — optional human-readable note for edge conditions (empty evidence, ambiguous support).
    """

    document_id: str
    confidence_score: float
    unsupported_claims: list[str]
    validation_status: ValidationStatus
    claim_validations: list[ClaimValidation] = Field(default_factory=list)
    warning: str | None = None

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

    @model_validator(mode="after")
    def claim_validation_ids_must_be_unique(self) -> "ValidationOutput":
        claim_ids = [
            claim_validation.claim_id
            for claim_validation in self.claim_validations
        ]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError(
                "claim_validations must not contain duplicate claim_id values"
            )
        return self
