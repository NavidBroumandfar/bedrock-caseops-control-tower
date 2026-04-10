"""
Pydantic models for the H-0 safety contracts layer.

These schemas define the typed foundation for deterministic safety and failure-policy
evaluation of pipeline outputs.  No runner logic, live AWS calls, or dataset population
belongs here — this is the contract layer only.

SafetyIssueSeverity   — severity levels for individual safety issues (warning / error / critical).
SafetyIssueCode       — typed codes for the supported safety and failure signal categories.
IssueSource           — origin layer that raised the issue (validation / retrieval / etc.).
SafetyStatus          — final outcome status for a safety assessment (allow / warn / escalate / block).
SafetyIssue           — one identified safety or failure condition.
SafetyAssessment      — full typed safety assessment for one candidate output.
FailurePolicy         — configurable deterministic policy settings used by the evaluator.

Status semantics:
  allow    — no meaningful issues detected; output is safe to proceed.
  warn     — non-blocking issues present; output may proceed with a flag.
  escalate — output may proceed but requires escalation or human review.
  block    — output should not be accepted as safe or usable.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ───────────────────────────────────────────────────────────────


class SafetyIssueSeverity(str, Enum):
    """Severity of one identified safety issue (independent of case severity)."""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SafetyIssueCode(str, Enum):
    """
    Typed codes for safety and failure signal categories.

    Raw observation codes (raised by policy rules from candidate content):
      UNSUPPORTED_CLAIMS_PRESENT        — output contains claims not grounded in evidence.
      MISSING_CITATIONS_WHEN_REQUIRED   — output has no citations but policy requires them.
      EMPTY_OR_WEAK_RETRIEVAL           — upstream retrieval returned too few chunks.
      LOW_CONFIDENCE_OUTPUT             — confidence_score is below policy threshold.
      SCHEMA_OR_CONTRACT_FAILURE        — candidate cannot be validated against CaseOutput schema.
      ESCALATION_POLICY_TRIGGERED       — candidate has escalation_required=True and policy reflects it.

    Derived outcome code (not raised from a raw observation):
      UNSAFE_OUTPUT_BLOCK_REQUIRED      — policy determined the output must be blocked;
                                          may be added as a synthetic summary issue by callers.
    """

    UNSUPPORTED_CLAIMS_PRESENT = "unsupported_claims_present"
    MISSING_CITATIONS_WHEN_REQUIRED = "missing_citations_when_required"
    EMPTY_OR_WEAK_RETRIEVAL = "empty_or_weak_retrieval"
    LOW_CONFIDENCE_OUTPUT = "low_confidence_output"
    SCHEMA_OR_CONTRACT_FAILURE = "schema_or_contract_failure"
    ESCALATION_POLICY_TRIGGERED = "escalation_policy_triggered"
    UNSAFE_OUTPUT_BLOCK_REQUIRED = "unsafe_output_block_required"


class IssueSource(str, Enum):
    """Pipeline layer that produced a safety issue."""

    VALIDATION = "validation"
    RETRIEVAL = "retrieval"
    CITATION_QUALITY = "citation_quality"
    OUTPUT_QUALITY = "output_quality"
    SCHEMA = "schema"
    POLICY = "policy"


class SafetyStatus(str, Enum):
    """
    Final outcome status for a SafetyAssessment.

    allow    — no meaningful issues; output is safe to proceed.
    warn     — non-blocking issues present; output proceeds with flag.
    escalate — output may proceed but must be escalated or reviewed.
    block    — output must not be accepted as safe or usable.
    """

    ALLOW = "allow"
    WARN = "warn"
    ESCALATE = "escalate"
    BLOCK = "block"


# ── SafetyIssue ────────────────────────────────────────────────────────────────


class SafetyIssue(BaseModel):
    """
    One identified safety or failure condition for a candidate output.

    issue_code — stable typed code identifying the kind of issue.
    severity   — severity of this individual issue (warning / error / critical).
    message    — human-readable description of the specific issue.
    blocking   — True if this issue alone is sufficient to block the output.
    source     — which pipeline layer raised this issue.
    metadata   — optional free-form context attached by the rule that raised the issue
                 (e.g. counts, threshold values, claim text).
    """

    issue_code: SafetyIssueCode
    severity: SafetyIssueSeverity
    message: str
    blocking: bool
    source: IssueSource
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be a non-empty string")
        return value


# ── SafetyAssessment ───────────────────────────────────────────────────────────


class SafetyAssessment(BaseModel):
    """
    Full typed safety assessment for one candidate output.

    Produced by safety_policy.evaluate_safety() and consumed by downstream
    pipeline routing, escalation logic, or H-1 Bedrock Guardrails integration.

    document_id         — ties this assessment to a specific pipeline output artifact.
    issues              — all SafetyIssues identified for this candidate (may be empty).
    has_blocking_issue  — True if at least one issue has blocking=True.
    requires_escalation — True when status is escalate or block.
    status              — final outcome: allow / warn / escalate / block.
    notes               — optional free-text observation attached by the evaluator.
    timestamp           — ISO 8601 UTC timestamp when this assessment was produced.
    """

    document_id: str
    issues: list[SafetyIssue] = Field(default_factory=list)
    has_blocking_issue: bool = False
    requires_escalation: bool = False
    status: SafetyStatus
    notes: str | None = None
    timestamp: str

    @field_validator("document_id")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must be a non-empty string")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_iso8601(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"timestamp must be a valid ISO 8601 datetime string, got: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def blocking_consistent_with_status(self) -> "SafetyAssessment":
        """A blocking issue means the status can never be allow."""
        if self.has_blocking_issue and self.status == SafetyStatus.ALLOW:
            raise ValueError(
                "status cannot be 'allow' when has_blocking_issue is True"
            )
        return self


# ── FailurePolicy ──────────────────────────────────────────────────────────────


class FailurePolicy(BaseModel):
    """
    Configurable deterministic policy settings used by the H-0 safety evaluator.

    All fields have safe, conservative defaults that reflect the existing pipeline
    escalation contracts (e.g. the 0.6 confidence threshold from D-1 Tool Executor).

    low_confidence_threshold            — confidence_score below this value triggers a
                                          low-confidence issue (default: 0.6, matching
                                          the existing ESCALATION_CONFIDENCE_THRESHOLD).
    max_unsupported_claims_before_block — unsupported_claims count strictly above this
                                          threshold makes the issue blocking; 0 means any
                                          unsupported claim is blocking (default: 0).
    require_citations                   — when True, an output with no citations is flagged.
    block_on_schema_failure             — schema/contract failures are always blocking;
                                          this flag is preserved for policy inspection.
    block_on_missing_citations          — when True, missing citations produce a blocking
                                          issue; when False, a warning is issued instead.
    warn_on_empty_retrieval             — when True, a retrieval context with zero chunks
                                          produces a warning issue (requires the caller to
                                          pass retrieval_chunk_count).
    escalate_on_low_confidence          — when True, low confidence triggers escalation;
                                          when False, it produces a warning instead.
    escalate_on_escalation_required     — when True, a candidate with escalation_required=True
                                          produces an escalation issue in the assessment.
    """

    low_confidence_threshold: float = 0.6
    max_unsupported_claims_before_block: int = 0
    require_citations: bool = True
    block_on_schema_failure: bool = True
    block_on_missing_citations: bool = True
    warn_on_empty_retrieval: bool = True
    escalate_on_low_confidence: bool = True
    escalate_on_escalation_required: bool = True

    @field_validator("low_confidence_threshold")
    @classmethod
    def threshold_must_be_in_unit_interval(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"low_confidence_threshold must be in [0.0, 1.0], got: {value!r}"
            )
        return value

    @field_validator("max_unsupported_claims_before_block")
    @classmethod
    def must_be_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                f"max_unsupported_claims_before_block must be >= 0, got: {value!r}"
            )
        return value
