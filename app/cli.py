"""
CaseOps CLI entry point.

Commands:
  intake   Validate and register a local document (intake only; no pipeline run).
  intake-gold
           Validate a local Databricks Gold export payload and register one
           record as the existing IntakeResult handoff.
  run      Run the full end-to-end pipeline: intake → retrieval → analysis →
           validation → output packaging.

Usage examples:

  # Register a document without running the pipeline:
  python3 -m app.cli intake path/to/advisory.txt \\
      --source-type FDA --document-date 2026-03-30

  # Run the full pipeline end-to-end:
  python3 -m app.cli run path/to/advisory.txt \\
      --source-type FDA --document-date 2026-03-30

  # With optional submitter note (used as KB retrieval query):
  python3 -m app.cli run path/to/advisory.txt \\
      --source-type CISA --document-date 2026-03-30 \\
      --submitter-note "Critical ICS vulnerability — immediate review required"

  python3 -m app.cli --help

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
import json
from pathlib import Path

import click
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from app.schemas.intake_models import IntakeMetadata
from app.schemas.safety_models import FailurePolicy, SafetyStatus
from app.services.databricks_gold_adapter import (
    DatabricksGoldAdapterError,
    consume_databricks_gold_payload_file,
)
from app.services.intake_service import IntakeError, run_intake
from app.services.s3_service import S3Service, StorageError
from app.utils.id_utils import generate_session_id
from app.utils.logging_utils import LoggingConfig, PipelineLogger
from app.utils.output_writer import OutputWriteError, write_case_output
from app.workflows.pipeline_workflow import PipelineWorkflowError, run_pipeline

_LIVE_RUN_REQUIRED_ENV = ("BEDROCK_KB_ID", "BEDROCK_MODEL_ID", "AWS_REGION")


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Bedrock CaseOps Multi-Agent Control Tower — CLI."""
    ctx.ensure_object(dict)
    ctx.obj["env_path"] = _load_env_file()


def _load_env_file() -> Path | None:
    """
    Load the nearest .env file from the current working directory tree.

    Existing environment variables win over .env values so CI, shells, and
    deployment environments can override local defaults explicitly.
    """
    env_path = find_dotenv(usecwd=True)
    if not env_path:
        return None
    load_dotenv(env_path, override=False)
    return Path(env_path)


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


# ── Databricks Gold intake command ─────────────────────────────────────────────


