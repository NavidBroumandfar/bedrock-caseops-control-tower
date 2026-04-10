"""
Tests for evaluation metrics translator functions — J-0.

Covers:
  - evaluation_run_summary_to_metrics: datum count, metric names, values, units, namespace, dimensions
  - comparison_summary_to_metrics: datum count, metric names, values, units, negative delta
  - safety_distribution_to_metrics: all four statuses, partial distributions, zero-fill, empty input
  - All datums carry the correct namespace from config
  - Environment dimension applied correctly
  - No live AWS calls
"""

import pytest

from app.evaluation.comparison_runner import ComparisonSummary
from app.evaluation.metrics_translator import (
    METRIC_CMP_AVG_SCORE_DELTA,
    METRIC_CMP_BASELINE_PASS_COUNT,
    METRIC_CMP_IMPROVED_COUNT,
    METRIC_CMP_OPTIMIZED_PASS_COUNT,
    METRIC_CMP_REGRESSED_COUNT,
    METRIC_CMP_UNCHANGED_COUNT,
    METRIC_EVAL_AVERAGE_SCORE,
    METRIC_EVAL_FAIL_COUNT,
    METRIC_EVAL_PASS_COUNT,
    METRIC_EVAL_TOTAL_CASES,
    METRIC_SAFETY_ALLOW,
    METRIC_SAFETY_BLOCK,
    METRIC_SAFETY_ESCALATE,
    METRIC_SAFETY_WARN,
    comparison_summary_to_metrics,
    evaluation_run_summary_to_metrics,
    safety_distribution_to_metrics,
)
from app.schemas.evaluation_models import EvaluationMetricDatum, EvaluationRunSummary
from app.utils.config import EvaluationDashboardConfig

from datetime import datetime, timezone


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_config(
    namespace: str = "CaseOps/Evaluation",
    environment: str = "test",
) -> EvaluationDashboardConfig:
    return EvaluationDashboardConfig(
        enable_evaluation_metrics=True,
        metrics_namespace=namespace,
        dashboard_name="TestDashboard",
        environment=environment,
        aws_region="us-east-1",
    )


def _make_eval_summary(
    *,
    run_id: str = "run-001",
    total_cases: int = 7,
    passed_cases: int = 5,
    failed_cases: int = 2,
    average_score: float = 0.82,
) -> EvaluationRunSummary:
    return EvaluationRunSummary(
        run_id=run_id,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        average_score=average_score,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _make_comparison_summary(
    *,
    total_cases: int = 4,
    baseline_average_score: float = 0.70,
    optimized_average_score: float = 0.80,
    average_score_delta: float = 0.10,
    baseline_pass_count: int = 2,
    optimized_pass_count: int = 3,
    baseline_safety_distribution: dict | None = None,
    optimized_safety_distribution: dict | None = None,
    improved_case_ids: tuple = ("cmp-001",),
    regressed_case_ids: tuple = ("cmp-003",),
    unchanged_case_ids: tuple = ("cmp-002", "cmp-004"),
) -> ComparisonSummary:
    return ComparisonSummary(
        total_cases=total_cases,
        baseline_average_score=baseline_average_score,
        optimized_average_score=optimized_average_score,
        average_score_delta=average_score_delta,
        baseline_pass_count=baseline_pass_count,
        optimized_pass_count=optimized_pass_count,
        baseline_safety_distribution=baseline_safety_distribution or {"allow": 2, "escalate": 2},
        optimized_safety_distribution=optimized_safety_distribution or {"allow": 3, "escalate": 1},
        improved_case_ids=improved_case_ids,
        regressed_case_ids=regressed_case_ids,
        unchanged_case_ids=unchanged_case_ids,
    )


def _datum_by_name(
    datums: list[EvaluationMetricDatum], metric_name: str
) -> EvaluationMetricDatum:
    for d in datums:
        if d.metric_name == metric_name:
            return d
    raise KeyError(f"No datum with metric_name={metric_name!r}")


# ── evaluation_run_summary_to_metrics tests ───────────────────────────────────


def test_eval_summary_produces_four_datums():
    summary = _make_eval_summary()
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    assert len(datums) == 4


def test_eval_summary_contains_pass_count():
    summary = _make_eval_summary(passed_cases=5)
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_EVAL_PASS_COUNT)
    assert d.value == 5.0


