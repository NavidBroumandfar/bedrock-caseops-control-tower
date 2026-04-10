"""
G-1 unit tests — citation quality scorer.

Coverage:
  citation_presence:
    - citations required and present (>= minimum) → 1.0 / passed=True
    - citations required and exactly minimum → 1.0 / passed=True
    - citations required but absent (0 citations) → 0.0 / passed=False
    - citations required but count below minimum → 0.0 / passed=False
    - citations not required → 1.0 / not-applicable

  citation_source_label_alignment:
    - no expected source labels defined → 1.0 / not-applicable
    - all expected source labels present (case-insensitive) → 1.0 / passed=True
    - partial source labels present → fractional score
    - no expected source labels matched → 0.0 / passed=False
    - case-insensitive label matching
    - whitespace-normalized label matching

  citation_excerpt_evidence_coverage:
    - no required excerpt terms defined → 1.0 / not-applicable
    - all terms found in excerpts → 1.0 / passed=True
    - partial terms found → fractional score
    - no terms found → 0.0 / passed=False
    - case-insensitive term matching
    - term matched across multiple excerpt fields

  citation_excerpt_nonempty:
    - all excerpts non-empty → 1.0 / passed=True
    - one excerpt is empty string → 0.0 / passed=False
    - one excerpt is whitespace only → 0.0 / passed=False
    - no citations and citations required → 0.0 / passed=False
    - no citations and citations not required → 1.0 / passed=True

  overall_score:
    - is the mean of the four dimension scores
    - all-pass → 1.0
    - all-fail → 0.0
    - mixed → correct fractional value

  pass_fail:
    - citation_presence hard gate: must pass for result to pass
    - citation_excerpt_nonempty hard gate: must pass for result to pass
    - overall_score >= pass_threshold required
    - custom pass_threshold respected
    - pass_threshold stored in result

  CitationScoringResult.get():
    - returns correct DimensionScore for known metric_name
    - returns None for unknown metric_name

  candidate_citation_count:
    - reflects len(candidate.citations)

  notes:
    - None by default
    - propagated when provided

  determinism:
    - identical inputs produce identical outputs across repeated calls

  candidate typing / loading:
    - supports typed CaseOutput objects directly
    - supports dict-constructed CaseOutput (covers fixture loading path)
    - supports JSON fixture files loaded as CaseOutput
    - rejects malformed candidate data via Pydantic validation

  dataset alignment:
    - citation expectation present in F-1 fixtures where expected (fda-001, fda-002, cisa-001, incident-001, edge-001)
    - fixtures without _citation_expectation (cisa-002, incident-002) silently omitted
    - edge-001 has citations_required=False (intentional absent-citation case)
    - scorer does not fail when citation expectations are absent and policy is not-applicable
    - load_citation_expectations() loads correct count from F-1 dataset

No AWS credentials or live calls required.
"""

import json
from pathlib import Path

import pytest

from app.evaluation.citation_scorer import (
    CITATION_PASS_THRESHOLD,
    DIM_EXCERPT_TERMS,
    DIM_NONEMPTY,
    DIM_PRESENCE,
    DIM_SOURCE_LABELS,
    CitationScoringResult,
    score_citations,
)
from app.evaluation.loader import load_citation_expectations
from app.schemas.evaluation_models import CitationExpectation
from app.schemas.output_models import CaseOutput, Citation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "citation_outputs"


def _make_citation(
    source_label: str = "Test Source",
    excerpt: str = "Some relevant excerpt text.",
    source_id: str = "kb-src-001",
    relevance_score: float = 0.85,
) -> Citation:
    return Citation(
        source_id=source_id,
        source_label=source_label,
        excerpt=excerpt,
        relevance_score=relevance_score,
    )


def _make_output(citations: list[Citation] | None = None) -> CaseOutput:
    return CaseOutput(
        document_id="doc-test-001",
        source_filename="test.md",
        source_type="FDA",
        severity="High",
        category="Regulatory",
        summary="The quality system was found inadequate.",
        recommendations=["Initiate CAPA."],
        citations=citations or [],
        confidence_score=0.85,
        unsupported_claims=[],
        escalation_required=False,
        escalation_reason=None,
        validated_by="validation-agent-v1",
        session_id="sess-test",
        timestamp="2026-04-10T12:00:00Z",
    )


def _make_expectation(**kwargs) -> CitationExpectation:
    defaults = {
        "case_id": "test-case-001",
        "citations_required": True,
        "expected_source_labels": [],
        "required_excerpt_terms": [],
        "minimum_citation_count": 1,
    }
    defaults.update(kwargs)
    return CitationExpectation(**defaults)


