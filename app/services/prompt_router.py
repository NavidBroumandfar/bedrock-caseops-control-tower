"""
Prompt routing layer — I-1.

Determines which Bedrock model ID to use for a given pipeline route
(analysis or validation) based on the PromptRoutingConfig.

Public surface:
  PromptRoute         — the supported route names for I-1
  resolve_model_id()  — pure function; no AWS, no I/O, no side-effects

Design:
  - Deterministic: same inputs always produce the same output
  - Isolated: no boto3 dependency, no config loading, no service imports
  - Honest scope: only task-based routing (analysis, validation) exists in I-1
  - Easy to extend: add new PromptRoute literals and handle them in resolve_model_id

Resolution priority when routing is enabled:
  1. Route-specific override (analysis_model_id or validation_model_id)
  2. routing_config.default_model_id
  3. fallback_model_id (caller-supplied; typically from BEDROCK_MODEL_ID env)

When routing is disabled the fallback is returned unchanged, so callers that
never set CASEOPS_ENABLE_PROMPT_ROUTING=true see no change in behaviour.
"""

from __future__ import annotations

from typing import Literal

from app.utils.config import PromptRoutingConfig

# Supported route names for I-1.
# Extend this type — and add a branch in resolve_model_id — for future routes.
PromptRoute = Literal["analysis", "validation"]


def resolve_model_id(
    route: PromptRoute,
    routing_config: PromptRoutingConfig,
    fallback_model_id: str,
) -> str:
    """
    Resolve the Bedrock model ID for the given route.

    Args:
        route:            The pipeline route making the request.
                          Must be one of the PromptRoute literals.
        routing_config:   The loaded PromptRoutingConfig.
        fallback_model_id: The model ID to use when routing is disabled or all
                          overrides are absent.  Callers typically derive this
                          from BEDROCK_MODEL_ID or a hardcoded safe default.

    Returns:
        A non-empty model ID string.  When all routing values are empty strings
        the fallback_model_id is returned, which is always non-empty as long as
        callers supply a valid default.

    Resolution priority (routing enabled):
      route-specific override → routing default → caller fallback

    Resolution priority (routing disabled):
      caller fallback (no routing logic applied)
    """
    if not routing_config.enable_prompt_routing:
        return fallback_model_id

    route_override = _get_route_override(route, routing_config)

    return route_override or routing_config.default_model_id or fallback_model_id


def _get_route_override(route: PromptRoute, config: PromptRoutingConfig) -> str:
    """Return the route-specific model ID override, or an empty string if unset."""
    if route == "analysis":
        return config.analysis_model_id
    if route == "validation":
        return config.validation_model_id
    # Unreachable for valid PromptRoute values, but safe to fall through.
    return ""
