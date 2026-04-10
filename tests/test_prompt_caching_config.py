"""
I-0 unit tests — PromptCachingConfig loading from environment variables.

Coverage:

  PromptCachingConfig — defaults:
  - enable_prompt_caching defaults to False
  - cache_system_prompt defaults to True
  - min_cacheable_tokens defaults to 1024
  - max_cache_checkpoints defaults to 1

  PromptCachingConfig — env var overrides:
  - CASEOPS_ENABLE_PROMPT_CACHING=true enables caching
  - CASEOPS_ENABLE_PROMPT_CACHING=false stays disabled
  - CASEOPS_CACHE_SYSTEM_PROMPT=false disables system prompt caching
  - CASEOPS_MIN_CACHEABLE_TOKENS overrides the minimum token count
  - CASEOPS_MAX_CACHE_CHECKPOINTS overrides the checkpoint limit

  PromptCachingConfig — case insensitivity:
  - CASEOPS_ENABLE_PROMPT_CACHING=TRUE (uppercase) is accepted
  - CASEOPS_CACHE_SYSTEM_PROMPT=FALSE (uppercase) is accepted

  PromptCachingConfig — validation (invalid values raise ValueError):
  - CASEOPS_MIN_CACHEABLE_TOKENS with a non-integer value
  - CASEOPS_MIN_CACHEABLE_TOKENS=0 (below minimum)
  - CASEOPS_MIN_CACHEABLE_TOKENS=-1 (below minimum)
  - CASEOPS_MAX_CACHE_CHECKPOINTS with a non-integer value
  - CASEOPS_MAX_CACHE_CHECKPOINTS=0 (below allowed range)
  - CASEOPS_MAX_CACHE_CHECKPOINTS=5 (above Bedrock limit of 4)

  PromptCachingConfig — immutability:
  - Returns a frozen dataclass (cannot mutate fields)

  PromptCachingConfig — boundary values:
  - min_cacheable_tokens=1 (minimum valid)
  - max_cache_checkpoints=1 (minimum valid)
  - max_cache_checkpoints=4 (maximum valid)

No live AWS calls required.  All values are loaded from environment variables only.
"""

import pytest

from app.utils.config import PromptCachingConfig, load_prompt_caching_config


# ── defaults ─────────────────────────────────────────────────────────────────


