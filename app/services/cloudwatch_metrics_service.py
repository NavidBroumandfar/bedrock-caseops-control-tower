"""
CloudWatch Metrics service wrapper — J-0.

Thin wrapper around the boto3 CloudWatch Metrics client for publishing
evaluation metrics from the CaseOps evaluation pipeline.

This is distinct from cloudwatch_service.py (CloudWatch Logs, E-0) — this
module publishes numeric metric data points to CloudWatch Metrics via
put_metric_data, rather than structured log events.

Design constraints:
  - No business logic: only metric datum serialisation and put_metric_data calls
  - All failures are caught and swallowed — metrics emission is always optional
  - Never raises; callers can rely on fire-and-forget semantics
  - Disabled gracefully via EvaluationDashboardConfig or when AWS is unavailable
  - Mockable: the boto3 client can be injected at construction time

Usage:
    from app.services.cloudwatch_metrics_service import build_metrics_service
    from app.utils.config import load_evaluation_dashboard_config

    config = load_evaluation_dashboard_config()
    service = build_metrics_service(config=config)
    service.publish_metrics(datums)

Test usage (inject a mock client):
    service = CloudWatchMetricsService(namespace="ns", client=mock_client)
    service.publish_metrics(datums)
"""

from __future__ import annotations

from typing import Any

from app.schemas.evaluation_models import EvaluationMetricDatum
from app.utils.config import EvaluationDashboardConfig


class CloudWatchMetricsService:
    """
    CloudWatch Metrics emitter for evaluation data.

    Publishes typed EvaluationMetricDatum objects to CloudWatch Metrics via
    put_metric_data.  All errors from the boto3 client are swallowed so that a
    CloudWatch outage or misconfigured credentials never break the evaluation
    pipeline.

    The client is accepted via constructor injection for testability.  When no
    client is provided, one is built lazily via boto3; if boto3 is unavailable
    the internal client is None and all publish calls are silent no-ops.
    """

    def __init__(
        self,
        *,
        namespace: str,
        region: str | None = None,
        client: Any = None,
    ) -> None:
        self._namespace = namespace
        self._region = region or "us-east-1"
        self._client: Any = client if client is not None else self._build_client()

    def publish_metrics(self, datums: list[EvaluationMetricDatum]) -> None:
        """
        Publish a list of EvaluationMetricDatum objects to CloudWatch Metrics.

        No-ops silently when the client is None or the datum list is empty.
        All boto3 exceptions are caught and discarded — metric emission must
        never break the evaluation pipeline.
        """
        if self._client is None or not datums:
            return
        try:
            metric_data = [self._to_aws_datum(d) for d in datums]
            self._client.put_metric_data(
                Namespace=self._namespace,
                MetricData=metric_data,
            )
        except Exception:
            pass

    # ── private helpers ──────────────────────────────────────────────────────

    def _to_aws_datum(self, datum: EvaluationMetricDatum) -> dict[str, Any]:
        """
        Convert an EvaluationMetricDatum to the dict format expected by
        CloudWatch put_metric_data.
        """
        entry: dict[str, Any] = {
            "MetricName": datum.metric_name,
            "Value": datum.value,
            "Unit": datum.unit,
        }
        if datum.dimensions:
            entry["Dimensions"] = [
                {"Name": k, "Value": v} for k, v in datum.dimensions.items()
            ]
        return entry

    def _build_client(self) -> Any:
        """
        Construct a boto3 CloudWatch client.

        Returns None if boto3 is unavailable or cannot construct the client so
        the service degrades gracefully in environments without AWS credentials.
        """
        try:
            import boto3

            return boto3.client("cloudwatch", region_name=self._region)
        except Exception:
            return None


# ── no-op service ────────────────────────────────────────────────────────────


class NoOpMetricsService:
    """
    Metrics emitter that discards all data.

    Returned by build_metrics_service() when CASEOPS_ENABLE_EVALUATION_METRICS
    is false or in tests where real AWS calls must not be made.
    """

    def publish_metrics(self, datums: list[EvaluationMetricDatum]) -> None:  # noqa: ARG002
        pass


# ── factory ──────────────────────────────────────────────────────────────────


def build_metrics_service(
    *,
    config: EvaluationDashboardConfig,
    client: Any = None,
) -> "CloudWatchMetricsService | NoOpMetricsService":
    """
    Factory: return the appropriate metrics service based on config.

    Returns NoOpMetricsService when evaluation metrics are disabled to prevent
    any AWS calls in offline or test environments.  Returns a real
    CloudWatchMetricsService when enabled, using the injected client when
    provided (for testability) or building one from boto3 when not.
    """
    if not config.enable_evaluation_metrics:
        return NoOpMetricsService()
    return CloudWatchMetricsService(
        namespace=config.metrics_namespace,
        region=config.aws_region,
        client=client,
    )
