"""
E-2 unit tests — config loading from environment variables.

Coverage:

  ObservabilityConfig — defaults:
  - log_level defaults to "INFO"
  - enable_local_file_log defaults to True
  - enable_cloudwatch defaults to False
  - cloudwatch_log_group defaults to "/caseops/pipeline"
  - cloudwatch_log_stream_prefix defaults to "caseops-session"
  - output_dir defaults to "outputs"
  - aws_region defaults to "us-east-1"

  ObservabilityConfig — env var overrides:
  - CASEOPS_LOG_LEVEL sets log_level (normalised to uppercase)
  - LOG_LEVEL fallback is accepted when CASEOPS_LOG_LEVEL is absent
  - CASEOPS_ENABLE_CLOUDWATCH=true enables CloudWatch
  - CASEOPS_ENABLE_CLOUDWATCH=false (explicit) stays disabled
  - CASEOPS_ENABLE_LOCAL_FILE_LOG=false disables local file logging
  - CASEOPS_CLOUDWATCH_LOG_GROUP overrides log group
  - CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX overrides stream prefix
  - OUTPUT_DIR overrides output directory
  - AWS_REGION overrides region

  ObservabilityConfig — edge cases:
  - CASEOPS_LOG_LEVEL lowercase value is normalised to uppercase
  - CASEOPS_ENABLE_CLOUDWATCH with unexpected value stays False

  PipelineConfig — defaults:
  - retrieval_max_results defaults to 5
  - escalation_confidence_threshold defaults to 0.60
  - max_agent_retries defaults to 2
  - bedrock_model_id defaults to Claude 3 Haiku
  - bedrock_kb_id defaults to empty string (validation is in kb_service)
  - aws_region defaults to "us-east-1"
  - s3_document_bucket defaults to empty string
  - s3_output_bucket defaults to empty string

  PipelineConfig — env var overrides:
  - RETRIEVAL_MAX_RESULTS overrides max results
  - ESCALATION_CONFIDENCE_THRESHOLD overrides threshold
  - MAX_AGENT_RETRIES overrides retry count
  - BEDROCK_MODEL_ID overrides model
  - BEDROCK_KB_ID overrides KB id
  - AWS_REGION overrides region
  - S3_DOCUMENT_BUCKET overrides document bucket
  - S3_OUTPUT_BUCKET overrides output bucket

  Both configs return frozen dataclasses (immutable after construction).

No live AWS calls required.  All values are loaded from environment variables only.
"""

import pytest

from app.utils.config import (
    ObservabilityConfig,
    PipelineConfig,
    load_observability_config,
    load_pipeline_config,
)


# ── ObservabilityConfig: defaults ─────────────────────────────────────────────


def test_observability_log_level_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    cfg = load_observability_config()
    assert cfg.log_level == "INFO"


def test_observability_enable_local_file_log_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", raising=False)
    cfg = load_observability_config()
    assert cfg.enable_local_file_log is True


def test_observability_enable_cloudwatch_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_ENABLE_CLOUDWATCH", raising=False)
    cfg = load_observability_config()
    assert cfg.enable_cloudwatch is False


def test_observability_cloudwatch_log_group_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_CLOUDWATCH_LOG_GROUP", raising=False)
    monkeypatch.delenv("CLOUDWATCH_LOG_GROUP", raising=False)
    cfg = load_observability_config()
    assert cfg.cloudwatch_log_group == "/caseops/pipeline"


def test_observability_cloudwatch_log_stream_prefix_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX", raising=False)
    cfg = load_observability_config()
    assert cfg.cloudwatch_log_stream_prefix == "caseops-session"


def test_observability_output_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    cfg = load_observability_config()
    assert cfg.output_dir == "outputs"


def test_observability_aws_region_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    cfg = load_observability_config()
    assert cfg.aws_region == "us-east-1"


# ── ObservabilityConfig: env var overrides ────────────────────────────────────


def test_observability_log_level_caseops_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_LOG_LEVEL", "DEBUG")
    cfg = load_observability_config()
    assert cfg.log_level == "DEBUG"


def test_observability_log_level_fallback_log_level_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_LOG_LEVEL", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    cfg = load_observability_config()
    assert cfg.log_level == "WARNING"


def test_observability_log_level_caseops_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    cfg = load_observability_config()
    assert cfg.log_level == "ERROR"


def test_observability_enable_cloudwatch_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_CLOUDWATCH", "true")
    cfg = load_observability_config()
    assert cfg.enable_cloudwatch is True


def test_observability_enable_cloudwatch_false_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_CLOUDWATCH", "false")
    cfg = load_observability_config()
    assert cfg.enable_cloudwatch is False


def test_observability_enable_local_file_log_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", "false")
    cfg = load_observability_config()
    assert cfg.enable_local_file_log is False


def test_observability_enable_local_file_log_true_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", "true")
    cfg = load_observability_config()
    assert cfg.enable_local_file_log is True


def test_observability_cloudwatch_log_group_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_CLOUDWATCH_LOG_GROUP", "/custom/log/group")
    cfg = load_observability_config()
    assert cfg.cloudwatch_log_group == "/custom/log/group"


def test_observability_cloudwatch_log_stream_prefix_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX", "my-prefix")
    cfg = load_observability_config()
    assert cfg.cloudwatch_log_stream_prefix == "my-prefix"


def test_observability_output_dir_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", "/tmp/custom_outputs")
    cfg = load_observability_config()
    assert cfg.output_dir == "/tmp/custom_outputs"


