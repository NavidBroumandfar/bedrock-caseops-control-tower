"""
F-2 evaluation harness — per-case scorer.

Scores one candidate CaseOutput against one ExpectedOutput using six deterministic
dimensions.  All scoring is local and requires no live AWS calls.

Scoring dimensions:
  severity_match               — exact match: 1.0 / 0.0
  category_match               — normalized exact match: 1.0 / 0.0
  escalation_match             — exact boolean match: 1.0 / 0.0
  summary_fact_coverage        — fraction of expected facts found in summary text
  recommendation_keyword_coverage — fraction of expected keywords found in recommendations
  forbidden_claims_check       — 1.0 if no forbidden claim appears in any output text; else 0.0

Pass/fail rule (see PASS_THRESHOLD):
  True when severity_match, escalation_match, and forbidden_claims_check all passed
  AND overall_score >= PASS_THRESHOLD.

Public surface:
  ScoringResult      — returned by score_case(); contains all DimensionScores + pass/fail
  score_case(candidate, expected) — score one candidate against one reference
  PASS_THRESHOLD     — configurable overall-score threshold for pass/fail (default 0.75)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.evaluation_models import DimensionScore, ExpectedOutput
from app.schemas.output_models import CaseOutput

# Overall-score threshold below which a case is marked as failed.
# Kept as a module-level constant so it can be overridden per-run if needed.
PASS_THRESHOLD: float = 0.75

# Names used for the six scoring dimensions — stable identifiers referenced by tests.
DIM_SEVERITY = "severity_match"
DIM_CATEGORY = "category_match"
DIM_ESCALATION = "escalation_match"
DIM_SUMMARY_FACTS = "summary_fact_coverage"
DIM_KEYWORD_COVERAGE = "recommendation_keyword_coverage"
DIM_FORBIDDEN = "forbidden_claims_check"

# Dimensions that must individually pass for the case to pass (hard gates).
_HARD_GATE_DIMS = {DIM_SEVERITY, DIM_ESCALATION, DIM_FORBIDDEN}


def _normalize_category(value: str) -> str:
    """Lowercase + strip whitespace for category comparison."""
    return value.strip().lower()


def _text_contains(haystack: str, needle: str) -> bool:
    """Case-insensitive substring search."""
    return needle.lower() in haystack.lower()


def _candidate_full_text(candidate: CaseOutput) -> str:
    """Concatenate all candidate text fields for forbidden-claims checking."""
    parts = [candidate.summary] + list(candidate.recommendations)
    if candidate.escalation_reason:
        parts.append(candidate.escalation_reason)
    return " ".join(parts)


def _score_severity(candidate: CaseOutput, expected: ExpectedOutput) -> DimensionScore:
    passed = candidate.severity == expected.expected_severity
    return DimensionScore(
        metric_name=DIM_SEVERITY,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            f"candidate={candidate.severity!r} expected={expected.expected_severity!r}"
        ),
    )


def _score_category(candidate: CaseOutput, expected: ExpectedOutput) -> DimensionScore:
    passed = _normalize_category(candidate.category) == _normalize_category(
        expected.expected_category
    )
    return DimensionScore(
        metric_name=DIM_CATEGORY,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            f"candidate={candidate.category!r} expected={expected.expected_category!r}"
        ),
    )


def _score_escalation(
    candidate: CaseOutput, expected: ExpectedOutput
) -> DimensionScore:
    passed = candidate.escalation_required == expected.expected_escalation_required
    return DimensionScore(
        metric_name=DIM_ESCALATION,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            f"candidate={candidate.escalation_required!r} "
            f"expected={expected.expected_escalation_required!r}"
        ),
    )


def _score_summary_facts(
    candidate: CaseOutput, expected: ExpectedOutput
) -> DimensionScore:
    facts = expected.expected_summary_facts
    if not facts:
        # Not applicable — treat as full score.
        return DimensionScore(
            metric_name=DIM_SUMMARY_FACTS,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no expected facts specified",
        )
    matched = sum(
        1 for fact in facts if _text_contains(candidate.summary, fact)
    )
    score = matched / len(facts)
    return DimensionScore(
        metric_name=DIM_SUMMARY_FACTS,
        score=score,
        max_score=1.0,
        passed=score >= 1.0,
        rationale=f"matched {matched}/{len(facts)} expected facts",
    )


def _score_recommendation_keywords(
    candidate: CaseOutput, expected: ExpectedOutput
) -> DimensionScore:
    keywords = expected.expected_recommendation_keywords
    if not keywords:
        return DimensionScore(
            metric_name=DIM_KEYWORD_COVERAGE,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no expected keywords specified",
        )
    rec_text = " ".join(candidate.recommendations)
    matched = sum(1 for kw in keywords if _text_contains(rec_text, kw))
    score = matched / len(keywords)
    return DimensionScore(
        metric_name=DIM_KEYWORD_COVERAGE,
        score=score,
        max_score=1.0,
        passed=score >= 1.0,
        rationale=f"matched {matched}/{len(keywords)} expected keywords",
    )


def _score_forbidden_claims(
    candidate: CaseOutput, expected: ExpectedOutput
) -> DimensionScore:
    forbidden = expected.forbidden_claims
    if not forbidden:
        return DimensionScore(
            metric_name=DIM_FORBIDDEN,
            score=1.0,
            max_score=1.0,
            passed=True,
            rationale="not applicable — no forbidden claims specified",
        )
    full_text = _candidate_full_text(candidate)
    violated = [claim for claim in forbidden if _text_contains(full_text, claim)]
    passed = len(violated) == 0
    return DimensionScore(
        metric_name=DIM_FORBIDDEN,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        passed=passed,
        rationale=(
            "no forbidden claims found"
            if passed
            else f"forbidden claims detected: {violated}"
        ),
    )


@dataclass(frozen=True)
class ScoringResult:
    """
    Output of score_case() for one candidate/expected pair.

    dimension_scores  — one DimensionScore per scoring dimension, in fixed order.
    overall_score     — mean of all dimension scores (normalized to [0.0, 1.0]).
    pass_fail         — True when all hard-gate dimensions passed AND overall_score
                        >= pass_threshold.
    pass_threshold    — the threshold used for this result.
    """

    dimension_scores: tuple[DimensionScore, ...]
    overall_score: float
    pass_fail: bool
    pass_threshold: float = field(default=PASS_THRESHOLD)

    def get(self, metric_name: str) -> DimensionScore | None:
        """Return the DimensionScore for the given metric_name, or None."""
        for ds in self.dimension_scores:
            if ds.metric_name == metric_name:
                return ds
        return None


def score_case(
    candidate: CaseOutput,
    expected: ExpectedOutput,
    pass_threshold: float = PASS_THRESHOLD,
) -> ScoringResult:
    """
    Score one candidate CaseOutput against one ExpectedOutput.

    Returns a ScoringResult with per-dimension scores, an overall normalized score,
    and a pass/fail determination using pass_threshold.
    """
    dims: list[DimensionScore] = [
        _score_severity(candidate, expected),
        _score_category(candidate, expected),
        _score_escalation(candidate, expected),
        _score_summary_facts(candidate, expected),
        _score_recommendation_keywords(candidate, expected),
        _score_forbidden_claims(candidate, expected),
    ]

    overall = sum(d.score for d in dims) / len(dims)

    hard_gates_passed = all(
        d.passed for d in dims if d.metric_name in _HARD_GATE_DIMS
    )
    pass_fail = hard_gates_passed and overall >= pass_threshold

    return ScoringResult(
        dimension_scores=tuple(dims),
        overall_score=round(overall, 6),
        pass_fail=pass_fail,
        pass_threshold=pass_threshold,
    )
