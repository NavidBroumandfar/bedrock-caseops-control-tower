"""
Pydantic models for the H-1 Bedrock Guardrails integration layer.

These schemas define the repo-local normalized contract for Guardrails assessment
results returned by the ApplyGuardrail API.  Raw AWS response shapes are never
exposed to callers — this module is the normalization boundary.

GuardrailSource           — typed enum for the content source assessed ("input" / "output").
GuardrailAssessmentResult — normalized outcome of one Guardrails check; the single
                            public result type produced by the guardrails service and
                            consumed by the H-0 adapter.

Design constraints:
  - This is the contract layer only; no service logic, no AWS calls.
  - Field names use repo snake_case conventions (not raw AWS API casing).
  - All fields needed by the H-0 adapter are present; trace is optional and
    disabled by default to avoid large dict payloads in tests.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ───────────────────────────────────────────────────────────────


class GuardrailSource(str, Enum):
    """
    Which content stream was submitted to the Guardrail for assessment.

    Maps to the 'source' parameter in the ApplyGuardrail API request:
      INPUT  — text from the user / document input side.
      OUTPUT — text from the model / pipeline output side.
    """

    INPUT = "input"
    OUTPUT = "output"


# ── GuardrailAssessmentResult ──────────────────────────────────────────────────


class GuardrailAssessmentResult(BaseModel):
    """
    Normalized outcome of one Bedrock Guardrails assessment.

    Produced by GuardrailsService.assess_text() and consumed downstream
    by the H-0 adapter (guardrails_adapter.py) to create SafetyIssue objects.

    guardrail_id      — identifier of the Guardrail that was applied.
    guardrail_version — version of the Guardrail (e.g. "1", "DRAFT").
    source            — whether the assessed content was model input or output.
    intervened        — True when the Guardrail detected a policy violation and
                        took action (action == "GUARDRAIL_INTERVENED").
    action            — raw action string from the API response (e.g.
                        "GUARDRAIL_INTERVENED" or "NONE"); None when the
                        response does not include an action field.
    output_text       — the modified or replacement text produced by the
                        Guardrail when it intervened; None when the Guardrail
                        did not intervene or did not produce alternate text.
    blocked           — True when the intervention is a hard block (the content
                        must not be used).  Set by the service based on the
                        action value; defaults to the same value as intervened
                        (any intervention is treated as a block unless the
                        service normalises it otherwise).
    finding_types     — human-readable labels describing what the Guardrail
                        found: topic names, content filter types, PII entity
                        types, etc.  Empty when no findings were raised.
    trace             — optional raw assessments payload from the AWS response,
                        preserved verbatim for debugging; None when trace is
                        not requested or not available.
    """

    guardrail_id: str
    guardrail_version: str
    source: GuardrailSource
    intervened: bool
    action: str | None = None
    output_text: str | None = None
    blocked: bool = False
    finding_types: list[str] = Field(default_factory=list)
    trace: dict[str, Any] | None = None

    @field_validator("guardrail_id")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("guardrail_id must be a non-empty string")
        return value

    @field_validator("guardrail_version")
    @classmethod
    def version_must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("guardrail_version must be a non-empty string")
        return value
