"""
G-0 retrieval quality scorer.

Scores one candidate RetrievalResult against one RetrievalExpectation using
three deterministic, offline metrics.  No live AWS calls are made.

Metrics:
  minimum_chunks_match          — 1.0 if candidate chunk count >= expected minimum; else 0.0
  source_label_hit_rate         — fraction of expected source labels found in candidate chunks
                                  (case-insensitive exact match after normalization);
                                  1.0 if no expected labels are defined (not applicable)
  required_evidence_term_coverage — fraction of required evidence terms found as substrings
                                    across the concatenated chunk text (case-insensitive);
                                    1.0 if no terms are defined (not applicable)

Overall retrieval score is the mean of the three metric scores.

Public surface:
  RetrievalScoringResult  — returned by score_retrieval(); typed, frozen dataclass
  score_retrieval(candidate, expectation) — score one retrieval against one expectation
  RETRIEVAL_PASS_THRESHOLD — default overall-score threshold for pass/fail (0.75)

Dimension name constants:
  DIM_CHUNKS         — "minimum_chunks_match"
  DIM_SOURCE_LABELS  — "source_label_hit_rate"
  DIM_EVIDENCE_TERMS — "required_evidence_term_coverage"
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.evaluation_models import DimensionScore, RetrievalExpectation
from app.schemas.retrieval_models import RetrievalResult

# Default overall-score threshold for pass/fail.
RETRIEVAL_PASS_THRESHOLD: float = 0.75

# Stable metric name identifiers referenced by tests.
DIM_CHUNKS = "minimum_chunks_match"
DIM_SOURCE_LABELS = "source_label_hit_rate"
DIM_EVIDENCE_TERMS = "required_evidence_term_coverage"


def _normalize_label(label: str) -> str:
    """Lowercase + strip whitespace for source label comparison."""
    return label.strip().lower()


def _text_contains(haystack: str, needle: str) -> bool:
    """Case-insensitive substring search."""
    return needle.lower() in haystack.lower()


def _candidate_full_text(candidate: RetrievalResult) -> str:
    """Concatenate all chunk text fields for evidence-term searching."""
    return " ".join(chunk.text for chunk in candidate.evidence_chunks)


def _score_minimum_chunks(
    candidate: RetrievalResult, expectation: RetrievalExpectation
) -> DimensionScore:
    count = candidate.retrieved_count
    minimum = expectation.minimum_expected_chunks
    passed = count >= minimum
    return DimensionScore(
        metric_name=DIM_CHUNKS,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=f"retrieved={count} minimum_expected={minimum}",
    )


def _score_source_label_hit_rate(
    candidate: RetrievalResult, expectation: RetrievalExpectation
) -> DimensionScore:
    expected_labels = expectation.expected_source_labels
    if not expected_labels:
        return DimensionScore(
            metric_name=DIM_SOURCE_LABELS,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no expected source labels defined",
        )
    candidate_labels = {
        _normalize_label(chunk.source_label)
        for chunk in candidate.evidence_chunks
    }
    matched = sum(
        1
        for label in expected_labels
        if _normalize_label(label) in candidate_labels
    )
    score = matched / len(expected_labels)
    return DimensionScore(
        metric_name=DIM_SOURCE_LABELS,
        score=score,
        max_score=1.0,
        passed=score >= 1.0,
        rationale=f"matched {matched}/{len(expected_labels)} expected source labels",
    )


def _score_required_evidence_terms(
    candidate: RetrievalResult, expectation: RetrievalExpectation
) -> DimensionScore:
    terms = expectation.required_evidence_terms
    if not terms:
        return DimensionScore(
            metric_name=DIM_EVIDENCE_TERMS,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no required evidence terms defined",
        )
    full_text = _candidate_full_text(candidate)
    matched = sum(1 for term in terms if _text_contains(full_text, term))
    score = matched / len(terms)
    return DimensionScore(
        metric_name=DIM_EVIDENCE_TERMS,
        score=score,
        max_score=1.0,
        passed=score >= 1.0,
        rationale=f"matched {matched}/{len(terms)} required evidence terms",
    )


@dataclass(frozen=True)
class RetrievalScoringResult:
    """
    Output of score_retrieval() for one candidate/expectation pair.

    dimension_scores   — one DimensionScore per retrieval metric, in fixed order.
    overall_score      — mean of all three dimension scores (normalized to [0.0, 1.0]).
    pass_fail          — True when overall_score >= pass_threshold.
    pass_threshold     — threshold used for this result.
    candidate_chunk_count — number of chunks in the evaluated candidate.
    notes              — optional free-text observation from the scorer.
    """

    dimension_scores: tuple[DimensionScore, ...]
    overall_score: float
    pass_fail: bool
    pass_threshold: float = field(default=RETRIEVAL_PASS_THRESHOLD)
    candidate_chunk_count: int = 0
    notes: str | None = None

    def get(self, metric_name: str) -> DimensionScore | None:
        """Return the DimensionScore for the given metric_name, or None."""
        for ds in self.dimension_scores:
            if ds.metric_name == metric_name:
                return ds
        return None


def score_retrieval(
    candidate: RetrievalResult,
    expectation: RetrievalExpectation,
    pass_threshold: float = RETRIEVAL_PASS_THRESHOLD,
    notes: str | None = None,
) -> RetrievalScoringResult:
    """
    Score one candidate RetrievalResult against one RetrievalExpectation.

    Returns a RetrievalScoringResult with per-dimension scores, an overall
    normalized score, and a pass/fail determination using pass_threshold.
    No live AWS calls are made.
    """
    dims: list[DimensionScore] = [
        _score_minimum_chunks(candidate, expectation),
        _score_source_label_hit_rate(candidate, expectation),
        _score_required_evidence_terms(candidate, expectation),
    ]

    overall = sum(d.score for d in dims) / len(dims)

    return RetrievalScoringResult(
        dimension_scores=tuple(dims),
        overall_score=round(overall, 6),
        pass_fail=overall >= pass_threshold,
        pass_threshold=pass_threshold,
        candidate_chunk_count=candidate.retrieved_count,
        notes=notes,
    )
