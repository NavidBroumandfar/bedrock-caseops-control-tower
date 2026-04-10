"""
Environment and configuration loading — E-0.

Centralises all environment variable reading for the CaseOps pipeline.
Each section maps to one functional area.  All values have safe defaults.

Design:
  - One function per config area to keep concerns separate
  - No global state: callers get a plain dataclass they can inspect
  - All values can be overridden via environment variables
  - Designed to be called once at startup and passed around as a config object

Usage:
    from app.utils.config import load_logging_config
    cfg = load_logging_config()
    # cfg.log_level, cfg.enable_cloudwatch, ...
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ── observability config ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ObservabilityConfig:
    """Observability and logging configuration for the CaseOps pipeline."""

    log_level: str
    enable_local_file_log: bool
    enable_cloudwatch: bool
    cloudwatch_log_group: str
    cloudwatch_log_stream_prefix: str
    output_dir: str
    aws_region: str


def load_observability_config() -> ObservabilityConfig:
    """
    Load observability config from environment variables.

    Environment variables:
      CASEOPS_LOG_LEVEL                    DEBUG | INFO | WARNING | ERROR
      CASEOPS_ENABLE_LOCAL_FILE_LOG        true | false  (default true)
      CASEOPS_ENABLE_CLOUDWATCH            true | false  (default false)
      CASEOPS_CLOUDWATCH_LOG_GROUP         (default /caseops/pipeline)
      CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX (default caseops-session)
      OUTPUT_DIR                           (default outputs)
      AWS_REGION                           (default us-east-1)
    """
    log_level = (
        os.getenv("CASEOPS_LOG_LEVEL")
        or os.getenv("LOG_LEVEL", "INFO")
    ).upper()

    enable_local_file_log = os.getenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", "true").lower() != "false"
    enable_cloudwatch = os.getenv("CASEOPS_ENABLE_CLOUDWATCH", "false").lower() == "true"

    cloudwatch_log_group = (
        os.getenv("CASEOPS_CLOUDWATCH_LOG_GROUP")
        or os.getenv("CLOUDWATCH_LOG_GROUP", "/caseops/pipeline")
    )
    cloudwatch_log_stream_prefix = os.getenv(
        "CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX", "caseops-session"
    )

    output_dir = os.getenv("OUTPUT_DIR", "outputs")
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    return ObservabilityConfig(
        log_level=log_level,
        enable_local_file_log=enable_local_file_log,
        enable_cloudwatch=enable_cloudwatch,
        cloudwatch_log_group=cloudwatch_log_group,
        cloudwatch_log_stream_prefix=cloudwatch_log_stream_prefix,
        output_dir=output_dir,
        aws_region=aws_region,
    )


# ── pipeline config ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineConfig:
    """Core pipeline configuration."""

    retrieval_max_results: int
    escalation_confidence_threshold: float
    max_agent_retries: int
    bedrock_model_id: str
    bedrock_kb_id: str
    aws_region: str
    s3_document_bucket: str
    s3_output_bucket: str


def load_pipeline_config() -> PipelineConfig:
    """
    Load pipeline config from environment variables.

    Environment variables:
      RETRIEVAL_MAX_RESULTS              (default 5)
      ESCALATION_CONFIDENCE_THRESHOLD    (default 0.60)
      MAX_AGENT_RETRIES                  (default 2)
      BEDROCK_MODEL_ID
      BEDROCK_KB_ID
      AWS_REGION                         (default us-east-1)
      S3_DOCUMENT_BUCKET
      S3_OUTPUT_BUCKET
    """
    return PipelineConfig(
        retrieval_max_results=int(os.getenv("RETRIEVAL_MAX_RESULTS", "5")),
        escalation_confidence_threshold=float(
            os.getenv("ESCALATION_CONFIDENCE_THRESHOLD", "0.60")
        ),
        max_agent_retries=int(os.getenv("MAX_AGENT_RETRIES", "2")),
        bedrock_model_id=os.getenv(
            "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
        ),
        bedrock_kb_id=os.getenv("BEDROCK_KB_ID", ""),
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        s3_document_bucket=os.getenv("S3_DOCUMENT_BUCKET", ""),
        s3_output_bucket=os.getenv("S3_OUTPUT_BUCKET", ""),
    )


# ── prompt caching config (I-0) ──────────────────────────────────────────────


@dataclass(frozen=True)
class PromptCachingConfig:
    """
    Prompt caching integration configuration (I-0).

    When enable_prompt_caching is False (default), the Bedrock invocation path
    is entirely unchanged.  When True, the service layer injects a cachePoint
    marker into the Converse system block so repeated calls with identical
    system prompts benefit from Bedrock-side prompt caching.

    Design notes:
    - Off by default so existing deployments are unaffected without opt-in.
    - cache_system_prompt controls whether the system block is eligible for
      caching; set to False to disable caching even when the feature is on.
    - min_cacheable_tokens is informational — Bedrock enforces a 1024-token
      minimum on the server side; this field lets operators document that
      constraint explicitly and is validated on load.
    - max_cache_checkpoints declares the intended ceiling for cachePoint
      markers per request (Bedrock allows up to 4).  In I-0,
      apply_prompt_caching always injects exactly one checkpoint (the system
      block) and does not read this field — enforcement is deferred to I-1
      when message-level caching is introduced.
    """

    enable_prompt_caching: bool
    cache_system_prompt: bool
    min_cacheable_tokens: int
    max_cache_checkpoints: int


def load_prompt_caching_config() -> PromptCachingConfig:
    """
    Load prompt caching config from environment variables.

    Environment variables:
      CASEOPS_ENABLE_PROMPT_CACHING     true | false  (default false)
      CASEOPS_CACHE_SYSTEM_PROMPT       true | false  (default true)
      CASEOPS_MIN_CACHEABLE_TOKENS      integer >= 1  (default 1024)
      CASEOPS_MAX_CACHE_CHECKPOINTS     integer 1–4   (default 1)

    Raises ValueError on invalid values so misconfigured deployments fail
    loudly at startup rather than silently proceeding with wrong behaviour.
    """
    enable_prompt_caching = (
        os.getenv("CASEOPS_ENABLE_PROMPT_CACHING", "false").lower() == "true"
    )
    cache_system_prompt = (
        os.getenv("CASEOPS_CACHE_SYSTEM_PROMPT", "true").lower() != "false"
    )

    raw_min_tokens = os.getenv("CASEOPS_MIN_CACHEABLE_TOKENS", "1024")
    try:
        min_cacheable_tokens = int(raw_min_tokens)
    except ValueError as exc:
        raise ValueError(
            f"CASEOPS_MIN_CACHEABLE_TOKENS must be a positive integer, got: {raw_min_tokens!r}"
        ) from exc
    if min_cacheable_tokens < 1:
        raise ValueError(
            f"CASEOPS_MIN_CACHEABLE_TOKENS must be >= 1, got: {min_cacheable_tokens}"
        )

    raw_checkpoints = os.getenv("CASEOPS_MAX_CACHE_CHECKPOINTS", "1")
    try:
        max_cache_checkpoints = int(raw_checkpoints)
    except ValueError as exc:
        raise ValueError(
            f"CASEOPS_MAX_CACHE_CHECKPOINTS must be an integer 1–4, got: {raw_checkpoints!r}"
        ) from exc
    if not (1 <= max_cache_checkpoints <= 4):
        raise ValueError(
            f"CASEOPS_MAX_CACHE_CHECKPOINTS must be between 1 and 4 inclusive, "
            f"got: {max_cache_checkpoints}"
        )

    return PromptCachingConfig(
        enable_prompt_caching=enable_prompt_caching,
        cache_system_prompt=cache_system_prompt,
        min_cacheable_tokens=min_cacheable_tokens,
        max_cache_checkpoints=max_cache_checkpoints,
    )


# ── guardrails config (H-1) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardrailsConfig:
    """Bedrock Guardrails integration configuration (H-1)."""

    enable_guardrails: bool
    guardrail_id: str
    guardrail_version: str
    guardrail_trace: bool


def load_guardrails_config() -> GuardrailsConfig:
    """
    Load Bedrock Guardrails config from environment variables.

    Environment variables:
      CASEOPS_ENABLE_GUARDRAILS    true | false  (default false)
      CASEOPS_GUARDRAIL_ID         Guardrail identifier (default "")
      CASEOPS_GUARDRAIL_VERSION    Guardrail version string (default "1")
      CASEOPS_GUARDRAIL_TRACE      true | false  (default false)
    """
    enable_guardrails = (
        os.getenv("CASEOPS_ENABLE_GUARDRAILS", "false").lower() == "true"
    )
    guardrail_id = os.getenv("CASEOPS_GUARDRAIL_ID", "")
    guardrail_version = os.getenv("CASEOPS_GUARDRAIL_VERSION", "1")
    guardrail_trace = (
        os.getenv("CASEOPS_GUARDRAIL_TRACE", "false").lower() == "true"
    )

    return GuardrailsConfig(
        enable_guardrails=enable_guardrails,
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
        guardrail_trace=guardrail_trace,
    )
