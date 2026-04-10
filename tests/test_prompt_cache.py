"""
I-0 unit tests — prompt caching integration layer.

Two test areas:

  A) apply_prompt_caching (app/services/prompt_cache.py)
     Tests the pure request-shaping function in isolation.

  B) BedrockAnalysisService and BedrockValidationService with caching
     Tests that the service layer correctly wires apply_prompt_caching into
     the Converse call, covering both the enabled and disabled code paths
     without any live AWS dependency.

Coverage:

  apply_prompt_caching — disabled path:
  - Returns input unchanged when enable_prompt_caching=False
  - Returns input unchanged when cache_system_prompt=False
  - Both disabled conditions — no copy, no allocation (identity check)

  apply_prompt_caching — enabled path:
  - Appends a cachePoint block after the last text block
  - The cachePoint has the expected {"cachePoint": {"type": "default"}} shape
  - Original text block is preserved at its original index
  - Result has exactly one more block than the input
  - Does not mutate the original list

  apply_prompt_caching — edge cases:
  - Empty system_blocks list → returns input unchanged
  - System block with no text keys → returns input unchanged
  - Multiple text blocks → cachePoint placed after the last one
  - Pre-existing cachePoint in input → preserved; new one added after last text
  - Single non-text block only → returns input unchanged

  apply_prompt_caching — determinism:
  - Same inputs always produce the same output

  Service integration — disabled (no caching_config):
  - BedrockAnalysisService without caching_config passes system as plain list
  - BedrockValidationService without caching_config passes system as plain list
  - Existing system=[{"text": ...}] structure is preserved exactly

  Service integration — disabled (caching_config provided but disabled):
  - enable_prompt_caching=False: system block is not modified

  Service integration — enabled:
  - BedrockAnalysisService with enabled config: system block includes cachePoint
  - BedrockValidationService with enabled config: system block includes cachePoint
  - cachePoint appears as the last element in the system list
  - Text content of the system block is unchanged

  Service integration — no live AWS:
  - All service tests use a mock boto3 client
  - No real Bedrock calls are made

No live AWS calls required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.prompt_cache import _CACHE_POINT_BLOCK, apply_prompt_caching
from app.utils.config import PromptCachingConfig


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config(
    *,
    enabled: bool = True,
    cache_system: bool = True,
    min_tokens: int = 1024,
    checkpoints: int = 1,
) -> PromptCachingConfig:
    return PromptCachingConfig(
        enable_prompt_caching=enabled,
        cache_system_prompt=cache_system,
        min_cacheable_tokens=min_tokens,
        max_cache_checkpoints=checkpoints,
    )


def _disabled_config() -> PromptCachingConfig:
    return _make_config(enabled=False)


def _enabled_config() -> PromptCachingConfig:
    return _make_config(enabled=True)


def _make_mock_client(response_text: str = '{"severity":"High","category":"Test","summary":"s","recommendations":["r"]}') -> MagicMock:
    """Return a mock boto3 client whose converse() returns a minimal valid response."""
    client = MagicMock()
    client.converse.return_value = {
        "output": {
            "message": {
                "content": [{"text": response_text}]
            }
        }
    }
    return client


def _make_validation_mock_client() -> MagicMock:
    client = MagicMock()
    client.converse.return_value = {
        "output": {
            "message": {
                "content": [
                    {
                        "text": '{"confidence_score":0.9,"unsupported_claims":[],"validation_status":"pass"}'
                    }
                ]
            }
        }
    }
    return client


# ── apply_prompt_caching: disabled path ───────────────────────────────────────


class TestApplyPromptCachingDisabled:
    def test_disabled_returns_input_unchanged(self):
        blocks = [{"text": "hello"}]
        result = apply_prompt_caching(blocks, _disabled_config())
        assert result is blocks  # same object — no copy

    def test_disabled_cache_system_prompt_false_returns_unchanged(self):
        blocks = [{"text": "hello"}]
        cfg = _make_config(enabled=True, cache_system=False)
        result = apply_prompt_caching(blocks, cfg)
        assert result is blocks

    def test_both_disabled_returns_same_object(self):
        blocks = [{"text": "x"}, {"text": "y"}]
        cfg = _make_config(enabled=False, cache_system=False)
        result = apply_prompt_caching(blocks, cfg)
        assert result is blocks


# ── apply_prompt_caching: enabled path ───────────────────────────────────────


class TestApplyPromptCachingEnabled:
    def test_appends_cache_point_block(self):
        blocks = [{"text": "system prompt"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert _CACHE_POINT_BLOCK in result

    def test_cache_point_shape(self):
        blocks = [{"text": "system prompt"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result[-1] == {"cachePoint": {"type": "default"}}

    def test_cache_point_placed_after_last_text_block(self):
        blocks = [{"text": "system prompt"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result[0] == {"text": "system prompt"}
        assert result[1] == _CACHE_POINT_BLOCK

    def test_result_has_one_more_block_than_input(self):
        blocks = [{"text": "system prompt"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert len(result) == len(blocks) + 1

    def test_does_not_mutate_original_list(self):
        blocks = [{"text": "system prompt"}]
        original_length = len(blocks)
        apply_prompt_caching(blocks, _enabled_config())
        assert len(blocks) == original_length

    def test_text_content_preserved_exactly(self):
        prompt = "You are a helpful analysis agent."
        blocks = [{"text": prompt}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result[0]["text"] == prompt

    def test_returns_new_list_object(self):
        blocks = [{"text": "something"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result is not blocks


# ── apply_prompt_caching: edge cases ─────────────────────────────────────────


class TestApplyPromptCachingEdgeCases:
    def test_empty_blocks_returns_unchanged(self):
        blocks: list[dict[str, Any]] = []
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result is blocks

    def test_no_text_keys_returns_unchanged(self):
        blocks = [{"cachePoint": {"type": "default"}}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result is blocks

    def test_multiple_text_blocks_cache_point_after_last(self):
        blocks = [{"text": "first"}, {"text": "second"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        # cachePoint should be at index 2, after the last text block at index 1
        assert result[0] == {"text": "first"}
        assert result[1] == {"text": "second"}
        assert result[2] == _CACHE_POINT_BLOCK

    def test_pre_existing_cache_point_is_preserved(self):
        existing_cache = {"cachePoint": {"type": "default"}}
        blocks = [{"text": "first"}, existing_cache, {"text": "second"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        # Original structure preserved; new cachePoint appended after last text block
        assert existing_cache in result
        assert result[-1] == _CACHE_POINT_BLOCK

    def test_single_non_text_block_only_unchanged(self):
        blocks = [{"other_key": "value"}]
        result = apply_prompt_caching(blocks, _enabled_config())
        assert result is blocks

    def test_blocks_after_last_text_are_preserved(self):
        """Blocks that trail the last text block should remain in their position after the new cachePoint."""
        trailing = {"cachePoint": {"type": "default"}}
        blocks = [{"text": "system"}, trailing]
        result = apply_prompt_caching(blocks, _enabled_config())
        # Last text block is at index 0; cachePoint injected at index 1;
        # the trailing block shifts to index 2.
        assert result[0] == {"text": "system"}
        assert result[1] == _CACHE_POINT_BLOCK
        assert result[2] == trailing


# ── apply_prompt_caching: determinism ────────────────────────────────────────


class TestApplyPromptCachingDeterminism:
    def test_same_inputs_same_output_enabled(self):
        blocks = [{"text": "system prompt"}]
        result1 = apply_prompt_caching(list(blocks), _enabled_config())
        result2 = apply_prompt_caching(list(blocks), _enabled_config())
        assert result1 == result2

    def test_same_inputs_same_output_disabled(self):
        blocks = [{"text": "system prompt"}]
        result1 = apply_prompt_caching(blocks, _disabled_config())
        result2 = apply_prompt_caching(blocks, _disabled_config())
        assert result1 is result2  # both return same object


# ── service integration — disabled (no caching_config) ───────────────────────


class TestBedrockAnalysisServiceNoCachingConfig:
    """No caching_config means the service behaves exactly as before I-0."""

    def test_system_passed_as_plain_text_block(self):
        from app.services.bedrock_service import BedrockAnalysisService
        from app.schemas.retrieval_models import EvidenceChunk

        mock_client = _make_mock_client()
        svc = BedrockAnalysisService(client=mock_client)
        chunk = EvidenceChunk(
            chunk_id="c1",
            text="Evidence text.",
            source_id="src-001",
            source_label="Test Source",
            excerpt="Evidence text.",
            relevance_score=0.9,
        )
        svc.analyze("doc-001", [chunk])

        call_kwargs = mock_client.converse.call_args.kwargs
        system_arg = call_kwargs["system"]
        # Without caching, system should be a plain list with one text block
        assert system_arg == [{"text": mock_client.converse.call_args.kwargs["system"][0]["text"]}]
        assert len(system_arg) == 1
        assert "cachePoint" not in str(system_arg)

    def test_no_cache_point_in_system_blocks(self):
        from app.services.bedrock_service import BedrockAnalysisService
        from app.schemas.retrieval_models import EvidenceChunk

        mock_client = _make_mock_client()
        svc = BedrockAnalysisService(client=mock_client)
        chunk = EvidenceChunk(
            chunk_id="c1", text="T", source_id="s", source_label="L", excerpt="T", relevance_score=0.8
        )
        svc.analyze("doc-001", [chunk])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert all("cachePoint" not in block for block in system_arg)


class TestBedrockValidationServiceNoCachingConfig:
    """Mirrors the analysis service tests for the validation service."""

    def _make_analysis_output(self):
        from app.schemas.analysis_models import AnalysisOutput
        return AnalysisOutput(
            document_id="doc-001",
            severity="High",
            category="Test",
            summary="Summary.",
            recommendations=["Do something."],
        )

    def test_no_cache_point_in_system_blocks(self):
        from app.services.bedrock_service import BedrockValidationService
        from app.schemas.retrieval_models import EvidenceChunk

        mock_client = _make_validation_mock_client()
        svc = BedrockValidationService(client=mock_client)
        chunk = EvidenceChunk(
            chunk_id="c1", text="T", source_id="s", source_label="L", excerpt="T", relevance_score=0.8
        )
        svc.validate("doc-001", self._make_analysis_output(), [chunk])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert all("cachePoint" not in block for block in system_arg)


# ── service integration — disabled caching_config ────────────────────────────


class TestBedrockServicesWithDisabledCachingConfig:
    def _chunk(self):
        from app.schemas.retrieval_models import EvidenceChunk
        return EvidenceChunk(
            chunk_id="c1", text="T", source_id="s", source_label="L", excerpt="T", relevance_score=0.8
        )

    def test_analysis_service_disabled_config_no_cache_point(self):
        from app.services.bedrock_service import BedrockAnalysisService

        mock_client = _make_mock_client()
        svc = BedrockAnalysisService(client=mock_client, caching_config=_disabled_config())
        svc.analyze("doc-001", [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert all("cachePoint" not in block for block in system_arg)

    def test_validation_service_disabled_config_no_cache_point(self):
        from app.services.bedrock_service import BedrockValidationService
        from app.schemas.analysis_models import AnalysisOutput

        mock_client = _make_validation_mock_client()
        analysis = AnalysisOutput(
            document_id="doc-001",
            severity="High",
            category="Test",
            summary="s",
            recommendations=["r"],
        )
        svc = BedrockValidationService(client=mock_client, caching_config=_disabled_config())
        svc.validate("doc-001", analysis, [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert all("cachePoint" not in block for block in system_arg)


# ── service integration — enabled caching_config ─────────────────────────────


class TestBedrockServicesWithEnabledCachingConfig:
    def _chunk(self):
        from app.schemas.retrieval_models import EvidenceChunk
        return EvidenceChunk(
            chunk_id="c1", text="T", source_id="s", source_label="L", excerpt="T", relevance_score=0.8
        )

    def test_analysis_service_enabled_config_has_cache_point(self):
        from app.services.bedrock_service import BedrockAnalysisService

        mock_client = _make_mock_client()
        svc = BedrockAnalysisService(client=mock_client, caching_config=_enabled_config())
        svc.analyze("doc-001", [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        cache_point_blocks = [b for b in system_arg if "cachePoint" in b]
        assert len(cache_point_blocks) == 1

    def test_analysis_service_cache_point_is_last_in_system(self):
        from app.services.bedrock_service import BedrockAnalysisService

        mock_client = _make_mock_client()
        svc = BedrockAnalysisService(client=mock_client, caching_config=_enabled_config())
        svc.analyze("doc-001", [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert system_arg[-1] == _CACHE_POINT_BLOCK

    def test_analysis_service_text_content_is_first_block(self):
        from app.services.bedrock_service import BedrockAnalysisService

        mock_client = _make_mock_client()
        svc = BedrockAnalysisService(client=mock_client, caching_config=_enabled_config())
        svc.analyze("doc-001", [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert "text" in system_arg[0]
        assert len(system_arg[0]["text"]) > 0

    def test_validation_service_enabled_config_has_cache_point(self):
        from app.services.bedrock_service import BedrockValidationService
        from app.schemas.analysis_models import AnalysisOutput

        mock_client = _make_validation_mock_client()
        analysis = AnalysisOutput(
            document_id="doc-001",
            severity="High",
            category="Test",
            summary="s",
            recommendations=["r"],
        )
        svc = BedrockValidationService(client=mock_client, caching_config=_enabled_config())
        svc.validate("doc-001", analysis, [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        cache_point_blocks = [b for b in system_arg if "cachePoint" in b]
        assert len(cache_point_blocks) == 1

    def test_validation_service_cache_point_is_last_in_system(self):
        from app.services.bedrock_service import BedrockValidationService
        from app.schemas.analysis_models import AnalysisOutput

        mock_client = _make_validation_mock_client()
        analysis = AnalysisOutput(
            document_id="doc-001",
            severity="High",
            category="Test",
            summary="s",
            recommendations=["r"],
        )
        svc = BedrockValidationService(client=mock_client, caching_config=_enabled_config())
        svc.validate("doc-001", analysis, [self._chunk()])

        system_arg = mock_client.converse.call_args.kwargs["system"]
        assert system_arg[-1] == _CACHE_POINT_BLOCK

    def test_analysis_result_still_parsed_correctly_with_caching(self):
        """Enabling caching must not break response parsing."""
        from app.services.bedrock_service import BedrockAnalysisService
        from app.schemas.analysis_models import AnalysisOutput

        mock_client = _make_mock_client(
            '{"severity":"Critical","category":"Security","summary":"Critical finding.","recommendations":["Patch immediately."]}'
        )
        svc = BedrockAnalysisService(client=mock_client, caching_config=_enabled_config())
        chunk = __import__(
            "app.schemas.retrieval_models", fromlist=["EvidenceChunk"]
        ).EvidenceChunk(chunk_id="c1", text="T", source_id="s", source_label="L", excerpt="T", relevance_score=0.9)
        result = svc.analyze("doc-001", [chunk])

        assert isinstance(result, AnalysisOutput)
        assert result.severity == "Critical"
        assert result.category == "Security"

    def test_validation_result_still_parsed_correctly_with_caching(self):
        """Enabling caching must not break validation response parsing."""
        from app.services.bedrock_service import BedrockValidationService
        from app.schemas.analysis_models import AnalysisOutput
        from app.schemas.validation_models import ValidationOutput

        mock_client = _make_validation_mock_client()
        analysis = AnalysisOutput(
            document_id="doc-001",
            severity="High",
            category="Test",
            summary="s",
            recommendations=["r"],
        )
        chunk = __import__(
            "app.schemas.retrieval_models", fromlist=["EvidenceChunk"]
        ).EvidenceChunk(chunk_id="c1", text="T", source_id="s", source_label="L", excerpt="T", relevance_score=0.9)
        svc = BedrockValidationService(client=mock_client, caching_config=_enabled_config())
        result = svc.validate("doc-001", analysis, [chunk])

        assert isinstance(result, ValidationOutput)
        assert result.confidence_score == 0.9


# ── no live AWS confirmation ──────────────────────────────────────────────────


class TestNoLiveAwsDependency:
    """Structural check: none of the I-0 modules import live AWS clients directly."""

    def test_prompt_cache_module_has_no_boto3_import(self):
        import importlib
        import sys

        # Ensure a fresh load is not needed — the module is already in sys.modules
        mod = sys.modules.get("app.services.prompt_cache")
        if mod is None:
            mod = importlib.import_module("app.services.prompt_cache")
        # boto3 should not be a direct dependency of the caching module
        import inspect
        source = inspect.getsource(mod)
        assert "boto3" not in source

    def test_prompt_caching_config_has_no_boto3_import(self):
        import importlib
        import sys
        import inspect

        mod = sys.modules.get("app.utils.config")
        if mod is None:
            mod = importlib.import_module("app.utils.config")
        source = inspect.getsource(mod)
        assert "boto3" not in source
