"""
J-0 evaluation metrics translator.

Maps typed evaluation summaries from the F-2 evaluation runner (EvaluationRunSummary)
and I-2 comparison runner (ComparisonSummary) into CloudWatch-friendly
EvaluationMetricDatum payloads.

All functions are pure: they accept a summary object and a config, and return a
list of EvaluationMetricDatum objects.  No AWS calls, no file I/O, no business
logic — translation only.

The metric name constants defined here are imported by dashboard_builder.py so
that widget metric references stay consistent with what the translator emits.

Public surface:
  evaluation_run_summary_to_metrics(summary, config)   → list[EvaluationMetricDatum]
  comparison_summary_to_metrics(summary, config)        → list[EvaluationMetricDatum]
  safety_distribution_to_metrics(distribution, config)  → list[EvaluationMetricDatum]

  METRIC_EVAL_PASS_COUNT        — emitted by evaluation_run_summary_to_metrics
  METRIC_EVAL_FAIL_COUNT        — emitted by evaluation_run_summary_to_metrics
  METRIC_EVAL_TOTAL_CASES       — emitted by evaluation_run_summary_to_metrics
  METRIC_EVAL_AVERAGE_SCORE     — emitted by evaluation_run_summary_to_metrics
  METRIC_SAFETY_ALLOW           — emitted by safety_distribution_to_metrics
  METRIC_SAFETY_WARN            — emitted by safety_distribution_to_metrics
  METRIC_SAFETY_ESCALATE        — emitted by safety_distribution_to_metrics
  METRIC_SAFETY_BLOCK           — emitted by safety_distribution_to_metrics
  METRIC_CMP_IMPROVED_COUNT     — emitted by comparison_summary_to_metrics
  METRIC_CMP_REGRESSED_COUNT    — emitted by comparison_summary_to_metrics
  METRIC_CMP_UNCHANGED_COUNT    — emitted by comparison_summary_to_metrics
  METRIC_CMP_AVG_SCORE_DELTA    — emitted by comparison_summary_to_metrics
  METRIC_CMP_BASELINE_PASS_COUNT  — emitted by comparison_summary_to_metrics
  METRIC_CMP_OPTIMIZED_PASS_COUNT — emitted by comparison_summary_to_metrics

Separation rules:
  - No boto3, no Bedrock client, no live AWS calls.
  - No CLI, Converse inference, or runtime pipeline imports.
  - Imports only: evaluation schemas, comparison runner dataclasses, and config.
"""

from __future__ import annotations

from app.evaluation.comparison_runner import ComparisonSummary
from app.schemas.evaluation_models import EvaluationMetricDatum, EvaluationRunSummary
from app.utils.config import EvaluationDashboardConfig

# ── Metric name constants ─────────────────────────────────────────────────────
# Used by both this translator and dashboard_builder.py to keep widget metric
# references consistent with what is actually emitted to CloudWatch.

METRIC_EVAL_PASS_COUNT = "EvalPassCount"
METRIC_EVAL_FAIL_COUNT = "EvalFailCount"
METRIC_EVAL_TOTAL_CASES = "EvalTotalCases"
METRIC_EVAL_AVERAGE_SCORE = "EvalAverageScore"

METRIC_SAFETY_ALLOW = "SafetyAllow"
METRIC_SAFETY_WARN = "SafetyWarn"
METRIC_SAFETY_ESCALATE = "SafetyEscalate"
METRIC_SAFETY_BLOCK = "SafetyBlock"

METRIC_CMP_IMPROVED_COUNT = "CmpImprovedCount"
METRIC_CMP_REGRESSED_COUNT = "CmpRegressedCount"
METRIC_CMP_UNCHANGED_COUNT = "CmpUnchangedCount"
METRIC_CMP_AVG_SCORE_DELTA = "CmpAverageScoreDelta"
METRIC_CMP_BASELINE_PASS_COUNT = "CmpBaselinePassCount"
METRIC_CMP_OPTIMIZED_PASS_COUNT = "CmpOptimizedPassCount"

# Maps SafetyStatus string values to their corresponding CloudWatch metric names.
_SAFETY_STATUS_TO_METRIC: dict[str, str] = {
    "allow": METRIC_SAFETY_ALLOW,
    "warn": METRIC_SAFETY_WARN,
    "escalate": METRIC_SAFETY_ESCALATE,
    "block": METRIC_SAFETY_BLOCK,
}

# Standard CloudWatch dimension key applied to all emitted evaluation metrics.
_DIMENSION_KEY = "Environment"


