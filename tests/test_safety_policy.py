"""
H-0 unit tests — safety policy evaluator.

Coverage:

  unsupported_claims rule:
    - no unsupported claims → no issue added
    - one unsupported claim with max=0 → blocking issue
    - one unsupported claim with max=1 → warning (within threshold)
    - two claims with max=1 → blocking issue (count > max)
    - three claims with max=2 → blocking issue
    - issue code is UNSUPPORTED_CLAIMS_PRESENT
    - issue source is VALIDATION
    - metadata contains claims list and count

  missing citations rule:
    - citations present → no citation issue
    - no citations + require_citations=True + block=True → blocking issue
    - no citations + require_citations=True + block=False → non-blocking warning
    - no citations + require_citations=False → no issue
    - issue code is MISSING_CITATIONS_WHEN_REQUIRED
    - issue source is CITATION_QUALITY

  empty / weak retrieval rule:
    - retrieval_chunk_count=None → check skipped entirely
    - chunk_count=0 + warn_on_empty_retrieval=True → warning issue
    - chunk_count=1 → no issue (meets minimum)
    - chunk_count=5 → no issue
    - warn_on_empty_retrieval=False → check skipped even when count=0
    - issue code is EMPTY_OR_WEAK_RETRIEVAL
    - issue source is RETRIEVAL
    - blocking=False always

  low confidence rule:
    - confidence=0.7, threshold=0.6 → no issue
    - confidence=0.6, threshold=0.6 → no issue (boundary: not strictly below)
    - confidence=0.59, threshold=0.6 → issue added
    - confidence=0.0, threshold=0.6 → issue added
    - escalate_on_low_confidence=True → ESCALATE status
    - escalate_on_low_confidence=False → WARN status (not escalate)
    - issue code is LOW_CONFIDENCE_OUTPUT
    - issue source is OUTPUT_QUALITY
    - blocking=False always

  escalation alignment rule:
    - escalation_required=True + escalate_on_escalation_required=True → ESCALATE issue
    - escalation_required=False → no escalation issue
    - escalation_required=True + escalate_on_escalation_required=False → no issue
    - issue code is ESCALATION_POLICY_TRIGGERED
    - issue source is POLICY
    - blocking=False always

  schema / contract failure (evaluate_safety_from_raw):
    - None input → blocking SCHEMA_OR_CONTRACT_FAILURE assessment
    - empty dict → blocking assessment
    - dict missing required fields → blocking assessment
    - dict with invalid confidence_score type → blocking assessment
    - valid dict → delegates to evaluate_safety normally
    - document_id extracted from raw dict when available
    - document_id defaults to "unknown" when absent or raw is not a dict
    - issue code is SCHEMA_OR_CONTRACT_FAILURE
    - issue source is SCHEMA
    - blocking=True always for schema failures
    - status is BLOCK for schema failures

  status selection (deterministic):
    - no issues → ALLOW
    - only non-blocking issues (excluding escalation codes) → WARN
    - low confidence + escalate_on_low_confidence=True → ESCALATE
    - low confidence + escalate_on_low_confidence=False → WARN
    - escalation_required=True + policy escalates → ESCALATE
    - blocking issue present → BLOCK (regardless of other issues)
    - blocking + escalation issues → BLOCK (blocking takes priority)
    - blocking issue alone → BLOCK

  SafetyAssessment fields from evaluate_safety:
    - has_blocking_issue=True when any issue is blocking
    - has_blocking_issue=False when no blocking issues
    - requires_escalation=True when status is ESCALATE
    - requires_escalation=True when status is BLOCK
    - requires_escalation=False when status is ALLOW or WARN
    - document_id matches candidate.document_id
    - timestamp is a valid ISO 8601 string
    - notes propagated when provided

  DEFAULT_POLICY:
    - evaluate_safety without policy argument uses DEFAULT_POLICY values

  determinism:
    - identical inputs produce identical status and issue count across repeated calls

  structural / architecture:
    - safety_policy does NOT import boto3 or botocore
    - safety_policy does NOT import any app.services module
    - safety_policy does NOT import retrieval_scorer, citation_scorer, output_quality_scorer
    - safety_policy does NOT import runner
    - safety_policy imports only output_models and safety_models

No AWS credentials or live calls required.
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from app.evaluation.safety_policy import (
    DEFAULT_POLICY,
    evaluate_safety,
    evaluate_safety_from_raw,
)
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.safety_models import (
    FailurePolicy,
    IssueSource,
    SafetyIssueCode,
    SafetyStatus,
)


# ── Candidate builders ─────────────────────────────────────────────────────────


def _make_citation() -> Citation:
    return Citation(
        source_id="src-001",
        source_label="FDA Warning Letter 2026",
        excerpt="Equipment cleaning procedures were inadequate.",
        relevance_score=0.9,
    )


def _make_candidate(
    *,
    document_id: str = "doc-20260101-abc12345",
    confidence_score: float = 0.9,
    unsupported_claims: list[str] | None = None,
    citations: list[Citation] | None = None,
    escalation_required: bool = False,
    escalation_reason: str | None = None,
) -> CaseOutput:
    return CaseOutput(
        document_id=document_id,
        source_filename="test_doc.md",
        source_type="FDA",
        severity="High",
        category="Regulatory / Manufacturing",
        summary="Facility failed to maintain equipment cleaning procedures.",
        recommendations=["Initiate CAPA", "Notify compliance team"],
        citations=citations if citations is not None else [_make_citation()],
        confidence_score=confidence_score,
        unsupported_claims=unsupported_claims if unsupported_claims is not None else [],
        escalation_required=escalation_required,
        escalation_reason=escalation_reason,
        validated_by="validation-agent-v1",
        timestamp="2026-04-10T12:00:00+00:00",
    )


def _make_clean_candidate() -> CaseOutput:
    """No issues with default policy: high confidence, citations present, no unsupported claims."""
    return _make_candidate(
        confidence_score=0.9,
        citations=[_make_citation()],
        unsupported_claims=[],
        escalation_required=False,
    )


# ── Unsupported claims rule ────────────────────────────────────────────────────


class TestUnsupportedClaimsRule:
    def test_no_claims_produces_no_issue(self):
        candidate = _make_candidate(unsupported_claims=[])
        result = evaluate_safety(candidate, policy=FailurePolicy(require_citations=False))
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert claim_issues == []

    def test_one_claim_with_max_0_is_blocking(self):
        candidate = _make_candidate(unsupported_claims=["ungrounded claim"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=0,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert len(claim_issues) == 1
        assert claim_issues[0].blocking is True

    def test_one_claim_with_max_1_is_warning_not_blocking(self):
        candidate = _make_candidate(unsupported_claims=["ungrounded claim"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=1,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert len(claim_issues) == 1
        assert claim_issues[0].blocking is False

    def test_two_claims_with_max_1_is_blocking(self):
        candidate = _make_candidate(unsupported_claims=["claim a", "claim b"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=1,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert claim_issues[0].blocking is True

    def test_three_claims_with_max_2_is_blocking(self):
        candidate = _make_candidate(unsupported_claims=["a", "b", "c"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=2,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert claim_issues[0].blocking is True

    def test_issue_source_is_validation(self):
        candidate = _make_candidate(unsupported_claims=["claim"])
        policy = FailurePolicy(require_citations=False)
        result = evaluate_safety(candidate, policy=policy)
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert claim_issues[0].source == IssueSource.VALIDATION

    def test_metadata_contains_claims_and_count(self):
        claims = ["claim a", "claim b"]
        candidate = _make_candidate(unsupported_claims=claims)
        policy = FailurePolicy(require_citations=False)
        result = evaluate_safety(candidate, policy=policy)
        claim_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
        ]
        assert claim_issues[0].metadata["count"] == 2
        assert "claim a" in claim_issues[0].metadata["unsupported_claims"]


# ── Missing citations rule ─────────────────────────────────────────────────────


class TestMissingCitationsRule:
    def test_citations_present_no_citation_issue(self):
        candidate = _make_candidate(citations=[_make_citation()])
        policy = FailurePolicy(require_citations=True)
        result = evaluate_safety(candidate, policy=policy)
        citation_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED
        ]
        assert citation_issues == []

    def test_no_citations_require_true_block_true_is_blocking(self):
        candidate = _make_candidate(citations=[])
        policy = FailurePolicy(
            require_citations=True,
            block_on_missing_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        citation_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED
        ]
        assert len(citation_issues) == 1
        assert citation_issues[0].blocking is True

    def test_no_citations_require_true_block_false_is_warning(self):
        candidate = _make_candidate(citations=[])
        policy = FailurePolicy(
            require_citations=True,
            block_on_missing_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        citation_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED
        ]
        assert len(citation_issues) == 1
        assert citation_issues[0].blocking is False

    def test_no_citations_require_false_no_issue(self):
        candidate = _make_candidate(citations=[])
        policy = FailurePolicy(require_citations=False)
        result = evaluate_safety(candidate, policy=policy)
        citation_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED
        ]
        assert citation_issues == []

    def test_issue_source_is_citation_quality(self):
        candidate = _make_candidate(citations=[])
        policy = FailurePolicy(require_citations=True)
        result = evaluate_safety(candidate, policy=policy)
        citation_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED
        ]
        assert citation_issues[0].source == IssueSource.CITATION_QUALITY


# ── Empty / weak retrieval rule ────────────────────────────────────────────────


class TestRetrievalContextRule:
    def test_no_chunk_count_skips_check(self):
        candidate = _make_clean_candidate()
        result = evaluate_safety(candidate, retrieval_chunk_count=None)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues == []

    def test_zero_chunks_warn_on_empty_true_adds_warning(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(warn_on_empty_retrieval=True, require_citations=True)
        result = evaluate_safety(
            candidate, policy=policy, retrieval_chunk_count=0
        )
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert len(retrieval_issues) == 1

    def test_one_chunk_meets_minimum_no_issue(self):
        candidate = _make_clean_candidate()
        result = evaluate_safety(candidate, retrieval_chunk_count=1)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues == []

    def test_five_chunks_no_issue(self):
        candidate = _make_clean_candidate()
        result = evaluate_safety(candidate, retrieval_chunk_count=5)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues == []

    def test_warn_on_empty_false_skips_check_even_with_zero_chunks(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(warn_on_empty_retrieval=False, require_citations=True)
        result = evaluate_safety(candidate, policy=policy, retrieval_chunk_count=0)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues == []

    def test_retrieval_issue_is_never_blocking(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(warn_on_empty_retrieval=True, require_citations=True)
        result = evaluate_safety(candidate, policy=policy, retrieval_chunk_count=0)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues[0].blocking is False

    def test_retrieval_issue_source_is_retrieval(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(warn_on_empty_retrieval=True, require_citations=True)
        result = evaluate_safety(candidate, policy=policy, retrieval_chunk_count=0)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues[0].source == IssueSource.RETRIEVAL

    def test_metadata_contains_chunk_count(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(warn_on_empty_retrieval=True, require_citations=True)
        result = evaluate_safety(candidate, policy=policy, retrieval_chunk_count=0)
        retrieval_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL
        ]
        assert retrieval_issues[0].metadata["chunk_count"] == 0
        assert retrieval_issues[0].metadata["minimum"] == 1


# ── Low confidence rule ────────────────────────────────────────────────────────


class TestLowConfidenceRule:
    def test_high_confidence_no_issue(self):
        candidate = _make_candidate(confidence_score=0.9)
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert conf_issues == []

    def test_exactly_at_threshold_no_issue(self):
        """Boundary: not strictly below threshold."""
        candidate = _make_candidate(confidence_score=0.6)
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert conf_issues == []

    def test_just_below_threshold_adds_issue(self):
        candidate = _make_candidate(confidence_score=0.59)
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert len(conf_issues) == 1

    def test_zero_confidence_adds_issue(self):
        candidate = _make_candidate(confidence_score=0.0)
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert len(conf_issues) == 1

    def test_low_confidence_escalate_true_produces_escalate_status(self):
        candidate = _make_candidate(confidence_score=0.4, citations=[_make_citation()])
        policy = FailurePolicy(
            escalate_on_low_confidence=True,
            require_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.ESCALATE

    def test_low_confidence_escalate_false_produces_warn_status(self):
        candidate = _make_candidate(confidence_score=0.4, citations=[_make_citation()])
        policy = FailurePolicy(
            escalate_on_low_confidence=False,
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.WARN

    def test_low_confidence_issue_is_never_blocking(self):
        candidate = _make_candidate(confidence_score=0.1, citations=[_make_citation()])
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert conf_issues[0].blocking is False

    def test_low_confidence_source_is_output_quality(self):
        candidate = _make_candidate(confidence_score=0.1, citations=[_make_citation()])
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert conf_issues[0].source == IssueSource.OUTPUT_QUALITY

    def test_metadata_contains_confidence_score(self):
        candidate = _make_candidate(confidence_score=0.45, citations=[_make_citation()])
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert conf_issues[0].metadata["confidence_score"] == pytest.approx(0.45)


# ── Escalation alignment rule ──────────────────────────────────────────────────


class TestEscalationAlignmentRule:
    def test_escalation_required_with_policy_adds_issue(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(
            escalate_on_escalation_required=True,
            require_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        esc_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.ESCALATION_POLICY_TRIGGERED
        ]
        assert len(esc_issues) == 1

    def test_escalation_required_false_no_issue(self):
        candidate = _make_candidate(escalation_required=False, citations=[_make_citation()])
        policy = FailurePolicy(escalate_on_escalation_required=True)
        result = evaluate_safety(candidate, policy=policy)
        esc_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.ESCALATION_POLICY_TRIGGERED
        ]
        assert esc_issues == []

    def test_escalation_required_true_policy_false_no_issue(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(
            escalate_on_escalation_required=False,
            require_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        esc_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.ESCALATION_POLICY_TRIGGERED
        ]
        assert esc_issues == []

    def test_escalation_issue_produces_escalate_status(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(
            escalate_on_escalation_required=True,
            require_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.ESCALATE

    def test_escalation_issue_is_never_blocking(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(escalate_on_escalation_required=True)
        result = evaluate_safety(candidate, policy=policy)
        esc_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.ESCALATION_POLICY_TRIGGERED
        ]
        assert esc_issues[0].blocking is False

    def test_escalation_issue_source_is_policy(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(escalate_on_escalation_required=True)
        result = evaluate_safety(candidate, policy=policy)
        esc_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.ESCALATION_POLICY_TRIGGERED
        ]
        assert esc_issues[0].source == IssueSource.POLICY


# ── Schema / contract failure (evaluate_safety_from_raw) ──────────────────────


class TestSchemaFailure:
    def test_none_input_returns_blocking_assessment(self):
        result = evaluate_safety_from_raw(None)
        assert result.status == SafetyStatus.BLOCK
        assert result.has_blocking_issue is True

    def test_empty_dict_returns_blocking_assessment(self):
        result = evaluate_safety_from_raw({})
        assert result.status == SafetyStatus.BLOCK
        assert result.has_blocking_issue is True

    def test_missing_required_fields_returns_blocking(self):
        raw = {"document_id": "doc-001", "source_type": "FDA"}
        result = evaluate_safety_from_raw(raw)
        assert result.status == SafetyStatus.BLOCK

    def test_invalid_confidence_score_type_returns_blocking(self):
        raw = {
            "document_id": "doc-001",
            "source_filename": "test.md",
            "source_type": "FDA",
            "severity": "High",
            "category": "Regulatory",
            "summary": "Test summary",
            "recommendations": ["rec 1"],
            "citations": [],
            "confidence_score": "not-a-float",
            "unsupported_claims": [],
            "escalation_required": False,
            "escalation_reason": None,
            "validated_by": "test-agent",
            "timestamp": "2026-04-10T12:00:00+00:00",
        }
        result = evaluate_safety_from_raw(raw)
        assert result.status == SafetyStatus.BLOCK

    def test_schema_failure_issue_code(self):
        result = evaluate_safety_from_raw({})
        schema_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE
        ]
        assert len(schema_issues) == 1

    def test_schema_failure_issue_source_is_schema(self):
        result = evaluate_safety_from_raw({})
        schema_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE
        ]
        assert schema_issues[0].source == IssueSource.SCHEMA

    def test_schema_failure_issue_is_blocking(self):
        result = evaluate_safety_from_raw({})
        schema_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE
        ]
        assert schema_issues[0].blocking is True

    def test_document_id_extracted_from_raw_dict(self):
        result = evaluate_safety_from_raw({"document_id": "doc-schema-fail"})
        assert result.document_id == "doc-schema-fail"

    def test_document_id_defaults_to_unknown_when_absent(self):
        result = evaluate_safety_from_raw({})
        assert result.document_id == "unknown"

    def test_document_id_defaults_to_unknown_when_raw_is_not_dict(self):
        result = evaluate_safety_from_raw("not-a-dict")
        assert result.document_id == "unknown"

    def test_valid_dict_delegates_to_evaluate_safety(self):
        raw = {
            "document_id": "doc-20260101-abc12345",
            "source_filename": "test.md",
            "source_type": "FDA",
            "severity": "High",
            "category": "Regulatory",
            "summary": "Facility failed cleaning procedures.",
            "recommendations": ["Initiate CAPA"],
            "citations": [
                {
                    "source_id": "src-001",
                    "source_label": "FDA Letter",
                    "excerpt": "No cleaning procedures found.",
                    "relevance_score": 0.9,
                }
            ],
            "confidence_score": 0.9,
            "unsupported_claims": [],
            "escalation_required": False,
            "escalation_reason": None,
            "validated_by": "test-agent",
            "timestamp": "2026-04-10T12:00:00+00:00",
        }
        policy = FailurePolicy(require_citations=True, escalate_on_escalation_required=False)
        result = evaluate_safety_from_raw(raw, policy=policy)
        assert result.status == SafetyStatus.ALLOW
        assert result.document_id == "doc-20260101-abc12345"


# ── Status selection (deterministic) ──────────────────────────────────────────


class TestStatusSelection:
    def test_no_issues_returns_allow(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.ALLOW

    def test_only_non_blocking_retrieval_warning_returns_warn(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(
            require_citations=True,
            warn_on_empty_retrieval=True,
            escalate_on_escalation_required=False,
            escalate_on_low_confidence=False,
        )
        result = evaluate_safety(candidate, policy=policy, retrieval_chunk_count=0)
        assert result.status == SafetyStatus.WARN

    def test_low_confidence_escalate_produces_escalate(self):
        candidate = _make_candidate(
            confidence_score=0.3,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(
            escalate_on_low_confidence=True,
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.ESCALATE

    def test_low_confidence_no_escalate_produces_warn(self):
        candidate = _make_candidate(
            confidence_score=0.3,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(
            escalate_on_low_confidence=False,
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.WARN

    def test_escalation_required_produces_escalate(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(
            escalate_on_escalation_required=True,
            require_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.ESCALATE

    def test_blocking_issue_produces_block(self):
        candidate = _make_candidate(unsupported_claims=["bad claim"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=0,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.BLOCK

    def test_blocking_plus_escalation_is_block(self):
        """Blocking takes priority over escalation."""
        candidate = _make_candidate(
            unsupported_claims=["bad claim"],
            escalation_required=True,
            confidence_score=0.3,
        )
        policy = FailurePolicy(
            max_unsupported_claims_before_block=0,
            require_citations=False,
            escalate_on_low_confidence=True,
            escalate_on_escalation_required=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.BLOCK

    def test_missing_citations_blocking_produces_block(self):
        candidate = _make_candidate(citations=[])
        policy = FailurePolicy(
            require_citations=True,
            block_on_missing_citations=True,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.BLOCK

    def test_missing_citations_non_blocking_produces_warn(self):
        candidate = _make_candidate(
            citations=[],
            confidence_score=0.9,
            escalation_required=False,
        )
        policy = FailurePolicy(
            require_citations=True,
            block_on_missing_citations=False,
            escalate_on_low_confidence=False,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.status == SafetyStatus.WARN


# ── SafetyAssessment output fields ────────────────────────────────────────────


class TestAssessmentOutputFields:
    def test_has_blocking_issue_true_when_blocking_issue_present(self):
        candidate = _make_candidate(unsupported_claims=["claim"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=0,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.has_blocking_issue is True

    def test_has_blocking_issue_false_when_no_blocking_issues(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.has_blocking_issue is False

    def test_requires_escalation_true_when_status_escalate(self):
        candidate = _make_candidate(
            escalation_required=True,
            confidence_score=0.9,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(escalate_on_escalation_required=True)
        result = evaluate_safety(candidate, policy=policy)
        assert result.requires_escalation is True

    def test_requires_escalation_true_when_status_block(self):
        candidate = _make_candidate(unsupported_claims=["claim"])
        policy = FailurePolicy(
            max_unsupported_claims_before_block=0,
            require_citations=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.requires_escalation is True

    def test_requires_escalation_false_when_status_allow(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.requires_escalation is False

    def test_requires_escalation_false_when_status_warn(self):
        candidate = _make_candidate(citations=[])
        policy = FailurePolicy(
            require_citations=True,
            block_on_missing_citations=False,
            escalate_on_low_confidence=False,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        assert result.requires_escalation is False

    def test_document_id_matches_candidate(self):
        candidate = _make_candidate(document_id="doc-test-999")
        policy = FailurePolicy(require_citations=False)
        result = evaluate_safety(candidate, policy=policy)
        assert result.document_id == "doc-test-999"

    def test_timestamp_is_valid_iso8601(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy)
        datetime.fromisoformat(result.timestamp)

    def test_notes_propagated_when_provided(self):
        candidate = _make_clean_candidate()
        policy = FailurePolicy(
            require_citations=True,
            escalate_on_escalation_required=False,
        )
        result = evaluate_safety(candidate, policy=policy, notes="reviewer note")
        assert result.notes == "reviewer note"

    def test_notes_none_by_default(self):
        candidate = _make_clean_candidate()
        result = evaluate_safety(candidate)
        assert result.notes is None


# ── DEFAULT_POLICY ─────────────────────────────────────────────────────────────


class TestDefaultPolicy:
    def test_no_policy_arg_uses_default(self):
        """evaluate_safety without policy uses DEFAULT_POLICY (0.6 threshold)."""
        candidate = _make_candidate(
            confidence_score=0.59,
            citations=[_make_citation()],
        )
        result = evaluate_safety(candidate)
        conf_issues = [
            i for i in result.issues
            if i.issue_code == SafetyIssueCode.LOW_CONFIDENCE_OUTPUT
        ]
        assert len(conf_issues) == 1

    def test_default_policy_blocks_on_unsupported_claims(self):
        candidate = _make_candidate(
            unsupported_claims=["bad claim"],
            confidence_score=0.9,
        )
        result = evaluate_safety(candidate)
        assert result.status == SafetyStatus.BLOCK

    def test_default_policy_blocks_on_missing_citations(self):
        candidate = _make_candidate(citations=[], confidence_score=0.9)
        result = evaluate_safety(candidate)
        assert result.status == SafetyStatus.BLOCK

    def test_default_policy_instance_is_failure_policy(self):
        assert isinstance(DEFAULT_POLICY, FailurePolicy)


# ── Determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_identical_inputs_produce_same_status(self):
        candidate = _make_candidate(
            confidence_score=0.4,
            citations=[_make_citation()],
        )
        policy = FailurePolicy(escalate_on_escalation_required=False)
        results = [evaluate_safety(candidate, policy=policy) for _ in range(5)]
        statuses = {r.status for r in results}
        assert len(statuses) == 1

    def test_identical_inputs_produce_same_issue_count(self):
        candidate = _make_candidate(unsupported_claims=["claim a"])
        policy = FailurePolicy(require_citations=False)
        results = [evaluate_safety(candidate, policy=policy) for _ in range(5)]
        counts = {len(r.issues) for r in results}
        assert len(counts) == 1

    def test_schema_failure_is_deterministic(self):
        results = [evaluate_safety_from_raw({}) for _ in range(5)]
        statuses = {r.status for r in results}
        assert statuses == {SafetyStatus.BLOCK}


# ── Structural / architecture ──────────────────────────────────────────────────


class TestArchitectureSeparation:
    def test_safety_policy_does_not_import_boto3(self):
        import app.evaluation.safety_policy as sp
        source = importlib.import_module("app.evaluation.safety_policy")
        spec = source.__spec__
        assert spec is not None
        import sys
        mod = sys.modules.get("app.evaluation.safety_policy")
        assert mod is not None
        # Check module's own namespace has no boto3 reference.
        assert "boto3" not in vars(mod)

    def test_safety_policy_does_not_import_botocore(self):
        import sys
        mod = sys.modules.get("app.evaluation.safety_policy")
        assert "botocore" not in vars(mod)

    def test_safety_policy_does_not_import_aws_services(self):
        """safety_policy must not import any app.services module."""
        import app.evaluation.safety_policy as sp_mod
        source_text = open(sp_mod.__file__).read()
        assert "app.services" not in source_text

    def test_safety_policy_does_not_import_retrieval_scorer(self):
        import app.evaluation.safety_policy as sp_mod
        source_text = open(sp_mod.__file__).read()
        assert "from app.evaluation.retrieval_scorer" not in source_text

    def test_safety_policy_does_not_import_citation_scorer(self):
        import app.evaluation.safety_policy as sp_mod
        source_text = open(sp_mod.__file__).read()
        assert "from app.evaluation.citation_scorer" not in source_text

    def test_safety_policy_does_not_import_output_quality_scorer(self):
        import app.evaluation.safety_policy as sp_mod
        source_text = open(sp_mod.__file__).read()
        assert "from app.evaluation.output_quality_scorer" not in source_text

    def test_safety_policy_does_not_import_runner(self):
        import app.evaluation.safety_policy as sp_mod
        source_text = open(sp_mod.__file__).read()
        assert "from app.evaluation.runner" not in source_text
        assert "import runner" not in source_text

    def test_safety_models_does_not_import_bedrock(self):
        import app.schemas.safety_models as sm_mod
        source_text = open(sm_mod.__file__).read()
        assert "boto3" not in source_text
        assert "import boto3" not in source_text
        assert "import bedrock" not in source_text.lower()
        assert "from app.services" not in source_text

    def test_safety_policy_imports_only_output_and_safety_models(self):
        import app.evaluation.safety_policy as sp_mod
        source_text = open(sp_mod.__file__).read()
        assert "from app.schemas.output_models import" in source_text
        assert "from app.schemas.safety_models import" in source_text
