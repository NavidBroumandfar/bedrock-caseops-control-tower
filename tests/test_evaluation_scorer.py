"""
F-2 unit tests — per-case scorer.

Coverage:
  severity_match:
    - exact match scores 1.0 / passed=True
    - mismatch scores 0.0 / passed=False

  category_match:
    - exact match (case-insensitive) scores 1.0 / passed=True
    - mismatch scores 0.0 / passed=False
    - leading/trailing whitespace normalized correctly

  escalation_match:
    - True == True scores 1.0 / passed=True
    - False == False scores 1.0 / passed=True
    - mismatch scores 0.0 / passed=False

  summary_fact_coverage:
    - empty expected_summary_facts → 1.0 / not-applicable
    - all facts present → 1.0
    - partial facts → fractional score
    - no facts present → 0.0
    - case-insensitive matching

  recommendation_keyword_coverage:
    - empty expected keywords → 1.0 / not-applicable
    - all keywords present → 1.0
    - partial keywords → fractional score
    - no keywords present → 0.0
    - case-insensitive matching

  forbidden_claims_check:
    - no forbidden claims configured → 1.0 / passed=True
    - forbidden claim absent → 1.0 / passed=True
    - forbidden claim present in summary → 0.0 / passed=False
    - forbidden claim present in recommendations → 0.0 / passed=False
    - forbidden claim present in escalation_reason → 0.0 / passed=False
    - case-insensitive detection

  overall_score:
    - is the mean of all six dimension scores
    - correct value for all-pass case
    - correct value for mixed case

  pass_fail:
    - True when hard gates pass and overall_score >= threshold
    - False when severity_match fails (hard gate)
    - False when escalation_match fails (hard gate)
    - False when forbidden_claims_check fails (hard gate)
    - False when overall_score < threshold even if hard gates pass
    - custom pass_threshold respected

  ScoringResult.get():
    - returns correct DimensionScore for known metric_name
    - returns None for unknown metric_name

No AWS credentials or live calls required.
"""

import pytest

from app.evaluation.scorer import (
    PASS_THRESHOLD,
    DIM_CATEGORY,
    DIM_ESCALATION,
    DIM_FORBIDDEN,
    DIM_KEYWORD_COVERAGE,
    DIM_SEVERITY,
    DIM_SUMMARY_FACTS,
    score_case,
)
from app.schemas.evaluation_models import ExpectedOutput
from app.schemas.output_models import CaseOutput


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_candidate(
    severity="High",
    category="Regulatory",
    summary="The facility had quality issues including CAPA deficiencies.",
    recommendations=None,
    escalation_required=True,
    escalation_reason="Severity is High.",
) -> CaseOutput:
    if recommendations is None:
        recommendations = ["Initiate CAPA corrective action per FDA compliance guidance."]
    return CaseOutput(
        document_id="doc-test-001",
        source_filename="test.md",
        source_type="FDA",
        severity=severity,
        category=category,
        summary=summary,
        recommendations=recommendations,
        citations=[],
        confidence_score=0.85,
        unsupported_claims=[],
        escalation_required=escalation_required,
        escalation_reason=escalation_reason,
        validated_by="validation-agent-v1",
        timestamp="2025-01-01T00:00:00+00:00",
    )


def _make_expected(
    expected_severity="High",
    expected_category="Regulatory",
    expected_escalation_required=True,
    expected_summary_facts=None,
    expected_recommendation_keywords=None,
    forbidden_claims=None,
) -> ExpectedOutput:
    return ExpectedOutput(
        case_id="eval-test-001",
        expected_severity=expected_severity,
        expected_category=expected_category,
        expected_escalation_required=expected_escalation_required,
        expected_summary_facts=expected_summary_facts or [],
        expected_recommendation_keywords=expected_recommendation_keywords or [],
        forbidden_claims=forbidden_claims or [],
    )


# ── severity_match ─────────────────────────────────────────────────────────────


class TestSeverityMatch:
    def test_exact_match_passes(self):
        result = score_case(_make_candidate(severity="High"), _make_expected(expected_severity="High"))
        ds = result.get(DIM_SEVERITY)
        assert ds is not None
        assert ds.score == 1.0
        assert ds.passed is True

    def test_mismatch_fails(self):
        result = score_case(_make_candidate(severity="Medium"), _make_expected(expected_severity="High"))
        ds = result.get(DIM_SEVERITY)
        assert ds.score == 0.0
        assert ds.passed is False

    def test_critical_vs_critical(self):
        result = score_case(_make_candidate(severity="Critical"), _make_expected(expected_severity="Critical"))
        assert result.get(DIM_SEVERITY).passed is True

    def test_low_vs_high_mismatch(self):
        result = score_case(_make_candidate(severity="Low"), _make_expected(expected_severity="High"))
        assert result.get(DIM_SEVERITY).passed is False


