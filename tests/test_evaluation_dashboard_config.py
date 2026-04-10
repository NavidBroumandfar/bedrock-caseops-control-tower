"""
Tests for EvaluationDashboardConfig and load_evaluation_dashboard_config() — J-0.

Covers:
  - Default values for all fields
  - Environment variable overrides for each field
  - Invalid enable flag raises ValueError
  - Case-insensitive boolean parsing
  - Whitespace handling in enable flag
  - Dataclass immutability (frozen=True)
  - Return type is EvaluationDashboardConfig
  - AWS_REGION env var shared with pipeline config
"""

import os
from unittest.mock import patch

import pytest

from app.utils.config import EvaluationDashboardConfig, load_evaluation_dashboard_config


# ── Default value tests ───────────────────────────────────────────────────────


def test_defaults_enable_evaluation_metrics_is_false():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is False


def test_defaults_metrics_namespace():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert cfg.metrics_namespace == "CaseOps/Evaluation"


def test_defaults_dashboard_name():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert cfg.dashboard_name == "CaseOps-EvaluationDashboard"


def test_defaults_environment():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert cfg.environment == "development"


def test_defaults_aws_region():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert cfg.aws_region == "us-east-1"


# ── Enable flag override tests ────────────────────────────────────────────────


def test_enable_metrics_override_true():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "true"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is True


def test_enable_metrics_override_false():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "false"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is False


def test_enable_metrics_case_insensitive_true():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "TRUE"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is True


def test_enable_metrics_case_insensitive_false():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "FALSE"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is False


def test_enable_metrics_case_insensitive_mixed():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "True"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is True


def test_enable_metrics_whitespace_is_stripped():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "  true  "}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.enable_evaluation_metrics is True


# ── Invalid enable flag tests ─────────────────────────────────────────────────


def test_invalid_enable_flag_raises_value_error():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "yes"}):
        with pytest.raises(ValueError, match="CASEOPS_ENABLE_EVALUATION_METRICS"):
            load_evaluation_dashboard_config()


def test_invalid_enable_flag_empty_string_raises():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": ""}):
        with pytest.raises(ValueError, match="CASEOPS_ENABLE_EVALUATION_METRICS"):
            load_evaluation_dashboard_config()


def test_invalid_enable_flag_numeric_raises():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "1"}):
        with pytest.raises(ValueError):
            load_evaluation_dashboard_config()


def test_invalid_enable_flag_on_off_raises():
    with patch.dict(os.environ, {"CASEOPS_ENABLE_EVALUATION_METRICS": "on"}):
        with pytest.raises(ValueError):
            load_evaluation_dashboard_config()


# ── Field override tests ──────────────────────────────────────────────────────


def test_metrics_namespace_override():
    with patch.dict(os.environ, {"CASEOPS_METRICS_NAMESPACE": "MyOrg/Eval"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.metrics_namespace == "MyOrg/Eval"


def test_dashboard_name_override():
    with patch.dict(
        os.environ, {"CASEOPS_EVALUATION_DASHBOARD_NAME": "My-Custom-Dashboard"}
    ):
        cfg = load_evaluation_dashboard_config()
    assert cfg.dashboard_name == "My-Custom-Dashboard"


def test_environment_override():
    with patch.dict(os.environ, {"CASEOPS_ENVIRONMENT": "production"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.environment == "production"


def test_aws_region_override():
    with patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}):
        cfg = load_evaluation_dashboard_config()
    assert cfg.aws_region == "eu-west-1"


# ── Immutability tests ────────────────────────────────────────────────────────


def test_config_is_frozen():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    with pytest.raises((AttributeError, TypeError)):
        cfg.enable_evaluation_metrics = True  # type: ignore[misc]


def test_config_metrics_namespace_is_frozen():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    with pytest.raises((AttributeError, TypeError)):
        cfg.metrics_namespace = "other"  # type: ignore[misc]


# ── Type tests ────────────────────────────────────────────────────────────────


def test_return_type_is_evaluation_dashboard_config():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert isinstance(cfg, EvaluationDashboardConfig)


def test_enable_evaluation_metrics_is_bool():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert isinstance(cfg.enable_evaluation_metrics, bool)


def test_metrics_namespace_is_str():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert isinstance(cfg.metrics_namespace, str)


def test_environment_is_str():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert isinstance(cfg.environment, str)


def test_aws_region_is_str():
    with patch.dict(os.environ, {}, clear=True):
        cfg = load_evaluation_dashboard_config()
    assert isinstance(cfg.aws_region, str)


# ── Multi-field override test ─────────────────────────────────────────────────


def test_all_fields_can_be_overridden_together():
    env_overrides = {
        "CASEOPS_ENABLE_EVALUATION_METRICS": "true",
        "CASEOPS_METRICS_NAMESPACE": "Custom/Namespace",
        "CASEOPS_EVALUATION_DASHBOARD_NAME": "My-Dashboard",
        "CASEOPS_ENVIRONMENT": "staging",
        "AWS_REGION": "ap-southeast-1",
    }
    with patch.dict(os.environ, env_overrides):
        cfg = load_evaluation_dashboard_config()

    assert cfg.enable_evaluation_metrics is True
    assert cfg.metrics_namespace == "Custom/Namespace"
    assert cfg.dashboard_name == "My-Dashboard"
    assert cfg.environment == "staging"
    assert cfg.aws_region == "ap-southeast-1"
