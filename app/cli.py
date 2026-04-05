"""
CaseOps CLI entry point.

Commands:
  intake   Validate and register a local document (intake only; no pipeline run).
  run      Run the full end-to-end pipeline: intake → retrieval → analysis →
           validation → output packaging.

Usage examples:

  # Register a document without running the pipeline:
  python -m app.cli intake path/to/advisory.txt \\
      --source-type FDA --document-date 2026-03-30

  # Run the full pipeline end-to-end:
  python -m app.cli run path/to/advisory.txt \\
      --source-type FDA --document-date 2026-03-30

  # With optional submitter note (used as KB retrieval query):
  python -m app.cli run path/to/advisory.txt \\
      --source-type CISA --document-date 2026-03-30 \\
      --submitter-note "Critical ICS vulnerability — immediate review required"

  python -m app.cli --help

Environment variables (see .env.example):
  S3_DOCUMENT_BUCKET        — enable S3 upload during intake (optional)
  BEDROCK_KB_ID             — required for retrieval (run command)
  BEDROCK_MODEL_ID          — Bedrock model for analysis/validation
  AWS_REGION                — AWS region (default: us-east-1)
  OUTPUT_DIR                — local output directory (default: outputs)
  CASEOPS_LOG_LEVEL         — DEBUG | INFO | WARNING | ERROR
  CASEOPS_ENABLE_LOCAL_FILE_LOG — write session log file (default: true)
  CASEOPS_ENABLE_CLOUDWATCH — emit to CloudWatch (default: false)
"""

import os
import sys
from pathlib import Path

import click
from pydantic import ValidationError

from app.schemas.intake_models import IntakeMetadata
from app.services.intake_service import IntakeError, run_intake
from app.services.s3_service import S3Service, StorageError
from app.utils.id_utils import generate_session_id
from app.utils.logging_utils import LoggingConfig, PipelineLogger
from app.utils.output_writer import OutputWriteError, write_case_output
from app.workflows.pipeline_workflow import PipelineWorkflowError, run_pipeline


@click.group()
def cli() -> None:
    """Bedrock CaseOps Multi-Agent Control Tower — CLI."""


# ── intake command ─────────────────────────────────────────────────────────────


@cli.command()
@click.argument("file_path", metavar="FILE")
@click.option(
    "--source-type",
    required=True,
    type=click.Choice(["FDA", "CISA", "Incident", "Other"], case_sensitive=True),
    help="Origin category of the document.",
)
@click.option(
    "--document-date",
    required=True,
    metavar="YYYY-MM-DD",
    help="Publication or issue date of the document.",
)
@click.option(
    "--submitter-note",
    default=None,
    help="Optional free-text note from the operator.",
)
def intake(
    file_path: str,
    source_type: str,
    document_date: str,
    submitter_note: str | None,
) -> None:
    """Validate and register a local document for processing."""
    try:
        metadata = IntakeMetadata(
            source_type=source_type,
            document_date=document_date,
            submitter_note=submitter_note,
        )
    except ValidationError as exc:
        click.echo(f"[error] Invalid metadata: {exc}", err=True)
        sys.exit(1)

    s3_service: S3Service | None = _build_s3_service()

    try:
        result = run_intake(
            file_path=file_path,
            metadata=metadata,
            s3_service=s3_service,
        )
    except IntakeError as exc:
        click.echo(f"[error] Intake failed: {exc}", err=True)
        sys.exit(1)

    _print_registration_summary(result)


