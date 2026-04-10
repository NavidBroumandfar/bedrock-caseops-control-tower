"""
Tests for Phase I-2 comparison model contracts.

Covers:
  - ComparisonVerdict type alias values
  - ComparisonCaseResult field types, field values, and immutability
  - ComparisonSummary aggregate field types and immutability
  - ComparisonRunResult container field types and immutability
  - Edge cases: empty results, zero-case summaries
  - No live AWS dependency
"""

from __future__ import annotations

import pytest

from app.evaluation.comparison_runner import (
    COMPARISON_DELTA_EPSILON,
    ComparisonCaseResult,
    ComparisonRunResult,
    ComparisonSummary,
)
from app.schemas.evaluation_models import ComparisonVerdict


# ── ComparisonVerdict ──────────────────────────────────────────────────────────


class TestComparisonVerdictType:
    def test_verdict_improved_value(self):
        verdict: ComparisonVerdict = "improved"
        assert verdict == "improved"

    def test_verdict_regressed_value(self):
        verdict: ComparisonVerdict = "regressed"
        assert verdict == "regressed"

    def test_verdict_unchanged_value(self):
        verdict: ComparisonVerdict = "unchanged"
        assert verdict == "unchanged"

    def test_verdict_is_string(self):
        assert isinstance("improved", str)
        assert isinstance("regressed", str)
        assert isinstance("unchanged", str)


# ── COMPARISON_DELTA_EPSILON ───────────────────────────────────────────────────


class TestComparisonDeltaEpsilon:
    def test_epsilon_is_positive_float(self):
        assert isinstance(COMPARISON_DELTA_EPSILON, float)
        assert COMPARISON_DELTA_EPSILON > 0.0

    def test_epsilon_is_small(self):
        # Epsilon should be small enough not to swallow real improvements.
        assert COMPARISON_DELTA_EPSILON < 0.1


# ── ComparisonCaseResult ───────────────────────────────────────────────────────


def _make_case_result(
    case_id: str = "test-case",
    baseline_score: float = 0.8,
    optimized_score: float = 0.9,
    score_delta: float = 0.1,
    baseline_pass: bool = True,
    optimized_pass: bool = True,
    baseline_safety_status: str = "allow",
    optimized_safety_status: str = "allow",
    safety_status_changed: bool = False,
    verdict: ComparisonVerdict = "improved",
) -> ComparisonCaseResult:
    return ComparisonCaseResult(
        case_id=case_id,
        baseline_score=baseline_score,
        optimized_score=optimized_score,
        score_delta=score_delta,
        baseline_pass=baseline_pass,
        optimized_pass=optimized_pass,
        baseline_safety_status=baseline_safety_status,
        optimized_safety_status=optimized_safety_status,
        safety_status_changed=safety_status_changed,
        verdict=verdict,
    )


class TestComparisonCaseResult:
    def test_fields_are_set_correctly(self):
        r = _make_case_result()
        assert r.case_id == "test-case"
        assert r.baseline_score == 0.8
        assert r.optimized_score == 0.9
        assert r.score_delta == 0.1
        assert r.baseline_pass is True
        assert r.optimized_pass is True
        assert r.baseline_safety_status == "allow"
        assert r.optimized_safety_status == "allow"
        assert r.safety_status_changed is False
        assert r.verdict == "improved"

    def test_is_frozen_immutable(self):
        r = _make_case_result()
        with pytest.raises((AttributeError, TypeError)):
            r.case_id = "other"  # type: ignore[misc]

    def test_verdict_improved(self):
        r = _make_case_result(verdict="improved")
        assert r.verdict == "improved"

    def test_verdict_regressed(self):
        r = _make_case_result(verdict="regressed", score_delta=-0.1)
        assert r.verdict == "regressed"

    def test_verdict_unchanged(self):
        r = _make_case_result(verdict="unchanged", score_delta=0.0)
        assert r.verdict == "unchanged"

    def test_safety_status_changed_true(self):
        r = _make_case_result(
            baseline_safety_status="escalate",
            optimized_safety_status="allow",
            safety_status_changed=True,
        )
        assert r.safety_status_changed is True
        assert r.baseline_safety_status != r.optimized_safety_status

    def test_safety_status_changed_false_when_same(self):
        r = _make_case_result(
            baseline_safety_status="allow",
            optimized_safety_status="allow",
            safety_status_changed=False,
        )
        assert r.safety_status_changed is False

    def test_baseline_and_optimized_can_differ(self):
        r = _make_case_result(
            baseline_score=0.5,
            optimized_score=1.0,
            score_delta=0.5,
            baseline_pass=False,
            optimized_pass=True,
        )
        assert r.baseline_score < r.optimized_score
        assert not r.baseline_pass
        assert r.optimized_pass

    def test_negative_delta_for_regression(self):
        r = _make_case_result(
            baseline_score=1.0,
            optimized_score=0.7,
            score_delta=-0.3,
            verdict="regressed",
        )
        assert r.score_delta < 0.0

    def test_case_id_is_string(self):
        r = _make_case_result(case_id="cmp-001")
        assert isinstance(r.case_id, str)