def _load_fixture(name: str) -> CaseOutput:
    data = json.loads((FIXTURE_DIR / name).read_text())
    # Strip private metadata keys before constructing.
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    return CaseOutput(**clean)


# ---------------------------------------------------------------------------
# citation_presence
# ---------------------------------------------------------------------------


class TestCitationPresence:
    def test_citations_required_and_present(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation(citations_required=True, minimum_citation_count=1)
        result = score_citations(output, exp)
        dim = result.get(DIM_PRESENCE)
        assert dim is not None
        assert dim.score == 1.0
        assert dim.passed is True

    def test_citations_required_exactly_minimum(self):
        output = _make_output([_make_citation(), _make_citation(source_id="kb-src-002")])
        exp = _make_expectation(citations_required=True, minimum_citation_count=2)
        result = score_citations(output, exp)
        dim = result.get(DIM_PRESENCE)
        assert dim.score == 1.0
        assert dim.passed is True

    def test_citations_required_but_absent(self):
        output = _make_output([])
        exp = _make_expectation(citations_required=True, minimum_citation_count=1)
        result = score_citations(output, exp)
        dim = result.get(DIM_PRESENCE)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_citations_required_below_minimum(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation(citations_required=True, minimum_citation_count=3)
        result = score_citations(output, exp)
        dim = result.get(DIM_PRESENCE)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_citations_not_required_not_applicable(self):
        output = _make_output([])
        exp = _make_expectation(citations_required=False)
        result = score_citations(output, exp)
        dim = result.get(DIM_PRESENCE)
        assert dim.score == 1.0
        assert dim.passed is True
        assert "not applicable" in dim.rationale.lower()

    def test_rationale_contains_counts(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation(citations_required=True, minimum_citation_count=1)
        result = score_citations(output, exp)
        dim = result.get(DIM_PRESENCE)
        assert "citation_count=1" in dim.rationale
        assert "minimum_required=1" in dim.rationale


# ---------------------------------------------------------------------------
# citation_source_label_alignment
# ---------------------------------------------------------------------------


class TestSourceLabelAlignment:
    def test_no_expected_labels_not_applicable(self):
        output = _make_output([_make_citation(source_label="Anything")])
        exp = _make_expectation(expected_source_labels=[])
        result = score_citations(output, exp)
        dim = result.get(DIM_SOURCE_LABELS)
        assert dim.score == 1.0
        assert dim.passed is True
        assert "not applicable" in dim.rationale.lower()

    def test_all_expected_labels_present(self):
        citations = [
            _make_citation(source_label="FDA Warning Letter 2025"),
            _make_citation(source_label="CISA Advisory 2025", source_id="kb-src-002"),
        ]
        output = _make_output(citations)
        exp = _make_expectation(
            expected_source_labels=["FDA Warning Letter 2025", "CISA Advisory 2025"]
        )
        result = score_citations(output, exp)
        dim = result.get(DIM_SOURCE_LABELS)
        assert dim.score == 1.0
        assert dim.passed is True

    def test_partial_labels_present(self):
        citations = [_make_citation(source_label="FDA Warning Letter 2025")]
        output = _make_output(citations)
        exp = _make_expectation(
            expected_source_labels=["FDA Warning Letter 2025", "Missing Source"]
        )
        result = score_citations(output, exp)
        dim = result.get(DIM_SOURCE_LABELS)
        assert dim.score == pytest.approx(0.5)
        assert dim.passed is False

    def test_no_expected_labels_matched(self):
        citations = [_make_citation(source_label="Unrelated Source")]
        output = _make_output(citations)
        exp = _make_expectation(expected_source_labels=["FDA Warning Letter", "CISA Doc"])
        result = score_citations(output, exp)
        dim = result.get(DIM_SOURCE_LABELS)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_case_insensitive_label_matching(self):
        citations = [_make_citation(source_label="fda warning letter 2025")]
        output = _make_output(citations)
        exp = _make_expectation(expected_source_labels=["FDA Warning Letter 2025"])
        result = score_citations(output, exp)
        dim = result.get(DIM_SOURCE_LABELS)
        assert dim.score == 1.0

    def test_whitespace_normalized_label_matching(self):
        citations = [_make_citation(source_label="  FDA Warning Letter 2025  ")]
        output = _make_output(citations)
        exp = _make_expectation(expected_source_labels=["FDA Warning Letter 2025"])
        result = score_citations(output, exp)
        dim = result.get(DIM_SOURCE_LABELS)
        assert dim.score == 1.0


# ---------------------------------------------------------------------------
# citation_excerpt_evidence_coverage
# ---------------------------------------------------------------------------


class TestExcerptEvidenceCoverage:
    def test_no_required_terms_not_applicable(self):
        output = _make_output([_make_citation(excerpt="any text")])
        exp = _make_expectation(required_excerpt_terms=[])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert dim.score == 1.0
        assert dim.passed is True
        assert "not applicable" in dim.rationale.lower()

    def test_all_terms_found(self):
        output = _make_output(
            [_make_citation(excerpt="corrective action and quality system review")]
        )
        exp = _make_expectation(required_excerpt_terms=["corrective", "quality"])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert dim.score == 1.0
        assert dim.passed is True

    def test_partial_terms_found(self):
        output = _make_output(
            [_make_citation(excerpt="corrective action procedures were not established")]
        )
        exp = _make_expectation(required_excerpt_terms=["corrective", "recall"])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert dim.score == pytest.approx(0.5)
        assert dim.passed is False

    def test_no_terms_found(self):
        output = _make_output([_make_citation(excerpt="some unrelated content")])
        exp = _make_expectation(required_excerpt_terms=["ransomware", "recall"])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_case_insensitive_term_matching(self):
        output = _make_output([_make_citation(excerpt="CORRECTIVE ACTION was not taken")])
        exp = _make_expectation(required_excerpt_terms=["corrective"])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert dim.score == 1.0

    def test_term_matched_across_multiple_excerpts(self):
        citations = [
            _make_citation(excerpt="corrective measures applied", source_id="kb-1"),
            _make_citation(excerpt="quality system deficiency noted", source_id="kb-2",
                           source_label="Source B"),
        ]
        output = _make_output(citations)
        exp = _make_expectation(required_excerpt_terms=["corrective", "quality"])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert dim.score == 1.0

    def test_rationale_contains_match_count(self):
        output = _make_output([_make_citation(excerpt="corrective procedures")])
        exp = _make_expectation(required_excerpt_terms=["corrective", "missing"])
        result = score_citations(output, exp)
        dim = result.get(DIM_EXCERPT_TERMS)
        assert "1/2" in dim.rationale


# ---------------------------------------------------------------------------
# citation_excerpt_nonempty
# ---------------------------------------------------------------------------


class TestExcerptNonempty:
    def test_all_excerpts_nonempty(self):
        citations = [
            _make_citation(excerpt="Some text", source_id="kb-1"),
            _make_citation(excerpt="More text", source_id="kb-2", source_label="Src B"),
        ]
        output = _make_output(citations)
        exp = _make_expectation()
        result = score_citations(output, exp)
        dim = result.get(DIM_NONEMPTY)
        assert dim.score == 1.0
        assert dim.passed is True

    def test_one_excerpt_empty_string(self):
        citations = [
            _make_citation(excerpt="Valid text", source_id="kb-1"),
            _make_citation(excerpt="", source_id="kb-2", source_label="Empty Src"),
        ]
        output = _make_output(citations)
        exp = _make_expectation()
        result = score_citations(output, exp)
        dim = result.get(DIM_NONEMPTY)
        assert dim.score == 0.0
        assert dim.passed is False
        assert "Empty Src" in dim.rationale

    def test_one_excerpt_whitespace_only(self):
        citations = [
            _make_citation(excerpt="Valid text", source_id="kb-1"),
            _make_citation(excerpt="   \t  ", source_id="kb-2", source_label="Whitespace Src"),
        ]
        output = _make_output(citations)
        exp = _make_expectation()
        result = score_citations(output, exp)
        dim = result.get(DIM_NONEMPTY)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_no_citations_required_nonempty_fails(self):
        output = _make_output([])
        exp = _make_expectation(citations_required=True)
        result = score_citations(output, exp)
        dim = result.get(DIM_NONEMPTY)
        assert dim.score == 0.0
        assert dim.passed is False

    def test_no_citations_not_required_nonempty_passes(self):
        output = _make_output([])
        exp = _make_expectation(citations_required=False)
        result = score_citations(output, exp)
        dim = result.get(DIM_NONEMPTY)
        assert dim.score == 1.0
        assert dim.passed is True


# ---------------------------------------------------------------------------
# overall_score
# ---------------------------------------------------------------------------


class TestOverallScore:
    def test_all_pass_overall_is_1(self):
        output = _make_output([_make_citation(excerpt="corrective quality text")])
        exp = _make_expectation(
            citations_required=True,
            required_excerpt_terms=["corrective", "quality"],
            minimum_citation_count=1,
        )
        result = score_citations(output, exp)
        assert result.overall_score == pytest.approx(1.0)

    def test_all_fail_overall_is_0(self):
        # All four dims fail: presence (required but absent), source labels (no match),
        # excerpt terms (no match since no citations), nonempty (required but absent).
        output = _make_output([])
        exp = _make_expectation(
            citations_required=True,
            expected_source_labels=["FDA Source"],
            required_excerpt_terms=["corrective"],
            minimum_citation_count=1,
        )
        result = score_citations(output, exp)
        # With no citations: presence=0, source_labels=0 (no match), excerpt_terms=0 (no text),
        # nonempty=0 (required but absent) → mean=0.0
        assert result.overall_score == pytest.approx(0.0)

    def test_mixed_score_is_correct_mean(self):
        # presence=1.0, source_labels=1.0 (N/A), excerpt_terms=0.5 (1/2 terms),
        # nonempty=1.0 → mean = (1+1+0.5+1)/4 = 0.875
        output = _make_output([_make_citation(excerpt="corrective action only")])
        exp = _make_expectation(
            citations_required=True,
            required_excerpt_terms=["corrective", "recall"],
        )
        result = score_citations(output, exp)
        assert result.overall_score == pytest.approx(0.875)

    def test_overall_score_is_rounded(self):
        output = _make_output([_make_citation(excerpt="corrective action")])
        exp = _make_expectation(
            citations_required=True,
            required_excerpt_terms=["corrective", "missing1", "missing2"],
        )
        result = score_citations(output, exp)
        # presence=1, source_labels=1, excerpt_terms=1/3≈0.333..., nonempty=1 → mean≈0.583...
        assert isinstance(result.overall_score, float)
        assert len(str(result.overall_score).split(".")[-1]) <= 6


# ---------------------------------------------------------------------------
# pass_fail
# ---------------------------------------------------------------------------


class TestPassFail:
    def test_all_pass_result_passes(self):
        output = _make_output([_make_citation(excerpt="corrective quality text")])
        exp = _make_expectation(required_excerpt_terms=["corrective"])
        result = score_citations(output, exp)
        assert result.pass_fail is True

    def test_presence_hard_gate_blocks_pass(self):
        output = _make_output([])
        exp = _make_expectation(citations_required=True, minimum_citation_count=1)
        result = score_citations(output, exp)
        assert result.pass_fail is False

    def test_nonempty_hard_gate_blocks_pass(self):
        output = _make_output([_make_citation(excerpt="")])
        exp = _make_expectation(citations_required=True)
        result = score_citations(output, exp)
        assert result.pass_fail is False

    def test_overall_below_threshold_blocks_pass(self):
        output = _make_output([_make_citation(excerpt="only one of four terms")])
        exp = _make_expectation(
            citations_required=True,
            expected_source_labels=["Missing Source A", "Missing Source B"],
            required_excerpt_terms=["term1", "term2", "term3", "term4"],
        )
        result = score_citations(output, exp, pass_threshold=0.9)
        assert result.pass_fail is False

    def test_custom_pass_threshold_respected(self):
        output = _make_output([_make_citation(excerpt="corrective text")])
        exp = _make_expectation(required_excerpt_terms=["corrective"])
        # With all N/A dims + full pass, score=1.0; passes at any threshold
        result_low = score_citations(output, exp, pass_threshold=0.1)
        result_high = score_citations(output, exp, pass_threshold=1.0)
        assert result_low.pass_fail is True
        assert result_high.pass_fail is True  # 1.0 >= 1.0

    def test_pass_threshold_stored_in_result(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation()
        result = score_citations(output, exp, pass_threshold=0.80)
        assert result.pass_threshold == pytest.approx(0.80)

    def test_default_threshold_is_constant(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation()
        result = score_citations(output, exp)
        assert result.pass_threshold == CITATION_PASS_THRESHOLD


# ---------------------------------------------------------------------------
# CitationScoringResult.get()
# ---------------------------------------------------------------------------


class TestCitationScoringResultGet:
    def test_returns_dimension_score_for_known_name(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation()
        result = score_citations(output, exp)
        for name in [DIM_PRESENCE, DIM_SOURCE_LABELS, DIM_EXCERPT_TERMS, DIM_NONEMPTY]:
            dim = result.get(name)
            assert dim is not None
            assert dim.metric_name == name

    def test_returns_none_for_unknown_name(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation()
        result = score_citations(output, exp)
        assert result.get("nonexistent_metric") is None


# ---------------------------------------------------------------------------
# candidate_citation_count
# ---------------------------------------------------------------------------


class TestCandidateCitationCount:
    def test_count_matches_citations_length(self):
        citations = [
            _make_citation(source_id="kb-1"),
            _make_citation(source_id="kb-2", source_label="Src B"),
            _make_citation(source_id="kb-3", source_label="Src C"),
        ]
        output = _make_output(citations)
        result = score_citations(output, _make_expectation())
        assert result.candidate_citation_count == 3

    def test_count_is_zero_when_no_citations(self):
        output = _make_output([])
        result = score_citations(output, _make_expectation(citations_required=False))
        assert result.candidate_citation_count == 0


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


class TestNotes:
    def test_notes_is_none_by_default(self):
        output = _make_output([_make_citation()])
        result = score_citations(output, _make_expectation())
        assert result.notes is None

    def test_notes_propagated_when_provided(self):
        output = _make_output([_make_citation()])
        result = score_citations(output, _make_expectation(), notes="test run")
        assert result.notes == "test run"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_calls_produce_identical_results(self):
        output = _make_output([_make_citation(excerpt="corrective quality text")])
        exp = _make_expectation(required_excerpt_terms=["corrective", "quality"])
        results = [score_citations(output, exp) for _ in range(5)]
        scores = [r.overall_score for r in results]
        assert all(s == scores[0] for s in scores)
        pass_fails = [r.pass_fail for r in results]
        assert all(p == pass_fails[0] for p in pass_fails)


# ---------------------------------------------------------------------------
# Candidate typing / loading
# ---------------------------------------------------------------------------


class TestCandidateTypingAndLoading:
    def test_supports_typed_case_output(self):
        output = _make_output([_make_citation()])
        exp = _make_expectation()
        result = score_citations(output, exp)
        assert isinstance(result, CitationScoringResult)

    def test_supports_dict_constructed_case_output(self):
        data = {
            "document_id": "doc-dict-001",
            "source_filename": "test.md",
            "source_type": "FDA",
            "severity": "High",
            "category": "Regulatory",
            "summary": "Summary text.",
            "recommendations": [],
            "citations": [
                {
                    "source_id": "kb-src-001",
                    "source_label": "Test Source",
                    "excerpt": "Some excerpt.",
                    "relevance_score": 0.80,
                }
            ],
            "confidence_score": 0.80,
            "unsupported_claims": [],
            "escalation_required": False,
            "escalation_reason": None,
            "validated_by": "test-agent",
            "timestamp": "2026-04-10T12:00:00Z",
        }
        output = CaseOutput(**data)
        result = score_citations(output, _make_expectation())
        assert isinstance(result, CitationScoringResult)

    def test_loads_strong_citations_fixture(self):
        output = _load_fixture("strong_citations.json")
        exp = _make_expectation(
            citations_required=True,
            required_excerpt_terms=["corrective", "quality"],
        )
        result = score_citations(output, exp)
        assert result.get(DIM_PRESENCE).passed is True
        assert result.get(DIM_NONEMPTY).passed is True

    def test_loads_missing_citations_fixture(self):
        output = _load_fixture("missing_citations.json")
        exp = _make_expectation(citations_required=True, minimum_citation_count=1)
        result = score_citations(output, exp)
        assert result.get(DIM_PRESENCE).passed is False
        assert result.pass_fail is False

    def test_loads_wrong_source_labels_fixture(self):
        output = _load_fixture("wrong_source_labels.json")
        exp = _make_expectation(
            citations_required=True,
            expected_source_labels=["Expected Source A", "Expected Source B"],
        )
        result = score_citations(output, exp)
        assert result.get(DIM_SOURCE_LABELS).score == 0.0

    def test_loads_empty_excerpts_fixture(self):
        output = _load_fixture("empty_excerpts.json")
        exp = _make_expectation(citations_required=True)
        result = score_citations(output, exp)
        assert result.get(DIM_NONEMPTY).passed is False
        assert result.pass_fail is False

    def test_loads_no_citations_not_required_fixture(self):
        output = _load_fixture("no_citations_not_required.json")
        exp = _make_expectation(citations_required=False)
        result = score_citations(output, exp)
        assert result.get(DIM_PRESENCE).passed is True
        assert result.get(DIM_NONEMPTY).passed is True
        assert result.pass_fail is True

    def test_rejects_malformed_citation_data(self):
        with pytest.raises(Exception):
            CaseOutput(
                document_id="doc-bad",
                source_filename="f.md",
                source_type="FDA",
                severity="High",
                category="Regulatory",
                summary="Summary.",
                recommendations=[],
                citations=[
                    {
                        "source_id": "x",
                        "source_label": "y",
                        # Missing excerpt and relevance_score.
                    }
                ],
                confidence_score=0.8,
                unsupported_claims=[],
                escalation_required=False,
                escalation_reason=None,
                validated_by="v1",
                timestamp="2026-04-10T12:00:00Z",
            )


# ---------------------------------------------------------------------------
# Dataset alignment
# ---------------------------------------------------------------------------


class TestDatasetAlignment:
    def test_load_citation_expectations_returns_expected_cases(self):
        expectations = load_citation_expectations()
        # We added _citation_expectation to: fda-001, fda-002, cisa-001, incident-001, edge-001
        # cisa-002 and incident-002 are intentionally absent.
        assert len(expectations) == 5

    def test_citation_expectation_present_for_fda_001(self):
        expectations = load_citation_expectations()
        assert "eval-fda-001" in expectations

    def test_citation_expectation_present_for_fda_002(self):
        expectations = load_citation_expectations()
        assert "eval-fda-002" in expectations

    def test_citation_expectation_present_for_cisa_001(self):
        expectations = load_citation_expectations()
        assert "eval-cisa-001" in expectations

    def test_citation_expectation_present_for_incident_001(self):
        expectations = load_citation_expectations()
        assert "eval-incident-001" in expectations

    def test_citation_expectation_absent_for_cisa_002(self):
        expectations = load_citation_expectations()
        assert "eval-cisa-002" not in expectations

    def test_citation_expectation_absent_for_incident_002(self):
        expectations = load_citation_expectations()
        assert "eval-incident-002" not in expectations

    def test_edge_001_citations_not_required(self):
        expectations = load_citation_expectations()
        edge = expectations.get("eval-edge-001")
        assert edge is not None
        assert edge.citations_required is False

    def test_fda_001_has_correct_required_terms(self):
        expectations = load_citation_expectations()
        fda = expectations["eval-fda-001"]
        assert "corrective" in fda.required_excerpt_terms
        assert "quality" in fda.required_excerpt_terms

    def test_scorer_handles_absent_citation_expectation(self):
        # When no citation expectation exists, callers should treat as not-applicable.
        # Verify scorer works with a minimal all-N/A expectation.
        output = _make_output([])
        exp = CitationExpectation(
            case_id="no-expectation-case",
            citations_required=False,
            expected_source_labels=[],
            required_excerpt_terms=[],
        )
        result = score_citations(output, exp)
        assert result.overall_score == pytest.approx(1.0)
        assert result.pass_fail is True

    def test_scorer_does_not_fail_with_no_source_labels(self):
        expectations = load_citation_expectations()
        fda = expectations["eval-fda-001"]
        # expected_source_labels is empty → N/A
        output = _make_output([_make_citation()])
        result = score_citations(output, fda)
        assert result.get(DIM_SOURCE_LABELS).score == 1.0

    def test_scorer_does_not_fail_with_empty_excerpt_terms(self):
        expectations = load_citation_expectations()
        edge = expectations["eval-edge-001"]
        # required_excerpt_terms is empty → N/A
        output = _make_output([])
        result = score_citations(output, edge)
        assert result.get(DIM_EXCERPT_TERMS).score == 1.0


# ---------------------------------------------------------------------------
# Separation from G-0 and F-2
# ---------------------------------------------------------------------------


class TestSeparationFromOtherScorers:
    def test_citation_scorer_has_no_retrieval_imports(self):
        """citation_scorer.py must not import from retrieval_scorer or runner."""
        import app.evaluation.citation_scorer as mod
        import inspect
        source = inspect.getsource(mod)
        assert "retrieval_scorer" not in source
        assert "runner" not in source

    def test_citation_scorer_result_is_independent_type(self):
        from app.evaluation.retrieval_scorer import RetrievalScoringResult
        output = _make_output([_make_citation()])
        result = score_citations(output, _make_expectation())
        assert not isinstance(result, RetrievalScoringResult)
        assert isinstance(result, CitationScoringResult)
