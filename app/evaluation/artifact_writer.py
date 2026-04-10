"""
J-1 local artifact writer for evaluation run results.

Writes structured JSON artifacts and markdown reports to disk for completed
evaluation runs.  All output is local and deterministic — no live AWS dependency.

Supported artifact types:
  - Evaluation run artifacts (F-2 EvaluationRunResult)
  - Safety suite artifacts (H-2 SafetySuiteSummary + list[SafetyCaseResult])
  - Comparison run artifacts (I-2 ComparisonRunResult)

Output directory structure:
  {output_root}/evaluation_runs/{run_id}/summary.json
  {output_root}/evaluation_runs/{run_id}/case_results.json
  {output_root}/evaluation_runs/{run_id}/report.md

  {output_root}/safety_runs/{suite_id}/summary.json
  {output_root}/safety_runs/{suite_id}/case_results.json
  {output_root}/safety_runs/{suite_id}/report.md

  {output_root}/comparison_runs/{run_id}/summary.json
  {output_root}/comparison_runs/{run_id}/case_results.json
  {output_root}/comparison_runs/{run_id}/report.md

JSON artifacts are:
  summary.json       — typed summary for the run (EvaluationRunSummary,
                       SafetySuiteSummary, or ComparisonSummary)
  case_results.json  — array of per-case result objects
  report.md          — human-readable markdown summary (when generate_report=True)

Public surface:
  ArtifactWriteError       — raised on filesystem failure.
  write_evaluation_run()   — write artifacts for a F-2 evaluation run.
  write_safety_run()       — write artifacts for an H-2 safety suite run.
  write_comparison_run()   — write artifacts for an I-2 comparison run.

Separation rules:
  - No boto3, no live AWS calls.
  - No scoring logic — consumes existing result types read-only.
  - Imports only: evaluation result types, report_generator, artifact_models, id_utils.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.comparison_runner import (
    ComparisonCaseResult,
    ComparisonRunResult,
    ComparisonSummary,
)
from app.evaluation.report_generator import (
    generate_comparison_run_report,
    generate_evaluation_run_report,
    generate_safety_run_report,
)
from app.evaluation.runner import EvaluationRunResult
from app.evaluation.safety_suite import SafetyCaseResult, SafetySuiteSummary
from app.schemas.artifact_models import ArtifactMetadata, ReportBundle
from app.utils.id_utils import generate_session_id


class ArtifactWriteError(Exception):
    """Raised when the artifact writer cannot write to the output directory."""


# ── Internal helpers ───────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(directory: Path) -> None:
    """Create directory (and parents) if absent; raise ArtifactWriteError on failure."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactWriteError(
            f"Cannot create artifact directory {directory}: {exc}"
        ) from exc


def _write_json(path: Path, data: object) -> None:
    """Serialize data to indented JSON and write to path; raise ArtifactWriteError on failure."""
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ArtifactWriteError(
            f"Cannot write JSON artifact to {path}: {exc}"
        ) from exc


def _write_text(path: Path, text: str) -> None:
    """Write a text string to path; raise ArtifactWriteError on failure."""
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ArtifactWriteError(
            f"Cannot write text artifact to {path}: {exc}"
        ) from exc


def _rel_posix(path: Path) -> str:
    """Convert a path to a forward-slash string for cross-platform portability."""
    return path.as_posix()


# ── Serialization helpers ──────────────────────────────────────────────────────


def _serialize_safety_case_result(r: SafetyCaseResult) -> dict:
    """Convert SafetyCaseResult (frozen dataclass with nested Pydantic) to a JSON dict."""
    return {
        "case_id": r.case_id,
        "expected_status": r.expected_status.value,
        "actual_status": r.actual_status.value,
        "passed": r.passed,
        "missing_issue_codes": [c.value for c in r.missing_issue_codes],
        "assessment": r.assessment.model_dump(mode="json"),
    }


def _serialize_safety_suite_summary(s: SafetySuiteSummary) -> dict:
    """Convert SafetySuiteSummary (frozen dataclass) to a JSON dict."""
    return {
        "total": s.total,
        "passed": s.passed,
        "failed": s.failed,
        "failed_case_ids": list(s.failed_case_ids),
    }


