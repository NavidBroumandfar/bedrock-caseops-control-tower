"""
G-0 unit tests — retrieval quality scorer.

Coverage:
  minimum_chunks_match:
    - chunk count meets minimum → 1.0 / passed=True
    - chunk count equals minimum exactly → 1.0 / passed=True
    - chunk count below minimum → 0.0 / passed=False
    - zero chunks, minimum=1 → 0.0 / passed=False

  source_label_hit_rate:
    - no expected labels defined → 1.0 / not-applicable
    - all expected labels present (case-insensitive) → 1.0 / passed=True
    - partial labels present → fractional score
    - no expected labels present → 0.0 / passed=False
    - case-insensitive label matching
    - whitespace-normalized label matching

  required_evidence_term_coverage:
    - no required terms defined → 1.0 / not-applicable
    - all terms present in chunk text → 1.0 / passed=True
    - partial terms present → fractional score
    - no terms present → 0.0 / passed=False
    - case-insensitive term matching
    - term matched across multiple chunks

  overall_score:
    - is the mean of the three dimension scores
    - all-pass → 1.0
    - all-fail → 0.0
    - mixed → correct fractional value

  pass_fail:
    - overall_score >= pass_threshold → True
    - overall_score < pass_threshold → False
    - custom pass_threshold respected
    - pass_threshold stored in result

  RetrievalScoringResult.get():
    - returns correct DimensionScore for known metric_name
    - returns None for unknown metric_name

  candidate_chunk_count:
    - reflects retrieved_count from the candidate

  notes:
    - None by default
    - propagated when provided

  determinism:
    - identical inputs produce identical outputs across repeated calls

  candidate typing / loading:
    - supports typed RetrievalResult objects directly
    - supports dict-constructed RetrievalResult (covers fixture loading path)
    - supports JSON fixture files loaded as RetrievalResult
    - rejects malformed candidate data via Pydantic validation

  dataset alignment:
    - retrieval expectation present in F-1 fixtures where expected
    - scoring does not fail when expected_source_labels is empty (not-applicable policy)
    - scoring does not fail when required_evidence_terms is empty (not-applicable policy)
    - edge case (eval-edge-001) fixture has no _retrieval_expectation — handled gracefully

No AWS credentials or live calls required.
"""

import json
from pathlib import Path

import pytest

from app.evaluation.retrieval_scorer import (
    RETRIEVAL_PASS_THRESHOLD,
    DIM_CHUNKS,
    DIM_EVIDENCE_TERMS,
    DIM_SOURCE_LABELS,
    RetrievalScoringResult,
    score_retrieval,
)
from app.schemas.evaluation_models import RetrievalExpectation
from app.schemas.retrieval_models import EvidenceChunk, RetrievalResult

# Path helpers
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "retrieval_outputs"
_EVAL_EXPECTED_DIR = (
    Path(__file__).parent.parent / "data" / "evaluation" / "expected"
)


# ── builder helpers ────────────────────────────────────────────────────────────


def _make_chunk(
    chunk_id: str = "chunk-001",
    text: str = "default chunk text",
    source_label: str = "Source A",
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        text=text,
        source_id=f"kb-{chunk_id}",
        source_label=source_label,
        excerpt=text[:50],
        relevance_score=0.80,
    )


def _make_candidate(
    chunks: list[EvidenceChunk] | None = None,
    retrieval_status: str = "success",
    document_id: str = "doc-test-001",
) -> RetrievalResult:
    if chunks is None:
        chunks = [_make_chunk()]
    return RetrievalResult(
        document_id=document_id,
        evidence_chunks=chunks,
        retrieval_status=retrieval_status,
        retrieved_count=len(chunks),
    )


def _make_expectation(
    minimum_expected_chunks: int = 1,
    expected_source_labels: list[str] | None = None,
    required_evidence_terms: list[str] | None = None,
    case_id: str = "eval-test-001",
) -> RetrievalExpectation:
    return RetrievalExpectation(
        case_id=case_id,
        minimum_expected_chunks=minimum_expected_chunks,
        expected_source_labels=expected_source_labels or [],
        required_evidence_terms=required_evidence_terms or [],
    )


def _load_fixture(filename: str) -> RetrievalResult:
    """Load a retrieval output fixture from the test fixtures directory."""
    path = _FIXTURES_DIR / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    # Strip private metadata keys
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    return RetrievalResult(**clean)


# ── minimum_chunks_match ───────────────────────────────────────────────────────