# ── Internal helper ───────────────────────────────────────────────────────────


def _make_datum(
    metric_name: str,
    value: float,
    unit: str,
    config: EvaluationDashboardConfig,
) -> EvaluationMetricDatum:
    """
    Build an EvaluationMetricDatum with the standard Environment dimension.

    All datums emitted by this module carry an "Environment" dimension keyed
    to config.environment so that metrics from different environments remain
    separable in CloudWatch.
    """
    return EvaluationMetricDatum(
        metric_name=metric_name,
        value=value,
        unit=unit,  # type: ignore[arg-type]
        namespace=config.metrics_namespace,
        dimensions={_DIMENSION_KEY: config.environment},
    )


# ── Public translation functions ──────────────────────────────────────────────


def evaluation_run_summary_to_metrics(
    summary: EvaluationRunSummary,
    config: EvaluationDashboardConfig,
) -> list[EvaluationMetricDatum]:
    """
    Translate an EvaluationRunSummary into CloudWatch metric datums.

    Produces four datums covering the core evaluation quality dimensions:
      EvalPassCount    — number of cases where pass_fail was True
      EvalFailCount    — number of cases where pass_fail was False
      EvalTotalCases   — total cases in this evaluation run
      EvalAverageScore — mean overall_score across all cases (unit: None)

    All datums carry the standard Environment dimension from config.
    """
    return [
        _make_datum(METRIC_EVAL_PASS_COUNT, float(summary.passed_cases), "Count", config),
        _make_datum(METRIC_EVAL_FAIL_COUNT, float(summary.failed_cases), "Count", config),
        _make_datum(METRIC_EVAL_TOTAL_CASES, float(summary.total_cases), "Count", config),
        _make_datum(METRIC_EVAL_AVERAGE_SCORE, summary.average_score, "None", config),
    ]


def comparison_summary_to_metrics(
    summary: ComparisonSummary,
    config: EvaluationDashboardConfig,
) -> list[EvaluationMetricDatum]:
    """
    Translate a ComparisonSummary into CloudWatch metric datums.

    Produces six datums covering the I-2 comparison results:
      CmpImprovedCount      — cases where optimized score improved meaningfully
      CmpRegressedCount     — cases where optimized score regressed meaningfully
      CmpUnchangedCount     — cases within the comparison delta epsilon
      CmpAverageScoreDelta  — mean score change (optimized − baseline); may be negative
      CmpBaselinePassCount  — baseline outputs that passed the quality threshold
      CmpOptimizedPassCount — optimized outputs that passed the quality threshold

    CmpAverageScoreDelta uses unit "None" since it can be negative.
    All other datums use unit "Count".
    """
    return [
        _make_datum(
            METRIC_CMP_IMPROVED_COUNT,
            float(len(summary.improved_case_ids)),
            "Count",
            config,
        ),
        _make_datum(
            METRIC_CMP_REGRESSED_COUNT,
            float(len(summary.regressed_case_ids)),
            "Count",
            config,
        ),
        _make_datum(
            METRIC_CMP_UNCHANGED_COUNT,
            float(len(summary.unchanged_case_ids)),
            "Count",
            config,
        ),
        _make_datum(
            METRIC_CMP_AVG_SCORE_DELTA,
            summary.average_score_delta,
            "None",
            config,
        ),
        _make_datum(
            METRIC_CMP_BASELINE_PASS_COUNT,
            float(summary.baseline_pass_count),
            "Count",
            config,
        ),
        _make_datum(
            METRIC_CMP_OPTIMIZED_PASS_COUNT,
            float(summary.optimized_pass_count),
            "Count",
            config,
        ),
    ]


def safety_distribution_to_metrics(
    distribution: dict[str, int],
    config: EvaluationDashboardConfig,
) -> list[EvaluationMetricDatum]:
    """
    Translate a SafetyStatus distribution dict into CloudWatch metric datums.

    Accepts a mapping of SafetyStatus string value → count, as produced by
    ComparisonSummary.baseline_safety_distribution / optimized_safety_distribution
    or from the H-2 safety suite runner.

    Always emits one datum for each of the four recognized safety statuses
    (allow, warn, escalate, block).  If a status is absent from the distribution,
    its count is treated as zero.  Unrecognized keys are silently ignored.

    Returns an empty list when the distribution argument is empty.
    """
    if not distribution:
        return []

    return [
        _make_datum(metric_name, float(distribution.get(status_value, 0)), "Count", config)
        for status_value, metric_name in _SAFETY_STATUS_TO_METRIC.items()
    ]