def _serialize_comparison_case_result(r: ComparisonCaseResult) -> dict:
    """Convert ComparisonCaseResult (frozen dataclass) to a JSON dict."""
    return {
        "case_id": r.case_id,
        "baseline_score": r.baseline_score,
        "optimized_score": r.optimized_score,
        "score_delta": r.score_delta,
        "baseline_pass": r.baseline_pass,
        "optimized_pass": r.optimized_pass,
        "baseline_safety_status": r.baseline_safety_status,
        "optimized_safety_status": r.optimized_safety_status,
        "safety_status_changed": r.safety_status_changed,
        "verdict": r.verdict,
    }


def _serialize_comparison_summary(s: ComparisonSummary) -> dict:
    """Convert ComparisonSummary (frozen dataclass) to a JSON dict."""
    return {
        "total_cases": s.total_cases,
        "baseline_average_score": s.baseline_average_score,
        "optimized_average_score": s.optimized_average_score,
        "average_score_delta": s.average_score_delta,
        "baseline_pass_count": s.baseline_pass_count,
        "optimized_pass_count": s.optimized_pass_count,
        "baseline_safety_distribution": s.baseline_safety_distribution,
        "optimized_safety_distribution": s.optimized_safety_distribution,
        "improved_case_ids": list(s.improved_case_ids),
        "regressed_case_ids": list(s.regressed_case_ids),
        "unchanged_case_ids": list(s.unchanged_case_ids),
    }


# ── Public writer functions ────────────────────────────────────────────────────


def write_evaluation_run(
    run_result: EvaluationRunResult,
    output_root: Path,
    *,
    generate_report: bool = True,
) -> ReportBundle:
    """
    Write artifacts for a completed F-2 evaluation run.

    Creates:
      {output_root}/evaluation_runs/{run_id}/summary.json
      {output_root}/evaluation_runs/{run_id}/case_results.json
      {output_root}/evaluation_runs/{run_id}/report.md  (when generate_report=True)

    The run_id is taken from run_result.summary.run_id.

    Parameters
    ----------
    run_result
        Completed EvaluationRunResult from runner.run_evaluation().
    output_root
        Root directory for all J-1 artifacts.
    generate_report
        When True (default), writes a markdown report alongside the JSON artifacts.

    Returns
    -------
    ReportBundle with ArtifactMetadata and the relative report path (if generated).

    Raises
    ------
    ArtifactWriteError on any filesystem failure.
    """
    run_id = run_result.summary.run_id
    artifact_dir = output_root / "evaluation_runs" / run_id
    _ensure_dir(artifact_dir)

    created_at = _utc_now_iso()
    artifact_files: list[str] = []

    _write_json(artifact_dir / "summary.json", run_result.summary.model_dump(mode="json"))
    artifact_files.append("summary.json")

    case_data = [r.model_dump(mode="json") for r in run_result.results]
    _write_json(artifact_dir / "case_results.json", case_data)
    artifact_files.append("case_results.json")

    report_path: str | None = None
    if generate_report:
        report_text = generate_evaluation_run_report(run_result)
        _write_text(artifact_dir / "report.md", report_text)
        artifact_files.append("report.md")
        report_path = _rel_posix(Path("evaluation_runs") / run_id / "report.md")

    metadata = ArtifactMetadata(
        run_id=run_id,
        kind="evaluation_run",
        created_at=created_at,
        artifact_dir=_rel_posix(Path("evaluation_runs") / run_id),
        artifact_files=artifact_files,
    )
    return ReportBundle(metadata=metadata, report_path=report_path)