# ── category_match ─────────────────────────────────────────────────────────────


class TestCategoryMatch:
    def test_exact_match_passes(self):
        result = score_case(_make_candidate(category="Regulatory"), _make_expected(expected_category="Regulatory"))
        assert result.get(DIM_CATEGORY).passed is True

    def test_case_insensitive_match_passes(self):
        result = score_case(_make_candidate(category="regulatory"), _make_expected(expected_category="Regulatory"))
        assert result.get(DIM_CATEGORY).passed is True

    def test_whitespace_normalized(self):
        result = score_case(_make_candidate(category="  Regulatory  "), _make_expected(expected_category="Regulatory"))
        assert result.get(DIM_CATEGORY).passed is True

    def test_mismatch_fails(self):
        result = score_case(_make_candidate(category="Security"), _make_expected(expected_category="Regulatory"))
        ds = result.get(DIM_CATEGORY)
        assert ds.score == 0.0
        assert ds.passed is False


# ── escalation_match ───────────────────────────────────────────────────────────


class TestEscalationMatch:
    def test_true_true_passes(self):
        result = score_case(
            _make_candidate(escalation_required=True),
            _make_expected(expected_escalation_required=True),
        )
        assert result.get(DIM_ESCALATION).passed is True

    def test_false_false_passes(self):
        result = score_case(
            _make_candidate(escalation_required=False, escalation_reason=None),
            _make_expected(expected_escalation_required=False),
        )
        assert result.get(DIM_ESCALATION).passed is True

    def test_true_false_fails(self):
        result = score_case(
            _make_candidate(escalation_required=True),
            _make_expected(expected_escalation_required=False),
        )
        ds = result.get(DIM_ESCALATION)
        assert ds.score == 0.0
        assert ds.passed is False

    def test_false_true_fails(self):
        result = score_case(
            _make_candidate(escalation_required=False, escalation_reason=None),
            _make_expected(expected_escalation_required=True),
        )
        assert result.get(DIM_ESCALATION).passed is False


# ── summary_fact_coverage ──────────────────────────────────────────────────────


class TestSummaryFactCoverage:
    def test_empty_facts_not_applicable(self):
        result = score_case(_make_candidate(), _make_expected(expected_summary_facts=[]))
        ds = result.get(DIM_SUMMARY_FACTS)
        assert ds.score == 1.0
        assert ds.passed is True
        assert "not applicable" in ds.rationale

    def test_all_facts_present_scores_1(self):
        candidate = _make_candidate(
            summary="The facility had quality issues including CAPA deficiencies and corrective actions."
        )
        expected = _make_expected(expected_summary_facts=["quality", "CAPA", "corrective"])
        result = score_case(candidate, expected)
        ds = result.get(DIM_SUMMARY_FACTS)
        assert ds.score == 1.0
        assert ds.passed is True

    def test_partial_facts_fractional_score(self):
        candidate = _make_candidate(summary="The facility had quality issues.")
        expected = _make_expected(expected_summary_facts=["quality", "CAPA", "corrective"])
        result = score_case(candidate, expected)
        ds = result.get(DIM_SUMMARY_FACTS)
        # Only "quality" matches → 1/3
        assert abs(ds.score - 1 / 3) < 1e-6
        assert ds.passed is False

    def test_no_facts_present_scores_0(self):
        candidate = _make_candidate(summary="A routine administrative notice.")
        expected = _make_expected(expected_summary_facts=["CAPA", "corrective", "quality"])
        result = score_case(candidate, expected)
        assert result.get(DIM_SUMMARY_FACTS).score == 0.0

    def test_case_insensitive_matching(self):
        candidate = _make_candidate(summary="CAPA procedures are required.")
        expected = _make_expected(expected_summary_facts=["capa"])
        result = score_case(candidate, expected)
        assert result.get(DIM_SUMMARY_FACTS).score == 1.0


# ── recommendation_keyword_coverage ───────────────────────────────────────────


