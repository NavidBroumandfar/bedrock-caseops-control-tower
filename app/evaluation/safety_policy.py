"""
H-0 local safety policy evaluator.

Accepts a typed CaseOutput (or a raw unvalidated dict) plus optional retrieval
context and returns a typed SafetyAssessment representing the deterministic
safety / failure-policy outcome for that candidate output.

No live AWS calls are made.  No Bedrock Guardrails are used.  No LLM judges.
All logic is deterministic and local.

Policy rules applied (in order):
  1. Unsupported claims      — issue added when candidate.unsupported_claims is non-empty;
                               blocking when count > policy.max_unsupported_claims_before_block.
  2. Missing citations       — blocking issue when policy.require_citations is True
                               and candidate.citations is empty;
                               non-blocking warn when block_on_missing_citations is False.
  3. Empty / weak retrieval  — warn issue when retrieval_chunk_count is provided,
                               count < minimum, and policy.warn_on_empty_retrieval is True.
                               Skipped entirely when retrieval_chunk_count is None (context absent).
  4. Low confidence          — issue added when confidence_score < low_confidence_threshold;
                               triggers ESCALATE status when escalate_on_low_confidence is True,
                               otherwise WARN only.
  5. Escalation alignment    — issue added when escalation_required=True on the candidate
                               and policy.escalate_on_escalation_required is True;
                               reflected as ESCALATE status (never auto-blocking).
  6. Schema / contract failure — a blocking CRITICAL issue is returned immediately without
                                 applying other rules when the raw input cannot be parsed
                                 into a valid CaseOutput.

Status decision (deterministic):
  any blocking issue                           → BLOCK
  any ESCALATION_POLICY_TRIGGERED issue        → ESCALATE
  LOW_CONFIDENCE_OUTPUT + escalate_on_low_confidence → ESCALATE
  any non-blocking issue present               → WARN
  no issues                                    → ALLOW

Public surface:
  DEFAULT_POLICY                  — default FailurePolicy instance.
  evaluate_safety(candidate, ...) — score a typed CaseOutput; returns SafetyAssessment.
  evaluate_safety_from_raw(raw, ...)  — score an unvalidated dict; handles schema failures.

Separation rules:
  - This module does not import any AWS service, Bedrock client, or CloudWatch code.
  - This module does not import retrieval_scorer (G-0), citation_scorer (G-1),
    output_quality_scorer (G-2), or the F-2 runner.
  - It imports only app.schemas.output_models and app.schemas.safety_models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.schemas.output_models import CaseOutput
from app.schemas.safety_models import (
    FailurePolicy,
    IssueSource,
    SafetyAssessment,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)

# Default policy instance — mirrors existing pipeline escalation thresholds.
DEFAULT_POLICY: FailurePolicy = FailurePolicy()

# Minimum chunk count that constitutes "present" retrieval; below this triggers an issue.
_MINIMUM_RETRIEVAL_CHUNKS: int = 1


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


# ── Issue builders ─────────────────────────────────────────────────────────────


def _issue_unsupported_claims(claims: list[str], blocking: bool) -> SafetyIssue:
    count = len(claims)
    severity = SafetyIssueSeverity.ERROR if blocking else SafetyIssueSeverity.WARNING
    note = "blocking threshold exceeded" if blocking else "within policy threshold — warn only"
    return SafetyIssue(
        issue_code=SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT,
        severity=severity,
        message=f"{count} unsupported claim(s) detected in output; {note}",
        blocking=blocking,
        source=IssueSource.VALIDATION,
        metadata={"unsupported_claims": list(claims), "count": count},
    )


def _issue_missing_citations(blocking: bool) -> SafetyIssue:
    severity = SafetyIssueSeverity.ERROR if blocking else SafetyIssueSeverity.WARNING
    action = "blocking" if blocking else "warning only"
    return SafetyIssue(
        issue_code=SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED,
        severity=severity,
        message=f"citations are required by policy but the output has none ({action})",
        blocking=blocking,
        source=IssueSource.CITATION_QUALITY,
    )


def _issue_empty_retrieval(chunk_count: int) -> SafetyIssue:
    return SafetyIssue(
        issue_code=SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL,
        severity=SafetyIssueSeverity.WARNING,
        message=(
            f"retrieval context provided but chunk count ({chunk_count}) "
            f"is below minimum threshold ({_MINIMUM_RETRIEVAL_CHUNKS})"
        ),
        blocking=False,
        source=IssueSource.RETRIEVAL,
        metadata={"chunk_count": chunk_count, "minimum": _MINIMUM_RETRIEVAL_CHUNKS},
    )


def _issue_low_confidence(score: float, escalating: bool) -> SafetyIssue:
    action = "escalating" if escalating else "warning only"
    return SafetyIssue(
        issue_code=SafetyIssueCode.LOW_CONFIDENCE_OUTPUT,
        severity=SafetyIssueSeverity.WARNING,
        message=f"confidence_score ({score:.3f}) is below policy threshold; {action}",
        blocking=False,
        source=IssueSource.OUTPUT_QUALITY,
        metadata={"confidence_score": score},
    )


def _issue_schema_failure(reason: str) -> SafetyIssue:
    return SafetyIssue(
        issue_code=SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE,
        severity=SafetyIssueSeverity.CRITICAL,
        message=f"schema or contract validation failed: {reason}",
        blocking=True,
        source=IssueSource.SCHEMA,
    )


def _issue_escalation_required() -> SafetyIssue:
    return SafetyIssue(
        issue_code=SafetyIssueCode.ESCALATION_POLICY_TRIGGERED,
        severity=SafetyIssueSeverity.WARNING,
        message=(
            "candidate output has escalation_required=True; "
            "escalation policy applies"
        ),
        blocking=False,
        source=IssueSource.POLICY,
    )


# ── Policy rule checks ─────────────────────────────────────────────────────────


def _check_unsupported_claims(
    candidate: CaseOutput,
    policy: FailurePolicy,
    issues: list[SafetyIssue],
) -> None:
    """Add an issue when the candidate has any unsupported claims."""
    if not candidate.unsupported_claims:
        return
    count = len(candidate.unsupported_claims)
    blocking = count > policy.max_unsupported_claims_before_block
    issues.append(_issue_unsupported_claims(candidate.unsupported_claims, blocking))


def _check_missing_citations(
    candidate: CaseOutput,
    policy: FailurePolicy,
    issues: list[SafetyIssue],
) -> None:
    """Add an issue when policy requires citations and the candidate has none."""
    if not policy.require_citations:
        return
    if not candidate.citations:
        issues.append(_issue_missing_citations(blocking=policy.block_on_missing_citations))


def _check_retrieval_context(
    chunk_count: int | None,
    policy: FailurePolicy,
    issues: list[SafetyIssue],
) -> None:
    """
    Add a warning when the supplied retrieval context is empty or below minimum.

    This check is skipped when chunk_count is None — the caller signals that
    no retrieval context was provided for this evaluation.
    """
    if chunk_count is None:
        return
    if not policy.warn_on_empty_retrieval:
        return
    if chunk_count < _MINIMUM_RETRIEVAL_CHUNKS:
        issues.append(_issue_empty_retrieval(chunk_count))


def _check_low_confidence(
    candidate: CaseOutput,
    policy: FailurePolicy,
    issues: list[SafetyIssue],
) -> None:
    """Add an issue when confidence_score is below the policy threshold."""
    if candidate.confidence_score < policy.low_confidence_threshold:
        issues.append(
            _issue_low_confidence(
                candidate.confidence_score,
                escalating=policy.escalate_on_low_confidence,
            )
        )


def _check_escalation_required(
    candidate: CaseOutput,
    policy: FailurePolicy,
    issues: list[SafetyIssue],
) -> None:
    """Reflect the candidate's escalation_required flag as an escalation issue."""
    if candidate.escalation_required and policy.escalate_on_escalation_required:
        issues.append(_issue_escalation_required())