class TestPromptCachingConfigDefaults:
    def test_enable_prompt_caching_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_CACHING", raising=False)
        cfg = load_prompt_caching_config()
        assert cfg.enable_prompt_caching is False

    def test_cache_system_prompt_defaults_to_true(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_CACHE_SYSTEM_PROMPT", raising=False)
        cfg = load_prompt_caching_config()
        assert cfg.cache_system_prompt is True

    def test_min_cacheable_tokens_defaults_to_1024(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_MIN_CACHEABLE_TOKENS", raising=False)
        cfg = load_prompt_caching_config()
        assert cfg.min_cacheable_tokens == 1024

    def test_max_cache_checkpoints_defaults_to_1(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_MAX_CACHE_CHECKPOINTS", raising=False)
        cfg = load_prompt_caching_config()
        assert cfg.max_cache_checkpoints == 1

    def test_all_defaults_at_once(self, monkeypatch):
        for var in [
            "CASEOPS_ENABLE_PROMPT_CACHING",
            "CASEOPS_CACHE_SYSTEM_PROMPT",
            "CASEOPS_MIN_CACHEABLE_TOKENS",
            "CASEOPS_MAX_CACHE_CHECKPOINTS",
        ]:
            monkeypatch.delenv(var, raising=False)
        cfg = load_prompt_caching_config()
        assert cfg.enable_prompt_caching is False
        assert cfg.cache_system_prompt is True
        assert cfg.min_cacheable_tokens == 1024
        assert cfg.max_cache_checkpoints == 1


# ── env var overrides ─────────────────────────────────────────────────────────


class TestPromptCachingConfigOverrides:
    def test_enable_prompt_caching_true(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_CACHING", "true")
        cfg = load_prompt_caching_config()
        assert cfg.enable_prompt_caching is True

    def test_enable_prompt_caching_false_explicit(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_CACHING", "false")
        cfg = load_prompt_caching_config()
        assert cfg.enable_prompt_caching is False

    def test_cache_system_prompt_false(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_CACHE_SYSTEM_PROMPT", "false")
        cfg = load_prompt_caching_config()
        assert cfg.cache_system_prompt is False

    def test_cache_system_prompt_true_explicit(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_CACHE_SYSTEM_PROMPT", "true")
        cfg = load_prompt_caching_config()
        assert cfg.cache_system_prompt is True

    def test_min_cacheable_tokens_override(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MIN_CACHEABLE_TOKENS", "2048")
        cfg = load_prompt_caching_config()
        assert cfg.min_cacheable_tokens == 2048

    def test_max_cache_checkpoints_override(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "3")
        cfg = load_prompt_caching_config()
        assert cfg.max_cache_checkpoints == 3


# ── case insensitivity ────────────────────────────────────────────────────────


class TestPromptCachingConfigCaseInsensitivity:
    def test_enable_caching_uppercase_true(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_CACHING", "TRUE")
        cfg = load_prompt_caching_config()
        assert cfg.enable_prompt_caching is True

    def test_enable_caching_mixed_case(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_CACHING", "True")
        cfg = load_prompt_caching_config()
        assert cfg.enable_prompt_caching is True

    def test_cache_system_prompt_uppercase_false(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_CACHE_SYSTEM_PROMPT", "FALSE")
        cfg = load_prompt_caching_config()
        assert cfg.cache_system_prompt is False


# ── validation — invalid values ───────────────────────────────────────────────


class TestPromptCachingConfigValidation:
    def test_min_cacheable_tokens_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MIN_CACHEABLE_TOKENS", "not-a-number")
        with pytest.raises(ValueError, match="CASEOPS_MIN_CACHEABLE_TOKENS"):
            load_prompt_caching_config()

    def test_min_cacheable_tokens_zero_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MIN_CACHEABLE_TOKENS", "0")
        with pytest.raises(ValueError, match="CASEOPS_MIN_CACHEABLE_TOKENS"):
            load_prompt_caching_config()

    def test_min_cacheable_tokens_negative_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MIN_CACHEABLE_TOKENS", "-1")
        with pytest.raises(ValueError, match="CASEOPS_MIN_CACHEABLE_TOKENS"):
            load_prompt_caching_config()

    def test_max_cache_checkpoints_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "abc")
        with pytest.raises(ValueError, match="CASEOPS_MAX_CACHE_CHECKPOINTS"):
            load_prompt_caching_config()

    def test_max_cache_checkpoints_zero_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "0")
        with pytest.raises(ValueError, match="CASEOPS_MAX_CACHE_CHECKPOINTS"):
            load_prompt_caching_config()

    def test_max_cache_checkpoints_above_bedrock_limit_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "5")
        with pytest.raises(ValueError, match="CASEOPS_MAX_CACHE_CHECKPOINTS"):
            load_prompt_caching_config()

    def test_max_cache_checkpoints_float_string_raises(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "1.5")
        with pytest.raises(ValueError, match="CASEOPS_MAX_CACHE_CHECKPOINTS"):
            load_prompt_caching_config()


# ── boundary values ───────────────────────────────────────────────────────────


class TestPromptCachingConfigBoundaryValues:
    def test_min_cacheable_tokens_minimum_valid_value(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MIN_CACHEABLE_TOKENS", "1")
        cfg = load_prompt_caching_config()
        assert cfg.min_cacheable_tokens == 1

    def test_max_cache_checkpoints_minimum_valid(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "1")
        cfg = load_prompt_caching_config()
        assert cfg.max_cache_checkpoints == 1

    def test_max_cache_checkpoints_maximum_valid(self, monkeypatch):
        monkeypatch.setenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "4")
        cfg = load_prompt_caching_config()
        assert cfg.max_cache_checkpoints == 4


# ── immutability ──────────────────────────────────────────────────────────────


class TestPromptCachingConfigImmutability:
    def test_config_is_frozen_dataclass(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_CACHING", raising=False)
        cfg = load_prompt_caching_config()
        assert isinstance(cfg, PromptCachingConfig)
        with pytest.raises(Exception):
            cfg.enable_prompt_caching = True  # type: ignore[misc]

    def test_cannot_mutate_min_cacheable_tokens(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_MIN_CACHEABLE_TOKENS", raising=False)
        cfg = load_prompt_caching_config()
        with pytest.raises(Exception):
            cfg.min_cacheable_tokens = 9999  # type: ignore[misc]


# ── return type ───────────────────────────────────────────────────────────────


class TestPromptCachingConfigReturnType:
    def test_returns_prompt_caching_config_instance(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_CACHING", raising=False)
        cfg = load_prompt_caching_config()
        assert isinstance(cfg, PromptCachingConfig)

    def test_enable_prompt_caching_is_bool(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_ENABLE_PROMPT_CACHING", raising=False)
        cfg = load_prompt_caching_config()
        assert type(cfg.enable_prompt_caching) is bool

    def test_cache_system_prompt_is_bool(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_CACHE_SYSTEM_PROMPT", raising=False)
        cfg = load_prompt_caching_config()
        assert type(cfg.cache_system_prompt) is bool

    def test_min_cacheable_tokens_is_int(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_MIN_CACHEABLE_TOKENS", raising=False)
        cfg = load_prompt_caching_config()
        assert type(cfg.min_cacheable_tokens) is int

    def test_max_cache_checkpoints_is_int(self, monkeypatch):
        monkeypatch.delenv("CASEOPS_MAX_CACHE_CHECKPOINTS", raising=False)
        cfg = load_prompt_caching_config()
        assert type(cfg.max_cache_checkpoints) is int
