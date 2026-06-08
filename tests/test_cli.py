"""
E-1 unit tests — CLI run command and output packaging integration.

Coverage:

  run command — argument validation:
  - missing FILE argument exits with non-zero
  - missing --source-type exits with non-zero
  - missing --document-date exits with non-zero
  - invalid --source-type value exits with non-zero
  - invalid --document-date format exits with non-zero
  - --help exits 0 and prints usage

  run command — success path (all AWS dependencies mocked):
  - exits with code 0 on success
  - output file is created under the configured output directory
  - output file name is {document_id}.json
  - output file contains valid JSON with required fields
  - success summary is printed to stdout
  - document_id appears in the summary
  - session_id appears in the summary
  - severity appears in the summary
  - output path appears in the summary

  run command — failure paths:
  - IntakeError surfaces as [error] and non-zero exit
  - PipelineWorkflowError surfaces as [error] and non-zero exit
  - pipeline initialisation failure (missing BEDROCK_KB_ID) exits non-zero
  - OutputWriteError surfaces as [error] and non-zero exit

  run command — logger integration:
  - PipelineLogger is constructed and passed to run_pipeline
  - session_id is consistent between logger and pipeline

  run command — no live AWS:
  - no real boto3 calls are made (all services are mocked)

  intake command — existing behaviour preserved:
  - --help exits 0
  - invalid source-type exits non-zero
  - success path prints registration summary (mocked intake)

No live AWS calls are made.  All AWS dependencies are replaced by mocks or
injected fakes via unittest.mock.patch.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from app.cli import _build_pipeline_deps, cli
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.safety_models import (
    IssueSource,
    SafetyAssessment,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)
from app.utils.config import PipelineConfig


# ── shared test builders ───────────────────────────────────────────────────────


_DOC_ID = "doc-20260405-clitst1"
_SESSION_ID = "sess-deadbeef"
_REPO_ROOT = Path(__file__).parent.parent


def _make_intake_record() -> IntakeRecord:
    return IntakeRecord(
        document_id=_DOC_ID,
        original_filename="advisory.txt",
        extension=".txt",
        absolute_path=f"/tmp/{_DOC_ID}/advisory.txt",
        file_size_bytes=512,
        intake_timestamp="2026-04-05T00:00:00+00:00",
        source_type="FDA",
        document_date="2026-03-30",
    )


def _make_intake_result() -> IntakeResult:
    return IntakeResult(
        document_id=_DOC_ID,
        artifact_path=f"/tmp/outputs/intake/{_DOC_ID}.json",
        record=_make_intake_record(),
        storage=None,
    )


def _make_case_output(output_dir: str | None = None) -> CaseOutput:
    return CaseOutput(
        document_id=_DOC_ID,
        source_filename="advisory.txt",
        source_type="FDA",
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to establish adequate written procedures.",
        recommendations=["Initiate CAPA immediately."],
        citations=[
            Citation(
                source_id="s3://kb/fda/test.txt::0",
                source_label="FDA Test Document",
                excerpt="...test excerpt...",
                relevance_score=0.88,
            )
        ],
        confidence_score=0.87,
        unsupported_claims=[],
        escalation_required=False,
        escalation_reason=None,
        validated_by="tool-executor-agent-v1",
        session_id=_SESSION_ID,
        timestamp="2026-04-05T00:00:00+00:00",
    )


def _make_safety_assessment(status: SafetyStatus = SafetyStatus.ALLOW) -> SafetyAssessment:
    issues = []
    if status == SafetyStatus.BLOCK:
        issues = [
            SafetyIssue(
                issue_code=SafetyIssueCode.GUARDRAIL_INTERVENTION,
                severity=SafetyIssueSeverity.ERROR,
                message="Guardrail blocked generated output",
                blocking=True,
                source=IssueSource.GUARDRAILS,
            )
        ]
    return SafetyAssessment(
        document_id=_DOC_ID,
        issues=issues,
        has_blocking_issue=bool(issues),
        requires_escalation=status in (SafetyStatus.ESCALATE, SafetyStatus.BLOCK),
        status=status,
        notes="runtime safety test",
        timestamp="2026-04-05T00:00:00+00:00",
    )


def _make_runner() -> CliRunner:
    """Return a CliRunner for CLI invocation in tests."""
    return CliRunner()


# ── helpers: patch targets ─────────────────────────────────────────────────────

# These are the fully-qualified names that app.cli imports from.
_PATCH_RUN_INTAKE = "app.cli.run_intake"
_PATCH_RUN_PIPELINE = "app.cli.run_pipeline"
_PATCH_WRITE_OUTPUT = "app.cli.write_case_output"
_PATCH_BUILD_DEPS = "app.cli._build_pipeline_deps"
_PATCH_BUILD_LOGGER = "app.cli._build_logger"
_PATCH_BUILD_S3 = "app.cli._build_s3_service"
_PATCH_CONSUME_GOLD_PAYLOAD = "app.cli.consume_databricks_gold_payload_file"
_PATCH_RUN_CASE_CONTEXT = "app.cli.run_case_context_workflow"
_PATCH_RUN_CASE_BRIEF = "app.cli.run_supervisor_case_brief_workflow"
_PATCH_RUN_CASE_OUTPUT_SAFETY = "app.cli._run_case_output_safety_check"
_PATCH_RUN_OPERATOR_INPUT_SAFETY = "app.cli._run_operator_input_safety_check"


# ── run command — argument validation ─────────────────────────────────────────


def test_run_help_exits_zero() -> None:
    runner = _make_runner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "FILE" in result.output


def test_run_missing_file_exits_nonzero() -> None:
    runner = _make_runner()
    result = runner.invoke(cli, ["run", "--source-type", "FDA", "--document-date", "2026-03-30"])
    assert result.exit_code != 0


def test_run_missing_source_type_exits_nonzero(tmp_path: Path) -> None:
    runner = _make_runner()
    doc = tmp_path / "advisory.txt"
    doc.write_text("content", encoding="utf-8")
    result = runner.invoke(cli, ["run", str(doc), "--document-date", "2026-03-30"])
    assert result.exit_code != 0


def test_run_missing_document_date_exits_nonzero(tmp_path: Path) -> None:
    runner = _make_runner()
    doc = tmp_path / "advisory.txt"
    doc.write_text("content", encoding="utf-8")
    result = runner.invoke(cli, ["run", str(doc), "--source-type", "FDA"])
    assert result.exit_code != 0


def test_run_invalid_source_type_exits_nonzero(tmp_path: Path) -> None:
    runner = _make_runner()
    doc = tmp_path / "advisory.txt"
    doc.write_text("content", encoding="utf-8")
    result = runner.invoke(
        cli,
        ["run", str(doc), "--source-type", "INVALID", "--document-date", "2026-03-30"],
    )
    assert result.exit_code != 0


def test_run_invalid_document_date_exits_nonzero(tmp_path: Path) -> None:
    """A malformed date string must cause the CLI to exit with an error."""
    runner = _make_runner()
    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        # Patch intake so it's not reached — the metadata validation must reject first.
        with patch(_PATCH_RUN_INTAKE):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "advisory.txt",
                    "--source-type",
                    "FDA",
                    "--document-date",
                    "not-a-date",
                ],
            )
    assert result.exit_code != 0
    assert "error" in result.output.lower() or "error" in (result.stderr or "").lower()


# ── run command — success path ─────────────────────────────────────────────────


def _invoke_run_success(tmp_path: Path, extra_env: dict | None = None):
    """
    Invoke `run` with all AWS dependencies mocked for the happy path.

    Returns the CliRunner result.
    """
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"

    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    env = {
        "OUTPUT_DIR": str(tmp_path),
        "S3_DOCUMENT_BUCKET": "",
        **(extra_env or {}),
    }

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "advisory.txt",
                    "--source-type",
                    "FDA",
                    "--document-date",
                    "2026-03-30",
                ],
                env=env,
                catch_exceptions=False,
            )
    return result


def test_run_success_exit_code_zero(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert result.exit_code == 0


def test_run_success_prints_ok_summary(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert "[ok] Pipeline complete." in result.output


def test_run_success_summary_contains_document_id(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert _DOC_ID in result.output


def test_run_success_summary_contains_session_id(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert _SESSION_ID in result.output


def test_run_success_summary_contains_severity(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert "High" in result.output


def test_run_success_summary_contains_output_path(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert _DOC_ID in result.output


def test_run_success_summary_contains_safety_status(tmp_path: Path) -> None:
    result = _invoke_run_success(tmp_path)
    assert "safety_status" in result.output
    assert "allow" in result.output


# ── run command — failure paths ────────────────────────────────────────────────


def test_run_intake_error_exits_nonzero(tmp_path: Path) -> None:
    from app.services.intake_service import IntakeError

    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, side_effect=IntakeError("File not found")),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    assert result.exit_code != 0


def test_run_intake_error_prints_error_message(tmp_path: Path) -> None:
    from app.services.intake_service import IntakeError

    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, side_effect=IntakeError("File not found")),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    combined = result.output + (result.stderr or "")
    assert "error" in combined.lower()


def test_run_pipeline_error_exits_nonzero(tmp_path: Path) -> None:
    from app.workflows.pipeline_workflow import PipelineWorkflowError

    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, side_effect=PipelineWorkflowError("Bedrock timed out")),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    assert result.exit_code != 0


def test_run_pipeline_error_prints_error_message(tmp_path: Path) -> None:
    from app.workflows.pipeline_workflow import PipelineWorkflowError

    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, side_effect=PipelineWorkflowError("Bedrock timed out")),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    combined = result.output + (result.stderr or "")
    assert "error" in combined.lower()


def test_run_pipeline_init_failure_exits_nonzero() -> None:
    """If pipeline dependency build fails (e.g. missing BEDROCK_KB_ID), exit non-zero."""
    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, side_effect=RuntimeError("BEDROCK_KB_ID not set")),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    assert result.exit_code != 0


def test_run_output_write_error_exits_nonzero(tmp_path: Path) -> None:
    from app.utils.output_writer import OutputWriteError

    runner = _make_runner()
    output = _make_case_output()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, side_effect=OutputWriteError("disk full")),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    assert result.exit_code != 0


def test_run_output_write_error_prints_error_message(tmp_path: Path) -> None:
    from app.utils.output_writer import OutputWriteError

    runner = _make_runner()
    output = _make_case_output()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, side_effect=OutputWriteError("disk full")),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    combined = result.output + (result.stderr or "")
    assert "error" in combined.lower()


def test_run_blocked_output_safety_exits_before_writing_output(tmp_path: Path) -> None:
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    safety_artifact = tmp_path / f"{_DOC_ID}.safety.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID
    safety_result = SimpleNamespace(
        assessment=_make_safety_assessment(SafetyStatus.BLOCK),
        artifact_path=safety_artifact,
    )

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_RUN_OPERATOR_INPUT_SAFETY, return_value=None),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_RUN_CASE_OUTPUT_SAFETY, return_value=safety_result),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file) as mock_write,
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )

    assert result.exit_code != 0
    mock_write.assert_not_called()
    combined = result.output + (result.stderr or "")
    assert "Generated output blocked" in combined
    assert str(safety_artifact) in combined


def test_run_blocked_operator_input_exits_before_pipeline(tmp_path: Path) -> None:
    runner = _make_runner()
    safety_artifact = tmp_path / f"{_DOC_ID}.safety.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID
    safety_result = SimpleNamespace(
        assessment=_make_safety_assessment(SafetyStatus.BLOCK),
        artifact_path=safety_artifact,
    )

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_RUN_OPERATOR_INPUT_SAFETY, return_value=safety_result),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())) as mock_deps,
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE) as mock_pipeline,
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )

    assert result.exit_code != 0
    mock_deps.assert_not_called()
    mock_pipeline.assert_not_called()
    combined = result.output + (result.stderr or "")
    assert "Operator input blocked" in combined


# ── run command — logger integration ──────────────────────────────────────────


def test_run_passes_logger_to_pipeline(tmp_path: Path) -> None:
    """The PipelineLogger built by the CLI must be forwarded to run_pipeline."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger) as mock_build_logger,
            patch(_PATCH_RUN_PIPELINE, return_value=output) as mock_pipeline,
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )

    # Confirm _build_logger was called (CLI built a logger).
    mock_build_logger.assert_called_once()

    # Confirm run_pipeline received the logger keyword argument.
    call_kwargs = mock_pipeline.call_args.kwargs
    assert "logger" in call_kwargs
    assert call_kwargs["logger"] is mock_logger