class TestMinimumChunksMatch:
    def test_count_meets_minimum_passes(self):
        candidate = _make_candidate(chunks=[_make_chunk("c1"), _make_chunk("c2")])
        exp = _make_expectation(minimum_expected_chunks=2)
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_CHUNKS)
        assert ds is not None
        assert ds.score == 1.0
        assert ds.passed is True

    def test_count_exceeds_minimum_passes(self):
        candidate = _make_candidate(
            chunks=[_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        )
        exp = _make_expectation(minimum_expected_chunks=2)
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_CHUNKS).passed is True

    def test_count_exactly_equals_minimum_passes(self):
        candidate = _make_candidate(chunks=[_make_chunk("c1")])
        exp = _make_expectation(minimum_expected_chunks=1)
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_CHUNKS).passed is True

    def test_count_below_minimum_fails(self):
        candidate = _make_candidate(chunks=[_make_chunk("c1")])
        exp = _make_expectation(minimum_expected_chunks=3)
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_CHUNKS)
        assert ds.score == 0.0
        assert ds.passed is False

    def test_zero_chunks_against_minimum_one_fails(self):
        candidate = _make_candidate(chunks=[], retrieval_status="empty")
        exp = _make_expectation(minimum_expected_chunks=1)
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_CHUNKS)
        assert ds.score == 0.0
        assert ds.passed is False

    def test_rationale_contains_counts(self):
        candidate = _make_candidate(chunks=[_make_chunk("c1")])
        exp = _make_expectation(minimum_expected_chunks=3)
        result = score_retrieval(candidate, exp)
        rationale = result.get(DIM_CHUNKS).rationale
        assert "1" in rationale
        assert "3" in rationale


# ── source_label_hit_rate ─────────────────────────────────────────────────────


class TestSourceLabelHitRate:
    def test_no_expected_labels_not_applicable(self):
        candidate = _make_candidate()
        exp = _make_expectation(expected_source_labels=[])
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_SOURCE_LABELS)
        assert ds.score == 1.0
        assert ds.passed is True
        assert "not applicable" in ds.rationale

    def test_all_labels_present_scores_1(self):
        chunks = [
            _make_chunk("c1", source_label="FDA Warning Letter 2025"),
            _make_chunk("c2", source_label="CISA Advisory 2025"),
        ]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            expected_source_labels=["FDA Warning Letter 2025", "CISA Advisory 2025"]
        )
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_SOURCE_LABELS)
        assert ds.score == 1.0
        assert ds.passed is True

    def test_partial_labels_fractional_score(self):
        chunks = [_make_chunk("c1", source_label="FDA Warning Letter 2025")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            expected_source_labels=["FDA Warning Letter 2025", "CISA Advisory 2025"]
        )
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_SOURCE_LABELS)
        assert abs(ds.score - 0.5) < 1e-6
        assert ds.passed is False

    def test_no_labels_present_scores_0(self):
        chunks = [_make_chunk("c1", source_label="Unknown Source")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            expected_source_labels=["FDA Warning Letter 2025", "CISA Advisory 2025"]
        )
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_SOURCE_LABELS).score == 0.0

    def test_case_insensitive_label_matching(self):
        chunks = [_make_chunk("c1", source_label="fda warning letter 2025")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(expected_source_labels=["FDA Warning Letter 2025"])
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_SOURCE_LABELS).passed is True

    def test_whitespace_normalized_label_matching(self):
        chunks = [_make_chunk("c1", source_label="  FDA Warning Letter 2025  ")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(expected_source_labels=["FDA Warning Letter 2025"])
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_SOURCE_LABELS).passed is True

    def test_rationale_contains_match_counts(self):
        chunks = [_make_chunk("c1", source_label="FDA Warning Letter 2025")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            expected_source_labels=["FDA Warning Letter 2025", "CISA Advisory 2025"]
        )
        result = score_retrieval(candidate, exp)
        rationale = result.get(DIM_SOURCE_LABELS).rationale
        assert "1" in rationale
        assert "2" in rationale


# ── required_evidence_term_coverage ───────────────────────────────────────────


