"""
Tests for build_evaluation_dashboard() and dashboard_body_to_json() — J-0.

Covers:
  - Returns a dict with a "widgets" key
  - Widgets list has the expected number of entries
  - Each widget has required structural keys (type, x, y, width, height)
  - Text widget has correct type and markdown content
  - Each metric widget has required properties (title, metrics, period, stat, region, view)
  - Namespace from config appears in every metric widget's metrics array
  - Environment dimension from config appears in every metric reference
  - All widget widths are valid (1–24) and widgets stay within the 24-column grid
  - Dashboard body is JSON-serialisable
  - dashboard_body_to_json returns a valid JSON string
  - Custom config values propagate correctly
  - Different environments produce different dimension values in widgets
  - No live AWS calls
"""

import json

import pytest

from app.evaluation.dashboard_builder import (
    build_evaluation_dashboard,
    dashboard_body_to_json,
)
from app.utils.config import EvaluationDashboardConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_config(
    namespace: str = "CaseOps/Evaluation",
    environment: str = "test",
    dashboard_name: str = "CaseOps-EvaluationDashboard",
    region: str = "us-east-1",
) -> EvaluationDashboardConfig:
    return EvaluationDashboardConfig(
        enable_evaluation_metrics=True,
        metrics_namespace=namespace,
        dashboard_name=dashboard_name,
        environment=environment,
        aws_region=region,
    )


def _default_body() -> dict:
    return build_evaluation_dashboard(_make_config())


def _metric_widgets(body: dict) -> list[dict]:
    return [w for w in body["widgets"] if w["type"] == "metric"]


def _text_widgets(body: dict) -> list[dict]:
    return [w for w in body["widgets"] if w["type"] == "text"]


# ── Top-level structure tests ─────────────────────────────────────────────────


def test_returns_dict():
    body = _default_body()
    assert isinstance(body, dict)


def test_has_widgets_key():
    body = _default_body()
    assert "widgets" in body


def test_widgets_is_list():
    body = _default_body()
    assert isinstance(body["widgets"], list)


def test_has_at_least_four_widgets():
    body = _default_body()
    assert len(body["widgets"]) >= 4


def test_has_exactly_five_widgets():
    body = _default_body()
    assert len(body["widgets"]) == 5


def test_has_exactly_one_text_widget():
    body = _default_body()
    assert len(_text_widgets(body)) == 1


def test_has_exactly_four_metric_widgets():
    body = _default_body()
    assert len(_metric_widgets(body)) == 4


# ── Widget structural key tests ───────────────────────────────────────────────


def test_all_widgets_have_type_key():
    body = _default_body()
    for widget in body["widgets"]:
        assert "type" in widget


def test_all_widgets_have_x_key():
    body = _default_body()
    for widget in body["widgets"]:
        assert "x" in widget


def test_all_widgets_have_y_key():
    body = _default_body()
    for widget in body["widgets"]:
        assert "y" in widget


def test_all_widgets_have_width_key():
    body = _default_body()
    for widget in body["widgets"]:
        assert "width" in widget


def test_all_widgets_have_height_key():
    body = _default_body()
    for widget in body["widgets"]:
        assert "height" in widget


def test_all_widgets_have_properties_key():
    body = _default_body()
    for widget in body["widgets"]:
        assert "properties" in widget


# ── Grid constraint tests ─────────────────────────────────────────────────────


def test_all_widgets_width_at_most_24():
    body = _default_body()
    for widget in body["widgets"]:
        assert widget["width"] <= 24


def test_all_widgets_width_at_least_1():
    body = _default_body()
    for widget in body["widgets"]:
        assert widget["width"] >= 1


def test_all_widgets_stay_within_24_column_grid():
    body = _default_body()
    for widget in body["widgets"]:
        assert widget["x"] + widget["width"] <= 24


def test_all_widgets_x_is_non_negative():
    body = _default_body()
    for widget in body["widgets"]:
        assert widget["x"] >= 0


def test_all_widgets_y_is_non_negative():
    body = _default_body()
    for widget in body["widgets"]:
        assert widget["y"] >= 0


# ── Text widget content tests ─────────────────────────────────────────────────


def test_text_widget_type_is_text():
    body = _default_body()
    text_widgets = _text_widgets(body)
    assert text_widgets[0]["type"] == "text"


def test_text_widget_has_markdown_property():
    body = _default_body()
    text_widgets = _text_widgets(body)
    assert "markdown" in text_widgets[0]["properties"]


def test_text_widget_markdown_is_nonempty():
    body = _default_body()
    text_widgets = _text_widgets(body)
    assert len(text_widgets[0]["properties"]["markdown"]) > 0


def test_text_widget_markdown_contains_environment():
    config = _make_config(environment="staging")
    body = build_evaluation_dashboard(config)
    text_widgets = _text_widgets(body)
    assert "staging" in text_widgets[0]["properties"]["markdown"]


