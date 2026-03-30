"""
CaseOps CLI entry point.

Usage:
  python -m app.cli intake <FILE> --source-type FDA --document-date 2026-03-30
  python -m app.cli intake <FILE> --source-type Incident --document-date 2026-03-30 \
      --submitter-note "Flagged for immediate review"

Run `python -m app.cli --help` for full usage.
"""

import sys

import click
from pydantic import ValidationError

from app.schemas.intake_models import IntakeMetadata
from app.services.intake_service import IntakeError, run_intake


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

    try:
        document_id = run_intake(file_path=file_path, metadata=metadata)
    except IntakeError as exc:
        click.echo(f"[error] Intake failed: {exc}", err=True)
        sys.exit(1)

    click.echo(f"[ok] Intake complete. document_id={document_id}")


if __name__ == "__main__":
    cli()
