"""
H-1 Guardrails → H-0 safety adapter.

Converts a GuardrailAssessmentResult (produced by the H-1 Guardrails service)
into the H-0 typed safety contracts (SafetyIssue / SafetyAssessment).

This is the normalization bridge between the Bedrock Guardrails integration
layer and the local deterministic safety evaluation system established in H-0.

Public surface:
  guardrail_result_to_issues(result)            → list[SafetyIssue]
  guardrail_result_to_assessment(result, ...)   → SafetyAssessment

Mapping rules:
  - Intervention (result.intervened == True):
      → one blocking SafetyIssue with code GUARDRAIL_INTERVENTION,
        source GUARDRAILS, severity ERROR.
      → SafetyAssessment.status = BLOCK.
  - Non-intervention (result.intervened == False):
      → empty issues list.
      → SafetyAssessment.status = ALLOW.
  - Metadata preserved: action, source, finding_types.
  - document_id is required only for full SafetyAssessment construction;
    issue construction is document_id-free.

Separation constraints:
  - This module does not import any AWS service or runtime client.
  - This module does not import the H-0 policy evaluator module.
  - It imports only the schema modules: guardrail_models and safety_models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.guardrail_models import GuardrailAssessmentResult
from app.schemas.safety_models import (
    IssueSource,
    SafetyAssessment,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


# ── Issue construction ─────────────────────────────────────────────────────────


def _build_intervention_issue(result: GuardrailAssessmentResult) -> SafetyIssue:
    """Build a blocking SafetyIssue from an intervening Guardrail result."""
    finding_summary = (
        f"; findings: {', '.join(result.finding_types)}"
        if result.finding_types
        else ""
    )
    message = (
        f"Bedrock Guardrail '{result.guardrail_id}' (v{result.guardrail_version}) "
        f"intervened on {result.source.value} content{finding_summary}"
    )
    metadata: dict[str, Any] = {
        "guardrail_id": result.guardrail_id,
        "guardrail_version": result.guardrail_version,
        "source": result.source.value,
        "action": result.action,
        "finding_types": list(result.finding_types),
    }
    return SafetyIssue(
        issue_code=SafetyIssueCode.GUARDRAIL_INTERVENTION,
        severity=SafetyIssueSeverity.ERROR,
        message=message,
        blocking=True,
        source=IssueSource.GUARDRAILS,
        metadata=metadata,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def guardrail_result_to_issues(
    result: GuardrailAssessmentResult,
) -> list[SafetyIssue]:
    """
    Convert a GuardrailAssessmentResult into a list of SafetyIssue objects.

    Returns a list containing one blocking issue when the Guardrail intervened,
    or an empty list when it did not.  The caller can merge the returned issues
    into an existing SafetyAssessment or pass them to the H-0 evaluator.

    Parameters
    ----------
    result : normalized outcome from GuardrailsService.assess_text().

    Returns
    -------
    list[SafetyIssue] — one blocking issue on intervention; empty on non-intervention.
    """
    if result.intervened:
        return [_build_intervention_issue(result)]
    return []


def guardrail_result_to_assessment(
    result: GuardrailAssessmentResult,
    document_id: str,
    notes: str | None = None,
) -> SafetyAssessment:
    """
    Convert a GuardrailAssessmentResult directly into a SafetyAssessment.

    Suitable for standalone use when a Guardrails check is the only safety
    evaluation being performed.  When combining with H-0 policy evaluation,
    prefer guardrail_result_to_issues() and merge the issues manually.

    Parameters
    ----------
    result      : normalized outcome from GuardrailsService.assess_text().
    document_id : document identifier to attach to the assessment.
    notes       : optional free-text observation.

    Returns
    -------
    SafetyAssessment with:
      - status BLOCK + has_blocking_issue=True + requires_escalation=True
        when the Guardrail intervened.
      - status ALLOW + has_blocking_issue=False + requires_escalation=False
        when the Guardrail did not intervene.
    """
    issues = guardrail_result_to_issues(result)
    has_blocking = any(i.blocking for i in issues)
    status = SafetyStatus.BLOCK if has_blocking else SafetyStatus.ALLOW
    requires_escalation = status in (SafetyStatus.BLOCK, SafetyStatus.ESCALATE)

    return SafetyAssessment(
        document_id=document_id,
        issues=issues,
        has_blocking_issue=has_blocking,
        requires_escalation=requires_escalation,
        status=status,
        notes=notes,
        timestamp=_now_iso(),
    )