# ── ComparisonSummary ──────────────────────────────────────────────────────────


def _make_summary(
    total_cases: int = 3,
    baseline_average_score: float = 0.8,
    optimized_average_score: float = 0.9,
    average_score_delta: float = 0.1,
    baseline_pass_count: int = 2,
    optimized_pass_count: int = 3,
    baseline_safety_distribution: dict | None = None,
    optimized_safety_distribution: dict | None = None,
    improved_case_ids: tuple = ("cmp-001",),
    regressed_case_ids: tuple = (),
    unchanged_case_ids: tuple = ("cmp-002", "cmp-003"),
) -> ComparisonSummary:
    return ComparisonSummary(
        total_cases=total_cases,
        baseline_average_score=baseline_average_score,
        optimized_average_score=optimized_average_score,
        average_score_delta=average_score_delta,
        baseline_pass_count=baseline_pass_count,
        optimized_pass_count=optimized_pass_count,
        baseline_safety_distribution=baseline_safety_distribution or {"allow": 2, "escalate": 1},
        optimized_safety_distribution=optimized_safety_distribution or {"allow": 3},
        improved_case_ids=improved_case_ids,
        regressed_case_ids=regressed_case_ids,
        unchanged_case_ids=unchanged_case_ids,
    )


class TestComparisonSummary:
    def test_fields_are_set_correctly(self):
        s = _make_summary()
        assert s.total_cases == 3
        assert s.baseline_average_score == 0.8
        assert s.optimized_average_score == 0.9
        assert s.average_score_delta == 0.1
        assert s.baseline_pass_count == 2
        assert s.optimized_pass_count == 3

    def test_is_frozen_immutable(self):
        s = _make_summary()
        with pytest.raises((AttributeError, TypeError)):
            s.total_cases = 99  # type: ignore[misc]

    def test_improved_case_ids_tuple(self):
        s = _make_summary(improved_case_ids=("cmp-001", "cmp-002"))
        assert isinstance(s.improved_case_ids, tuple)
        assert len(s.improved_case_ids) == 2

    def test_regressed_case_ids_tuple(self):
        s = _make_summary(regressed_case_ids=("cmp-003",))
        assert isinstance(s.regressed_case_ids, tuple)
        assert "cmp-003" in s.regressed_case_ids

    def test_unchanged_case_ids_tuple(self):
        s = _make_summary(unchanged_case_ids=("cmp-004",))
        assert isinstance(s.unchanged_case_ids, tuple)

    def test_safety_distribution_is_dict(self):
        s = _make_summary()
        assert isinstance(s.baseline_safety_distribution, dict)
        assert isinstance(s.optimized_safety_distribution, dict)

    def test_empty_summary_zero_cases(self):
        s = ComparisonSummary(
            total_cases=0,
            baseline_average_score=0.0,
            optimized_average_score=0.0,
            average_score_delta=0.0,
            baseline_pass_count=0,
            optimized_pass_count=0,
            baseline_safety_distribution={},
            optimized_safety_distribution={},
            improved_case_ids=(),
            regressed_case_ids=(),
            unchanged_case_ids=(),
        )
        assert s.total_cases == 0
        assert s.improved_case_ids == ()
        assert s.regressed_case_ids == ()
        assert s.unchanged_case_ids == ()

    def test_all_verdict_buckets_present(self):
        s = _make_summary(
            improved_case_ids=("cmp-001",),
            regressed_case_ids=("cmp-002",),
            unchanged_case_ids=("cmp-003",),
        )
        assert len(s.improved_case_ids) == 1
        assert len(s.regressed_case_ids) == 1
        assert len(s.unchanged_case_ids) == 1

    def test_negative_average_score_delta(self):
        s = _make_summary(
            baseline_average_score=0.9,
            optimized_average_score=0.7,
            average_score_delta=-0.2,
        )
        assert s.average_score_delta < 0.0

    def test_pass_count_can_increase(self):
        s = _make_summary(baseline_pass_count=1, optimized_pass_count=3)
        assert s.optimized_pass_count > s.baseline_pass_count


