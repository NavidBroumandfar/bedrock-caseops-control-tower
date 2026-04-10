"""
Tests for CloudWatchMetricsService, NoOpMetricsService, and build_metrics_service — J-0.

Covers:
  - NoOpMetricsService discards all calls without error
  - build_metrics_service returns NoOp when disabled
  - build_metrics_service returns real service when enabled
  - publish_metrics calls put_metric_data with correct Namespace
  - publish_metrics builds correct MetricData payload
  - publish_metrics includes Dimensions when present
  - publish_metrics omits Dimensions when empty
  - publish_metrics handles multiple datums
  - publish_metrics swallows boto3 exceptions (fail-safe)
  - publish_metrics no-ops when client is None
  - publish_metrics no-ops when datum list is empty
  - No live AWS calls in any test
"""

from unittest.mock import MagicMock, call, patch

import pytest

from app.schemas.evaluation_models import EvaluationMetricDatum
from app.services.cloudwatch_metrics_service import (
    CloudWatchMetricsService,
    NoOpMetricsService,
    build_metrics_service,
)
from app.utils.config import EvaluationDashboardConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_config(*, enabled: bool = True, namespace: str = "Test/Eval") -> EvaluationDashboardConfig:
    return EvaluationDashboardConfig(
        enable_evaluation_metrics=enabled,
        metrics_namespace=namespace,
        dashboard_name="TestDashboard",
        environment="test",
        aws_region="us-east-1",
    )


def _make_datum(
    *,
    metric_name: str = "EvalPassCount",
    value: float = 5.0,
    unit: str = "Count",
    namespace: str = "Test/Eval",
    dimensions: dict | None = None,
) -> EvaluationMetricDatum:
    return EvaluationMetricDatum(
        metric_name=metric_name,
        value=value,
        unit=unit,  # type: ignore[arg-type]
        namespace=namespace,
        dimensions=dimensions or {"Environment": "test"},
    )


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.put_metric_data = MagicMock()
    return client


# ── NoOpMetricsService tests ──────────────────────────────────────────────────


def test_noop_publish_metrics_does_not_raise():
    svc = NoOpMetricsService()
    svc.publish_metrics([_make_datum()])


def test_noop_publish_metrics_with_empty_list_does_not_raise():
    svc = NoOpMetricsService()
    svc.publish_metrics([])


def test_noop_publish_metrics_returns_none():
    svc = NoOpMetricsService()
    result = svc.publish_metrics([_make_datum()])
    assert result is None


def test_noop_does_not_call_any_aws_client():
    mock_client = _make_mock_client()
    svc = NoOpMetricsService()
    svc.publish_metrics([_make_datum()])
    mock_client.put_metric_data.assert_not_called()


# ── build_metrics_service factory tests ──────────────────────────────────────


def test_build_service_disabled_returns_noop():
    config = _make_config(enabled=False)
    svc = build_metrics_service(config=config)
    assert isinstance(svc, NoOpMetricsService)


def test_build_service_enabled_returns_real_service():
    config = _make_config(enabled=True)
    mock_client = _make_mock_client()
    svc = build_metrics_service(config=config, client=mock_client)
    assert isinstance(svc, CloudWatchMetricsService)


def test_build_service_disabled_with_client_still_returns_noop():
    config = _make_config(enabled=False)
    mock_client = _make_mock_client()
    svc = build_metrics_service(config=config, client=mock_client)
    assert isinstance(svc, NoOpMetricsService)


def test_build_service_uses_namespace_from_config():
    config = _make_config(enabled=True, namespace="Custom/Namespace")
    mock_client = _make_mock_client()
    svc = build_metrics_service(config=config, client=mock_client)
    assert isinstance(svc, CloudWatchMetricsService)
    assert svc._namespace == "Custom/Namespace"


# ── CloudWatchMetricsService.publish_metrics tests ───────────────────────────


def test_publish_metrics_calls_put_metric_data():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    svc.publish_metrics([_make_datum()])
    mock_client.put_metric_data.assert_called_once()


def test_publish_metrics_passes_correct_namespace():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="CaseOps/Evaluation", client=mock_client)
    svc.publish_metrics([_make_datum(namespace="CaseOps/Evaluation")])
    _, kwargs = mock_client.put_metric_data.call_args
    assert kwargs["Namespace"] == "CaseOps/Evaluation"


