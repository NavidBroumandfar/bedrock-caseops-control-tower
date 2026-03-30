"""
CaseOps CLI entry point.

Usage:
  python -m app.cli intake <FILE> --source-type FDA --document-date 2026-03-30
  python -m app.cli intake <FILE> --source-type Incident --document-date 2026-03-30 \
      --submitter-note "Flagged for immediate review"

Run `python -m app.cli --help` for full usage.

S3 upload behaviour:
  If S3_DOCUMENT_BUCKET is set in the environment, the source document and
  intake artifact are uploaded to S3 automatically after local intake.
  If the variable is absent, intake runs in local-only mode (no S3 calls).
"""

import os
import sys

import click
from pydantic import ValidationError

from app.schemas.intake_models import IntakeMetadata
from app.services.intake_service import IntakeError, run_intake
from app.services.s3_service import S3Service, StorageError


@click.group()
def cli() -> None:
    """Bedrock CaseOps Multi-Agent Control Tower — CLI."""


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

    s3_service: S3Service | None = None
    bucket = os.getenv("S3_DOCUMENT_BUCKET")
    if bucket:
        try:
            s3_service = S3Service(bucket_name=bucket)
        except StorageError as exc:
            click.echo(f"[error] Could not initialise S3 client: {exc}", err=True)
            sys.exit(1)
    else:
        click.echo("[info] S3_DOCUMENT_BUCKET not set — running in local-only mode.")

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


def _print_registration_summary(result) -> None:
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


if __name__ == "__main__":
    cli()