def test_text_widget_markdown_contains_namespace():
    config = _make_config(namespace="Custom/Namespace")
    body = build_evaluation_dashboard(config)
    text_widgets = _text_widgets(body)
    assert "Custom/Namespace" in text_widgets[0]["properties"]["markdown"]


# ── Metric widget property tests ──────────────────────────────────────────────


def test_metric_widgets_have_title():
    body = _default_body()
    for widget in _metric_widgets(body):
        assert "title" in widget["properties"]
        assert len(widget["properties"]["title"]) > 0


def test_metric_widgets_have_metrics_list():
    body = _default_body()
    for widget in _metric_widgets(body):
        assert "metrics" in widget["properties"]
        assert isinstance(widget["properties"]["metrics"], list)
        assert len(widget["properties"]["metrics"]) > 0


def test_metric_widgets_have_period():
    body = _default_body()
    for widget in _metric_widgets(body):
        assert "period" in widget["properties"]
        assert isinstance(widget["properties"]["period"], int)
        assert widget["properties"]["period"] > 0


def test_metric_widgets_have_stat():
    body = _default_body()
    for widget in _metric_widgets(body):
        assert "stat" in widget["properties"]
        assert widget["properties"]["stat"] in ("Sum", "Average", "Maximum", "Minimum", "SampleCount")


def test_metric_widgets_have_region():
    body = _default_body()
    for widget in _metric_widgets(body):
        assert "region" in widget["properties"]


def test_metric_widgets_have_view():
    body = _default_body()
    for widget in _metric_widgets(body):
        assert "view" in widget["properties"]


# ── Namespace and dimension propagation tests ─────────────────────────────────


def test_namespace_from_config_appears_in_all_metric_refs():
    config = _make_config(namespace="MyOrg/Evaluation")
    body = build_evaluation_dashboard(config)
    for widget in _metric_widgets(body):
        for metric_ref in widget["properties"]["metrics"]:
            assert metric_ref[0] == "MyOrg/Evaluation"


def test_environment_dimension_in_metric_refs():
    config = _make_config(environment="production")
    body = build_evaluation_dashboard(config)
    for widget in _metric_widgets(body):
        for metric_ref in widget["properties"]["metrics"]:
            # Format: [namespace, metric_name, dim_key, dim_value]
            assert metric_ref[2] == "Environment"
            assert metric_ref[3] == "production"


def test_region_from_config_in_metric_widget_properties():
    config = _make_config(region="eu-west-1")
    body = build_evaluation_dashboard(config)
    for widget in _metric_widgets(body):
        assert widget["properties"]["region"] == "eu-west-1"


def test_different_environments_produce_different_dimension_values():
    body_dev = build_evaluation_dashboard(_make_config(environment="development"))
    body_prod = build_evaluation_dashboard(_make_config(environment="production"))

    def _collect_envs(body: dict) -> set[str]:
        envs = set()
        for widget in _metric_widgets(body):
            for ref in widget["properties"]["metrics"]:
                envs.add(ref[3])
        return envs

    assert "development" in _collect_envs(body_dev)
    assert "production" in _collect_envs(body_prod)
    assert "development" not in _collect_envs(body_prod)


# ── JSON serialisability tests ────────────────────────────────────────────────


def test_dashboard_body_is_json_serialisable():
    body = _default_body()
    serialised = json.dumps(body)
    assert isinstance(serialised, str)


def test_dashboard_body_roundtrips_through_json():
    body = _default_body()
    serialised = json.dumps(body)
    roundtripped = json.loads(serialised)
    assert roundtripped["widgets"] is not None
    assert len(roundtripped["widgets"]) == len(body["widgets"])


def test_dashboard_body_to_json_returns_str():
    body = _default_body()
    result = dashboard_body_to_json(body)
    assert isinstance(result, str)


def test_dashboard_body_to_json_is_valid_json():
    body = _default_body()
    result = dashboard_body_to_json(body)
    parsed = json.loads(result)
    assert "widgets" in parsed


def test_dashboard_body_to_json_is_compact():
    """Compact JSON uses tight separators — no whitespace between keys and values in structure."""
    body = _default_body()
    result = dashboard_body_to_json(body)
    # json.dumps with separators=(",", ":") produces no space between a colon and the next
    # integer or boolean value (e.g. "x":0 not "x": 0); check a known numeric field
    assert '"x":0' in result or '"y":0' in result  # tight key:int format


# ── Determinism test ──────────────────────────────────────────────────────────


def test_build_dashboard_is_deterministic():
    config = _make_config()
    body1 = build_evaluation_dashboard(config)
    body2 = build_evaluation_dashboard(config)
    assert json.dumps(body1) == json.dumps(body2)