class TestRequiredEvidenceTermCoverage:
    def test_no_required_terms_not_applicable(self):
        candidate = _make_candidate()
        exp = _make_expectation(required_evidence_terms=[])
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_EVIDENCE_TERMS)
        assert ds.score == 1.0
        assert ds.passed is True
        assert "not applicable" in ds.rationale

    def test_all_terms_present_scores_1(self):
        chunks = [
            _make_chunk(
                "c1",
                text="The quality system corrective action device procedure was deficient.",
            )
        ]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            required_evidence_terms=["quality system", "corrective", "device"]
        )
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_EVIDENCE_TERMS)
        assert ds.score == 1.0
        assert ds.passed is True

    def test_partial_terms_fractional_score(self):
        chunks = [_make_chunk("c1", text="The quality system was reviewed.")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            required_evidence_terms=["quality system", "corrective", "device"]
        )
        result = score_retrieval(candidate, exp)
        ds = result.get(DIM_EVIDENCE_TERMS)
        assert abs(ds.score - 1 / 3) < 1e-6
        assert ds.passed is False

    def test_no_terms_present_scores_0(self):
        chunks = [_make_chunk("c1", text="A routine administrative notice was filed.")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            required_evidence_terms=["quality system", "corrective", "device"]
        )
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_EVIDENCE_TERMS).score == 0.0

    def test_case_insensitive_term_matching(self):
        chunks = [_make_chunk("c1", text="CORRECTIVE action plans were missing.")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(required_evidence_terms=["corrective"])
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_EVIDENCE_TERMS).passed is True

    def test_term_matched_across_multiple_chunks(self):
        chunks = [
            _make_chunk("c1", text="The outage was caused by a cron failure."),
            _make_chunk("c2", text="Root cause analysis identified a connection pool issue."),
        ]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            required_evidence_terms=["outage", "root cause"]
        )
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_EVIDENCE_TERMS).passed is True

    def test_term_not_present_in_empty_retrieval(self):
        candidate = _make_candidate(chunks=[], retrieval_status="empty")
        exp = _make_expectation(required_evidence_terms=["recall", "undeclared"])
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_EVIDENCE_TERMS).score == 0.0


# ── overall_score ─────────────────────────────────────────────────────────────


class TestOverallScore:
    def test_all_pass_overall_is_1(self):
        chunks = [
            _make_chunk(
                "c1",
                text="Quality system corrective action device.",
                source_label="FDA Source",
            )
        ]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=["FDA Source"],
            required_evidence_terms=["corrective"],
        )
        result = score_retrieval(candidate, exp)
        assert result.overall_score == 1.0

    def test_all_fail_overall_is_0(self):
        candidate = _make_candidate(chunks=[], retrieval_status="empty")
        exp = _make_expectation(
            minimum_expected_chunks=3,
            expected_source_labels=["FDA Source"],
            required_evidence_terms=["corrective"],
        )
        result = score_retrieval(candidate, exp)
        assert result.overall_score == 0.0

    def test_one_of_three_passes_is_one_third(self):
        # chunks=1 meets minimum → DIM_CHUNKS=1.0
        # source labels don't match → DIM_SOURCE_LABELS=0.0
        # evidence terms absent → DIM_EVIDENCE_TERMS=0.0
        chunks = [_make_chunk("c1", text="unrelated text", source_label="Unknown")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=["FDA Source"],
            required_evidence_terms=["corrective"],
        )
        result = score_retrieval(candidate, exp)
        assert abs(result.overall_score - 1 / 3) < 1e-6

    def test_not_applicable_dimensions_count_as_1(self):
        # no expected labels (N/A=1.0), no required terms (N/A=1.0), chunks pass → 1.0
        candidate = _make_candidate()
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=[],
            required_evidence_terms=[],
        )
        result = score_retrieval(candidate, exp)
        assert result.overall_score == 1.0


# ── pass_fail ─────────────────────────────────────────────────────────────────


class TestPassFail:
    def test_overall_score_above_threshold_passes(self):
        chunks = [
            _make_chunk("c1", text="corrective action device", source_label="FDA")
        ]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=["FDA"],
            required_evidence_terms=["corrective"],
        )
        result = score_retrieval(candidate, exp, pass_threshold=0.75)
        assert result.pass_fail is True

    def test_overall_score_below_threshold_fails(self):
        chunks = [_make_chunk("c1", text="unrelated", source_label="Unknown")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            minimum_expected_chunks=3,
            expected_source_labels=["FDA Source"],
            required_evidence_terms=["corrective"],
        )
        result = score_retrieval(candidate, exp, pass_threshold=0.75)
        assert result.pass_fail is False

    def test_custom_threshold_zero_always_passes(self):
        candidate = _make_candidate(chunks=[], retrieval_status="empty")
        exp = _make_expectation(minimum_expected_chunks=1)
        result = score_retrieval(candidate, exp, pass_threshold=0.0)
        assert result.pass_fail is True

    def test_custom_threshold_one_requires_perfect_score(self):
        chunks = [_make_chunk("c1", text="unrelated", source_label="Unknown")]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=["Missing Source"],
            required_evidence_terms=["corrective"],
        )
        result = score_retrieval(candidate, exp, pass_threshold=1.0)
        assert result.pass_fail is False

    def test_pass_threshold_stored_in_result(self):
        candidate = _make_candidate()
        exp = _make_expectation()
        result = score_retrieval(candidate, exp, pass_threshold=0.9)
        assert result.pass_threshold == 0.9