# ── ComparisonRunResult ────────────────────────────────────────────────────────


def _make_run_result(
    case_results: tuple = (),
    summary: ComparisonSummary | None = None,
    missing_baseline: tuple = (),
    missing_optimized: tuple = (),
) -> ComparisonRunResult:
    if summary is None:
        summary = ComparisonSummary(
            total_cases=0,
            baseline_average_score=0.0,
            optimized_average_score=0.0,
            average_score_delta=0.0,
            baseline_pass_count=0,
            optimized_pass_count=0,
            baseline_safety_distribution={},
            optimized_safety_distribution={},
            improved_case_ids=(),
            regressed_case_ids=(),
            unchanged_case_ids=(),
        )
    return ComparisonRunResult(
        case_results=case_results,
        summary=summary,
        missing_baseline_case_ids=missing_baseline,
        missing_optimized_case_ids=missing_optimized,
    )


class TestComparisonRunResult:
    def test_fields_are_set_correctly(self):
        cr = _make_case_result()
        rr = _make_run_result(case_results=(cr,))
        assert len(rr.case_results) == 1
        assert rr.case_results[0] is cr

    def test_is_frozen_immutable(self):
        rr = _make_run_result()
        with pytest.raises((AttributeError, TypeError)):
            rr.case_results = ()  # type: ignore[misc]

    def test_case_results_is_tuple(self):
        rr = _make_run_result()
        assert isinstance(rr.case_results, tuple)

    def test_missing_baseline_case_ids_is_tuple(self):
        rr = _make_run_result(missing_baseline=("cmp-005",))
        assert isinstance(rr.missing_baseline_case_ids, tuple)
        assert "cmp-005" in rr.missing_baseline_case_ids

    def test_missing_optimized_case_ids_is_tuple(self):
        rr = _make_run_result(missing_optimized=("cmp-006",))
        assert isinstance(rr.missing_optimized_case_ids, tuple)
        assert "cmp-006" in rr.missing_optimized_case_ids

    def test_summary_is_comparison_summary(self):
        rr = _make_run_result()
        assert isinstance(rr.summary, ComparisonSummary)

    def test_empty_run_result(self):
        rr = _make_run_result()
        assert rr.case_results == ()
        assert rr.missing_baseline_case_ids == ()
        assert rr.missing_optimized_case_ids == ()
        assert rr.summary.total_cases == 0

    def test_multiple_case_results(self):
        r1 = _make_case_result(case_id="cmp-001", verdict="improved")
        r2 = _make_case_result(case_id="cmp-002", verdict="unchanged")
        r3 = _make_case_result(case_id="cmp-003", verdict="regressed")
        rr = _make_run_result(case_results=(r1, r2, r3))
        assert len(rr.case_results) == 3
        verdicts = {r.verdict for r in rr.case_results}
        assert verdicts == {"improved", "unchanged", "regressed"}

    def test_missing_both_directions_independent(self):
        rr = _make_run_result(
            missing_baseline=("case-A",),
            missing_optimized=("case-B",),
        )
        assert "case-A" in rr.missing_baseline_case_ids
        assert "case-B" in rr.missing_optimized_case_ids
        assert "case-A" not in rr.missing_optimized_case_ids
        assert "case-B" not in rr.missing_baseline_case_ids