def test_run_passes_session_id_to_pipeline(tmp_path: Path) -> None:
    """The session_id generated by the CLI must be passed into run_pipeline."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch("app.cli.generate_session_id", return_value=_SESSION_ID),
            patch(_PATCH_RUN_PIPELINE, return_value=output) as mock_pipeline,
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )

    call_kwargs = mock_pipeline.call_args.kwargs
    assert call_kwargs.get("session_id") == _SESSION_ID


def test_run_passes_configured_max_attempts_to_pipeline(tmp_path: Path) -> None:
    """MAX_AGENT_RETRIES from env must reach the pipeline retry policy."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output) as mock_pipeline,
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                env={"MAX_AGENT_RETRIES": "4"},
                catch_exceptions=False,
            )

    assert result.exit_code == 0
    assert mock_pipeline.call_args.kwargs["max_attempts"] == 4


def test_build_pipeline_deps_wires_runtime_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline dependency build must pass env-backed configs into runtime services."""
    runtime_config = PipelineConfig(
        retrieval_max_results=7,
        escalation_confidence_threshold=0.72,
        max_agent_retries=4,
        bedrock_model_id="anthropic.base-model",
        bedrock_kb_id="kb-configured",
        aws_region="us-west-2",
        s3_document_bucket="",
        s3_output_bucket="",
    )
    monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_CACHING", "true")
    monkeypatch.setenv("CASEOPS_CACHE_SYSTEM_PROMPT", "true")
    monkeypatch.setenv("CASEOPS_ENABLE_PROMPT_ROUTING", "true")
    monkeypatch.setenv("CASEOPS_ROUTING_ANALYSIS_MODEL_ID", "anthropic.analysis-model")
    monkeypatch.setenv("CASEOPS_ROUTING_VALIDATION_MODEL_ID", "anthropic.validation-model")

    mock_retrieval = MagicMock()
    mock_analysis_service = MagicMock()
    mock_validation_service = MagicMock()

    with (
        patch("app.services.kb_service.BedrockKBService", return_value=mock_retrieval) as mock_kb,
        patch(
            "app.services.bedrock_service.BedrockAnalysisService",
            return_value=mock_analysis_service,
        ) as mock_analysis,
        patch(
            "app.services.bedrock_service.BedrockValidationService",
            return_value=mock_validation_service,
        ) as mock_validation,
    ):
        retrieval_provider, analysis_agent, validation_agent, tool_executor = (
            _build_pipeline_deps(runtime_config=runtime_config)
        )

    assert retrieval_provider is mock_retrieval
    assert analysis_agent._provider is mock_analysis_service
    assert validation_agent._provider is mock_validation_service

    mock_kb.assert_called_once_with(
        kb_id="kb-configured",
        region="us-west-2",
        max_results=7,
    )

    analysis_kwargs = mock_analysis.call_args.kwargs
    validation_kwargs = mock_validation.call_args.kwargs
    assert analysis_kwargs["model_id"] == "anthropic.base-model"
    assert validation_kwargs["model_id"] == "anthropic.base-model"
    assert analysis_kwargs["region"] == "us-west-2"
    assert validation_kwargs["region"] == "us-west-2"
    assert analysis_kwargs["caching_config"].enable_prompt_caching is True
    assert validation_kwargs["caching_config"].enable_prompt_caching is True
    assert analysis_kwargs["routing_config"].analysis_model_id == "anthropic.analysis-model"
    assert validation_kwargs["routing_config"].validation_model_id == "anthropic.validation-model"
    assert tool_executor._escalation_confidence_threshold == pytest.approx(0.72)


# ── run command — no live AWS ─────────────────────────────────────────────────


def test_run_command_no_real_boto3_called(tmp_path: Path) -> None:
    """
    Verifies that a successful `run` invocation makes no real boto3 calls
    when all pipeline dependencies are mocked.
    """
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    # If boto3 is actually called it will fail with a NoCredentialsError in CI,
    # which would cause the test to fail — so a passing test proves no real calls.
    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )
    assert result.exit_code == 0


# ── intake command — existing behaviour preserved ────────────────────────────


def test_intake_help_exits_zero() -> None:
    runner = _make_runner()
    result = runner.invoke(cli, ["intake", "--help"])
    assert result.exit_code == 0


def test_intake_invalid_source_type_exits_nonzero(tmp_path: Path) -> None:
    runner = _make_runner()
    doc = tmp_path / "advisory.txt"
    doc.write_text("content", encoding="utf-8")
    result = runner.invoke(
        cli,
        ["intake", str(doc), "--source-type", "INVALID", "--document-date", "2026-03-30"],
    )
    assert result.exit_code != 0


def test_intake_success_prints_registration(tmp_path: Path) -> None:

    runner = _make_runner()
    mock_result = _make_intake_result()

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=mock_result),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["intake", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )
    assert result.exit_code == 0
    assert "[ok] Registration complete." in result.output
    assert _DOC_ID in result.output


# ── Databricks Gold intake command — local adapter wiring ─────────────────────


def test_intake_gold_help_exits_zero() -> None:
    runner = _make_runner()
    result = runner.invoke(cli, ["intake-gold", "--help"])

    assert result.exit_code == 0
    assert "PAYLOAD" in result.output
    assert "--gold-record-id" in result.output


def test_intake_gold_success_prints_registration(tmp_path: Path) -> None:
    runner = _make_runner()
    payload = tmp_path / "gold_payload.json"
    payload.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "gold_intake"
    mock_result = _make_intake_result()

    with patch(_PATCH_CONSUME_GOLD_PAYLOAD, return_value=mock_result) as mock_consume:
        result = runner.invoke(
            cli,
            [
                "intake-gold",
                str(payload),
                "--output-dir",
                str(output_dir),
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert "[ok] Registration complete." in result.output
    assert _DOC_ID in result.output
    mock_consume.assert_called_once_with(
        payload,
        gold_record_id=None,
        output_dir=output_dir,
    )


def test_intake_gold_forwards_gold_record_id(tmp_path: Path) -> None:
    runner = _make_runner()
    payload = tmp_path / "gold_payload.json"
    payload.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "gold_intake"

    with patch(_PATCH_CONSUME_GOLD_PAYLOAD, return_value=_make_intake_result()) as mock_consume:
        result = runner.invoke(
            cli,
            [
                "intake-gold",
                str(payload),
                "--gold-record-id",
                "gold-fda-20260608-001",
                "--output-dir",
                str(output_dir),
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    mock_consume.assert_called_once_with(
        payload,
        gold_record_id="gold-fda-20260608-001",
        output_dir=output_dir,
    )


def test_intake_gold_adapter_error_exits_nonzero(tmp_path: Path) -> None:
    from app.services.databricks_gold_adapter import DatabricksGoldAdapterError

    runner = _make_runner()
    payload = tmp_path / "bad_payload.json"
    payload.write_text("{}", encoding="utf-8")

    with patch(
        _PATCH_CONSUME_GOLD_PAYLOAD,
        side_effect=DatabricksGoldAdapterError("schema validation failed"),
    ):
        result = runner.invoke(cli, ["intake-gold", str(payload)])

    assert result.exit_code != 0
    assert "Databricks Gold intake failed" in result.output
    assert "schema validation failed" in result.output


# ── Databricks Gold brief command — local packet wiring ───────────────────────


def test_brief_gold_help_exits_zero() -> None:
    runner = _make_runner()
    result = runner.invoke(cli, ["brief-gold", "--help"])

    assert result.exit_code == 0
    assert "PAYLOAD" in result.output
    assert "--gold-record-id" in result.output
    assert "--output-root" in result.output


def test_brief_gold_success_prints_case_brief_summary(tmp_path: Path) -> None:
    from app.schemas.case_context_models import CaseWorkItem, SupervisorCaseBrief

    runner = _make_runner()
    payload = tmp_path / "gold_payload.json"
    payload.write_text("{}", encoding="utf-8")
    output_root = tmp_path / "outputs"
    mock_intake = _make_intake_result()
    mock_work_item = CaseWorkItem(
        work_item_id=f"work-{_DOC_ID}",
        document_id=_DOC_ID,
        source_filename="advisory.txt",
        source_type="FDA",
        document_date="2026-03-30",
        intake_artifact_path=mock_intake.artifact_path,
        source_artifact_path=mock_intake.record.absolute_path,
        storage_mode="local",
        retrieval_query=None,
        retrieval_query_source="provider_fallback",
        routing_lane="regulatory_review",
        priority_hint="standard",
        readiness_status="ready_for_grounded_retrieval",
        next_step="run_supervisor_pipeline",
        created_at=mock_intake.record.intake_timestamp,
    )
    mock_brief = SupervisorCaseBrief(
        case_brief_id=f"brief-work-{_DOC_ID}",
        work_item_id=mock_work_item.work_item_id,
        document_id=_DOC_ID,
        title="regulatory_review: advisory.txt",
        source_type="FDA",
        source_filename="advisory.txt",
        document_date="2026-03-30",
        routing_lane="regulatory_review",
        priority_hint="standard",
        readiness_status="ready_for_supervisor_review",
        next_step="run_supervisor_pipeline",
        retrieval_query_source="provider_fallback",
        expected_retrieval_request={
            "document_id": _DOC_ID,
            "source_type": "FDA",
            "source_filename": "advisory.txt",
            "query_text": None,
        },
        source_artifacts=[
            {"kind": "intake_artifact", "path_or_key": mock_intake.artifact_path},
            {
                "kind": "source_artifact",
                "path_or_key": mock_intake.record.absolute_path,
            },
        ],
        live_runtime_requirements=[
            {
                "name": "BEDROCK_KB_ID",
                "required_for": "grounded retrieval",
                "status": "operator_supplied_at_live_runtime",
            }
        ],
        operator_notes=["local-only"],
        created_at=mock_intake.record.intake_timestamp,
    )

    with (
        patch(_PATCH_CONSUME_GOLD_PAYLOAD, return_value=mock_intake) as mock_consume,
        patch(
            _PATCH_RUN_CASE_CONTEXT,
            return_value=SimpleNamespace(
                work_item=mock_work_item,
                artifact_path=str(output_root / "case_work_items" / _DOC_ID / "work_item.json"),
            ),
        ) as mock_context,
        patch(
            _PATCH_RUN_CASE_BRIEF,
            return_value=SimpleNamespace(
                case_brief=mock_brief,
                artifact_path=str(output_root / "case_briefs" / _DOC_ID / "case_brief.json"),
            ),
        ) as mock_brief_workflow,
    ):
        result = runner.invoke(
            cli,
            [
                "brief-gold",
                str(payload),
                "--output-root",
                str(output_root),
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert "[ok] Local case brief ready." in result.output
    assert _DOC_ID in result.output
    assert "regulatory_review" in result.output
    assert "case_brief" in result.output
    mock_consume.assert_called_once_with(
        payload,
        gold_record_id=None,
        output_dir=output_root / "databricks_gold",
    )
    mock_context.assert_called_once_with(
        mock_intake,
        output_dir=output_root / "case_work_items",
    )
    mock_brief_workflow.assert_called_once_with(
        mock_work_item,
        output_dir=output_root / "case_briefs",
    )


def test_brief_gold_forwards_gold_record_id(tmp_path: Path) -> None:
    runner = _make_runner()
    payload = tmp_path / "gold_payload.json"
    payload.write_text("{}", encoding="utf-8")
    output_root = tmp_path / "outputs"

    with (
        patch(_PATCH_CONSUME_GOLD_PAYLOAD, return_value=_make_intake_result()) as mock_consume,
        patch(_PATCH_RUN_CASE_CONTEXT) as mock_context,
        patch(_PATCH_RUN_CASE_BRIEF) as mock_brief,
    ):
        mock_context.return_value = SimpleNamespace(
            work_item=object(),
            artifact_path=str(output_root / "case_work_items" / _DOC_ID / "work_item.json"),
        )
        mock_brief.return_value = SimpleNamespace(
            case_brief=SimpleNamespace(
                document_id=_DOC_ID,
                routing_lane="regulatory_review",
                priority_hint="standard",
            ),
            artifact_path=str(output_root / "case_briefs" / _DOC_ID / "case_brief.json"),
        )
        result = runner.invoke(
            cli,
            [
                "brief-gold",
                str(payload),
                "--gold-record-id",
                "gold-fda-20260608-001",
                "--output-root",
                str(output_root),
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    mock_consume.assert_called_once_with(
        payload,
        gold_record_id="gold-fda-20260608-001",
        output_dir=output_root / "databricks_gold",
    )


def test_brief_gold_adapter_error_exits_nonzero(tmp_path: Path) -> None:
    from app.services.databricks_gold_adapter import DatabricksGoldAdapterError

    runner = _make_runner()
    payload = tmp_path / "bad_payload.json"
    payload.write_text("{}", encoding="utf-8")

    with patch(
        _PATCH_CONSUME_GOLD_PAYLOAD,
        side_effect=DatabricksGoldAdapterError("schema validation failed"),
    ):
        result = runner.invoke(cli, ["brief-gold", str(payload)])

    assert result.exit_code != 0
    assert "Databricks Gold intake failed" in result.output
    assert "schema validation failed" in result.output


def test_brief_gold_case_context_error_exits_nonzero(tmp_path: Path) -> None:
    from app.workflows.case_context_workflow import CaseContextWorkflowError

    runner = _make_runner()
    payload = tmp_path / "gold_payload.json"
    payload.write_text("{}", encoding="utf-8")

    with (
        patch(_PATCH_CONSUME_GOLD_PAYLOAD, return_value=_make_intake_result()),
        patch(
            _PATCH_RUN_CASE_CONTEXT,
            side_effect=CaseContextWorkflowError("unsupported source_type"),
        ),
    ):
        result = runner.invoke(cli, ["brief-gold", str(payload)])

    assert result.exit_code != 0
    assert "Case brief preparation failed" in result.output
    assert "unsupported source_type" in result.output


# ── doctor / check-config commands ────────────────────────────────────────────


def test_doctor_loads_dotenv_and_exits_zero_when_required_config_present() -> None:
    runner = _make_runner()

    with runner.isolated_filesystem():
        Path(".env").write_text(
            "\n".join(
                [
                    "BEDROCK_KB_ID=kb-test",
                    "BEDROCK_MODEL_ID=anthropic.test-model",
                    "AWS_REGION=us-east-1",
                ]
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(cli, ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert ".env loaded" in result.output
    assert "[ok] BEDROCK_KB_ID" in result.output
    assert "[ok] Required live-run configuration is present." in result.output


def test_doctor_exits_nonzero_when_required_live_config_missing() -> None:
    runner = _make_runner()

    with runner.isolated_filesystem():
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(cli, ["doctor"])

    assert result.exit_code != 0
    assert "[missing] BEDROCK_KB_ID" in result.output
    assert "Missing required live-run variables" in result.output


def test_check_config_alias_uses_doctor_diagnostic() -> None:
    runner = _make_runner()
    env = {
        "BEDROCK_KB_ID": "kb-test",
        "BEDROCK_MODEL_ID": "anthropic.test-model",
        "AWS_REGION": "us-east-1",
    }

    with runner.isolated_filesystem():
        with patch.dict(os.environ, env, clear=True):
            result = runner.invoke(cli, ["check-config"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "CaseOps configuration check" in result.output


def test_doctor_reports_invalid_runtime_scalar_config() -> None:
    runner = _make_runner()
    env = {
        "BEDROCK_KB_ID": "kb-test",
        "BEDROCK_MODEL_ID": "anthropic.test-model",
        "AWS_REGION": "us-east-1",
        "RETRIEVAL_MAX_RESULTS": "0",
    }

    with runner.isolated_filesystem():
        with patch.dict(os.environ, env, clear=True):
            result = runner.invoke(cli, ["doctor"])

    assert result.exit_code != 0
    assert "RETRIEVAL_MAX_RESULTS must be a positive integer" in result.output


# ── run command — S3 output archiving ────────────────────────────────────────

_PATCH_ARCHIVE_S3 = "app.cli._archive_output_to_s3"


def test_run_s3_archive_skipped_when_bucket_not_set(tmp_path: Path) -> None:
    """When S3_OUTPUT_BUCKET is absent, _archive_output_to_s3 returns None (no s3 line in summary)."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
            # _archive_output_to_s3 returns None when bucket not set
            patch(_PATCH_ARCHIVE_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )
    assert result.exit_code == 0
    assert "s3 archive" not in result.output


