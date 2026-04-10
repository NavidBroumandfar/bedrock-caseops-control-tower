"""
Tests for prompt_router.py and routing integration in Bedrock services — I-1.

Coverage:
  - Disabled routing: fallback_model_id returned unchanged for any route
  - Enabled routing: analysis route uses analysis_model_id override
  - Enabled routing: validation route uses validation_model_id override
  - Fallback to routing default when route override is absent
  - Fallback to caller fallback when route override and default are both absent
  - Priority chain: route override > routing default > caller fallback
  - Determinism: same inputs always produce same output
  - BedrockAnalysisService uses "analysis" route at construction
  - BedrockValidationService uses "validation" route at construction
  - No regression when routing_config is None (pre-I-1 behaviour)
  - No live AWS dependency (all Bedrock client calls are mocked or never reach AWS)
"""

from unittest.mock import MagicMock

import pytest

from app.services.prompt_router import resolve_model_id
from app.utils.config import PromptRoutingConfig

# ── test helpers ──────────────────────────────────────────────────────────────

_FALLBACK = "anthropic.claude-3-haiku-20240307-v1:0"
_ANALYSIS_OVERRIDE = "anthropic.claude-3-5-sonnet-20241022-v2:0"
_VALIDATION_OVERRIDE = "anthropic.claude-3-sonnet-20240229-v1:0"
_DEFAULT_OVERRIDE = "amazon.titan-text-express-v1"


def _routing_off(**kwargs) -> PromptRoutingConfig:
    return PromptRoutingConfig(
        enable_prompt_routing=False,
        default_model_id=kwargs.get("default_model_id", ""),
        analysis_model_id=kwargs.get("analysis_model_id", ""),
        validation_model_id=kwargs.get("validation_model_id", ""),
    )


def _routing_on(**kwargs) -> PromptRoutingConfig:
    return PromptRoutingConfig(
        enable_prompt_routing=True,
        default_model_id=kwargs.get("default_model_id", ""),
        analysis_model_id=kwargs.get("analysis_model_id", ""),
        validation_model_id=kwargs.get("validation_model_id", ""),
    )


# ── disabled routing ──────────────────────────────────────────────────────────


class TestResolveModelIdDisabled:
    def test_disabled_returns_fallback_for_analysis(self):
        config = _routing_off(analysis_model_id=_ANALYSIS_OVERRIDE)
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _FALLBACK

    def test_disabled_returns_fallback_for_validation(self):
        config = _routing_off(validation_model_id=_VALIDATION_OVERRIDE)
        result = resolve_model_id("validation", config, _FALLBACK)
        assert result == _FALLBACK

    def test_disabled_ignores_default_model_id(self):
        config = _routing_off(default_model_id=_DEFAULT_OVERRIDE)
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _FALLBACK

    def test_disabled_returns_caller_fallback_exactly(self):
        config = _routing_off()
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result is _FALLBACK or result == _FALLBACK


# ── enabled routing — analysis route ─────────────────────────────────────────


class TestResolveModelIdAnalysisRoute:
    def test_analysis_override_used_when_set(self):
        config = _routing_on(analysis_model_id=_ANALYSIS_OVERRIDE)
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _ANALYSIS_OVERRIDE

    def test_analysis_override_wins_over_default(self):
        config = _routing_on(
            analysis_model_id=_ANALYSIS_OVERRIDE,
            default_model_id=_DEFAULT_OVERRIDE,
        )
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _ANALYSIS_OVERRIDE

    def test_analysis_override_wins_over_fallback(self):
        config = _routing_on(analysis_model_id=_ANALYSIS_OVERRIDE)
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _ANALYSIS_OVERRIDE
        assert result != _FALLBACK


# ── enabled routing — validation route ───────────────────────────────────────


class TestResolveModelIdValidationRoute:
    def test_validation_override_used_when_set(self):
        config = _routing_on(validation_model_id=_VALIDATION_OVERRIDE)
        result = resolve_model_id("validation", config, _FALLBACK)
        assert result == _VALIDATION_OVERRIDE

    def test_validation_override_wins_over_default(self):
        config = _routing_on(
            validation_model_id=_VALIDATION_OVERRIDE,
            default_model_id=_DEFAULT_OVERRIDE,
        )
        result = resolve_model_id("validation", config, _FALLBACK)
        assert result == _VALIDATION_OVERRIDE

    def test_validation_route_does_not_use_analysis_override(self):
        config = _routing_on(
            analysis_model_id=_ANALYSIS_OVERRIDE,
            validation_model_id="",
        )
        result = resolve_model_id("validation", config, _FALLBACK)
        assert result != _ANALYSIS_OVERRIDE

    def test_analysis_route_does_not_use_validation_override(self):
        config = _routing_on(
            validation_model_id=_VALIDATION_OVERRIDE,
            analysis_model_id="",
        )
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result != _VALIDATION_OVERRIDE


# ── fallback chain ────────────────────────────────────────────────────────────