# ── RetrievalScoringResult.get() ──────────────────────────────────────────────


class TestScoringResultGet:
    def test_get_known_metric_chunks(self):
        result = score_retrieval(_make_candidate(), _make_expectation())
        ds = result.get(DIM_CHUNKS)
        assert ds is not None
        assert ds.metric_name == DIM_CHUNKS

    def test_get_known_metric_labels(self):
        result = score_retrieval(_make_candidate(), _make_expectation())
        ds = result.get(DIM_SOURCE_LABELS)
        assert ds is not None
        assert ds.metric_name == DIM_SOURCE_LABELS

    def test_get_known_metric_terms(self):
        result = score_retrieval(_make_candidate(), _make_expectation())
        ds = result.get(DIM_EVIDENCE_TERMS)
        assert ds is not None
        assert ds.metric_name == DIM_EVIDENCE_TERMS

    def test_get_unknown_metric_returns_none(self):
        result = score_retrieval(_make_candidate(), _make_expectation())
        assert result.get("nonexistent_metric") is None


# ── candidate_chunk_count ─────────────────────────────────────────────────────


class TestCandidateChunkCount:
    def test_chunk_count_reflected_in_result(self):
        chunks = [_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        candidate = _make_candidate(chunks=chunks)
        result = score_retrieval(candidate, _make_expectation())
        assert result.candidate_chunk_count == 3

    def test_empty_retrieval_count_is_zero(self):
        candidate = _make_candidate(chunks=[], retrieval_status="empty")
        result = score_retrieval(candidate, _make_expectation())
        assert result.candidate_chunk_count == 0


# ── notes ─────────────────────────────────────────────────────────────────────


class TestNotes:
    def test_notes_default_is_none(self):
        result = score_retrieval(_make_candidate(), _make_expectation())
        assert result.notes is None

    def test_notes_propagated_when_provided(self):
        result = score_retrieval(
            _make_candidate(), _make_expectation(), notes="manual inspection flag"
        )
        assert result.notes == "manual inspection flag"


# ── determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_identical_inputs_produce_identical_scores(self):
        chunks = [
            _make_chunk("c1", text="corrective device quality system", source_label="FDA")
        ]
        candidate = _make_candidate(chunks=chunks)
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=["FDA"],
            required_evidence_terms=["corrective", "device"],
        )
        result_a = score_retrieval(candidate, exp)
        result_b = score_retrieval(candidate, exp)
        assert result_a.overall_score == result_b.overall_score
        assert result_a.pass_fail == result_b.pass_fail
        for ds_a, ds_b in zip(result_a.dimension_scores, result_b.dimension_scores):
            assert ds_a.score == ds_b.score
            assert ds_a.passed == ds_b.passed


# ── candidate typing / loading ────────────────────────────────────────────────


class TestCandidateTyping:
    def test_typed_retrieval_result_accepted(self):
        candidate = _make_candidate()
        exp = _make_expectation()
        result = score_retrieval(candidate, exp)
        assert isinstance(result, RetrievalScoringResult)

    def test_dict_constructed_retrieval_result_accepted(self):
        data = {
            "document_id": "doc-dict-001",
            "evidence_chunks": [
                {
                    "chunk_id": "c1",
                    "text": "corrective action",
                    "source_id": "kb-001",
                    "source_label": "Source A",
                    "excerpt": "corrective action",
                    "relevance_score": 0.8,
                }
            ],
            "retrieval_status": "success",
            "retrieved_count": 1,
        }
        candidate = RetrievalResult(**data)
        exp = _make_expectation()
        result = score_retrieval(candidate, exp)
        assert isinstance(result, RetrievalScoringResult)

    def test_json_fixture_strong_loads_and_scores(self):
        candidate = _load_fixture("strong_retrieval.json")
        exp = _make_expectation(
            minimum_expected_chunks=1,
            expected_source_labels=[],
            required_evidence_terms=["quality system", "corrective"],
        )
        result = score_retrieval(candidate, exp)
        assert result.overall_score == 1.0
        assert result.pass_fail is True

    def test_json_fixture_weak_chunk_count_fails(self):
        candidate = _load_fixture("weak_retrieval.json")
        exp = _make_expectation(minimum_expected_chunks=3)
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_CHUNKS).passed is False

    def test_json_fixture_missing_source_labels_scores_zero(self):
        candidate = _load_fixture("missing_source_labels.json")
        exp = _make_expectation(
            expected_source_labels=["CISA Official Advisory 2025"]
        )
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_SOURCE_LABELS).score == 0.0

    def test_json_fixture_missing_evidence_terms_scores_zero(self):
        # The missing_evidence_terms fixture contains generic maintenance text —
        # none of these critical security terms appear in it.
        candidate = _load_fixture("missing_evidence_terms.json")
        exp = _make_expectation(
            required_evidence_terms=["ransomware", "vulnerability", "exploit"]
        )
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_EVIDENCE_TERMS).score == 0.0

    def test_json_fixture_empty_retrieval_scores_zero_chunks(self):
        candidate = _load_fixture("empty_retrieval.json")
        exp = _make_expectation(minimum_expected_chunks=1)
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_CHUNKS).score == 0.0
        assert result.candidate_chunk_count == 0

    def test_malformed_candidate_rejected(self):
        with pytest.raises(Exception):
            RetrievalResult(
                document_id="",
                evidence_chunks="not-a-list",
                retrieval_status="success",
                retrieved_count=0,
            )