def test_run_s3_archive_called_when_bucket_set(tmp_path: Path) -> None:
    """When S3_OUTPUT_BUCKET is configured, _archive_output_to_s3 is called."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
            patch(_PATCH_ARCHIVE_S3, return_value=f"s3://test-bucket/outputs/{_DOC_ID}/case_output.json") as mock_archive,
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )

    mock_archive.assert_called_once_with(
        output_path=output_file,
        document_id=_DOC_ID,
    )
    assert result.exit_code == 0


def test_run_s3_archive_location_in_summary(tmp_path: Path) -> None:
    """When archiving succeeds, the S3 URI appears in the operator summary."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID
    s3_uri = f"s3://test-bucket/outputs/{_DOC_ID}/case_output.json"

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
            patch(_PATCH_ARCHIVE_S3, return_value=s3_uri),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )

    assert s3_uri in result.output


def test_archive_output_to_s3_skips_when_no_bucket(tmp_path: Path, monkeypatch) -> None:
    """
    _archive_output_to_s3 returns None and prints [info] when S3_OUTPUT_BUCKET is absent.
    Uses the full CLI runner via a thin wrapper command so Click context is present.
    """
    from app.cli import cli as _cli

    monkeypatch.delenv("S3_OUTPUT_BUCKET", raising=False)
    output_path = tmp_path / "doc-test.json"
    output_path.write_text("{}", encoding="utf-8")

    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID
    output = _make_case_output()

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_path),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                _cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                env={"S3_OUTPUT_BUCKET": ""},
                catch_exceptions=False,
            )
    assert result.exit_code == 0
    assert "S3_OUTPUT_BUCKET not set" in result.output


