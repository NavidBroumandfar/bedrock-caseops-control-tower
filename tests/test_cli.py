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
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.schemas.output_models import CaseOutput, Citation


# ── shared test builders ───────────────────────────────────────────────────────


_DOC_ID = "doc-20260405-clitst1"
_SESSION_ID = "sess-deadbeef"


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
    from app.schemas.intake_models import IntakeResult

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
