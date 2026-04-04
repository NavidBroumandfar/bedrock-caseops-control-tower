"""
Pydantic models for the validation contract layer — C-2.

ValidationStatus — narrow three-value literal for audit outcomes.
ValidationOutput — typed output of the Validation / Critic Agent.

These models define what the Validation Agent produces after auditing an
AnalysisOutput against the original EvidenceChunks.  No Bedrock Converse calls,
prompt logic, or escalation/orchestration logic belong here.

C-2 scope: validation output shape only.
Escalation fields (escalation_required, escalation_reason) live in D-phase CaseOutput.
"""

import math
from typing import Literal

from pydantic import BaseModel, field_validator


# Three-value outcome: pass = fully grounded, warning = partial support,
# fail = unsupported claims detected or confidence below acceptable threshold.
ValidationStatus = Literal["pass", "warning", "fail"]


class ValidationOutput(BaseModel):
    """
    Typed output produced by the Validation / Critic Agent after auditing an AnalysisOutput.

    C-2 populates this model from a Bedrock Converse response via BedrockValidationService.
    D-phase Tool Executor reads confidence_score, unsupported_claims, and validation_status
    to determine escalation_required.

    confidence_score — model's overall grounding confidence (0.0 = no support, 1.0 = fully grounded).
    unsupported_claims — specific claims the model found without evidence backing; may be empty.
    validation_status — coarse outcome label; must align with confidence and unsupported_claims.
    warning — optional human-readable note for edge conditions (empty evidence, ambiguous support).
    """

    document_id: str
    confidence_score: float
    unsupported_claims: list[str]
    validation_status: ValidationStatus
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