def test_observability_aws_region_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    cfg = load_observability_config()
    assert cfg.aws_region == "eu-west-1"


# ── ObservabilityConfig: edge cases ──────────────────────────────────────────


def test_observability_log_level_lowercase_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Log level values must be uppercased regardless of how they are set."""
    monkeypatch.setenv("CASEOPS_LOG_LEVEL", "debug")
    cfg = load_observability_config()
    assert cfg.log_level == "DEBUG"


def test_observability_enable_cloudwatch_unexpected_value_stays_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any value that is not exactly 'true' must leave CloudWatch disabled."""
    monkeypatch.setenv("CASEOPS_ENABLE_CLOUDWATCH", "yes")
    cfg = load_observability_config()
    assert cfg.enable_cloudwatch is False


def test_observability_config_is_frozen() -> None:
    """ObservabilityConfig must be a frozen dataclass — it must not allow mutation."""
    cfg = load_observability_config()
    with pytest.raises((AttributeError, TypeError)):
        cfg.log_level = "DEBUG"  # type: ignore[misc]


def test_observability_returns_observability_config_type() -> None:
    cfg = load_observability_config()
    assert isinstance(cfg, ObservabilityConfig)


# ── PipelineConfig: defaults ──────────────────────────────────────────────────


def test_pipeline_retrieval_max_results_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRIEVAL_MAX_RESULTS", raising=False)
    cfg = load_pipeline_config()
    assert cfg.retrieval_max_results == 5


def test_pipeline_escalation_threshold_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ESCALATION_CONFIDENCE_THRESHOLD", raising=False)
    cfg = load_pipeline_config()
    assert cfg.escalation_confidence_threshold == pytest.approx(0.60)


def test_pipeline_max_agent_retries_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_AGENT_RETRIES", raising=False)
    cfg = load_pipeline_config()
    assert cfg.max_agent_retries == 2


def test_pipeline_bedrock_model_id_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    cfg = load_pipeline_config()
    assert cfg.bedrock_model_id == "anthropic.claude-3-haiku-20240307-v1:0"


def test_pipeline_bedrock_kb_id_default_is_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """BEDROCK_KB_ID defaults to empty string; validation is the KB service's responsibility."""
    monkeypatch.delenv("BEDROCK_KB_ID", raising=False)
    cfg = load_pipeline_config()
    assert cfg.bedrock_kb_id == ""


def test_pipeline_aws_region_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    cfg = load_pipeline_config()
    assert cfg.aws_region == "us-east-1"


def test_pipeline_s3_document_bucket_default_is_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_DOCUMENT_BUCKET", raising=False)
    cfg = load_pipeline_config()
    assert cfg.s3_document_bucket == ""


def test_pipeline_s3_output_bucket_default_is_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_OUTPUT_BUCKET", raising=False)
    cfg = load_pipeline_config()
    assert cfg.s3_output_bucket == ""


# ── PipelineConfig: env var overrides ────────────────────────────────────────


def test_pipeline_retrieval_max_results_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "10")
    cfg = load_pipeline_config()
    assert cfg.retrieval_max_results == 10


def test_pipeline_escalation_threshold_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESCALATION_CONFIDENCE_THRESHOLD", "0.75")
    cfg = load_pipeline_config()
    assert cfg.escalation_confidence_threshold == pytest.approx(0.75)


def test_pipeline_max_agent_retries_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_AGENT_RETRIES", "3")
    cfg = load_pipeline_config()
    assert cfg.max_agent_retries == 3


def test_pipeline_bedrock_model_id_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    cfg = load_pipeline_config()
    assert cfg.bedrock_model_id == "anthropic.claude-3-sonnet-20240229-v1:0"


def test_pipeline_bedrock_kb_id_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_KB_ID", "abc123def456")
    cfg = load_pipeline_config()
    assert cfg.bedrock_kb_id == "abc123def456"


def test_pipeline_aws_region_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    cfg = load_pipeline_config()
    assert cfg.aws_region == "us-west-2"


def test_pipeline_s3_document_bucket_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_DOCUMENT_BUCKET", "my-document-bucket")
    cfg = load_pipeline_config()
    assert cfg.s3_document_bucket == "my-document-bucket"


def test_pipeline_s3_output_bucket_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_OUTPUT_BUCKET", "my-output-bucket")
    cfg = load_pipeline_config()
    assert cfg.s3_output_bucket == "my-output-bucket"


# ── PipelineConfig: type correctness ─────────────────────────────────────────


def test_pipeline_retrieval_max_results_is_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "7")
    cfg = load_pipeline_config()
    assert isinstance(cfg.retrieval_max_results, int)


def test_pipeline_escalation_threshold_is_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESCALATION_CONFIDENCE_THRESHOLD", "0.5")
    cfg = load_pipeline_config()
    assert isinstance(cfg.escalation_confidence_threshold, float)


def test_pipeline_max_agent_retries_is_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_AGENT_RETRIES", "1")
    cfg = load_pipeline_config()
    assert isinstance(cfg.max_agent_retries, int)


def test_pipeline_config_is_frozen() -> None:
    """PipelineConfig must be a frozen dataclass — it must not allow mutation."""
    cfg = load_pipeline_config()
    with pytest.raises((AttributeError, TypeError)):
        cfg.bedrock_kb_id = "mutated"  # type: ignore[misc]


def test_pipeline_returns_pipeline_config_type() -> None:
    cfg = load_pipeline_config()
    assert isinstance(cfg, PipelineConfig)