class TestRecommendationKeywordCoverage:
    def test_empty_keywords_not_applicable(self):
        result = score_case(_make_candidate(), _make_expected(expected_recommendation_keywords=[]))
        ds = result.get(DIM_KEYWORD_COVERAGE)
        assert ds.score == 1.0
        assert "not applicable" in ds.rationale

    def test_all_keywords_present_scores_1(self):
        candidate = _make_candidate(
            recommendations=["Initiate CAPA corrective action per FDA compliance guidance."]
        )
        expected = _make_expected(expected_recommendation_keywords=["CAPA", "corrective", "FDA"])
        result = score_case(candidate, expected)
        assert result.get(DIM_KEYWORD_COVERAGE).score == 1.0

    def test_partial_keywords_fractional_score(self):
        candidate = _make_candidate(
            recommendations=["Review documentation and escalate."]
        )
        expected = _make_expected(expected_recommendation_keywords=["CAPA", "corrective", "escalate"])
        result = score_case(candidate, expected)
        ds = result.get(DIM_KEYWORD_COVERAGE)
        # Only "escalate" matches → 1/3
        assert abs(ds.score - 1 / 3) < 1e-6

    def test_no_keywords_present_scores_0(self):
        candidate = _make_candidate(recommendations=["File for reference."])
        expected = _make_expected(expected_recommendation_keywords=["CAPA", "corrective", "escalate"])
        result = score_case(candidate, expected)
        assert result.get(DIM_KEYWORD_COVERAGE).score == 0.0

    def test_case_insensitive_matching(self):
        candidate = _make_candidate(recommendations=["Apply capa protocols immediately."])
        expected = _make_expected(expected_recommendation_keywords=["CAPA"])
        result = score_case(candidate, expected)
        assert result.get(DIM_KEYWORD_COVERAGE).score == 1.0

    def test_keyword_matched_across_multiple_recommendations(self):
        candidate = _make_candidate(
            recommendations=["Escalate to legal.", "Apply corrective CAPA measures."]
        )
        expected = _make_expected(expected_recommendation_keywords=["escalate", "CAPA"])
        result = score_case(candidate, expected)
        assert result.get(DIM_KEYWORD_COVERAGE).score == 1.0


# ── forbidden_claims_check ─────────────────────────────────────────────────────


class TestForbiddenClaimsCheck:
    def test_no_forbidden_configured_passes(self):
        result = score_case(_make_candidate(), _make_expected(forbidden_claims=[]))
        ds = result.get(DIM_FORBIDDEN)
        assert ds.score == 1.0
        assert ds.passed is True

    def test_forbidden_absent_passes(self):
        result = score_case(
            _make_candidate(summary="CAPA deficiencies were found."),
            _make_expected(forbidden_claims=["no action required", "fully compliant"]),
        )
        ds = result.get(DIM_FORBIDDEN)
        assert ds.score == 1.0
        assert ds.passed is True

    def test_forbidden_in_summary_fails(self):
        candidate = _make_candidate(
            summary="The facility is fully compliant with all requirements."
        )
        result = score_case(candidate, _make_expected(forbidden_claims=["fully compliant"]))
        ds = result.get(DIM_FORBIDDEN)
        assert ds.score == 0.0
        assert ds.passed is False

    def test_forbidden_in_recommendations_fails(self):
        candidate = _make_candidate(
            recommendations=["No action required at this time."]
        )
        result = score_case(candidate, _make_expected(forbidden_claims=["no action required"]))
        assert result.get(DIM_FORBIDDEN).passed is False

    def test_forbidden_in_escalation_reason_fails(self):
        candidate = _make_candidate(
            escalation_reason="No deficiencies were found in this review."
        )
        result = score_case(candidate, _make_expected(forbidden_claims=["no deficiencies"]))
        assert result.get(DIM_FORBIDDEN).passed is False

    def test_case_insensitive_detection(self):
        candidate = _make_candidate(summary="Facility is FULLY COMPLIANT with all regulations.")
        result = score_case(candidate, _make_expected(forbidden_claims=["fully compliant"]))
        assert result.get(DIM_FORBIDDEN).passed is False


# ── overall_score ─────────────────────────────────────────────────────────────


