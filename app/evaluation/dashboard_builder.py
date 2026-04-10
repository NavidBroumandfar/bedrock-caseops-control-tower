"""
J-0 CloudWatch evaluation dashboard builder.

Builds a valid CloudWatch dashboard body for the CaseOps evaluation metrics.
The dashboard body is a Python dict that can be JSON-serialised and passed to
the CloudWatch put_dashboard API:

    put_dashboard(
        DashboardName=config.dashboard_name,
        DashboardBody=dashboard_body_to_json(body),
    )

No live AWS calls are made.  The builder is a pure function — same inputs
always produce the same output.  The returned dict can be tested offline and
deployed to CloudWatch when AWS credentials are available.

Dashboard layout (CloudWatch 24-column grid):
  y=0   Title text widget                     (24w × 2h)
  y=2   Evaluation Quality metrics (12w × 6h) | Safety Status counts (12w × 6h)
  y=8   Comparison Results       (12w × 6h)   | Output Quality scores (12w × 6h)

Metric names used in widget definitions are the same names emitted by
metrics_translator.py so that widgets automatically display data when the
translator is used to publish metrics to the same namespace and environment.

Public surface:
  build_evaluation_dashboard(config)   → dict  (full dashboard body)
  dashboard_body_to_json(body)         → str   (compact JSON for put_dashboard)

Separation rules:
  - No boto3, no Bedrock client, no live AWS calls.
  - No CLI, evaluation runner, or scoring logic imports.
  - Imports only: metrics_translator constants and config.
"""

from __future__ import annotations

import json

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
)
from app.utils.config import EvaluationDashboardConfig

# CloudWatch metric widget defaults.
_DIMENSION_KEY = "Environment"
_DEFAULT_PERIOD = 3600   # 1-hour aggregation period; suitable for batch evaluation runs
_STAT_SUM = "Sum"
_STAT_AVERAGE = "Average"
_VIEW_SINGLE = "singleValue"


# ── Widget builder helpers ────────────────────────────────────────────────────


def _metric_ref(namespace: str, metric_name: str, environment: str) -> list:
    """
    Build a single CloudWatch metrics-array entry for one metric with the
    standard Environment dimension.

    Format: [namespace, metric_name, dimension_key, dimension_value]
    """
    return [namespace, metric_name, _DIMENSION_KEY, environment]


def _metric_widget(
    title: str,
    x: int,
    y: int,
    width: int,
    height: int,
    namespace: str,
    environment: str,
    region: str,
    metric_names: list[str],
    *,
    stat: str = _STAT_SUM,
    view: str = _VIEW_SINGLE,
) -> dict:
    """
    Build a CloudWatch metric widget definition.

    Each metric in metric_names becomes one entry in the widget's metrics array,
    referencing the given namespace and Environment dimension value.
    """
    return {
        "type": "metric",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "properties": {
            "title": title,
            "metrics": [
                _metric_ref(namespace, name, environment) for name in metric_names
            ],
            "period": _DEFAULT_PERIOD,
            "stat": stat,
            "region": region,
            "view": view,
        },
    }


def _text_widget(markdown: str, x: int, y: int, width: int, height: int) -> dict:
    """Build a CloudWatch text/markdown widget definition."""
    return {
        "type": "text",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "properties": {
            "markdown": markdown,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────


def build_evaluation_dashboard(config: EvaluationDashboardConfig) -> dict:
    """
    Build a CloudWatch dashboard body for the CaseOps evaluation metrics.

    Returns a dict representing the full dashboard body as expected by the
    CloudWatch put_dashboard API.  JSON-serialise with json.dumps() or use
    dashboard_body_to_json() before passing to put_dashboard().

    The dashboard has four content widgets:

      1. Evaluation Quality — pass count, fail count, total cases, average score.
         Surfaces whether the evaluation run met the quality bar.

      2. Safety Status Distribution — allow, warn, escalate, block counts.
         Surfaces the safety posture of outputs under evaluation.

      3. Baseline vs. Optimized Comparison — improved, regressed, unchanged
         case counts.  Surfaces the outcome of the I-2 comparison workflow.

      4. Output Quality Scores — overall average score and baseline/optimized
         pass counts side-by-side for quick comparison.

    Widget metric references use the metric names from metrics_translator.py so
    that the dashboard automatically displays data when evaluation metrics are
    published to the same namespace and environment.

    No live AWS calls are made.  The returned dict can be verified in tests and
    deployed when AWS credentials are available.
    """
    ns = config.metrics_namespace
    env = config.environment
    region = config.aws_region

    widgets = [
        # Row 0: Dashboard title and context
        _text_widget(
            markdown=(
                "## CaseOps Evaluation Dashboard\n"
                f"Environment: **{env}**  |  Namespace: `{ns}`\n\n"
                "Evaluation output quality, safety status distributions, and "
                "baseline vs. optimized comparison results from the offline "
                "evaluation pipeline (Phases F / G / H / I)."
            ),
            x=0, y=0, width=24, height=2,
        ),

        # Row 2 left: Evaluation quality metrics (F-2 runner outcomes)
        _metric_widget(
            title="Evaluation Quality — Pass / Fail",
            x=0, y=2, width=12, height=6,
            namespace=ns, environment=env, region=region,
            metric_names=[
                METRIC_EVAL_PASS_COUNT,
                METRIC_EVAL_FAIL_COUNT,
                METRIC_EVAL_TOTAL_CASES,
            ],
            stat=_STAT_SUM,
            view=_VIEW_SINGLE,
        ),

        # Row 2 right: Safety status distribution (H-0 / H-2 suite outcomes)
        _metric_widget(
            title="Safety Status Distribution",
            x=12, y=2, width=12, height=6,
            namespace=ns, environment=env, region=region,
            metric_names=[
                METRIC_SAFETY_ALLOW,
                METRIC_SAFETY_WARN,
                METRIC_SAFETY_ESCALATE,
                METRIC_SAFETY_BLOCK,
            ],
            stat=_STAT_SUM,
            view=_VIEW_SINGLE,
        ),

        # Row 8 left: Comparison verdict counts (I-2 runner outcomes)
        _metric_widget(
            title="Baseline vs. Optimized — Verdict Counts",
            x=0, y=8, width=12, height=6,
            namespace=ns, environment=env, region=region,
            metric_names=[
                METRIC_CMP_IMPROVED_COUNT,
                METRIC_CMP_REGRESSED_COUNT,
                METRIC_CMP_UNCHANGED_COUNT,
            ],
            stat=_STAT_SUM,
            view=_VIEW_SINGLE,
        ),

        # Row 8 right: Output quality scores and pass rates
        _metric_widget(
            title="Output Quality Scores",
            x=12, y=8, width=12, height=6,
            namespace=ns, environment=env, region=region,
            metric_names=[
                METRIC_EVAL_AVERAGE_SCORE,
                METRIC_CMP_BASELINE_PASS_COUNT,
                METRIC_CMP_OPTIMIZED_PASS_COUNT,
            ],
            stat=_STAT_AVERAGE,
            view=_VIEW_SINGLE,
        ),
    ]

    return {"widgets": widgets}


def dashboard_body_to_json(body: dict) -> str:
    """
    JSON-serialise a dashboard body dict.

    Returns the compact JSON string expected by the CloudWatch put_dashboard
    DashboardBody parameter.
    """
    return json.dumps(body, separators=(",", ":"))