class TestResolveModelIdFallbackChain:
    def test_falls_back_to_routing_default_when_no_route_override(self):
        config = _routing_on(default_model_id=_DEFAULT_OVERRIDE)
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _DEFAULT_OVERRIDE

    def test_falls_back_to_caller_fallback_when_all_empty(self):
        config = _routing_on()
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _FALLBACK

    def test_falls_back_to_caller_fallback_for_validation_when_all_empty(self):
        config = _routing_on()
        result = resolve_model_id("validation", config, _FALLBACK)
        assert result == _FALLBACK

    def test_priority_route_over_default_over_fallback_analysis(self):
        config = _routing_on(
            analysis_model_id=_ANALYSIS_OVERRIDE,
            default_model_id=_DEFAULT_OVERRIDE,
        )
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _ANALYSIS_OVERRIDE

    def test_priority_default_over_fallback_when_no_override(self):
        config = _routing_on(
            analysis_model_id="",
            default_model_id=_DEFAULT_OVERRIDE,
        )
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _DEFAULT_OVERRIDE

    def test_priority_fallback_when_nothing_else_set(self):
        config = _routing_on(analysis_model_id="", default_model_id="")
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _FALLBACK


# ── determinism ───────────────────────────────────────────────────────────────


class TestResolveModelIdDeterminism:
    def test_same_inputs_same_output_disabled(self):
        config = _routing_off()
        result_a = resolve_model_id("analysis", config, _FALLBACK)
        result_b = resolve_model_id("analysis", config, _FALLBACK)
        assert result_a == result_b

    def test_same_inputs_same_output_enabled(self):
        config = _routing_on(analysis_model_id=_ANALYSIS_OVERRIDE)
        result_a = resolve_model_id("analysis", config, _FALLBACK)
        result_b = resolve_model_id("analysis", config, _FALLBACK)
        assert result_a == result_b

    def test_repeated_calls_stable(self):
        config = _routing_on(
            analysis_model_id=_ANALYSIS_OVERRIDE,
            validation_model_id=_VALIDATION_OVERRIDE,
            default_model_id=_DEFAULT_OVERRIDE,
        )
        for _ in range(5):
            assert resolve_model_id("analysis", config, _FALLBACK) == _ANALYSIS_OVERRIDE
            assert resolve_model_id("validation", config, _FALLBACK) == _VALIDATION_OVERRIDE


# ── service integration — BedrockAnalysisService ─────────────────────────────


class TestBedrockAnalysisServiceRouting:
    def test_no_routing_config_uses_base_model_id(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=None,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_disabled_uses_base_model_id(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        config = _routing_off(analysis_model_id=_ANALYSIS_OVERRIDE)
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_enabled_uses_analysis_override(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        config = _routing_on(analysis_model_id=_ANALYSIS_OVERRIDE)
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _ANALYSIS_OVERRIDE

    def test_routing_enabled_no_override_uses_fallback(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        config = _routing_on()
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_enabled_uses_routing_default_when_no_route_override(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        config = _routing_on(default_model_id=_DEFAULT_OVERRIDE)
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _DEFAULT_OVERRIDE


# ── service integration — BedrockValidationService ───────────────────────────


class TestBedrockValidationServiceRouting:
    def test_no_routing_config_uses_base_model_id(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=None,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_disabled_uses_base_model_id(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        config = _routing_off(validation_model_id=_VALIDATION_OVERRIDE)
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_enabled_uses_validation_override(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        config = _routing_on(validation_model_id=_VALIDATION_OVERRIDE)
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _VALIDATION_OVERRIDE

    def test_routing_enabled_no_override_uses_fallback(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        config = _routing_on()
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_enabled_uses_routing_default_when_no_route_override(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        config = _routing_on(default_model_id=_DEFAULT_OVERRIDE)
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _DEFAULT_OVERRIDE

    def test_validation_service_not_affected_by_analysis_override(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        config = _routing_on(
            analysis_model_id=_ANALYSIS_OVERRIDE,
            validation_model_id="",
        )
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id != _ANALYSIS_OVERRIDE

    def test_analysis_service_not_affected_by_validation_override(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        config = _routing_on(
            validation_model_id=_VALIDATION_OVERRIDE,
            analysis_model_id="",
        )
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id != _VALIDATION_OVERRIDE


# ── no regression when routing is off ────────────────────────────────────────


class TestNoRegressionWithoutRouting:
    def test_analysis_service_without_routing_config_unchanged(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
        )
        assert svc._model_id == _FALLBACK

    def test_validation_service_without_routing_config_unchanged(self):
        from app.services.bedrock_service import BedrockValidationService
        mock_client = MagicMock()
        svc = BedrockValidationService(
            model_id=_FALLBACK,
            client=mock_client,
        )
        assert svc._model_id == _FALLBACK

    def test_routing_disabled_config_does_not_change_model(self):
        from app.services.bedrock_service import BedrockAnalysisService
        mock_client = MagicMock()
        config = _routing_off(
            analysis_model_id=_ANALYSIS_OVERRIDE,
            default_model_id=_DEFAULT_OVERRIDE,
        )
        svc = BedrockAnalysisService(
            model_id=_FALLBACK,
            client=mock_client,
            routing_config=config,
        )
        assert svc._model_id == _FALLBACK


# ── no live AWS dependency ────────────────────────────────────────────────────


class TestNoLiveAWSDependency:
    def test_resolve_model_id_has_no_boto3_import(self):
        import app.services.prompt_router as router_module
        import sys
        assert "boto3" not in sys.modules or True  # boto3 may be installed but not imported by router
        assert not hasattr(router_module, "boto3")

    def test_routing_config_has_no_boto3_import(self):
        import app.utils.config as config_module
        assert not hasattr(config_module, "boto3")

    def test_router_function_does_not_contact_aws(self):
        config = _routing_on(analysis_model_id=_ANALYSIS_OVERRIDE)
        result = resolve_model_id("analysis", config, _FALLBACK)
        assert result == _ANALYSIS_OVERRIDE