def write_safety_run(
    results: list[SafetyCaseResult],
    summary: SafetySuiteSummary,
    output_root: Path,
    *,
    suite_id: str | None = None,
    generate_report: bool = True,
) -> ReportBundle:
    """
    Write artifacts for a completed H-2 safety suite run.

    Creates:
      {output_root}/safety_runs/{suite_id}/summary.json
      {output_root}/safety_runs/{suite_id}/case_results.json
      {output_root}/safety_runs/{suite_id}/report.md  (when generate_report=True)

    suite_id defaults to a fresh session-style ID when not provided.

    Parameters
    ----------
    results
        Per-case SafetyCaseResult list from safety_suite.run_safety_suite().
    summary
        SafetySuiteSummary from safety_suite.run_safety_suite().
    output_root
        Root directory for all J-1 artifacts.
    suite_id
        Stable identifier for this suite run; generated if not provided.
    generate_report
        When True (default), writes a markdown report alongside the JSON artifacts.

    Returns
    -------
    ReportBundle with ArtifactMetadata and the relative report path (if generated).

    Raises
    ------
    ArtifactWriteError on any filesystem failure.
    """
    effective_suite_id = suite_id if suite_id else generate_session_id()
    artifact_dir = output_root / "safety_runs" / effective_suite_id
    _ensure_dir(artifact_dir)

    created_at = _utc_now_iso()
    artifact_files: list[str] = []

    _write_json(artifact_dir / "summary.json", _serialize_safety_suite_summary(summary))
    artifact_files.append("summary.json")

    case_data = [_serialize_safety_case_result(r) for r in results]
    _write_json(artifact_dir / "case_results.json", case_data)
    artifact_files.append("case_results.json")

    report_path: str | None = None
    if generate_report:
        report_text = generate_safety_run_report(results, summary, suite_id=effective_suite_id)
        _write_text(artifact_dir / "report.md", report_text)
        artifact_files.append("report.md")
        report_path = _rel_posix(Path("safety_runs") / effective_suite_id / "report.md")

    metadata = ArtifactMetadata(
        run_id=effective_suite_id,
        kind="safety_run",
        created_at=created_at,
        artifact_dir=_rel_posix(Path("safety_runs") / effective_suite_id),
        artifact_files=artifact_files,
    )
    return ReportBundle(metadata=metadata, report_path=report_path)


def write_comparison_run(
    run_result: ComparisonRunResult,
    output_root: Path,
    *,
    run_id: str | None = None,
    generate_report: bool = True,
) -> ReportBundle:
    """
    Write artifacts for a completed I-2 comparison run.

    Creates:
      {output_root}/comparison_runs/{run_id}/summary.json
      {output_root}/comparison_runs/{run_id}/case_results.json
      {output_root}/comparison_runs/{run_id}/report.md  (when generate_report=True)

    run_id defaults to a fresh session-style ID when not provided.

    Parameters
    ----------
    run_result
        Completed ComparisonRunResult from comparison_runner.run_comparison().
    output_root
        Root directory for all J-1 artifacts.
    run_id
        Stable identifier for this comparison run; generated if not provided.
    generate_report
        When True (default), writes a markdown report alongside the JSON artifacts.

    Returns
    -------
    ReportBundle with ArtifactMetadata and the relative report path (if generated).

    Raises
    ------
    ArtifactWriteError on any filesystem failure.
    """
    effective_run_id = run_id if run_id else generate_session_id()
    artifact_dir = output_root / "comparison_runs" / effective_run_id
    _ensure_dir(artifact_dir)

    created_at = _utc_now_iso()
    artifact_files: list[str] = []

    _write_json(artifact_dir / "summary.json", _serialize_comparison_summary(run_result.summary))
    artifact_files.append("summary.json")

    case_data = [_serialize_comparison_case_result(r) for r in run_result.case_results]
    _write_json(artifact_dir / "case_results.json", case_data)
    artifact_files.append("case_results.json")

    report_path: str | None = None
    if generate_report:
        report_text = generate_comparison_run_report(run_result, run_id=effective_run_id)
        _write_text(artifact_dir / "report.md", report_text)
        artifact_files.append("report.md")
        report_path = _rel_posix(Path("comparison_runs") / effective_run_id / "report.md")

    metadata = ArtifactMetadata(
        run_id=effective_run_id,
        kind="comparison_run",
        created_at=created_at,
        artifact_dir=_rel_posix(Path("comparison_runs") / effective_run_id),
        artifact_files=artifact_files,
    )
    return ReportBundle(metadata=metadata, report_path=report_path)