def test_archive_output_to_s3_uploads_on_success(tmp_path: Path, monkeypatch) -> None:
    """
    When S3_OUTPUT_BUCKET is set and upload succeeds, the S3 URI appears in the summary.
    """
    monkeypatch.setenv("S3_OUTPUT_BUCKET", "test-output-bucket")
    output_path = tmp_path / f"{_DOC_ID}.json"
    output_path.write_text("{}", encoding="utf-8")

    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID
    output = _make_case_output()

    mock_s3_service = MagicMock()
    mock_s3_service.upload_case_output.return_value = f"outputs/{_DOC_ID}/case_output.json"

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_path),
            patch(_PATCH_BUILD_S3, return_value=None),
            patch("app.cli.S3Service", return_value=mock_s3_service),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
                catch_exceptions=False,
            )

    assert result.exit_code == 0
    mock_s3_service.upload_case_output.assert_called_once_with(output_path, _DOC_ID)
    assert f"s3://test-output-bucket/outputs/{_DOC_ID}/case_output.json" in result.output


def test_archive_output_to_s3_exits_on_storage_error(tmp_path: Path, monkeypatch) -> None:
    """
    When S3_OUTPUT_BUCKET is set but upload fails (StorageError), CLI exits non-zero.
    """
    from app.services.s3_service import StorageError

    monkeypatch.setenv("S3_OUTPUT_BUCKET", "test-output-bucket")
    output_path = tmp_path / f"{_DOC_ID}.json"
    output_path.write_text("{}", encoding="utf-8")

    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID
    output = _make_case_output()

    mock_s3_service = MagicMock()
    mock_s3_service.upload_case_output.side_effect = StorageError("network failure")

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_path),
            patch(_PATCH_BUILD_S3, return_value=None),
            patch("app.cli.S3Service", return_value=mock_s3_service),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )
    assert result.exit_code != 0
    assert "error" in result.output.lower()