def test_eval_summary_contains_fail_count():
    summary = _make_eval_summary(failed_cases=2)
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_EVAL_FAIL_COUNT)
    assert d.value == 2.0


def test_eval_summary_contains_total_cases():
    summary = _make_eval_summary(total_cases=7)
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_EVAL_TOTAL_CASES)
    assert d.value == 7.0


def test_eval_summary_contains_average_score():
    summary = _make_eval_summary(average_score=0.82)
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_EVAL_AVERAGE_SCORE)
    assert d.value == pytest.approx(0.82)


def test_eval_summary_count_datums_use_count_unit():
    summary = _make_eval_summary()
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    count_metrics = {METRIC_EVAL_PASS_COUNT, METRIC_EVAL_FAIL_COUNT, METRIC_EVAL_TOTAL_CASES}
    for d in datums:
        if d.metric_name in count_metrics:
            assert d.unit == "Count"


def test_eval_summary_average_score_uses_none_unit():
    summary = _make_eval_summary()
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_EVAL_AVERAGE_SCORE)
    assert d.unit == "None"


def test_eval_summary_datums_have_correct_namespace():
    summary = _make_eval_summary()
    config = _make_config(namespace="MyOrg/Eval")
    datums = evaluation_run_summary_to_metrics(summary, config)
    for d in datums:
        assert d.namespace == "MyOrg/Eval"


def test_eval_summary_datums_have_environment_dimension():
    summary = _make_eval_summary()
    config = _make_config(environment="staging")
    datums = evaluation_run_summary_to_metrics(summary, config)
    for d in datums:
        assert d.dimensions.get("Environment") == "staging"


def test_eval_summary_returns_list_of_metric_datums():
    summary = _make_eval_summary()
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    assert all(isinstance(d, EvaluationMetricDatum) for d in datums)


def test_eval_summary_zero_cases():
    summary = EvaluationRunSummary(
        run_id="empty-run",
        total_cases=0,
        passed_cases=0,
        failed_cases=0,
        average_score=0.0,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    config = _make_config()
    datums = evaluation_run_summary_to_metrics(summary, config)
    assert len(datums) == 4
    d = _datum_by_name(datums, METRIC_EVAL_PASS_COUNT)
    assert d.value == 0.0


# ── comparison_summary_to_metrics tests ──────────────────────────────────────


def test_comparison_summary_produces_six_datums():
    summary = _make_comparison_summary()
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    assert len(datums) == 6


def test_comparison_improved_count():
    summary = _make_comparison_summary(improved_case_ids=("cmp-001", "cmp-002"))
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_IMPROVED_COUNT)
    assert d.value == 2.0


def test_comparison_regressed_count():
    summary = _make_comparison_summary(regressed_case_ids=("cmp-003",))
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_REGRESSED_COUNT)
    assert d.value == 1.0


def test_comparison_unchanged_count():
    summary = _make_comparison_summary(unchanged_case_ids=("cmp-002", "cmp-004"))
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_UNCHANGED_COUNT)
    assert d.value == 2.0


def test_comparison_avg_score_delta():
    summary = _make_comparison_summary(average_score_delta=0.10)
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_AVG_SCORE_DELTA)
    assert d.value == pytest.approx(0.10)


def test_comparison_negative_avg_score_delta():
    """Regression scenario: average_score_delta can be negative."""
    summary = _make_comparison_summary(average_score_delta=-0.05)
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_AVG_SCORE_DELTA)
    assert d.value == pytest.approx(-0.05)


def test_comparison_baseline_pass_count():
    summary = _make_comparison_summary(baseline_pass_count=2)
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_BASELINE_PASS_COUNT)
    assert d.value == 2.0


def test_comparison_optimized_pass_count():
    summary = _make_comparison_summary(optimized_pass_count=3)
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_OPTIMIZED_PASS_COUNT)
    assert d.value == 3.0


