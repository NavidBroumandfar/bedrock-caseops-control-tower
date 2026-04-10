"""
G-1 citation quality scorer.

Scores one candidate CaseOutput against one CitationExpectation using four
deterministic, offline metrics.  No live AWS calls are made.

Metrics:
  citation_presence               — 1.0 if citations are present when required (or not required);
                                    0.0 if citations are absent when required
  citation_source_label_alignment — fraction of expected source labels found in candidate
                                    citation source_label fields (case-insensitive exact match
                                    after normalization); 1.0 (N/A) if none defined
  citation_excerpt_evidence_coverage — fraction of required excerpt terms found as substrings
                                       across concatenated citation excerpts (case-insensitive);
                                       1.0 (N/A) if none defined
  citation_excerpt_nonempty       — 1.0 if all citation excerpts are non-empty / non-whitespace;
                                    0.0 if any excerpt is empty or whitespace-only after stripping;
                                    1.0 when no citations are present and citations were not required

Overall citation score is the mean of the four metric scores.

Pass/fail rule (see CITATION_PASS_THRESHOLD):
  citation_presence must have passed=True
  AND citation_excerpt_nonempty must have passed=True
  AND overall_score >= pass_threshold

Public surface:
  CitationScoringResult   — returned by score_citations(); typed, frozen dataclass
  score_citations(candidate, expectation) — score one output against one expectation
  CITATION_PASS_THRESHOLD — default overall-score threshold for pass/fail (0.75)

Dimension name constants:
  DIM_PRESENCE        — "citation_presence"
  DIM_SOURCE_LABELS   — "citation_source_label_alignment"
  DIM_EXCERPT_TERMS   — "citation_excerpt_evidence_coverage"
  DIM_NONEMPTY        — "citation_excerpt_nonempty"
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.evaluation_models import CitationExpectation, DimensionScore
from app.schemas.output_models import CaseOutput

# Default overall-score threshold for pass/fail.
CITATION_PASS_THRESHOLD: float = 0.75

# Stable metric name identifiers referenced by tests.
DIM_PRESENCE = "citation_presence"
DIM_SOURCE_LABELS = "citation_source_label_alignment"
DIM_EXCERPT_TERMS = "citation_excerpt_evidence_coverage"
DIM_NONEMPTY = "citation_excerpt_nonempty"

# Hard-gate dimensions: both must individually pass for the result to pass.
_HARD_GATE_DIMS = {DIM_PRESENCE, DIM_NONEMPTY}


def _normalize_label(label: str) -> str:
    """Lowercase + strip whitespace for source label comparison."""
    return label.strip().lower()


def _text_contains(haystack: str, needle: str) -> bool:
    """Case-insensitive substring search."""
    return needle.lower() in haystack.lower()


def _excerpts_full_text(candidate: CaseOutput) -> str:
    """Concatenate all citation excerpt fields for term searching."""
    return " ".join(c.excerpt for c in candidate.citations)


def _score_citation_presence(
    candidate: CaseOutput, expectation: CitationExpectation
) -> DimensionScore:
    if not expectation.citations_required:
        # Citations are intentionally absent for this case — always N/A pass.
        return DimensionScore(
            metric_name=DIM_PRESENCE,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — citations not required for this case",
        )
    has_enough = len(candidate.citations) >= expectation.minimum_citation_count
    return DimensionScore(
        metric_name=DIM_PRESENCE,
        score=1.0 if has_enough else 0.0,
        max_score=1.0,
        passed=has_enough,
        rationale=(
            f"citation_count={len(candidate.citations)} "
            f"minimum_required={expectation.minimum_citation_count}"
        ),
    )


def _score_source_label_alignment(
    candidate: CaseOutput, expectation: CitationExpectation
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
        _normalize_label(c.source_label) for c in candidate.citations
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


def _score_excerpt_evidence_coverage(
    candidate: CaseOutput, expectation: CitationExpectation
) -> DimensionScore:
    terms = expectation.required_excerpt_terms
    if not terms:
        return DimensionScore(
            metric_name=DIM_EXCERPT_TERMS,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no required excerpt terms defined",
        )
    full_text = _excerpts_full_text(candidate)
    matched = sum(1 for term in terms if _text_contains(full_text, term))
    score = matched / len(terms)
    return DimensionScore(
        metric_name=DIM_EXCERPT_TERMS,
        score=score,
        max_score=1.0,
        passed=score >= 1.0,
        rationale=f"matched {matched}/{len(terms)} required excerpt terms",
    )


def _score_excerpt_nonempty(
    candidate: CaseOutput, expectation: CitationExpectation
) -> DimensionScore:
    if not candidate.citations:
        # No citations present — pass only if citations were not required.
        passed = not expectation.citations_required
        return DimensionScore(
            metric_name=DIM_NONEMPTY,
            score=1.0 if passed else 0.0,
            max_score=1.0,
            passed=passed,
            rationale=(
                "no citations present — "
                + ("citations not required so nonempty check passes"
                   if passed
                   else "citations required but absent")
            ),
        )
    empty_excerpts = [
        c.source_label for c in candidate.citations if not c.excerpt.strip()
    ]
    passed = len(empty_excerpts) == 0
    return DimensionScore(
        metric_name=DIM_NONEMPTY,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            "all citation excerpts are non-empty"
            if passed
            else f"empty or whitespace excerpts found in: {empty_excerpts}"
        ),
    )


@dataclass(frozen=True)
class CitationScoringResult:
    """
    Output of score_citations() for one candidate/expectation pair.

    dimension_scores        — one DimensionScore per citation metric, in fixed order.
    overall_score           — mean of all four dimension scores (normalized to [0.0, 1.0]).
    pass_fail               — True when hard-gate dims passed AND overall_score >= pass_threshold.
    pass_threshold          — threshold used for this result.
    candidate_citation_count — number of citations in the evaluated candidate.
    notes                   — optional free-text observation from the scorer.
    """

    dimension_scores: tuple[DimensionScore, ...]
    overall_score: float
    pass_fail: bool
    pass_threshold: float = field(default=CITATION_PASS_THRESHOLD)
    candidate_citation_count: int = 0
    notes: str | None = None

    def get(self, metric_name: str) -> DimensionScore | None:
        """Return the DimensionScore for the given metric_name, or None."""
        for ds in self.dimension_scores:
            if ds.metric_name == metric_name:
                return ds
        return None


def score_citations(
    candidate: CaseOutput,
    expectation: CitationExpectation,
    pass_threshold: float = CITATION_PASS_THRESHOLD,
    notes: str | None = None,
) -> CitationScoringResult:
    """
    Score one candidate CaseOutput against one CitationExpectation.

    Returns a CitationScoringResult with per-dimension scores, an overall
    normalized score, and a pass/fail determination using pass_threshold.
    No live AWS calls are made.
    """
    dims: list[DimensionScore] = [
        _score_citation_presence(candidate, expectation),
        _score_source_label_alignment(candidate, expectation),
        _score_excerpt_evidence_coverage(candidate, expectation),
        _score_excerpt_nonempty(candidate, expectation),
    ]

    overall = sum(d.score for d in dims) / len(dims)

    hard_gates_passed = all(
        d.passed for d in dims if d.metric_name in _HARD_GATE_DIMS
    )
    pass_fail = hard_gates_passed and overall >= pass_threshold

    return CitationScoringResult(
        dimension_scores=tuple(dims),
        overall_score=round(overall, 6),
        pass_fail=pass_fail,
        pass_threshold=pass_threshold,
        candidate_citation_count=len(candidate.citations),
        notes=notes,
    )
