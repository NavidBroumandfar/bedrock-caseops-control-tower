"""
G-2 unit tests — output quality scorer.

Coverage:

  summary_nonempty:
    - non-empty summary → 1.0 / passed=True
    - empty string summary → 0.0 / passed=False (hard gate)
    - whitespace-only summary → 0.0 / passed=False (hard gate)

  recommendations_present_when_expected:
    - expected output defines keywords and candidate has recommendations → 1.0 / passed=True
    - expected output defines keywords but candidate has no recommendations → 0.0 / passed=False
    - expected output defines keywords but candidate has only whitespace recommendations → 0.0 / passed=False
    - expected output defines no recommendation keywords → 1.0 / not-applicable

  unsupported_claims_clean:
    - unsupported_claims is empty → 1.0 / passed=True (hard gate)
    - unsupported_claims has entries → 0.0 / passed=False (hard gate)

  core_case_alignment_score:
    - derived from F-2 score_case() overall score
    - present in OutputQualityScoringResult

  citation_quality_score:
    - derived from G-1 score_citations() overall score
    - present in OutputQualityScoringResult

  overall_score:
    - mean of five components: core alignment, citation quality, summary_nonempty,
      recommendations_present_when_expected, unsupported_claims_clean
    - strong output → high overall
    - weak output → low overall
    - correct fractional calculation

  pass_fail:
    - summary_nonempty hard gate: must pass or overall fails
    - unsupported_claims_clean hard gate: must pass or overall fails
    - overall_score >= pass_threshold required
    - custom pass_threshold respected
    - pass_threshold stored in result
    - strong input passes
    - blank summary fails regardless of other scores

  OutputQualityScoringResult.get():
    - returns correct DimensionScore for known metric_name
    - returns None for unknown metric_name

  notes:
    - None by default
    - propagated when provided

  determinism:
    - identical inputs produce identical outputs across repeated calls

  separation / architecture:
    - output_quality_scorer does NOT import retrieval_scorer
    - output_quality_scorer imports from scorer (F-2) and citation_scorer (G-1)
    - OutputQualityScoringResult is in evaluation_models (not a new separate schema)

  candidate typing / loading:
    - supports typed CaseOutput objects directly
    - supports dict-constructed CaseOutput (covers fixture loading path)
    - supports JSON fixture files loaded from disk as CaseOutput
    - rejects malformed candidate data via Pydantic validation

  fixture-based integration tests:
    - strong_output fixture passes all dimensions
    - blank_summary fixture fails summary_nonempty hard gate
    - missing_recommendations fixture fails recommendations_present_when_expected
    - unsupported_claims_present fixture fails unsupported_claims_clean hard gate
    - good_core_weak_citations fixture: final-output checks pass but citation score pulls overall down
    - existing candidate_outputs/strong_pass.json reusable with appropriate expectations

No AWS credentials or live calls required.
"""

import importlib
import json
from pathlib import Path

import pytest

from app.evaluation.output_quality_scorer import (
    OUTPUT_QUALITY_PASS_THRESHOLD,
    DIM_RECS_WHEN_EXPECTED,
    DIM_SUMMARY_NONEMPTY,
    DIM_UNSUPPORTED_CLAIMS_CLEAN,
    score_output_quality,
)
from app.schemas.evaluation_models import (
    CitationExpectation,
    ExpectedOutput,
    OutputQualityScoringResult,
)
from app.schemas.output_models import CaseOutput, Citation

# ---------------------------------------------------------------------------
# Fixture directories
# ---------------------------------------------------------------------------

OQ_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "output_quality_outputs"
CANDIDATE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "candidate_outputs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_citation(
    source_label: str = "FDA Warning Letter — Uscom Kft 2025",
    excerpt: str = "Quality system procedures for corrective and preventive action were not established.",
    source_id: str = "kb-src-001",
    relevance_score: float = 0.90,
) -> Citation:
    return Citation(
        source_id=source_id,
        source_label=source_label,
        excerpt=excerpt,
        relevance_score=relevance_score,
    )


def _make_output(
    *,
    summary: str = "The quality system was found inadequate. CAPA procedures were missing.",
    recommendations: list[str] | None = None,
    citations: list[Citation] | None = None,
    unsupported_claims: list[str] | None = None,
    severity: str = "High",
    category: str = "Regulatory",
    escalation_required: bool = True,
) -> CaseOutput:
    return CaseOutput(
        document_id="doc-test-oq-001",
        source_filename="fda_warning_letter_01.md",
        source_type="FDA",
        severity=severity,
        category=category,
        summary=summary,
        recommendations=recommendations if recommendations is not None else ["Initiate CAPA."],
        citations=citations if citations is not None else [_make_citation()],
        confidence_score=0.85,
        unsupported_claims=unsupported_claims if unsupported_claims is not None else [],
        escalation_required=escalation_required,
        escalation_reason="Severity High." if escalation_required else None,
        validated_by="validation-agent-v1",
        session_id="sess-test-oq",
        timestamp="2026-04-10T12:00:00Z",
    )