# ── run command ────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("file_path", metavar="FILE")
@click.option(
    "--source-type",
    required=True,
    type=click.Choice(["FDA", "CISA", "Incident", "Other"], case_sensitive=True),
    help="Origin category of the document.",
)
@click.option(
    "--document-date",
    required=True,
    metavar="YYYY-MM-DD",
    help="Publication or issue date of the document.",
)
@click.option(
    "--submitter-note",
    default=None,
    help="Optional free-text note (used as KB retrieval query when provided).",
)
def run(
    file_path: str,
    source_type: str,
    document_date: str,
    submitter_note: str | None,
) -> None:
    """Run the full end-to-end pipeline for a document.

    Validates and registers the document, retrieves grounded evidence from the
    Bedrock Knowledge Base, runs analysis and validation agents, and writes a
    structured JSON output to the local outputs directory.

    Requires BEDROCK_KB_ID to be set in the environment.  S3 upload is
    performed if S3_DOCUMENT_BUCKET is also set; otherwise intake runs in
    local-only mode.

    Note: live Bedrock / Knowledge Base calls require valid AWS credentials
    and a provisioned Knowledge Base.  The pipeline fails clearly when AWS
    is unavailable rather than silently producing an incomplete result.
    """
    # ── step 1: validate metadata ──────────────────────────────────────────────
    try:
        metadata = IntakeMetadata(
            source_type=source_type,
            document_date=document_date,
            submitter_note=submitter_note,
        )
    except ValidationError as exc:
        click.echo(f"[error] Invalid metadata: {exc}", err=True)
        sys.exit(1)

    # ── step 2: build logger ───────────────────────────────────────────────────
    session_id = generate_session_id()
    log_config = LoggingConfig.from_env()
    logger = _build_logger(session_id, log_config)

    # ── step 3: run intake ─────────────────────────────────────────────────────
    s3_service = _build_s3_service()
    try:
        intake_result = run_intake(
            file_path=file_path,
            metadata=metadata,
            s3_service=s3_service,
        )
    except IntakeError as exc:
        click.echo(f"[error] Intake failed: {exc}", err=True)
        sys.exit(1)

    # ── step 4: build pipeline dependencies ───────────────────────────────────
    try:
        retrieval_provider, analysis_agent, validation_agent, tool_executor = (
            _build_pipeline_deps()
        )
    except Exception as exc:
        click.echo(f"[error] Pipeline initialisation failed: {exc}", err=True)
        click.echo(
            "[hint]  Check that BEDROCK_KB_ID is set in your environment or .env file.",
            err=True,
        )
        sys.exit(1)

    # ── step 5: run pipeline ───────────────────────────────────────────────────
    try:
        output = run_pipeline(
            intake_result,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=tool_executor,
            logger=logger,
            session_id=session_id,
        )
    except PipelineWorkflowError as exc:
        click.echo(f"[error] Pipeline failed: {exc}", err=True)
        click.echo(
            "[hint]  Live Bedrock / KB calls require valid AWS credentials and a "
            "provisioned Knowledge Base.  See README.md for setup instructions.",
            err=True,
        )
        sys.exit(1)
    except Exception as exc:
        click.echo(f"[error] Unexpected pipeline error: {exc}", err=True)
        sys.exit(1)

    # ── step 6: write output locally ──────────────────────────────────────────
    output_dir = os.getenv("OUTPUT_DIR", "outputs")
    try:
        output_path = write_case_output(output, output_dir=output_dir)
    except OutputWriteError as exc:
        click.echo(f"[error] Could not write output: {exc}", err=True)
        sys.exit(1)

    # ── step 7: archive to S3 (if S3_OUTPUT_BUCKET is configured) ─────────────
    s3_archive_location: str | None = _archive_output_to_s3(
        output_path=output_path,
        document_id=output.document_id,
    )

    # ── step 8: print success summary ─────────────────────────────────────────
    _print_pipeline_summary(output, output_path, logger, s3_archive_location)


# ── private helpers ────────────────────────────────────────────────────────────


def _build_s3_service() -> "S3Service | None":
    """Return an S3Service if S3_DOCUMENT_BUCKET is configured, else None."""
    bucket = os.getenv("S3_DOCUMENT_BUCKET")
    if not bucket:
        click.echo("[info] S3_DOCUMENT_BUCKET not set — running in local-only mode.")
        return None
    try:
        return S3Service(bucket_name=bucket)
    except StorageError as exc:
        click.echo(f"[error] Could not initialise S3 client: {exc}", err=True)
        sys.exit(1)


def _build_logger(session_id: str, config: LoggingConfig) -> PipelineLogger:
    """
    Build a PipelineLogger for the pipeline run.

    CloudWatch is initialised only when CASEOPS_ENABLE_CLOUDWATCH is true.
    If CloudWatch initialisation fails for any reason the logger degrades to
    local-only mode so the pipeline is never blocked by observability setup.
    """
    from app.services.cloudwatch_service import build_cloudwatch_emitter

    cloudwatch_emitter = build_cloudwatch_emitter(enabled=config.enable_cloudwatch)
    return PipelineLogger(
        session_id=session_id,
        config=config,
        cloudwatch_emitter=cloudwatch_emitter,
    )


