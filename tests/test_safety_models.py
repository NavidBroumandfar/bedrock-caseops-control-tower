"""
H-0 unit tests — safety schema contracts.

Coverage:

  SafetyIssueSeverity:
    - all three enum values parse from string
    - invalid string raises validation error

  SafetyIssueCode:
    - all seven codes parse from string
    - invalid code raises validation error

  IssueSource:
    - all six sources parse from string

  SafetyStatus:
    - all four status values parse from string
    - invalid status raises validation error

  SafetyIssue:
    - valid construction with required fields succeeds
    - empty message raises validation error
    - whitespace-only message raises validation error
    - blocking=True is preserved
    - blocking=False is preserved
    - metadata defaults to empty dict
    - metadata accepts arbitrary key/value pairs
    - severity and source enums are validated

  SafetyAssessment:
    - valid construction with no issues → status allow
    - valid construction with issues → status warn or block
    - empty document_id raises validation error
    - whitespace-only document_id raises validation error
    - invalid timestamp raises validation error
    - valid ISO 8601 timestamp with Z suffix is accepted
    - valid ISO 8601 timestamp with offset is accepted
    - has_blocking_issue=True with status=allow raises validation error
    - has_blocking_issue=True with status=block is valid
    - has_blocking_issue=True with status=escalate is valid
    - has_blocking_issue=True with status=warn is valid
    - has_blocking_issue=False with status=allow is valid
    - requires_escalation defaults to False
    - notes defaults to None
    - issues defaults to empty list

  FailurePolicy:
    - default values match pipeline escalation threshold (0.6 confidence)
    - low_confidence_threshold above 1.0 raises validation error
    - low_confidence_threshold below 0.0 raises validation error
    - low_confidence_threshold at boundaries (0.0, 1.0) is valid
    - max_unsupported_claims_before_block < 0 raises validation error
    - max_unsupported_claims_before_block = 0 is valid (block on any)
    - max_unsupported_claims_before_block = 5 is valid
    - all boolean flags default to expected values
    - custom policy values round-trip correctly

  Schema coherence:
    - SafetyIssue with blocking=True and severity=CRITICAL is coherent
    - SafetyIssue with blocking=False and severity=WARNING is coherent
    - SafetyAssessment with a blocking issue must not have status=allow
    - SafetyAssessment serialises to dict and back without data loss

No AWS credentials or live calls required.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.safety_models import (
    FailurePolicy,
    IssueSource,
    SafetyAssessment,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_issue(
    *,
    issue_code: SafetyIssueCode = SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE,
    severity: SafetyIssueSeverity = SafetyIssueSeverity.CRITICAL,
    message: str = "test issue",
    blocking: bool = True,
    source: IssueSource = IssueSource.SCHEMA,
    metadata: dict | None = None,
) -> SafetyIssue:
    kwargs: dict = dict(
        issue_code=issue_code,
        severity=severity,
        message=message,
        blocking=blocking,
        source=source,
    )
    if metadata is not None:
        kwargs["metadata"] = metadata
    return SafetyIssue(**kwargs)


def _make_assessment(
    *,
    document_id: str = "doc-20260101-abc12345",
    issues: list[SafetyIssue] | None = None,
    has_blocking_issue: bool = False,
    requires_escalation: bool = False,
    status: SafetyStatus = SafetyStatus.ALLOW,
    notes: str | None = None,
    timestamp: str | None = None,
) -> SafetyAssessment:
    if timestamp is None:
        timestamp = datetime.now(tz=timezone.utc).isoformat()
    return SafetyAssessment(
        document_id=document_id,
        issues=issues or [],
        has_blocking_issue=has_blocking_issue,
        requires_escalation=requires_escalation,
        status=status,
        notes=notes,
        timestamp=timestamp,
    )


# ── SafetyIssueSeverity ────────────────────────────────────────────────────────


class TestSafetyIssueSeverity:
    def test_warning_value(self):
        assert SafetyIssueSeverity.WARNING == "warning"

    def test_error_value(self):
        assert SafetyIssueSeverity.ERROR == "error"

    def test_critical_value(self):
        assert SafetyIssueSeverity.CRITICAL == "critical"

    def test_all_three_parse_from_string(self):
        for val in ("warning", "error", "critical"):
            parsed = SafetyIssueSeverity(val)
            assert parsed.value == val

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError):
            SafetyIssueSeverity("fatal")


# ── SafetyIssueCode ────────────────────────────────────────────────────────────


class TestSafetyIssueCode:
    def test_all_seven_codes_parse(self):
        expected = [
            "unsupported_claims_present",
            "missing_citations_when_required",
            "empty_or_weak_retrieval",
            "low_confidence_output",
            "schema_or_contract_failure",
            "escalation_policy_triggered",
            "unsafe_output_block_required",
        ]
        for val in expected:
            code = SafetyIssueCode(val)
            assert code.value == val

    def test_invalid_code_raises(self):
        with pytest.raises(ValueError):
            SafetyIssueCode("not_a_real_code")

    def test_unsupported_claims_present(self):
        assert SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT.value == "unsupported_claims_present"

    def test_schema_or_contract_failure(self):
        assert SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE.value == "schema_or_contract_failure"

    def test_unsafe_output_block_required(self):
        assert SafetyIssueCode.UNSAFE_OUTPUT_BLOCK_REQUIRED.value == "unsafe_output_block_required"


# ── IssueSource ────────────────────────────────────────────────────────────────


class TestIssueSource:
    def test_all_six_sources_parse(self):
        expected = [
            "validation",
            "retrieval",
            "citation_quality",
            "output_quality",
            "schema",
            "policy",
        ]
        for val in expected:
            src = IssueSource(val)
            assert src.value == val

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError):
            IssueSource("unknown_source")


# ── SafetyStatus ───────────────────────────────────────────────────────────────


class TestSafetyStatus:
    def test_all_four_values(self):
        assert SafetyStatus.ALLOW == "allow"
        assert SafetyStatus.WARN == "warn"
        assert SafetyStatus.ESCALATE == "escalate"
        assert SafetyStatus.BLOCK == "block"

    def test_all_parse_from_string(self):
        for val in ("allow", "warn", "escalate", "block"):
            status = SafetyStatus(val)
            assert status.value == val

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError):
            SafetyStatus("reject")


# ── SafetyIssue ────────────────────────────────────────────────────────────────


class TestSafetyIssue:
    def test_valid_blocking_issue(self):
        issue = _make_issue(blocking=True, severity=SafetyIssueSeverity.CRITICAL)
        assert issue.blocking is True
        assert issue.severity == SafetyIssueSeverity.CRITICAL

    def test_valid_non_blocking_issue(self):
        issue = _make_issue(blocking=False, severity=SafetyIssueSeverity.WARNING)
        assert issue.blocking is False
        assert issue.severity == SafetyIssueSeverity.WARNING

    def test_empty_message_raises(self):
        with pytest.raises(ValidationError):
            _make_issue(message="")

    def test_whitespace_only_message_raises(self):
        with pytest.raises(ValidationError):
            _make_issue(message="   ")

    def test_metadata_defaults_to_empty_dict(self):
        issue = _make_issue()
        assert issue.metadata == {}

    def test_metadata_accepts_arbitrary_keys(self):
        issue = _make_issue(metadata={"count": 3, "claims": ["claim a", "claim b"]})
        assert issue.metadata["count"] == 3
        assert issue.metadata["claims"] == ["claim a", "claim b"]

    def test_all_issue_codes_accepted(self):
        for code in SafetyIssueCode:
            issue = _make_issue(issue_code=code)
            assert issue.issue_code == code

    def test_all_sources_accepted(self):
        for source in IssueSource:
            issue = _make_issue(source=source)
            assert issue.source == source

    def test_issue_round_trips_via_model_dump(self):
        issue = _make_issue(
            issue_code=SafetyIssueCode.LOW_CONFIDENCE_OUTPUT,
            severity=SafetyIssueSeverity.WARNING,
            message="confidence too low",
            blocking=False,
            source=IssueSource.OUTPUT_QUALITY,
            metadata={"confidence_score": 0.45},
        )
        dumped = issue.model_dump()
        restored = SafetyIssue.model_validate(dumped)
        assert restored == issue

    def test_unsupported_claims_issue(self):
        issue = _make_issue(
            issue_code=SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT,
            severity=SafetyIssueSeverity.ERROR,
            source=IssueSource.VALIDATION,
            blocking=True,
        )
        assert issue.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        assert issue.source == IssueSource.VALIDATION

    def test_retrieval_issue_non_blocking(self):
        issue = _make_issue(
            issue_code=SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL,
            severity=SafetyIssueSeverity.WARNING,
            source=IssueSource.RETRIEVAL,
            blocking=False,
            metadata={"chunk_count": 0, "minimum": 1},
        )
        assert issue.blocking is False
        assert issue.metadata["chunk_count"] == 0


# ── SafetyAssessment ───────────────────────────────────────────────────────────


class TestSafetyAssessment:
    def test_allow_status_no_issues(self):
        a = _make_assessment(status=SafetyStatus.ALLOW)
        assert a.status == SafetyStatus.ALLOW
        assert a.issues == []
        assert a.has_blocking_issue is False

    def test_warn_status_with_non_blocking_issue(self):
        issue = _make_issue(blocking=False, severity=SafetyIssueSeverity.WARNING)
        a = _make_assessment(status=SafetyStatus.WARN, issues=[issue])
        assert a.status == SafetyStatus.WARN
        assert len(a.issues) == 1

    def test_block_status_with_blocking_issue(self):
        issue = _make_issue(blocking=True, severity=SafetyIssueSeverity.CRITICAL)
        a = _make_assessment(
            status=SafetyStatus.BLOCK,
            issues=[issue],
            has_blocking_issue=True,
        )
        assert a.status == SafetyStatus.BLOCK
        assert a.has_blocking_issue is True

    def test_escalate_status_valid(self):
        issue = _make_issue(blocking=False)
        a = _make_assessment(
            status=SafetyStatus.ESCALATE,
            issues=[issue],
            requires_escalation=True,
        )
        assert a.status == SafetyStatus.ESCALATE
        assert a.requires_escalation is True

    def test_empty_document_id_raises(self):
        with pytest.raises(ValidationError):
            _make_assessment(document_id="")

    def test_whitespace_document_id_raises(self):
        with pytest.raises(ValidationError):
            _make_assessment(document_id="   ")

    def test_invalid_timestamp_raises(self):
        with pytest.raises(ValidationError):
            _make_assessment(timestamp="not-a-timestamp")

    def test_valid_timestamp_with_z_suffix(self):
        a = _make_assessment(timestamp="2026-04-10T12:00:00Z")
        assert "2026" in a.timestamp

    def test_valid_timestamp_with_offset(self):
        a = _make_assessment(timestamp="2026-04-10T12:00:00+00:00")
        assert "2026" in a.timestamp

    def test_blocking_issue_with_allow_status_raises(self):
        with pytest.raises(ValidationError):
            _make_assessment(
                status=SafetyStatus.ALLOW,
                has_blocking_issue=True,
            )

    def test_blocking_issue_with_block_status_valid(self):
        issue = _make_issue(blocking=True)
        a = _make_assessment(
            status=SafetyStatus.BLOCK,
            has_blocking_issue=True,
            issues=[issue],
        )
        assert a.status == SafetyStatus.BLOCK

    def test_blocking_issue_with_escalate_status_valid(self):
        issue = _make_issue(blocking=True)
        a = _make_assessment(
            status=SafetyStatus.ESCALATE,
            has_blocking_issue=True,
            issues=[issue],
        )
        assert a.status == SafetyStatus.ESCALATE

    def test_blocking_issue_with_warn_status_valid(self):
        issue = _make_issue(blocking=True)
        a = _make_assessment(
            status=SafetyStatus.WARN,
            has_blocking_issue=True,
            issues=[issue],
        )
        assert a.status == SafetyStatus.WARN

    def test_notes_default_is_none(self):
        a = _make_assessment()
        assert a.notes is None

    def test_notes_accepted(self):
        a = _make_assessment(notes="test note")
        assert a.notes == "test note"

    def test_requires_escalation_default_is_false(self):
        a = _make_assessment()
        assert a.requires_escalation is False

    def test_issues_default_is_empty_list(self):
        a = _make_assessment()
        assert a.issues == []

    def test_multiple_issues_accepted(self):
        issue1 = _make_issue(
            issue_code=SafetyIssueCode.LOW_CONFIDENCE_OUTPUT,
            blocking=False,
        )
        issue2 = _make_issue(
            issue_code=SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL,
            blocking=False,
        )
        a = _make_assessment(status=SafetyStatus.WARN, issues=[issue1, issue2])
        assert len(a.issues) == 2

    def test_assessment_round_trips_via_model_dump(self):
        issue = _make_issue(blocking=False, severity=SafetyIssueSeverity.WARNING)
        a = _make_assessment(
            status=SafetyStatus.WARN,
            issues=[issue],
            notes="round-trip check",
        )
        dumped = a.model_dump()
        restored = SafetyAssessment.model_validate(dumped)
        assert restored.status == a.status
        assert len(restored.issues) == 1
        assert restored.notes == a.notes


# ── FailurePolicy ──────────────────────────────────────────────────────────────


class TestFailurePolicy:
    def test_default_low_confidence_threshold_is_0_6(self):
        """Default matches the existing pipeline ESCALATION_CONFIDENCE_THRESHOLD."""
        policy = FailurePolicy()
        assert policy.low_confidence_threshold == 0.6

    def test_default_max_unsupported_claims_is_0(self):
        policy = FailurePolicy()
        assert policy.max_unsupported_claims_before_block == 0

    def test_default_require_citations_is_true(self):
        policy = FailurePolicy()
        assert policy.require_citations is True

    def test_default_block_on_schema_failure_is_true(self):
        policy = FailurePolicy()
        assert policy.block_on_schema_failure is True

    def test_default_block_on_missing_citations_is_true(self):
        policy = FailurePolicy()
        assert policy.block_on_missing_citations is True

    def test_default_warn_on_empty_retrieval_is_true(self):
        policy = FailurePolicy()
        assert policy.warn_on_empty_retrieval is True

    def test_default_escalate_on_low_confidence_is_true(self):
        policy = FailurePolicy()
        assert policy.escalate_on_low_confidence is True

    def test_default_escalate_on_escalation_required_is_true(self):
        policy = FailurePolicy()
        assert policy.escalate_on_escalation_required is True

    def test_threshold_above_1_raises(self):
        with pytest.raises(ValidationError):
            FailurePolicy(low_confidence_threshold=1.1)

    def test_threshold_below_0_raises(self):
        with pytest.raises(ValidationError):
            FailurePolicy(low_confidence_threshold=-0.1)

    def test_threshold_at_0_is_valid(self):
        policy = FailurePolicy(low_confidence_threshold=0.0)
        assert policy.low_confidence_threshold == 0.0

    def test_threshold_at_1_is_valid(self):
        policy = FailurePolicy(low_confidence_threshold=1.0)
        assert policy.low_confidence_threshold == 1.0

    def test_max_unsupported_claims_negative_raises(self):
        with pytest.raises(ValidationError):
            FailurePolicy(max_unsupported_claims_before_block=-1)

    def test_max_unsupported_claims_zero_is_valid(self):
        policy = FailurePolicy(max_unsupported_claims_before_block=0)
        assert policy.max_unsupported_claims_before_block == 0

    def test_max_unsupported_claims_positive_is_valid(self):
        policy = FailurePolicy(max_unsupported_claims_before_block=5)
        assert policy.max_unsupported_claims_before_block == 5

    def test_all_boolean_flags_can_be_set_false(self):
        policy = FailurePolicy(
            require_citations=False,
            block_on_schema_failure=False,
            block_on_missing_citations=False,
            warn_on_empty_retrieval=False,
            escalate_on_low_confidence=False,
            escalate_on_escalation_required=False,
        )
        assert policy.require_citations is False
        assert policy.block_on_schema_failure is False
        assert policy.block_on_missing_citations is False
        assert policy.warn_on_empty_retrieval is False
        assert policy.escalate_on_low_confidence is False
        assert policy.escalate_on_escalation_required is False

    def test_custom_policy_values_round_trip(self):
        policy = FailurePolicy(
            low_confidence_threshold=0.75,
            max_unsupported_claims_before_block=2,
            require_citations=True,
            block_on_schema_failure=True,
            block_on_missing_citations=False,
            warn_on_empty_retrieval=True,
            escalate_on_low_confidence=False,
            escalate_on_escalation_required=True,
        )
        dumped = policy.model_dump()
        restored = FailurePolicy.model_validate(dumped)
        assert restored == policy

    def test_policy_is_immutable_via_model_copy(self):
        policy = FailurePolicy()
        modified = policy.model_copy(update={"low_confidence_threshold": 0.8})
        assert policy.low_confidence_threshold == 0.6
        assert modified.low_confidence_threshold == 0.8