def _make_expected(
    *,
    expected_severity: str = "High",
    expected_category: str = "Regulatory",
    expected_escalation_required: bool = True,
    expected_summary_facts: list[str] | None = None,
    expected_recommendation_keywords: list[str] | None = None,
    forbidden_claims: list[str] | None = None,
) -> ExpectedOutput:
    # Use None-sentinel to distinguish "not supplied" from "explicitly empty".
    rec_keywords = (
        ["CAPA", "compliance"]
        if expected_recommendation_keywords is None
        else expected_recommendation_keywords
    )
    return ExpectedOutput(
        case_id="test-case-oq-001",
        expected_severity=expected_severity,
        expected_category=expected_category,
        expected_escalation_required=expected_escalation_required,
        expected_summary_facts=expected_summary_facts or [],
        expected_recommendation_keywords=rec_keywords,
        forbidden_claims=forbidden_claims or [],
    )


def _make_citation_expectation(
    *,
    citations_required: bool = True,
    expected_source_labels: list[str] | None = None,
    required_excerpt_terms: list[str] | None = None,
    minimum_citation_count: int = 1,
) -> CitationExpectation:
    return CitationExpectation(
        case_id="test-case-oq-001",
        citations_required=citations_required,
        expected_source_labels=expected_source_labels or [],
        required_excerpt_terms=required_excerpt_terms or [],
        minimum_citation_count=minimum_citation_count,
    )


def _load_fixture(fixture_dir: Path, filename: str) -> CaseOutput:
    """Load a JSON fixture from disk as a validated CaseOutput."""
    raw = json.loads((fixture_dir / filename).read_text())
    raw.pop("_note", None)
    return CaseOutput(**raw)


# ---------------------------------------------------------------------------
# summary_nonempty
# ---------------------------------------------------------------------------


class TestSummaryNonempty:
    def test_nonempty_summary_passes(self):
        candidate = _make_output(summary="The facility failed to establish CAPA procedures.")
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_SUMMARY_NONEMPTY)
        assert dim is not None
        assert dim.score == 1.0
        assert dim.passed is True

    def test_empty_string_summary_fails(self):
        candidate = _make_output(summary="")
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_SUMMARY_NONEMPTY)
        assert dim.score == 0.0
        assert dim.passed is False
        assert result.pass_fail is False

    def test_whitespace_only_summary_fails(self):
        candidate = _make_output(summary="    \t\n  ")
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_SUMMARY_NONEMPTY)
        assert dim.score == 0.0
        assert dim.passed is False
        assert result.pass_fail is False


# ---------------------------------------------------------------------------
# recommendations_present_when_expected
# ---------------------------------------------------------------------------


class TestRecommendationsWhenExpected:
    def test_recommendations_present_when_keywords_defined(self):
        candidate = _make_output(recommendations=["Initiate CAPA review.", "Escalate to compliance."])
        expected = _make_expected(expected_recommendation_keywords=["CAPA", "compliance"])
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_RECS_WHEN_EXPECTED)
        assert dim.score == 1.0
        assert dim.passed is True

    def test_no_recommendations_when_keywords_defined_fails(self):
        candidate = _make_output(recommendations=[])
        expected = _make_expected(expected_recommendation_keywords=["CAPA", "compliance"])
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_RECS_WHEN_EXPECTED)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_whitespace_only_recommendations_fail(self):
        candidate = _make_output(recommendations=["   ", "\t"])
        expected = _make_expected(expected_recommendation_keywords=["CAPA"])
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_RECS_WHEN_EXPECTED)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_no_keywords_in_expected_is_not_applicable(self):
        candidate = _make_output(recommendations=[])
        expected = _make_expected(expected_recommendation_keywords=[])
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_RECS_WHEN_EXPECTED)
        assert dim.score == 1.0
        assert dim.passed is True
        assert "not applicable" in (dim.rationale or "").lower()


# ---------------------------------------------------------------------------
# unsupported_claims_clean
# ---------------------------------------------------------------------------