# ── Status derivation ──────────────────────────────────────────────────────────


def _derive_status(issues: list[SafetyIssue], policy: FailurePolicy) -> SafetyStatus:
    """
    Derive the final SafetyStatus from the collected issues and policy.

    Priority order (deterministic):
      1. Any blocking issue                                               → BLOCK
      2. ESCALATION_POLICY_TRIGGERED present                             → ESCALATE
         OR LOW_CONFIDENCE_OUTPUT present AND escalate_on_low_confidence  → ESCALATE
      3. Any issue present (non-blocking)                                → WARN
      4. No issues                                                       → ALLOW
    """
    if any(i.blocking for i in issues):
        return SafetyStatus.BLOCK

    for issue in issues:
        if issue.issue_code == SafetyIssueCode.ESCALATION_POLICY_TRIGGERED:
            return SafetyStatus.ESCALATE
        if (
            issue.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
            and policy.escalate_on_low_confidence
        ):
            return SafetyStatus.ESCALATE

    if issues:
        return SafetyStatus.WARN

    return SafetyStatus.ALLOW


# ── Assessment builder ─────────────────────────────────────────────────────────


def _build_assessment(
    document_id: str,
    issues: list[SafetyIssue],
    policy: FailurePolicy,
    notes: str | None = None,
) -> SafetyAssessment:
    status = _derive_status(issues, policy)
    has_blocking = any(i.blocking for i in issues)
    requires_escalation = status in (SafetyStatus.ESCALATE, SafetyStatus.BLOCK)
    return SafetyAssessment(
        document_id=document_id,
        issues=issues,
        has_blocking_issue=has_blocking,
        requires_escalation=requires_escalation,
        status=status,
        notes=notes,
        timestamp=_now_iso(),
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def evaluate_safety(
    candidate: CaseOutput,
    policy: FailurePolicy | None = None,
    retrieval_chunk_count: int | None = None,
    notes: str | None = None,
) -> SafetyAssessment:
    """
    Evaluate the safety and failure-policy status of one candidate CaseOutput.

    Applies all H-0 policy rules deterministically; no live AWS calls are made.

    Parameters
    ----------
    candidate             : typed CaseOutput to evaluate.
    policy                : FailurePolicy governing thresholds and flags;
                            defaults to DEFAULT_POLICY when not provided.
    retrieval_chunk_count : optional chunk count from an upstream RetrievalResult.
                            When provided, the empty-retrieval check is applied.
                            When None, the retrieval check is skipped entirely.
    notes                 : optional free-text observation attached to the assessment.

    Returns
    -------
    SafetyAssessment with status allow / warn / escalate / block.
    """
    if policy is None:
        policy = DEFAULT_POLICY

    issues: list[SafetyIssue] = []
    _check_unsupported_claims(candidate, policy, issues)
    _check_missing_citations(candidate, policy, issues)
    _check_retrieval_context(retrieval_chunk_count, policy, issues)
    _check_low_confidence(candidate, policy, issues)
    _check_escalation_required(candidate, policy, issues)

    return _build_assessment(candidate.document_id, issues, policy, notes)


def evaluate_safety_from_raw(
    raw: Any,
    policy: FailurePolicy | None = None,
    retrieval_chunk_count: int | None = None,
    notes: str | None = None,
) -> SafetyAssessment:
    """
    Evaluate safety for a raw, potentially malformed, candidate value.

    If the raw input cannot be parsed into a valid CaseOutput, returns a
    blocking SafetyAssessment with a schema_or_contract_failure issue immediately,
    without attempting any other policy rules.  This is the H-0 schema-failure path.

    Parameters
    ----------
    raw                   : any value (dict, None, malformed data, etc.).
    policy                : FailurePolicy; defaults to DEFAULT_POLICY.
    retrieval_chunk_count : passed through to evaluate_safety on a valid candidate.
    notes                 : optional free-text observation.

    Returns
    -------
    SafetyAssessment with status block (on schema failure) or the result of
    evaluate_safety (on a valid candidate).
    """
    if policy is None:
        policy = DEFAULT_POLICY

    try:
        candidate = CaseOutput.model_validate(raw)
    except ValidationError as exc:
        schema_issue = _issue_schema_failure(str(exc))
        document_id = (
            str(raw.get("document_id", "unknown"))
            if isinstance(raw, dict)
            else "unknown"
        )
        return _build_assessment(document_id, [schema_issue], policy, notes)

    return evaluate_safety(
        candidate,
        policy=policy,
        retrieval_chunk_count=retrieval_chunk_count,
        notes=notes,
    )