def test_comparison_count_metrics_use_count_unit():
    summary = _make_comparison_summary()
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    count_metrics = {
        METRIC_CMP_IMPROVED_COUNT, METRIC_CMP_REGRESSED_COUNT,
        METRIC_CMP_UNCHANGED_COUNT, METRIC_CMP_BASELINE_PASS_COUNT,
        METRIC_CMP_OPTIMIZED_PASS_COUNT,
    }
    for d in datums:
        if d.metric_name in count_metrics:
            assert d.unit == "Count"


def test_comparison_delta_uses_none_unit():
    summary = _make_comparison_summary()
    config = _make_config()
    datums = comparison_summary_to_metrics(summary, config)
    d = _datum_by_name(datums, METRIC_CMP_AVG_SCORE_DELTA)
    assert d.unit == "None"


def test_comparison_datums_have_correct_namespace():
    summary = _make_comparison_summary()
    config = _make_config(namespace="CaseOps/Evaluation")
    datums = comparison_summary_to_metrics(summary, config)
    for d in datums:
        assert d.namespace == "CaseOps/Evaluation"


def test_comparison_datums_have_environment_dimension():
    summary = _make_comparison_summary()
    config = _make_config(environment="production")
    datums = comparison_summary_to_metrics(summary, config)
    for d in datums:
        assert d.dimensions.get("Environment") == "production"


# ── safety_distribution_to_metrics tests ─────────────────────────────────────


def test_safety_full_distribution_produces_four_datums():
    distribution = {"allow": 3, "warn": 1, "escalate": 1, "block": 0}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    assert len(datums) == 4


def test_safety_allow_count():
    distribution = {"allow": 5, "warn": 0, "escalate": 0, "block": 0}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    d = _datum_by_name(datums, METRIC_SAFETY_ALLOW)
    assert d.value == 5.0


def test_safety_warn_count():
    distribution = {"allow": 0, "warn": 2, "escalate": 0, "block": 0}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    d = _datum_by_name(datums, METRIC_SAFETY_WARN)
    assert d.value == 2.0


def test_safety_escalate_count():
    distribution = {"allow": 0, "warn": 0, "escalate": 3, "block": 0}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    d = _datum_by_name(datums, METRIC_SAFETY_ESCALATE)
    assert d.value == 3.0


def test_safety_block_count():
    distribution = {"allow": 0, "warn": 0, "escalate": 0, "block": 1}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    d = _datum_by_name(datums, METRIC_SAFETY_BLOCK)
    assert d.value == 1.0


def test_safety_partial_distribution_zero_fills_missing_statuses():
    distribution = {"allow": 4}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    assert len(datums) == 4
    d_warn = _datum_by_name(datums, METRIC_SAFETY_WARN)
    d_block = _datum_by_name(datums, METRIC_SAFETY_BLOCK)
    assert d_warn.value == 0.0
    assert d_block.value == 0.0


def test_safety_empty_distribution_returns_empty_list():
    distribution: dict[str, int] = {}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    assert datums == []


def test_safety_datums_use_count_unit():
    distribution = {"allow": 3, "warn": 1, "escalate": 1, "block": 0}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    for d in datums:
        assert d.unit == "Count"


def test_safety_datums_have_correct_namespace():
    distribution = {"allow": 2}
    config = _make_config(namespace="CaseOps/Evaluation")
    datums = safety_distribution_to_metrics(distribution, config)
    for d in datums:
        assert d.namespace == "CaseOps/Evaluation"


def test_safety_datums_have_environment_dimension():
    distribution = {"allow": 2, "warn": 1}
    config = _make_config(environment="staging")
    datums = safety_distribution_to_metrics(distribution, config)
    for d in datums:
        assert d.dimensions.get("Environment") == "staging"


def test_safety_unrecognized_keys_are_ignored():
    distribution = {"allow": 2, "unknown_status": 99}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    metric_names = {d.metric_name for d in datums}
    assert "unknown_status" not in metric_names


def test_safety_returns_list_of_metric_datums():
    distribution = {"allow": 3, "warn": 0}
    config = _make_config()
    datums = safety_distribution_to_metrics(distribution, config)
    assert all(isinstance(d, EvaluationMetricDatum) for d in datums)