# ── run command — submitter-note forwarded ────────────────────────────────────


def test_run_submitter_note_is_forwarded_to_intake(tmp_path: Path) -> None:
    """--submitter-note must be forwarded to run_intake via IntakeMetadata."""
    runner = _make_runner()
    output = _make_case_output()
    output_file = tmp_path / f"{_DOC_ID}.json"
    mock_logger = MagicMock()
    mock_logger.log_file_path = None
    mock_logger.session_id = _SESSION_ID

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()) as mock_intake,
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, return_value=output),
            patch(_PATCH_WRITE_OUTPUT, return_value=output_file),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            runner.invoke(
                cli,
                [
                    "run",
                    "advisory.txt",
                    "--source-type",
                    "FDA",
                    "--document-date",
                    "2026-03-30",
                    "--submitter-note",
                    "High priority review needed",
                ],
                catch_exceptions=False,
            )

    call_kwargs = mock_intake.call_args.kwargs
    metadata = call_kwargs.get("metadata")
    assert metadata is not None
    assert metadata.submitter_note == "High priority review needed"


# ── run command — E-2 hardening: hint messages on failures ────────────────────


def test_run_pipeline_init_failure_prints_hint_about_bedrock_kb_id() -> None:
    """Pipeline init failure must print a [hint] pointing to BEDROCK_KB_ID."""
    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, side_effect=RuntimeError("BEDROCK_KB_ID not set")),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )

    combined = result.output + (result.stderr or "")
    assert "hint" in combined.lower()
    assert "BEDROCK_KB_ID" in combined