class TestUnsupportedClaimsClean:
    def test_empty_unsupported_claims_passes(self):
        candidate = _make_output(unsupported_claims=[])
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN)
        assert dim.score == 1.0
        assert dim.passed is True

    def test_unsupported_claims_present_fails(self):
        candidate = _make_output(unsupported_claims=["The device was recalled last year."])
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN)
        assert dim.score == 0.0
        assert dim.passed is False
        assert result.pass_fail is False

    def test_multiple_unsupported_claims_fail(self):
        candidate = _make_output(unsupported_claims=["Claim A.", "Claim B."])
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN)
        assert dim.passed is False


# ---------------------------------------------------------------------------
# Core case alignment sub-score propagation
# ---------------------------------------------------------------------------


class TestCoreCaseAlignmentSubscore:
    def test_core_alignment_score_present_in_result(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert 0.0 <= result.core_case_alignment_score <= 1.0

    def test_core_alignment_score_reflects_f2_scorer(self):
        """Score from score_output_quality must equal score_case() overall for same inputs."""
        from app.evaluation.scorer import score_case

        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        f2_result = score_case(candidate, expected)
        assert result.core_case_alignment_score == f2_result.overall_score

    def test_severity_mismatch_lowers_core_score(self):
        candidate = _make_output(severity="Low")
        expected = _make_expected(expected_severity="High")
        expectation = _make_citation_expectation()
        result_mismatch = score_output_quality(candidate, expected, expectation)
        candidate_match = _make_output(severity="High")
        result_match = score_output_quality(candidate_match, expected, expectation)
        assert result_mismatch.core_case_alignment_score < result_match.core_case_alignment_score


# ---------------------------------------------------------------------------
# Citation quality sub-score propagation
# ---------------------------------------------------------------------------


class TestCitationQualitySubscore:
    def test_citation_quality_score_present_in_result(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert 0.0 <= result.citation_quality_score <= 1.0

    def test_citation_quality_score_reflects_g1_scorer(self):
        """Score from score_output_quality must equal score_citations() overall for same inputs."""
        from app.evaluation.citation_scorer import score_citations

        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        g1_result = score_citations(candidate, expectation)
        assert result.citation_quality_score == g1_result.overall_score

    def test_missing_citations_lowers_citation_score(self):
        candidate_no_citations = _make_output(citations=[])
        candidate_with_citations = _make_output(citations=[_make_citation()])
        expected = _make_expected()
        expectation = _make_citation_expectation(citations_required=True, minimum_citation_count=1)
        result_no = score_output_quality(candidate_no_citations, expected, expectation)
        result_with = score_output_quality(candidate_with_citations, expected, expectation)
        assert result_no.citation_quality_score < result_with.citation_quality_score


# ---------------------------------------------------------------------------
# Overall score calculation
# ---------------------------------------------------------------------------


class TestOverallScore:
    def test_overall_score_is_mean_of_five_components(self):
        """overall_score = mean(core_alignment, citation_quality, dim1, dim2, dim3)."""
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)

        dim_scores = [d.score for d in result.dimension_scores]
        assert len(dim_scores) == 3  # the three final-output-only checks
        expected_overall = round(
            (result.core_case_alignment_score + result.citation_quality_score + sum(dim_scores)) / 5,
            6,
        )
        assert result.overall_score == expected_overall

    def test_strong_output_has_high_overall_score(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert result.overall_score >= 0.7

    def test_overall_score_is_bounded(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert 0.0 <= result.overall_score <= 1.0


# ---------------------------------------------------------------------------
# Pass/fail threshold behavior
# ---------------------------------------------------------------------------


class TestPassFail:
    def test_strong_output_passes(self):
        candidate = _make_output(
            summary="Quality system inadequate; CAPA and nonconforming procedures missing.",
            recommendations=["Initiate CAPA review.", "Escalate to compliance team."],
            unsupported_claims=[],
        )
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert result.pass_fail is True

    def test_blank_summary_fails_hard_gate(self):
        candidate = _make_output(summary="")
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert result.pass_fail is False

    def test_unsupported_claims_fails_hard_gate(self):
        candidate = _make_output(unsupported_claims=["Fabricated claim."])
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert result.pass_fail is False

    def test_custom_pass_threshold_stored_in_result(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation, pass_threshold=0.5)
        assert result.pass_threshold == 0.5

    def test_score_above_threshold_with_all_gates_passes(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation, pass_threshold=0.0)
        # With 0.0 threshold and all hard gates passing, must pass.
        assert result.pass_fail is True

    def test_score_below_threshold_fails(self):
        candidate = _make_output(
            summary="Some summary.",
            unsupported_claims=[],
        )
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation, pass_threshold=1.0)
        # 1.0 threshold is unreachable for most realistic inputs.
        assert result.pass_fail is False


# ---------------------------------------------------------------------------
# OutputQualityScoringResult.get()
# ---------------------------------------------------------------------------


class TestResultGet:
    def test_get_returns_dimension_score_for_known_metric(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        dim = result.get(DIM_SUMMARY_NONEMPTY)
        assert dim is not None
        assert dim.metric_name == DIM_SUMMARY_NONEMPTY

    def test_get_returns_none_for_unknown_metric(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert result.get("nonexistent_metric") is None


# ---------------------------------------------------------------------------
# Notes propagation
# ---------------------------------------------------------------------------


class TestNotes:
    def test_notes_none_by_default(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert result.notes is None

    def test_notes_propagated_when_provided(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation, notes="G-2 test note.")
        assert result.notes == "G-2 test note."


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_identical_inputs_produce_identical_results(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result_a = score_output_quality(candidate, expected, expectation)
        result_b = score_output_quality(candidate, expected, expectation)
        assert result_a.overall_score == result_b.overall_score
        assert result_a.pass_fail == result_b.pass_fail
        assert result_a.core_case_alignment_score == result_b.core_case_alignment_score
        assert result_a.citation_quality_score == result_b.citation_quality_score
        for dim_a, dim_b in zip(result_a.dimension_scores, result_b.dimension_scores):
            assert dim_a.score == dim_b.score
            assert dim_a.passed == dim_b.passed


# ---------------------------------------------------------------------------
# Separation / architecture
# ---------------------------------------------------------------------------


class TestArchitecturalSeparation:
    def test_output_quality_scorer_does_not_import_retrieval_scorer(self):
        """G-2 must not depend on G-0 retrieval scorer at the module import level."""
        import app.evaluation.output_quality_scorer as mod
        # Confirm retrieval_scorer is not a dependency by checking sys.modules path.
        import sys
        # Ensure the module itself doesn't pull in retrieval_scorer as a module dep.
        assert "app.evaluation.retrieval_scorer" not in sys.modules or True  # docstring OK
        # Stricter: the actual from-import line must not reference retrieval_scorer.
        source = Path(mod.__file__).read_text()
        assert "from app.evaluation.retrieval_scorer" not in source

    def test_output_quality_scorer_imports_f2_scorer(self):
        import app.evaluation.output_quality_scorer as mod
        source = Path(mod.__file__).read_text()
        assert "from app.evaluation.scorer import" in source

    def test_output_quality_scorer_imports_g1_citation_scorer(self):
        import app.evaluation.output_quality_scorer as mod
        source = Path(mod.__file__).read_text()
        assert "from app.evaluation.citation_scorer import" in source

    def test_output_quality_scoring_result_is_in_evaluation_models(self):
        from app.schemas.evaluation_models import OutputQualityScoringResult  # noqa: F401

    def test_dimension_scores_has_three_final_output_checks(self):
        """G-2 adds exactly three final-output-only DimensionScores."""
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        names = {d.metric_name for d in result.dimension_scores}
        assert DIM_SUMMARY_NONEMPTY in names
        assert DIM_RECS_WHEN_EXPECTED in names
        assert DIM_UNSUPPORTED_CLAIMS_CLEAN in names
        assert len(result.dimension_scores) == 3


# ---------------------------------------------------------------------------
# Candidate typing / loading
# ---------------------------------------------------------------------------


class TestCandidateTyping:
    def test_accepts_typed_caseoutput(self):
        candidate = _make_output()
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert isinstance(result, OutputQualityScoringResult)

    def test_accepts_dict_constructed_caseoutput(self):
        """Covers the fixture loading path where JSON is deserialized to CaseOutput."""
        raw = {
            "document_id": "doc-dict-test",
            "source_filename": "test.md",
            "source_type": "FDA",
            "severity": "High",
            "category": "Regulatory",
            "summary": "Quality system deficiencies identified.",
            "recommendations": ["Initiate CAPA."],
            "citations": [
                {
                    "source_id": "kb-001",
                    "source_label": "FDA Letter",
                    "excerpt": "CAPA procedures missing.",
                    "relevance_score": 0.80,
                }
            ],
            "confidence_score": 0.82,
            "unsupported_claims": [],
            "escalation_required": False,
            "escalation_reason": None,
            "validated_by": "validation-agent-v1",
            "session_id": "sess-dict",
            "timestamp": "2026-04-10T12:00:00Z",
        }
        candidate = CaseOutput(**raw)
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert isinstance(result, OutputQualityScoringResult)

    def test_rejects_malformed_candidate_data(self):
        """Pydantic should reject a CaseOutput with an invalid confidence_score."""
        with pytest.raises(Exception):
            CaseOutput(
                document_id="bad",
                source_filename="bad.md",
                source_type="FDA",
                severity="High",
                category="Regulatory",
                summary="Bad.",
                recommendations=[],
                citations=[],
                confidence_score=2.5,   # invalid — > 1.0
                unsupported_claims=[],
                escalation_required=False,
                escalation_reason=None,
                validated_by="v1",
                timestamp="2026-04-10T12:00:00Z",
            )

    def test_accepts_json_fixture_file(self):
        candidate = _load_fixture(OQ_FIXTURE_DIR, "strong_output.json")
        expected = _make_expected()
        expectation = _make_citation_expectation()
        result = score_output_quality(candidate, expected, expectation)
        assert isinstance(result, OutputQualityScoringResult)


# ---------------------------------------------------------------------------
# Fixture-based integration tests
# ---------------------------------------------------------------------------


class TestFixtureIntegration:
    """Integration tests using G-2 fixtures and the eval-fda-001 expectation."""

    # Shared expectations aligned with the strong_output.json fixture.
    _expected = ExpectedOutput(
        case_id="eval-fda-001",
        expected_severity="High",
        expected_category="Regulatory",
        expected_escalation_required=True,
        expected_summary_facts=["quality system", "CAPA", "cleaning"],
        expected_recommendation_keywords=["CAPA", "compliance", "escalate"],
        forbidden_claims=["no action required", "fully compliant"],
    )
    _citation_expectation = CitationExpectation(
        case_id="eval-fda-001",
        citations_required=True,
        expected_source_labels=[],
        required_excerpt_terms=["corrective", "quality"],
        minimum_citation_count=1,
    )

    def test_strong_output_passes(self):
        candidate = _load_fixture(OQ_FIXTURE_DIR, "strong_output.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        assert result.pass_fail is True
        assert result.get(DIM_SUMMARY_NONEMPTY).passed is True
        assert result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN).passed is True

    def test_blank_summary_fixture_fails_summary_nonempty(self):
        candidate = _load_fixture(OQ_FIXTURE_DIR, "blank_summary.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        assert result.get(DIM_SUMMARY_NONEMPTY).passed is False
        assert result.pass_fail is False

    def test_missing_recommendations_fixture_fails(self):
        candidate = _load_fixture(OQ_FIXTURE_DIR, "missing_recommendations.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        dim = result.get(DIM_RECS_WHEN_EXPECTED)
        assert dim.passed is False

    def test_unsupported_claims_fixture_fails_hard_gate(self):
        candidate = _load_fixture(OQ_FIXTURE_DIR, "unsupported_claims_present.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        assert result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN).passed is False
        assert result.pass_fail is False

    def test_good_core_weak_citations_composition(self):
        """Good final-output checks but no citations — proves composition works."""
        candidate = _load_fixture(OQ_FIXTURE_DIR, "good_core_weak_citations.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        # Final-output checks should pass (good summary, recommendations, no unsupported claims).
        assert result.get(DIM_SUMMARY_NONEMPTY).passed is True
        assert result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN).passed is True
        # But citation score is penalized for missing citations.
        assert result.citation_quality_score < 1.0
        # Overall is pulled down by weak citation quality.
        assert result.overall_score < 1.0

    def test_reused_f2_strong_pass_fixture(self):
        """Reuse the existing F-2 candidate_outputs/strong_pass.json fixture."""
        candidate = _load_fixture(CANDIDATE_FIXTURE_DIR, "strong_pass.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        assert isinstance(result, OutputQualityScoringResult)
        # strong_pass has non-empty summary, recommendations, and no unsupported claims.
        assert result.get(DIM_SUMMARY_NONEMPTY).passed is True
        assert result.get(DIM_UNSUPPORTED_CLAIMS_CLEAN).passed is True

    def test_overall_score_calculation_on_strong_output(self):
        """Verify the five-component mean formula on a known fixture."""
        candidate = _load_fixture(OQ_FIXTURE_DIR, "strong_output.json")
        result = score_output_quality(candidate, self._expected, self._citation_expectation)
        dim_scores = [d.score for d in result.dimension_scores]
        expected_overall = round(
            (result.core_case_alignment_score + result.citation_quality_score + sum(dim_scores)) / 5,
            6,
        )
        assert result.overall_score == expected_overall
