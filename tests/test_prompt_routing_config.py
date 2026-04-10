"""
Tests for PromptRoutingConfig and load_prompt_routing_config() — I-1.

Coverage:
  - Config defaults (all four fields)
  - Env var overrides for each field
  - Case-insensitivity of the boolean flag
  - Invalid enable flag raises ValueError (fail-loud)
  - Immutability of the frozen dataclass
  - Return type correctness
  - Empty model ID strings are allowed (fallback chain handles them)
  - No live AWS dependency
"""

import pytest

from app.utils.config import PromptRoutingConfig, load_prompt_routing_config


# ── default values ────────────────────────────────────────────────────────────


class TestPromptRoutingConfigDefaults:
    def test_enable_prompt_routing_default_is_false(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_ROUTING", raising=False)
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is False

    def test_default_model_id_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ROUTING_DEFAULT_MODEL_ID", raising=False)
        config = load_prompt_routing_config()
        assert config.default_model_id == ""

    def test_analysis_model_id_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ROUTING_ANALYSIS_MODEL_ID", raising=False)
        config = load_prompt_routing_config()
        assert config.analysis_model_id == ""

    def test_validation_model_id_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ROUTING_VALIDATION_MODEL_ID", raising=False)
        config = load_prompt_routing_config()
        assert config.validation_model_id == ""

    def test_all_defaults_at_once(self, monkeypatch):
        for var in (
            "CASEOPS_ENABLE_PROMPT_ROUTING",
            "CASEOPS_ROUTING_DEFAULT_MODEL_ID",
            "CASEOPS_ROUTING_ANALYSIS_MODEL_ID",
            "CASEOPS_ROUTING_VALIDATION_MODEL_ID",
        ):
            monkeypatch.delenv(var, raising=False)
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is False
        assert config.default_model_id == ""
        assert config.analysis_model_id == ""
        assert config.validation_model_id == ""


# ── env var overrides ─────────────────────────────────────────────────────────


class TestPromptRoutingConfigOverrides:
    def test_enable_true(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "true")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is True

    def test_enable_false(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "false")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is False

    def test_default_model_id_override(self, monkeypatch):
        monkeypatch.setenv(
            "CASEOPS_ROUTING_DEFAULT_MODEL_ID",
            "anthropic.claude-3-sonnet-20240229-v1:0",
        )
        config = load_prompt_routing_config()
        assert config.default_model_id == "anthropic.claude-3-sonnet-20240229-v1:0"

    def test_analysis_model_id_override(self, monkeypatch):
        monkeypatch.setenv(
            "CASEOPS_ROUTING_ANALYSIS_MODEL_ID",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
        )
        config = load_prompt_routing_config()
        assert config.analysis_model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_validation_model_id_override(self, monkeypatch):
        monkeypatch.setenv(
            "CASEOPS_ROUTING_VALIDATION_MODEL_ID",
            "anthropic.claude-3-haiku-20240307-v1:0",
        )
        config = load_prompt_routing_config()
        assert config.validation_model_id == "anthropic.claude-3-haiku-20240307-v1:0"

    def test_all_overrides_together(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "true")
        monkeypatch.setenv(
            "CASEOPS_ROUTING_DEFAULT_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0"
        )
        monkeypatch.setenv(
            "CASEOPS_ROUTING_ANALYSIS_MODEL_ID",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
        )
        monkeypatch.setenv(
            "CASEOPS_ROUTING_VALIDATION_MODEL_ID",
            "anthropic.claude-3-haiku-20240307-v1:0",
        )
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is True
        assert config.default_model_id == "anthropic.claude-3-sonnet-20240229-v1:0"
        assert config.analysis_model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert config.validation_model_id == "anthropic.claude-3-haiku-20240307-v1:0"


# ── case insensitivity ────────────────────────────────────────────────────────


class TestPromptRoutingConfigCaseInsensitivity:
    def test_enable_uppercase_true(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "TRUE")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is True

    def test_enable_uppercase_false(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "FALSE")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is False

    def test_enable_mixed_case_true(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "True")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is True

    def test_enable_mixed_case_false(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "False")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is False


# ── invalid config raises ValueError ─────────────────────────────────────────


class TestPromptRoutingConfigValidation:
    def test_invalid_enable_flag_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "yes")
        with pytest.raises(ValueError, match="CASEOPS_ENABLE_PROMPT_ROUTING"):
            load_prompt_routing_config()

    def test_numeric_enable_flag_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "1")
        with pytest.raises(ValueError, match="CASEOPS_ENABLE_PROMPT_ROUTING"):
            load_prompt_routing_config()

    def test_empty_enable_flag_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "")
        with pytest.raises(ValueError, match="CASEOPS_ENABLE_PROMPT_ROUTING"):
            load_prompt_routing_config()

    def test_whitespace_enable_flag_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "  ")
        with pytest.raises(ValueError, match="CASEOPS_ENABLE_PROMPT_ROUTING"):
            load_prompt_routing_config()

    def test_empty_model_id_is_allowed(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "true")
        monkeypatch.setenv("CASEOPS_ROUTING_DEFAULT_MODEL_ID", "")
        monkeypatch.setenv("CASEOPS_ROUTING_ANALYSIS_MODEL_ID", "")
        monkeypatch.setenv("CASEOPS_ROUTING_VALIDATION_MODEL_ID", "")
        config = load_prompt_routing_config()
        assert config.enable_prompt_routing is True
        assert config.default_model_id == ""


# ── immutability ──────────────────────────────────────────────────────────────


class TestPromptRoutingConfigImmutability:
    def test_frozen_dataclass_rejects_mutation(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_ROUTING", raising=False)
        config = load_prompt_routing_config()
        with pytest.raises((AttributeError, TypeError)):
            config.enable_prompt_routing = True  # type: ignore[misc]

    def test_model_id_field_immutable(self, monkeypatch):
        monkeypatch.setenv(
            "CASEOPS_ROUTING_ANALYSIS_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
        )
        config = load_prompt_routing_config()
        with pytest.raises((AttributeError, TypeError)):
            config.analysis_model_id = "other-model"  # type: ignore[misc]


# ── return type ───────────────────────────────────────────────────────────────


class TestPromptRoutingConfigReturnType:
    def test_returns_prompt_routing_config_instance(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_ROUTING", raising=False)
        config = load_prompt_routing_config()
        assert isinstance(config, PromptRoutingConfig)

    def test_enable_field_is_bool(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "true")
        config = load_prompt_routing_config()
        assert isinstance(config.enable_prompt_routing, bool)

    def test_model_id_fields_are_strings(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ROUTING_ANALYSIS_MODEL_ID", raising=False)
        config = load_prompt_routing_config()
        assert isinstance(config.analysis_model_id, str)
        assert isinstance(config.validation_model_id, str)
        assert isinstance(config.default_model_id, str)