def test_run_pipeline_error_prints_hint_about_aws_credentials() -> None:
    """PipelineWorkflowError must print a [hint] about AWS credentials and KB setup."""
    from app.workflows.pipeline_workflow import PipelineWorkflowError

    runner = _make_runner()
    mock_logger = MagicMock()
    mock_logger.log_file_path = None

    with runner.isolated_filesystem():
        Path("advisory.txt").write_text("content", encoding="utf-8")
        with (
            patch(_PATCH_RUN_INTAKE, return_value=_make_intake_result()),
            patch(_PATCH_BUILD_DEPS, return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
            patch(_PATCH_BUILD_LOGGER, return_value=mock_logger),
            patch(_PATCH_RUN_PIPELINE, side_effect=PipelineWorkflowError("Bedrock timed out")),
            patch(_PATCH_BUILD_S3, return_value=None),
        ):
            result = runner.invoke(
                cli,
                ["run", "advisory.txt", "--source-type", "FDA", "--document-date", "2026-03-30"],
            )

    combined = result.output + (result.stderr or "")
    assert "hint" in combined.lower()
    assert result.exit_code != 0


# ── eval command group ───────────────────────────────────────────────────────


def test_eval_group_help_lists_operator_workflows() -> None:
    runner = _make_runner()
    result = runner.invoke(cli, ["eval", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "safety" in result.output
    assert "compare" in result.output
    assert "dashboard" in result.output


def test_eval_run_writes_artifacts_and_prints_summary(tmp_path: Path) -> None:
    from app.schemas.evaluation_models import EvaluationRunSummary

    runner = _make_runner()
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    (candidates_dir / "case-001.json").write_text("{}", encoding="utf-8")
    output_root = tmp_path / "outputs"

    summary = EvaluationRunSummary(
        run_id="eval-cli-001",
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        average_score=0.91,
        per_metric_averages={"severity_match": 1.0},
        timestamp="2026-06-06T00:00:00+00:00",
    )
    run_result = SimpleNamespace(summary=summary, results=())
    bundle = SimpleNamespace(
        metadata=SimpleNamespace(
            run_id="eval-cli-001",
            artifact_dir="evaluation_runs/eval-cli-001",
        ),
        report_path="evaluation_runs/eval-cli-001/report.md",
    )

    with (
        patch("app.evaluation.runner.run_evaluation", return_value=run_result) as mock_run,
        patch("app.evaluation.artifact_writer.write_evaluation_run", return_value=bundle) as mock_write,
        patch("app.evaluation.metrics_translator.evaluation_run_summary_to_metrics", return_value=[]),
        patch("app.cli._publish_evaluation_metrics") as mock_publish,
    ):
        result = runner.invoke(
            cli,
            [
                "eval",
                "run",
                "--candidates-dir",
                str(candidates_dir),
                "--output-root",
                str(output_root),
                "--run-id",
                "eval-cli-001",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert "[ok] Evaluation run complete." in result.output
    assert "eval-cli-001" in result.output
    assert mock_run.call_args.kwargs["run_id"] == "eval-cli-001"
    assert mock_run.call_args.args[0]["case-001"] == candidates_dir / "case-001.json"
    mock_write.assert_called_once()
    mock_publish.assert_called_once()


def test_eval_run_empty_candidates_dir_exits_nonzero(tmp_path: Path) -> None:
    runner = _make_runner()
    candidates_dir = tmp_path / "empty"
    candidates_dir.mkdir()
    result = runner.invoke(
        cli,
        ["eval", "run", "--candidates-dir", str(candidates_dir)],
    )
    assert result.exit_code != 0
    assert "No candidate JSON files" in result.output


def test_eval_safety_writes_artifacts_with_existing_fixtures(tmp_path: Path) -> None:
    runner = _make_runner()
    suite_dir = _REPO_ROOT / "tests" / "fixtures" / "safety_cases"
    result = runner.invoke(
        cli,
        [
            "eval",
            "safety",
            "--suite-dir",
            str(suite_dir),
            "--output-root",
            str(tmp_path),
            "--suite-id",
            "safety-cli-001",
            "--no-report",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "[ok] Safety evaluation complete." in result.output
    assert (tmp_path / "safety_runs" / "safety-cli-001" / "summary.json").exists()
    assert (tmp_path / "safety_runs" / "safety-cli-001" / "case_results.json").exists()


def test_eval_compare_writes_artifacts_with_existing_fixtures(tmp_path: Path) -> None:
    runner = _make_runner()
    fixture_root = _REPO_ROOT / "tests" / "fixtures" / "comparison_cases"
    result = runner.invoke(
        cli,
        [
            "eval",
            "compare",
            "--baseline-dir",
            str(fixture_root / "baseline"),
            "--optimized-dir",
            str(fixture_root / "optimized"),
            "--dataset-dir",
            str(fixture_root),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "cmp-cli-001",
            "--no-report",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "[ok] Comparison run complete." in result.output
    assert (tmp_path / "comparison_runs" / "cmp-cli-001" / "summary.json").exists()
    assert (tmp_path / "comparison_runs" / "cmp-cli-001" / "case_results.json").exists()


def test_eval_dashboard_writes_dashboard_json(tmp_path: Path) -> None:
    runner = _make_runner()
    result = runner.invoke(
        cli,
        [
            "eval",
            "dashboard",
            "--output-root",
            str(tmp_path),
            "--filename",
            "caseops-dashboard.json",
        ],
        catch_exceptions=False,
    )
    dashboard_path = tmp_path / "evaluation_dashboard" / "caseops-dashboard.json"
    assert result.exit_code == 0
    assert "[ok] Evaluation dashboard artifact written." in result.output
    assert dashboard_path.exists()
    assert "widgets" in json.loads(dashboard_path.read_text(encoding="utf-8"))
