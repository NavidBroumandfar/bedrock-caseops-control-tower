"""
G-2 output quality scorer — composite final-output quality evaluation.

Answers: "How good is this final CaseOutput as an auditable, usable output artifact?"

This scorer is a composition layer.  It reuses:
  - F-2 score_case()       for core case alignment (severity, escalation, summary, etc.)
  - G-1 score_citations()  for citation quality (presence, labels, excerpts)

It then adds three deterministic final-output-only checks that are genuinely about
the CaseOutput as delivered to a reviewer, not about retrieval or intermediate scoring:
  - summary_nonempty                      — output has a non-empty summary
  - recommendations_present_when_expected — recommendations exist when the expected output
                                            defines recommendation keywords
  - unsupported_claims_clean              — unsupported_claims list is empty

The overall score is the mean of all five component scores.

Pass/fail rule (see OUTPUT_QUALITY_PASS_THRESHOLD):
  summary_nonempty must pass (hard gate)
  AND unsupported_claims_clean must pass (hard gate)
  AND overall_score >= pass_threshold

Public surface:
  score_output_quality(candidate, expected, expectation, ...)
      — score one CaseOutput against one ExpectedOutput and one CitationExpectation
  OUTPUT_QUALITY_PASS_THRESHOLD — default threshold (0.75)

Dimension name constants:
  DIM_SUMMARY_NONEMPTY            — "summary_nonempty"
  DIM_RECS_WHEN_EXPECTED          — "recommendations_present_when_expected"
  DIM_UNSUPPORTED_CLAIMS_CLEAN    — "unsupported_claims_clean"

Separation rules:
  - This module depends on scorer.py (F-2) and citation_scorer.py (G-1).
  - This module does NOT import retrieval_scorer.py (G-0).
  - Retrieval quality remains a separate evaluation layer.
"""

from __future__ import annotations

from app.evaluation.citation_scorer import (
    CITATION_PASS_THRESHOLD,
    CitationScoringResult,
    score_citations,
)
from app.evaluation.scorer import PASS_THRESHOLD, ScoringResult, score_case
from app.schemas.evaluation_models import (
    CitationExpectation,
    DimensionScore,
    ExpectedOutput,
    OutputQualityScoringResult,
)
from app.schemas.output_models import CaseOutput

# Default overall-score threshold below which a case is marked as failed.
OUTPUT_QUALITY_PASS_THRESHOLD: float = 0.75

# Stable metric name identifiers referenced by tests.
DIM_SUMMARY_NONEMPTY = "summary_nonempty"
DIM_RECS_WHEN_EXPECTED = "recommendations_present_when_expected"
DIM_UNSUPPORTED_CLAIMS_CLEAN = "unsupported_claims_clean"

# Hard-gate dimensions: all must individually pass for the overall result to pass.
_HARD_GATE_DIMS = {DIM_SUMMARY_NONEMPTY, DIM_UNSUPPORTED_CLAIMS_CLEAN}


# ── Final-output-only checks ───────────────────────────────────────────────────


def _score_summary_nonempty(candidate: CaseOutput) -> DimensionScore:
    """1.0 if summary.strip() is non-empty; else 0.0."""
    passed = bool(candidate.summary.strip())
    return DimensionScore(
        metric_name=DIM_SUMMARY_NONEMPTY,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            "summary is non-empty"
            if passed
            else "summary is empty or whitespace-only"
        ),
    )


def _score_recommendations_when_expected(
    candidate: CaseOutput, expected: ExpectedOutput
) -> DimensionScore:
    """
    1.0 if:
      - The expected output defines recommendation keywords AND candidate has at least
        one non-empty recommendation.
      - OR the expected output defines no recommendation keywords (not applicable).
    0.0 if:
      - The expected output defines recommendation keywords AND candidate has no
        non-empty recommendations.
    """
    keywords_defined = bool(expected.expected_recommendation_keywords)

    if not keywords_defined:
        # No recommendation expectation — treat as not applicable, always pass.
        return DimensionScore(
            metric_name=DIM_RECS_WHEN_EXPECTED,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no recommendation keywords specified in expected output",
        )

    has_recommendations = any(r.strip() for r in candidate.recommendations)
    passed = has_recommendations
    return DimensionScore(
        metric_name=DIM_RECS_WHEN_EXPECTED,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            f"at least one non-empty recommendation present (count={len(candidate.recommendations)})"
            if passed
            else "no non-empty recommendations found; expected output defines recommendation keywords"
        ),
    )


def _score_unsupported_claims_clean(candidate: CaseOutput) -> DimensionScore:
    """1.0 if unsupported_claims is empty; 0.0 if any unsupported claims are present."""
    passed = len(candidate.unsupported_claims) == 0
    return DimensionScore(
        metric_name=DIM_UNSUPPORTED_CLAIMS_CLEAN,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            "no unsupported claims"
            if passed
            else f"unsupported_claims present: {candidate.unsupported_claims}"
        ),
    )


# ── Public scorer ──────────────────────────────────────────────────────────────


def score_output_quality(
    candidate: CaseOutput,
    expected: ExpectedOutput,
    citation_expectation: CitationExpectation,
    pass_threshold: float = OUTPUT_QUALITY_PASS_THRESHOLD,
    notes: str | None = None,
) -> OutputQualityScoringResult:
    """
    Score one candidate CaseOutput as a final output artifact.

    Composes:
      1. F-2 core case alignment score  (reused from scorer.score_case)
      2. G-1 citation quality score     (reused from citation_scorer.score_citations)
      3. summary_nonempty               (final-output-only check)
      4. recommendations_present_when_expected  (final-output-only check)
      5. unsupported_claims_clean       (final-output-only check)

    Overall score = mean of all five component scores.

    Pass/fail:
      summary_nonempty must pass (hard gate)
      AND unsupported_claims_clean must pass (hard gate)
      AND overall_score >= pass_threshold

    No live AWS calls are made.  Fully deterministic.
    """
    # ── Reused sub-scores ──────────────────────────────────────────────────────
    core_result: ScoringResult = score_case(candidate, expected)
    citation_result: CitationScoringResult = score_citations(
        candidate, citation_expectation
    )

    # ── Final-output-only checks ───────────────────────────────────────────────
    dim_summary = _score_summary_nonempty(candidate)
    dim_recs = _score_recommendations_when_expected(candidate, expected)
    dim_claims = _score_unsupported_claims_clean(candidate)

    final_output_dims = [dim_summary, dim_recs, dim_claims]

    # ── Aggregate overall score ────────────────────────────────────────────────
    # Five equal-weight components: core alignment, citation quality, + 3 final checks.
    all_scores = [
        core_result.overall_score,
        citation_result.overall_score,
        dim_summary.score,
        dim_recs.score,
        dim_claims.score,
    ]
    overall = sum(all_scores) / len(all_scores)

    # ── Pass/fail ─────────────────────────────────────────────────────────────
    hard_gates_passed = all(
        d.passed for d in final_output_dims if d.metric_name in _HARD_GATE_DIMS
    )
    pass_fail = hard_gates_passed and overall >= pass_threshold

    return OutputQualityScoringResult(
        core_case_alignment_score=core_result.overall_score,
        citation_quality_score=citation_result.overall_score,
        dimension_scores=final_output_dims,
        overall_score=round(overall, 6),
        pass_fail=pass_fail,
        pass_threshold=pass_threshold,
        notes=notes,
    )