# ── dataset alignment ─────────────────────────────────────────────────────────


class TestDatasetAlignment:
    """Verify that F-1 expected fixtures embed RetrievalExpectation where expected
    and that scoring handles all cases correctly."""

    def _load_retrieval_expectation(self, case_id: str) -> RetrievalExpectation | None:
        """Extract _retrieval_expectation from an F-1 expected fixture if present."""
        path = _EVAL_EXPECTED_DIR / f"{case_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        block = raw.get("_retrieval_expectation")
        if block is None:
            return None
        data = {k: v for k, v in block.items() if not k.startswith("_")}
        return RetrievalExpectation(**data)

    def test_fda_001_has_retrieval_expectation(self):
        exp = self._load_retrieval_expectation("eval-fda-001")
        assert exp is not None
        assert exp.case_id == "eval-fda-001"
        assert exp.minimum_expected_chunks >= 1

    def test_fda_002_has_retrieval_expectation(self):
        exp = self._load_retrieval_expectation("eval-fda-002")
        assert exp is not None
        assert exp.case_id == "eval-fda-002"

    def test_cisa_001_has_retrieval_expectation(self):
        exp = self._load_retrieval_expectation("eval-cisa-001")
        assert exp is not None
        assert exp.case_id == "eval-cisa-001"

    def test_incident_001_has_retrieval_expectation(self):
        exp = self._load_retrieval_expectation("eval-incident-001")
        assert exp is not None
        assert exp.case_id == "eval-incident-001"

    def test_edge_001_has_no_retrieval_expectation(self):
        # eval-edge-001 is a thin edge case and intentionally has no retrieval expectation.
        exp = self._load_retrieval_expectation("eval-edge-001")
        assert exp is None

    def test_empty_source_labels_policy_is_not_applicable(self):
        # Cases with expected_source_labels=[] must score 1.0 (N/A), not 0.0.
        exp = self._load_retrieval_expectation("eval-fda-001")
        assert exp is not None
        assert exp.expected_source_labels == []
        candidate = _make_candidate(chunks=[_make_chunk("c1", text="quality system device")])
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_SOURCE_LABELS).score == 1.0
        assert "not applicable" in result.get(DIM_SOURCE_LABELS).rationale

    def test_strong_candidate_passes_fda_001_expectation(self):
        exp = self._load_retrieval_expectation("eval-fda-001")
        assert exp is not None
        chunks = [
            _make_chunk(
                "c1",
                text="The quality system corrective action was required for the device.",
                source_label="FDA Warning Letter",
            )
        ]
        candidate = _make_candidate(chunks=chunks)
        result = score_retrieval(candidate, exp)
        assert result.overall_score == 1.0
        assert result.pass_fail is True

    def test_empty_retrieval_fails_any_expectation_with_minimum_chunk_requirement(self):
        exp = self._load_retrieval_expectation("eval-cisa-001")
        assert exp is not None
        candidate = _make_candidate(chunks=[], retrieval_status="empty")
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_CHUNKS).passed is False
        assert result.overall_score < 1.0

    def test_scoring_does_not_fail_when_required_terms_absent_from_expectation(self):
        # If required_evidence_terms is [], score must be 1.0 (N/A), no exception.
        exp = _make_expectation(required_evidence_terms=[])
        candidate = _make_candidate()
        result = score_retrieval(candidate, exp)
        assert result.get(DIM_EVIDENCE_TERMS).score == 1.0