def test_publish_metrics_passes_correct_metric_name():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    svc.publish_metrics([_make_datum(metric_name="EvalPassCount")])
    _, kwargs = mock_client.put_metric_data.call_args
    datum = kwargs["MetricData"][0]
    assert datum["MetricName"] == "EvalPassCount"


def test_publish_metrics_passes_correct_value():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    svc.publish_metrics([_make_datum(value=42.0)])
    _, kwargs = mock_client.put_metric_data.call_args
    datum = kwargs["MetricData"][0]
    assert datum["Value"] == 42.0


def test_publish_metrics_passes_correct_unit():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    svc.publish_metrics([_make_datum(unit="Count")])
    _, kwargs = mock_client.put_metric_data.call_args
    datum = kwargs["MetricData"][0]
    assert datum["Unit"] == "Count"


def test_publish_metrics_includes_dimensions_when_present():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    datum = _make_datum(dimensions={"Environment": "staging"})
    svc.publish_metrics([datum])
    _, kwargs = mock_client.put_metric_data.call_args
    aws_datum = kwargs["MetricData"][0]
    assert "Dimensions" in aws_datum
    assert aws_datum["Dimensions"] == [{"Name": "Environment", "Value": "staging"}]


def test_publish_metrics_omits_dimensions_when_empty():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    datum = EvaluationMetricDatum(
        metric_name="EvalPassCount",
        value=3.0,
        unit="Count",
        namespace="Test/Eval",
        dimensions={},
    )
    svc.publish_metrics([datum])
    _, kwargs = mock_client.put_metric_data.call_args
    aws_datum = kwargs["MetricData"][0]
    assert "Dimensions" not in aws_datum


def test_publish_metrics_handles_multiple_datums():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    datums = [
        _make_datum(metric_name="EvalPassCount", value=3.0),
        _make_datum(metric_name="EvalFailCount", value=1.0),
        _make_datum(metric_name="EvalTotalCases", value=4.0),
    ]
    svc.publish_metrics(datums)
    _, kwargs = mock_client.put_metric_data.call_args
    assert len(kwargs["MetricData"]) == 3


def test_publish_metrics_datums_in_order():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    datums = [
        _make_datum(metric_name="First", value=1.0),
        _make_datum(metric_name="Second", value=2.0),
    ]
    svc.publish_metrics(datums)
    _, kwargs = mock_client.put_metric_data.call_args
    names = [d["MetricName"] for d in kwargs["MetricData"]]
    assert names == ["First", "Second"]


def test_publish_metrics_swallows_boto3_exception():
    mock_client = _make_mock_client()
    mock_client.put_metric_data.side_effect = RuntimeError("AWS unavailable")
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    # Must not raise
    svc.publish_metrics([_make_datum()])


def test_publish_metrics_noop_when_client_is_none():
    svc = CloudWatchMetricsService.__new__(CloudWatchMetricsService)
    svc._namespace = "Test/Eval"
    svc._region = "us-east-1"
    svc._client = None
    # Must not raise
    svc.publish_metrics([_make_datum()])


def test_publish_metrics_noop_when_datum_list_is_empty():
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    svc.publish_metrics([])
    mock_client.put_metric_data.assert_not_called()


def test_no_boto3_call_when_disabled():
    """Disabled service must never reach CloudWatch even if a real client were present."""
    config = _make_config(enabled=False)
    mock_client = _make_mock_client()
    svc = build_metrics_service(config=config, client=mock_client)
    svc.publish_metrics([_make_datum()])
    mock_client.put_metric_data.assert_not_called()


def test_publish_metrics_with_none_unit_score():
    """Score/ratio datums use unit='None' which CloudWatch accepts."""
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    datum = _make_datum(metric_name="EvalAverageScore", value=0.87, unit="None")
    svc.publish_metrics([datum])
    _, kwargs = mock_client.put_metric_data.call_args
    aws_datum = kwargs["MetricData"][0]
    assert aws_datum["Unit"] == "None"


def test_publish_metrics_with_negative_value():
    """Score delta can be negative (regression); service must pass it unchanged."""
    mock_client = _make_mock_client()
    svc = CloudWatchMetricsService(namespace="Test/Eval", client=mock_client)
    datum = _make_datum(metric_name="CmpAverageScoreDelta", value=-0.12, unit="None")
    svc.publish_metrics([datum])
    _, kwargs = mock_client.put_metric_data.call_args
    aws_datum = kwargs["MetricData"][0]
    assert aws_datum["Value"] == pytest.approx(-0.12)