class TestOverallScore:
    def test_all_pass_overall_is_1(self):
        candidate = _make_candidate(
            severity="High",
            category="Regulatory",
            escalation_required=True,
            summary="quality CAPA corrective adulterated misbranded",
            recommendations=["CAPA corrective FDA compliance escalate"],
        )
        expected = _make_expected(
            expected_severity="High",
            expected_category="Regulatory",
            expected_escalation_required=True,
            expected_summary_facts=["quality", "CAPA"],
            expected_recommendation_keywords=["CAPA", "corrective"],
            forbidden_claims=["no action required"],
        )
        result = score_case(candidate, expected)
        assert result.overall_score == 1.0

    def test_all_fail_overall_is_0(self):
        candidate = _make_candidate(
            severity="Low",
            category="Security",
            escalation_required=False,
            escalation_reason=None,
            summary="no action required",
            recommendations=["no action required"],
        )
        expected = _make_expected(
            expected_severity="High",
            expected_category="Regulatory",
            expected_escalation_required=True,
            expected_summary_facts=["CAPA"],
            expected_recommendation_keywords=["corrective"],
            forbidden_claims=["no action required"],
        )
        result = score_case(candidate, expected)
        assert result.overall_score == 0.0

    def test_partial_score_is_mean(self):
        # 3 out of 6 dimensions should score 1.0, rest 0.0 → overall = 0.5
        candidate = _make_candidate(
            severity="High",       # match → 1.0
            category="Security",   # mismatch → 0.0
            escalation_required=True,  # match → 1.0
            summary="unrelated summary text",  # no facts → 0.0
            recommendations=["No action required."],  # no keywords → 0.0
        )
        expected = _make_expected(
            expected_severity="High",
            expected_category="Regulatory",
            expected_escalation_required=True,
            expected_summary_facts=["CAPA"],
            expected_recommendation_keywords=["corrective"],
            forbidden_claims=["not-present-claim"],  # absent → 1.0
        )
        result = score_case(candidate, expected)
        # severity=1, category=0, escalation=1, facts=0, keywords=0, forbidden=1 → 3/6 = 0.5
        assert abs(result.overall_score - 0.5) < 1e-6


# ── pass_fail ─────────────────────────────────────────────────────────────────


class TestPassFail:
    def _perfect_candidate(self):
        return _make_candidate(
            severity="High",
            category="Regulatory",
            escalation_required=True,
            summary="quality CAPA corrective",
            recommendations=["CAPA corrective FDA compliance escalate regulatory"],
        )

    def _perfect_expected(self):
        return _make_expected(
            expected_severity="High",
            expected_category="Regulatory",
            expected_escalation_required=True,
            expected_summary_facts=["quality", "CAPA"],
            expected_recommendation_keywords=["CAPA"],
            forbidden_claims=["no action required"],
        )

    def test_all_pass_returns_true(self):
        result = score_case(self._perfect_candidate(), self._perfect_expected())
        assert result.pass_fail is True

    def test_severity_mismatch_fails(self):
        candidate = _make_candidate(severity="Low")
        result = score_case(candidate, self._perfect_expected())
        assert result.pass_fail is False

    def test_escalation_mismatch_fails(self):
        candidate = _make_candidate(escalation_required=False, escalation_reason=None)
        result = score_case(candidate, self._perfect_expected())
        assert result.pass_fail is False

    def test_forbidden_claim_fails(self):
        candidate = _make_candidate(summary="no action required at this time")
        result = score_case(candidate, self._perfect_expected())
        assert result.pass_fail is False

    def test_low_overall_score_fails_even_with_hard_gates(self):
        # Hard gates pass but overall_score will be low due to missing facts/keywords.
        candidate = _make_candidate(
            severity="High",
            category="Security",       # mismatch — category is not a hard gate
            escalation_required=True,
            summary="unrelated text",  # no facts
            recommendations=["nothing useful"],  # no keywords
        )
        expected = _make_expected(
            expected_severity="High",
            expected_category="Regulatory",
            expected_escalation_required=True,
            expected_summary_facts=["CAPA", "corrective", "quality"],
            expected_recommendation_keywords=["CAPA", "corrective", "FDA", "escalate"],
            forbidden_claims=[],
        )
        result = score_case(candidate, expected, pass_threshold=0.75)
        # severity=1, category=0, escalation=1, facts=0, keywords=0, forbidden=1 → 3/6=0.5 < 0.75
        assert result.pass_fail is False

    def test_custom_threshold_zero_always_passes_hard_gates(self):
        candidate = _make_candidate(severity="High", category="Security", escalation_required=True)
        expected = _make_expected(
            expected_severity="High",
            expected_escalation_required=True,
            expected_summary_facts=["CAPA"],
        )
        result = score_case(candidate, expected, pass_threshold=0.0)
        # Hard gates all pass; threshold=0 so overall_score always qualifies.
        assert result.pass_fail is True

    def test_pass_threshold_stored_in_result(self):
        result = score_case(_make_candidate(), _make_expected(), pass_threshold=0.9)
        assert result.pass_threshold == 0.9


# ── ScoringResult.get() ───────────────────────────────────────────────────────


class TestScoringResultGet:
    def test_get_known_metric(self):
        result = score_case(_make_candidate(), _make_expected())
        ds = result.get(DIM_SEVERITY)
        assert ds is not None
        assert ds.metric_name == DIM_SEVERITY

    def test_get_unknown_metric_returns_none(self):
        result = score_case(_make_candidate(), _make_expected())
        assert result.get("nonexistent_metric") is None
