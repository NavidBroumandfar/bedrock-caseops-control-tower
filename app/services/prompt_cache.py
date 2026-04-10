"""
Prompt caching integration layer — I-0.

Single responsibility: shape the Bedrock Converse `system` block to include
a cachePoint marker when prompt caching is enabled.

This module is the only place in the codebase that knows how to inject
prompt-caching directives into a Converse request.  It is:

  - Optional: callers pass their existing system blocks; when caching is
    disabled the input is returned unchanged.
  - Isolated: no business logic lives here; prompt construction stays in
    bedrock_service.py.
  - Testable without live AWS: the function is pure — same inputs, same output.

Bedrock prompt caching constraints (as of 2026):
  - Supported models: Claude 3.5 Sonnet, Claude 3.7 Sonnet (and later releases).
  - Minimum cacheable tokens: 1024 (enforced server-side; requests below this
    threshold are served normally without error).
  - Maximum cachePoint markers per request: 4.
  - cachePoint placement: immediately after the content block to be cached.
    Bedrock caches everything up to and including the preceding content block.

I-0 scope: cachePoint injection into the system block only.  Message-level
caching (e.g. few-shot examples in the user turn) is deferred to I-1.

Usage:
    from app.services.prompt_cache import apply_prompt_caching
    from app.utils.config import load_prompt_caching_config

    cfg = load_prompt_caching_config()
    system_blocks = [{"text": my_system_prompt}]
    cacheable_system = apply_prompt_caching(system_blocks, cfg)
    # pass cacheable_system to client.converse(system=cacheable_system, ...)
"""

from __future__ import annotations

from typing import Any

from app.utils.config import PromptCachingConfig

# The Bedrock cachePoint block type — a constant so tests and callers
# can import the sentinel rather than hardcoding the dict shape.
_CACHE_POINT_BLOCK: dict[str, Any] = {"cachePoint": {"type": "default"}}


def apply_prompt_caching(
    system_blocks: list[dict[str, Any]],
    config: PromptCachingConfig,
) -> list[dict[str, Any]]:
    """
    Return a system block list with a cachePoint injected after the last text
    block, when prompt caching is enabled and configured for system prompts.

    When caching is disabled, or cache_system_prompt is False, or there are no
    text blocks to cache, the original list is returned unchanged (no copy, no
    allocation).

    Args:
        system_blocks: The list of block dicts that would be passed to
            ``client.converse(system=...)``.  Each dict is either a
            ``{"text": "..."}`` content block or a pre-existing cachePoint.
        config: PromptCachingConfig loaded from environment variables.

    Returns:
        The same list (disabled path) or a new list with a cachePoint appended
        after the last text block (enabled path).
    """
    if not config.enable_prompt_caching or not config.cache_system_prompt:
        return system_blocks

    # Find the last text block index so we place the cachePoint immediately
    # after it.  Any pre-existing cachePoints in the input are preserved.
    last_text_index = _find_last_text_block_index(system_blocks)
    if last_text_index == -1:
        # Nothing to cache — no text blocks present.
        return system_blocks

    # Build the result: blocks up to and including the last text block,
    # then the cachePoint, then anything that follows.
    result: list[dict[str, Any]] = list(system_blocks[: last_text_index + 1])
    result.append(_CACHE_POINT_BLOCK)
    result.extend(system_blocks[last_text_index + 1 :])
    return result


def _find_last_text_block_index(blocks: list[dict[str, Any]]) -> int:
    """Return the index of the last block that contains a 'text' key, or -1."""
    for i in range(len(blocks) - 1, -1, -1):
        if "text" in blocks[i]:
            return i
    return -1