@cli.command("intake-gold")
@click.argument(
    "payload_path",
    metavar="PAYLOAD",
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.option(
    "--gold-record-id",
    default=None,
    help="Gold record ID to consume when the payload contains multiple records.",
)
@click.option(
    "--output-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Optional local artifact root. Defaults to outputs/databricks_gold.",
)
def intake_gold(
    payload_path: Path,
    gold_record_id: str | None,
    output_dir: Path | None,
) -> None:
    """
    Validate and register a Databricks Gold export payload record.

    This is an intake-only local adapter path. It preserves the existing
    IntakeResult boundary and does not call Databricks, Delta Share, Bedrock,
    Knowledge Bases, S3, or the agent pipeline.
    """
    try:
        result = consume_databricks_gold_payload_file(
            payload_path,
            gold_record_id=gold_record_id,
            output_dir=output_dir,
        )
    except DatabricksGoldAdapterError as exc:
        click.echo(f"[error] Databricks Gold intake failed: {exc}", err=True)
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
    output_dir = os.getenv("OUTPUT_DIR", "outputs")

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

    # ── step 4: apply operator input guardrails when enabled ──────────────────
    try:
        guardrails_config = _load_guardrails_runtime_config()
        guardrails_service = _build_guardrails_service(guardrails_config)
        input_safety = _run_operator_input_safety_check(
            document_id=intake_result.document_id,
            file_path=file_path,
            source_type=source_type,
            document_date=document_date,
            submitter_note=submitter_note,
            output_dir=output_dir,
            guardrails_config=guardrails_config,
            guardrails_service=guardrails_service,
        )
    except Exception as exc:
        click.echo(f"[error] Runtime safety initialisation failed: {exc}", err=True)
        sys.exit(1)

    if input_safety and input_safety.assessment.status == SafetyStatus.BLOCK:
        _print_safety_block_summary(
            "Operator input blocked by runtime safety policy.",
            input_safety.assessment,
            input_safety.artifact_path,
        )
        sys.exit(1)

    # ── step 5: build pipeline dependencies ───────────────────────────────────
    try:
        runtime_config = _load_pipeline_runtime_config()
        retrieval_provider, analysis_agent, validation_agent, tool_executor = (
            _build_pipeline_deps(runtime_config=runtime_config)
        )
    except Exception as exc:
        click.echo(f"[error] Pipeline initialisation failed: {exc}", err=True)
        click.echo(
            "[hint]  Check that BEDROCK_KB_ID is set in your environment or .env file.",
            err=True,
        )
        sys.exit(1)

    # ── step 6: run pipeline ───────────────────────────────────────────────────
    try:
        output = run_pipeline(
            intake_result,
            retrieval_provider=retrieval_provider,
            analysis_agent=analysis_agent,
            validation_agent=validation_agent,
            tool_executor=tool_executor,
            logger=logger,
            session_id=session_id,
            max_attempts=runtime_config.max_agent_retries,
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

    # ── step 7: run output safety gate before writing final JSON ──────────────
    try:
        safety_result = _run_case_output_safety_check(
            output=output,
            output_dir=output_dir,
            policy=FailurePolicy(
                low_confidence_threshold=runtime_config.escalation_confidence_threshold
            ),
            guardrails_config=guardrails_config,
            guardrails_service=guardrails_service,
        )
    except Exception as exc:
        click.echo(f"[error] Runtime safety check failed: {exc}", err=True)
        sys.exit(1)

    if safety_result.assessment.status == SafetyStatus.BLOCK:
        _print_safety_block_summary(
            "Generated output blocked by runtime safety policy.",
            safety_result.assessment,
            safety_result.artifact_path,
        )
        sys.exit(1)

    # ── step 8: write output locally ──────────────────────────────────────────
    try:
        output_path = write_case_output(output, output_dir=output_dir)
    except OutputWriteError as exc:
        click.echo(f"[error] Could not write output: {exc}", err=True)
        sys.exit(1)

    # ── step 9: archive to S3 (if S3_OUTPUT_BUCKET is configured) ─────────────
    s3_archive_location: str | None = _archive_output_to_s3(
        output_path=output_path,
        document_id=output.document_id,
    )

    # ── step 10: print success summary ────────────────────────────────────────
    _print_pipeline_summary(
        output,
        output_path,
        logger,
        s3_archive_location,
        safety_assessment=safety_result.assessment,
        safety_artifact_path=safety_result.artifact_path,
    )


# ── doctor / check-config commands ────────────────────────────────────────────


@cli.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check local runtime configuration without making AWS calls."""
    _run_config_diagnostic(ctx)


@cli.command("check-config")
@click.pass_context
def check_config(ctx: click.Context) -> None:
    """Alias for doctor."""
    _run_config_diagnostic(ctx)


# ── eval commands ─────────────────────────────────────────────────────────────


@cli.group("eval")
def eval_group() -> None:
    """Run local evaluation workflows and write operator artifacts."""


@eval_group.command("run")
@click.option(
    "--candidates-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of candidate CaseOutput JSON files named {case_id}.json.",
)
@click.option(
    "--dataset-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Evaluation dataset directory. Defaults to data/evaluation.",
)
@click.option(
    "--output-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Root directory for artifacts. Defaults to OUTPUT_DIR or outputs.",
)
@click.option("--run-id", default=None, help="Optional stable evaluation run ID.")
@click.option("--no-report", is_flag=True, help="Skip markdown report generation.")
def eval_run(
    candidates_dir: Path,
    dataset_dir: Path | None,
    output_root: Path | None,
    run_id: str | None,
    no_report: bool,
) -> None:
    """Score candidate outputs against the evaluation dataset."""
    from app.evaluation.artifact_writer import ArtifactWriteError, write_evaluation_run
    from app.evaluation.metrics_translator import evaluation_run_summary_to_metrics
    from app.evaluation.runner import RunnerError, run_evaluation

    try:
        candidates = _load_candidate_output_map(candidates_dir)
        result = run_evaluation(
            candidates,
            dataset_dir=dataset_dir,
            run_id=run_id,
        )
        bundle = write_evaluation_run(
            result,
            _resolve_output_root(output_root),
            generate_report=not no_report,
        )
        _publish_evaluation_metrics(
            evaluation_run_summary_to_metrics(result.summary, _load_eval_dashboard_config())
        )
    except (RunnerError, ArtifactWriteError, ValueError) as exc:
        click.echo(f"[error] Evaluation run failed: {exc}", err=True)
        sys.exit(1)

    _print_eval_run_summary(result, bundle)


@eval_group.command("safety")
@click.option(
    "--suite-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Safety fixture directory. Defaults to tests/fixtures/safety_cases.",
)
@click.option(
    "--output-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Root directory for artifacts. Defaults to OUTPUT_DIR or outputs.",
)
@click.option("--suite-id", default=None, help="Optional stable safety suite run ID.")
@click.option("--no-report", is_flag=True, help="Skip markdown report generation.")
def eval_safety(
    suite_dir: Path | None,
    output_root: Path | None,
    suite_id: str | None,
    no_report: bool,
) -> None:
    """Run the adversarial safety suite."""
    from app.evaluation.artifact_writer import ArtifactWriteError, write_safety_run
    from app.evaluation.metrics_translator import safety_distribution_to_metrics
    from app.evaluation.safety_suite import run_safety_suite

    try:
        results, summary = run_safety_suite(suite_dir=suite_dir)
        bundle = write_safety_run(
            results,
            summary,
            _resolve_output_root(output_root),
            suite_id=suite_id,
            generate_report=not no_report,
        )
        _publish_evaluation_metrics(
            safety_distribution_to_metrics(
                _safety_status_distribution(results),
                _load_eval_dashboard_config(),
            )
        )
    except (ArtifactWriteError, ValueError) as exc:
        click.echo(f"[error] Safety evaluation failed: {exc}", err=True)
        sys.exit(1)

    _print_safety_eval_summary(summary, bundle)


@eval_group.command("compare")
@click.option(
    "--baseline-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of baseline CaseOutput JSON files named {case_id}.json.",
)
@click.option(
    "--optimized-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of optimized CaseOutput JSON files named {case_id}.json.",
)
@click.option(
    "--dataset-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Evaluation dataset directory. Defaults to data/evaluation.",
)
@click.option(
    "--output-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Root directory for artifacts. Defaults to OUTPUT_DIR or outputs.",
)
@click.option("--run-id", default=None, help="Optional stable comparison run ID.")
@click.option("--no-report", is_flag=True, help="Skip markdown report generation.")
def eval_compare(
    baseline_dir: Path,
    optimized_dir: Path,
    dataset_dir: Path | None,
    output_root: Path | None,
    run_id: str | None,
    no_report: bool,
) -> None:
    """Compare baseline and optimized candidate output directories."""
    from app.evaluation.artifact_writer import ArtifactWriteError, write_comparison_run
    from app.evaluation.comparison_runner import (
        ComparisonAlignmentError,
        run_comparison,
    )
    from app.evaluation.metrics_translator import comparison_summary_to_metrics

    try:
        result = run_comparison(
            baseline_dir=baseline_dir,
            optimized_dir=optimized_dir,
            dataset_dir=dataset_dir,
        )
        bundle = write_comparison_run(
            result,
            _resolve_output_root(output_root),
            run_id=run_id,
            generate_report=not no_report,
        )
        _publish_evaluation_metrics(
            comparison_summary_to_metrics(result.summary, _load_eval_dashboard_config())
        )
    except (ComparisonAlignmentError, ArtifactWriteError, ValueError) as exc:
        click.echo(f"[error] Comparison run failed: {exc}", err=True)
        sys.exit(1)

    _print_comparison_summary(result, bundle)


@eval_group.command("dashboard")
@click.option(
    "--output-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Root directory for dashboard artifact. Defaults to OUTPUT_DIR or outputs.",
)
@click.option(
    "--filename",
    default="dashboard.json",
    help="Dashboard JSON filename under evaluation_dashboard/.",
)
def eval_dashboard(output_root: Path | None, filename: str) -> None:
    """Build the local CloudWatch evaluation dashboard JSON body."""
    from app.evaluation.dashboard_builder import build_evaluation_dashboard

    try:
        config = _load_eval_dashboard_config()
        body = build_evaluation_dashboard(config)
        dashboard_path = _write_dashboard_body(
            body,
            output_root=_resolve_output_root(output_root),
            filename=filename,
        )
    except (OSError, ValueError) as exc:
        click.echo(f"[error] Dashboard generation failed: {exc}", err=True)
        sys.exit(1)

    click.echo("[ok] Evaluation dashboard artifact written.")
    click.echo(f"     dashboard_name : {config.dashboard_name}")
    click.echo(f"     namespace      : {config.metrics_namespace}")
    click.echo(f"     environment    : {config.environment}")
    click.echo(f"     dashboard      : {dashboard_path}")


# ── private helpers ────────────────────────────────────────────────────────────


def _run_config_diagnostic(ctx: click.Context) -> None:
    """Print a local configuration diagnostic and exit non-zero on errors."""
    env_path = (ctx.obj or {}).get("env_path")
    errors: list[str] = []

    click.echo("CaseOps configuration check")
    if env_path:
        click.echo(f"[ok] .env loaded: {env_path}")
    else:
        click.echo("[info] .env loaded: no .env file found")

    click.echo("")
    click.echo("Required for live pipeline:")
    missing_required = []
    for name in _LIVE_RUN_REQUIRED_ENV:
        value = os.getenv(name, "").strip()
        if value:
            click.echo(f"[ok] {name}: {_display_config_value(name, value)}")
        else:
            missing_required.append(name)
            click.echo(f"[missing] {name}")

    if missing_required:
        errors.append(
            "Missing required live-run variables: "
            + ", ".join(missing_required)
        )

    config_errors = _validate_runtime_config_values()
    errors.extend(config_errors)

    click.echo("")
    click.echo("Optional local/runtime settings:")
    _print_optional_config("AWS_PROFILE", fallback="default credential chain")
    _print_optional_config("S3_DOCUMENT_BUCKET", fallback="local-only intake")
    _print_optional_config("S3_OUTPUT_BUCKET", fallback="local-only output")
    _print_optional_config("CASEOPS_ENABLE_GUARDRAILS", fallback="false")
    if os.getenv("CASEOPS_ENABLE_GUARDRAILS", "").strip().lower() == "true":
        _print_optional_config("CASEOPS_GUARDRAIL_ID", fallback="required")
        _print_optional_config("CASEOPS_GUARDRAIL_VERSION", fallback="1")
    click.echo(f"[info] OUTPUT_DIR: {os.getenv('OUTPUT_DIR', 'outputs')}")

    if config_errors:
        click.echo("")
        click.echo("Config value errors:")
        for error in config_errors:
            click.echo(f"[error] {error}")

    click.echo("")
    if errors:
        click.echo("[fail] Configuration is incomplete for a live pipeline run.")
        for error in errors:
            click.echo(f"       {error}")
        sys.exit(1)

    click.echo("[ok] Required live-run configuration is present.")


def _display_config_value(name: str, value: str) -> str:
    """Return a diagnostic-safe display value for a configuration variable."""
    if name in {"AWS_REGION", "BEDROCK_MODEL_ID"}:
        return value
    return "set"


def _print_optional_config(name: str, *, fallback: str) -> None:
    value = os.getenv(name, "").strip()
    if value:
        click.echo(f"[ok] {name}: {_display_config_value(name, value)}")
    else:
        click.echo(f"[info] {name}: not set ({fallback})")


def _resolve_output_root(output_root: Path | None) -> Path:
    """Resolve an evaluation artifact root from CLI option or OUTPUT_DIR."""
    return output_root if output_root is not None else Path(os.getenv("OUTPUT_DIR", "outputs"))


def _load_candidate_output_map(candidates_dir: Path):
    """
    Load a case_id -> path map from a candidate output directory.

    Files must be named {case_id}.json. Validation is left to the evaluation
    runner so errors include the case ID and existing runner semantics.
    """
    candidates = {path.stem: path for path in sorted(candidates_dir.glob("*.json"))}
    if not candidates:
        raise ValueError(f"No candidate JSON files found in {candidates_dir}.")
    return candidates


def _load_eval_dashboard_config():
    """Load evaluation dashboard / metrics config."""
    from app.utils.config import load_evaluation_dashboard_config

    return load_evaluation_dashboard_config()


def _publish_evaluation_metrics(datums) -> None:  # type: ignore[no-untyped-def]
    """Publish evaluation metrics only when CASEOPS_ENABLE_EVALUATION_METRICS=true."""
    from app.services.cloudwatch_metrics_service import build_metrics_service

    config = _load_eval_dashboard_config()
    service = build_metrics_service(config=config)
    service.publish_metrics(datums)


def _safety_status_distribution(results) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Count actual safety statuses from safety suite results."""
    distribution: dict[str, int] = {}
    for result in results:
        status = result.actual_status.value
        distribution[status] = distribution.get(status, 0) + 1
    return distribution


def _write_dashboard_body(
    body: dict,
    *,
    output_root: Path,
    filename: str,
) -> Path:
    """Write the dashboard body under {output_root}/evaluation_dashboard/."""
    safe_filename = filename.strip()
    if not safe_filename:
        raise ValueError("dashboard filename must be non-empty")
    if Path(safe_filename).name != safe_filename:
        raise ValueError("dashboard filename must not include path separators")

    dashboard_dir = output_root / "evaluation_dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    path = dashboard_dir / safe_filename
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path.resolve()


def _print_eval_run_summary(result, bundle) -> None:  # type: ignore[no-untyped-def]
    """Print a concise eval run summary."""
    click.echo("[ok] Evaluation run complete.")
    click.echo(f"     run_id        : {result.summary.run_id}")
    click.echo(f"     total_cases   : {result.summary.total_cases}")
    click.echo(f"     passed_cases  : {result.summary.passed_cases}")
    click.echo(f"     failed_cases  : {result.summary.failed_cases}")
    click.echo(f"     average_score : {result.summary.average_score:.3f}")
    click.echo(f"     artifacts     : {bundle.metadata.artifact_dir}")
    if bundle.report_path:
        click.echo(f"     report        : {bundle.report_path}")


def _print_safety_eval_summary(summary, bundle) -> None:  # type: ignore[no-untyped-def]
    """Print a concise safety suite summary."""
    click.echo("[ok] Safety evaluation complete.")
    click.echo(f"     run_id      : {bundle.metadata.run_id}")
    click.echo(f"     total       : {summary.total}")
    click.echo(f"     passed      : {summary.passed}")
    click.echo(f"     failed      : {summary.failed}")
    if summary.failed_case_ids:
        click.echo(f"     failed_ids  : {', '.join(summary.failed_case_ids)}")
    click.echo(f"     artifacts   : {bundle.metadata.artifact_dir}")
    if bundle.report_path:
        click.echo(f"     report      : {bundle.report_path}")


def _print_comparison_summary(result, bundle) -> None:  # type: ignore[no-untyped-def]
    """Print a concise comparison summary."""
    summary = result.summary
    click.echo("[ok] Comparison run complete.")
    click.echo(f"     run_id          : {bundle.metadata.run_id}")
    click.echo(f"     total_cases     : {summary.total_cases}")
    click.echo(f"     baseline_avg    : {summary.baseline_average_score:.3f}")
    click.echo(f"     optimized_avg   : {summary.optimized_average_score:.3f}")
    click.echo(f"     avg_delta       : {summary.average_score_delta:.3f}")
    click.echo(f"     improved        : {len(summary.improved_case_ids)}")
    click.echo(f"     regressed       : {len(summary.regressed_case_ids)}")
    click.echo(f"     unchanged       : {len(summary.unchanged_case_ids)}")
    if result.missing_baseline_case_ids:
        click.echo(f"     missing baseline: {', '.join(result.missing_baseline_case_ids)}")
    if result.missing_optimized_case_ids:
        click.echo(f"     missing optimized: {', '.join(result.missing_optimized_case_ids)}")
    click.echo(f"     artifacts       : {bundle.metadata.artifact_dir}")
    if bundle.report_path:
        click.echo(f"     report          : {bundle.report_path}")


def _validate_runtime_config_values() -> list[str]:
    """Validate local scalar config values that commonly break startup."""
    errors: list[str] = []
    _validate_positive_int_env("RETRIEVAL_MAX_RESULTS", "5", errors)
    _validate_positive_int_env("MAX_AGENT_RETRIES", "2", errors)
    _validate_probability_env("ESCALATION_CONFIDENCE_THRESHOLD", "0.60", errors)
    _validate_guardrails_env(errors)
    return errors


def _validate_positive_int_env(name: str, default: str, errors: list[str]) -> None:
    raw = os.getenv(name, default).strip()
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be a positive integer, got {raw!r}.")
        return
    if value < 1:
        errors.append(f"{name} must be a positive integer, got {value!r}.")


def _validate_probability_env(name: str, default: str, errors: list[str]) -> None:
    raw = os.getenv(name, default).strip()
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{name} must be a number between 0.0 and 1.0, got {raw!r}.")
        return
    if not (0.0 <= value <= 1.0):
        errors.append(f"{name} must be between 0.0 and 1.0, got {value!r}.")


def _validate_guardrails_env(errors: list[str]) -> None:
    raw_enable = os.getenv("CASEOPS_ENABLE_GUARDRAILS", "false").strip().lower()
    if raw_enable not in ("true", "false"):
        errors.append(
            "CASEOPS_ENABLE_GUARDRAILS must be 'true' or 'false', "
            f"got {raw_enable!r}."
        )
        return
    if raw_enable == "true" and not os.getenv("CASEOPS_GUARDRAIL_ID", "").strip():
        errors.append(
            "CASEOPS_GUARDRAIL_ID is required when CASEOPS_ENABLE_GUARDRAILS=true."
        )


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


def _load_pipeline_runtime_config():
    """Load pipeline config for the CLI runtime."""
    from app.utils.config import load_pipeline_config

    return load_pipeline_config()


def _load_guardrails_runtime_config():
    """Load Guardrails config for runtime safety gates."""
    from app.utils.config import load_guardrails_config

    return load_guardrails_config()


def _build_guardrails_service(guardrails_config):  # type: ignore[no-untyped-def]
    """Build a Guardrails service only when runtime Guardrails are enabled."""
    if not guardrails_config.enable_guardrails:
        return None

    from app.services.guardrails_service import GuardrailsService

    return GuardrailsService(region=os.getenv("AWS_REGION", "us-east-1"))


def _run_operator_input_safety_check(  # type: ignore[no-untyped-def]
    *,
    document_id: str,
    file_path: str,
    source_type: str,
    document_date: str,
    submitter_note: str | None,
    output_dir: str,
    guardrails_config,
    guardrails_service,
):
    """Apply the runtime operator-input safety gate."""
    from app.workflows.runtime_safety import (
        build_operator_input_text,
        run_operator_input_safety_check,
    )

    operator_input_text = build_operator_input_text(
        file_path=file_path,
        source_type=source_type,
        document_date=document_date,
        submitter_note=submitter_note,
    )
    return run_operator_input_safety_check(
        document_id=document_id,
        operator_input_text=operator_input_text,
        output_dir=output_dir,
        guardrails_config=guardrails_config,
        guardrails_service=guardrails_service,
    )


def _run_case_output_safety_check(  # type: ignore[no-untyped-def]
    *,
    output,
    output_dir: str,
    policy: FailurePolicy,
    guardrails_config,
    guardrails_service,
):
    """Apply deterministic and optional Guardrails checks to a CaseOutput."""
    from app.workflows.runtime_safety import run_case_output_safety_check

    return run_case_output_safety_check(
        output=output,
        output_dir=output_dir,
        policy=policy,
        guardrails_config=guardrails_config,
        guardrails_service=guardrails_service,
    )


def _build_pipeline_deps(*, runtime_config=None):  # type: ignore[return]
    """
    Build and wire all pipeline service dependencies.

    Returns a 4-tuple: (retrieval_provider, analysis_agent, validation_agent, tool_executor).

    Raises if required environment variables are missing (e.g. BEDROCK_KB_ID).
    AWS service clients are constructed here; live connectivity is not validated
    at build time — failures manifest when the pipeline first calls the service.
    """
    from app.workflows.runtime_factory import build_pipeline_dependencies

    return build_pipeline_dependencies(runtime_config=runtime_config)


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
    safety_assessment=None,
    safety_artifact_path: "Path | None" = None,
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
    if safety_assessment is not None:
        click.echo(f"     safety_status    : {safety_assessment.status.value}")
        click.echo(f"     safety_issues    : {len(safety_assessment.issues)}")
    click.echo(f"     output           : {output_path}")
    if safety_artifact_path is not None:
        click.echo(f"     safety artifact  : {safety_artifact_path}")
    if s3_archive:
        click.echo(f"     s3 archive       : {s3_archive}")
    if logger.log_file_path is not None:
        click.echo(f"     session log      : {logger.log_file_path}")


def _print_safety_block_summary(  # type: ignore[no-untyped-def]
    message: str,
    safety_assessment,
    safety_artifact_path: Path,
) -> None:
    """Print a concise blocked-run safety summary."""
    click.echo(f"[error] {message}", err=True)
    click.echo(f"        document_id     : {safety_assessment.document_id}", err=True)
    click.echo(f"        safety_status   : {safety_assessment.status.value}", err=True)
    click.echo(f"        safety_issues   : {len(safety_assessment.issues)}", err=True)
    click.echo(f"        safety artifact : {safety_artifact_path}", err=True)


if __name__ == "__main__":
    cli()