def _build_pipeline_deps():  # type: ignore[return]
    """
    Build and wire all pipeline service dependencies.

    Returns a 4-tuple: (retrieval_provider, analysis_agent, validation_agent, tool_executor).

    Raises if required environment variables are missing (e.g. BEDROCK_KB_ID).
    AWS service clients are constructed here; live connectivity is not validated
    at build time — failures manifest when the pipeline first calls the service.
    """
    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.tool_executor_agent import ToolExecutorAgent
    from app.agents.validation_agent import ValidationAgent
    from app.services.bedrock_service import (
        BedrockAnalysisService,
        BedrockValidationService,
    )
    from app.services.kb_service import BedrockKBService, RetrievalServiceError

    try:
        retrieval_provider = BedrockKBService()
    except RetrievalServiceError as exc:
        raise RuntimeError(
            f"Knowledge Base configuration error: {exc}\n"
            "Ensure BEDROCK_KB_ID is set in your environment or .env file."
        ) from exc

    analysis_service = BedrockAnalysisService()
    validation_service = BedrockValidationService()

    return (
        retrieval_provider,
        AnalysisAgent(provider=analysis_service),
        ValidationAgent(provider=validation_service),
        ToolExecutorAgent(),
    )


def _archive_output_to_s3(output_path: Path, document_id: str) -> "str | None":
    """
    Archive the local output file to S3 if S3_OUTPUT_BUCKET is configured.

    Returns the full S3 URI on success (e.g. s3://bucket/outputs/doc-xxx/case_output.json).
    Returns None when S3_OUTPUT_BUCKET is not set (archiving skipped, not an error).
    Prints a clear message in both cases.
    Exits non-zero if the bucket is configured but the upload fails.
    """
    output_bucket = os.getenv("S3_OUTPUT_BUCKET")
    if not output_bucket:
        click.echo("[info] S3_OUTPUT_BUCKET not set — skipping S3 output archive.")
        return None

    try:
        s3_service = S3Service(bucket_name=output_bucket)
        s3_key = s3_service.upload_case_output(output_path, document_id)
    except StorageError as exc:
        click.echo(f"[error] S3 output archive failed: {exc}", err=True)
        sys.exit(1)

    return f"s3://{output_bucket}/{s3_key}"


def _print_registration_summary(result) -> None:  # type: ignore[no-untyped-def]
    """Print a concise registration summary to stdout."""
    click.echo("[ok] Registration complete.")
    click.echo(f"     document_id  : {result.document_id}")
    click.echo(f"     artifact     : {result.artifact_path}")
    if result.storage:
        click.echo(f"     s3 bucket    : {result.storage.bucket_name}")
        click.echo(f"     source key   : {result.storage.source_document_key}")
        click.echo(f"     artifact key : {result.storage.intake_artifact_key}")
    else:
        click.echo("     storage      : local only")


def _print_pipeline_summary(  # type: ignore[no-untyped-def]
    output,
    output_path: Path,
    logger: PipelineLogger,
    s3_archive: "str | None" = None,
) -> None:
    """Print a concise operator-facing summary after a successful pipeline run."""
    click.echo("")
    click.echo("[ok] Pipeline complete.")
    click.echo(f"     document_id      : {output.document_id}")
    click.echo(f"     session_id       : {output.session_id}")
    click.echo(f"     severity         : {output.severity}")
    click.echo(f"     category         : {output.category}")
    click.echo(f"     confidence_score : {output.confidence_score:.2f}")
    click.echo(f"     escalation       : {'YES' if output.escalation_required else 'no'}")
    if output.escalation_required and output.escalation_reason:
        click.echo(f"     escalation_reason: {output.escalation_reason}")
    click.echo(f"     citations        : {len(output.citations)}")
    click.echo(f"     output           : {output_path}")
    if s3_archive:
        click.echo(f"     s3 archive       : {s3_archive}")
    if logger.log_file_path is not None:
        click.echo(f"     session log      : {logger.log_file_path}")


if __name__ == "__main__":
    cli()
