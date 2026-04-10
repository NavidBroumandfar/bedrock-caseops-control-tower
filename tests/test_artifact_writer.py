"""
J-1 unit tests — local artifact writer.

Coverage:

  write_evaluation_run():
    - creates the expected directory structure
    - summary.json exists and is valid JSON
    - case_results.json exists and is valid JSON
    - report.md exists when generate_report=True (default)
    - report.md is absent when generate_report=False
    - returns a ReportBundle with correct run_id
    - returns kind="evaluation_run"
    - artifact_files contains correct filenames with generate_report=True
    - artifact_files contains correct filenames with generate_report=False
    - report_path is None when generate_report=False
    - report_path is non-None when generate_report=True
    - summary JSON round-trips correctly (run_id preserved)
    - case_results JSON is a list with correct length
    - ArtifactWriteError raised on invalid output path
    - deterministic: two calls with same input produce same JSON content

  write_safety_run():
    - creates the expected directory under safety_runs/
    - summary.json contains correct total/passed/failed counts
    - case_results.json contains correct number of case entries
    - suite_id is used as directory name when provided
    - generates a suite_id when not provided
    - report.md is written when generate_report=True
    - artifact_files list is correct
    - returned metadata has kind="safety_run"

  write_comparison_run():
    - creates the expected directory under comparison_runs/
    - summary.json contains total_cases and verdict counts
    - case_results.json is a list with correct length
    - run_id is used as directory name when provided
    - generates a run_id when not provided
    - report.md is written when generate_report=True
    - artifact_files list is correct
    - returned metadata has kind="comparison_run"

  JSON serialization correctness:
    - evaluation summary fields are serialized correctly
    - safety case result fields are serialized correctly (enum values as strings)
    - comparison case result fields are serialized correctly (verdict as string)
    - nested SafetyAssessment is serialized correctly in case_results

  Structural:
    - artifact_writer does not import boto3
    - artifact_writer does not import any Bedrock or CloudWatch client
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.evaluation.artifact_writer import (
    ArtifactWriteError,
    write_comparison_run,
    write_evaluation_run,
    write_safety_run,
)
from app.evaluation.comparison_runner import (
    ComparisonCaseResult,
    ComparisonRunResult,
    ComparisonSummary,
)
from app.evaluation.runner import EvaluationRunResult
from app.evaluation.safety_suite import SafetyCaseResult, SafetySuiteSummary
from app.schemas.artifact_models import ReportBundle
from app.schemas.evaluation_models import DimensionScore, EvaluationResult, EvaluationRunSummary
from app.schemas.safety_models import (
    IssueSource,
    SafetyAssessment,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

_TS = "2026-04-11T00:00:00+00:00"


def _make_dim_score(name: str, score: float, passed: bool) -> DimensionScore:
    return DimensionScore(metric_name=name, score=score, passed=passed)


def _make_eval_result(case_id: str, score: float, pass_fail: bool) -> EvaluationResult:
    return EvaluationResult(
        case_id=case_id,
        run_id="test-run-001",
        evaluation_version="f2-v1",
        overall_score=score,
        pass_fail=pass_fail,
        dimension_scores=[_make_dim_score("severity_match", score, pass_fail)],
        timestamp=_TS,
    )


def _make_evaluation_run_result(run_id: str = "test-run-001") -> EvaluationRunResult:
    results = (
        _make_eval_result("case-001", 0.9, True),
        _make_eval_result("case-002", 0.4, False),
    )
    summary = EvaluationRunSummary(
        run_id=run_id,
        total_cases=2,
        passed_cases=1,
        failed_cases=1,
        average_score=0.65,
        per_metric_averages={"severity_match": 0.65},
        timestamp=_TS,
    )
    return EvaluationRunResult(results=results, summary=summary)


def _make_clean_assessment(doc_id: str = "doc-001") -> SafetyAssessment:
    return SafetyAssessment(
        document_id=doc_id,
        status=SafetyStatus.ALLOW,
        issues=[],
        has_blocking_issue=False,
        requires_escalation=False,
        timestamp=_TS,
    )


def _make_blocking_assessment(doc_id: str = "doc-bad") -> SafetyAssessment:
    issue = SafetyIssue(
        issue_code=SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT,
        severity=SafetyIssueSeverity.CRITICAL,
        message="Unsupported claims detected",
        blocking=True,
        source=IssueSource.VALIDATION,
    )
    return SafetyAssessment(
        document_id=doc_id,
        status=SafetyStatus.BLOCK,
        issues=[issue],
        has_blocking_issue=True,
        requires_escalation=True,
        timestamp=_TS,
    )


def _make_safety_suite() -> tuple[list[SafetyCaseResult], SafetySuiteSummary]:
    results = [
        SafetyCaseResult(
            case_id="safe-001",
            expected_status=SafetyStatus.ALLOW,
            actual_status=SafetyStatus.ALLOW,
            passed=True,
            missing_issue_codes=(),
            assessment=_make_clean_assessment("safe-001"),
        ),
        SafetyCaseResult(
            case_id="safe-002",
            expected_status=SafetyStatus.BLOCK,
            actual_status=SafetyStatus.BLOCK,
            passed=True,
            missing_issue_codes=(),
            assessment=_make_blocking_assessment("safe-002"),
        ),
    ]
    summary = SafetySuiteSummary(total=2, passed=2, failed=0, failed_case_ids=())
    return results, summary


def _make_comparison_run_result(run_id_hint: str = "cmp-run") -> ComparisonRunResult:
    case1 = ComparisonCaseResult(
        case_id="cmp-001",
        baseline_score=0.6,
        optimized_score=0.85,
        score_delta=0.25,
        baseline_pass=False,
        optimized_pass=True,
        baseline_safety_status="allow",
        optimized_safety_status="allow",
        safety_status_changed=False,
        verdict="improved",
    )
    case2 = ComparisonCaseResult(
        case_id="cmp-002",
        baseline_score=0.8,
        optimized_score=0.8,
        score_delta=0.0,
        baseline_pass=True,
        optimized_pass=True,
        baseline_safety_status="allow",
        optimized_safety_status="allow",
        safety_status_changed=False,
        verdict="unchanged",
    )
    summary = ComparisonSummary(
        total_cases=2,
        baseline_average_score=0.7,
        optimized_average_score=0.825,
        average_score_delta=0.125,
        baseline_pass_count=1,
        optimized_pass_count=2,
        baseline_safety_distribution={"allow": 2},
        optimized_safety_distribution={"allow": 2},
        improved_case_ids=("cmp-001",),
        regressed_case_ids=(),
        unchanged_case_ids=("cmp-002",),
    )
    return ComparisonRunResult(
        case_results=(case1, case2),
        summary=summary,
        missing_baseline_case_ids=(),
        missing_optimized_case_ids=(),
    )


# ── write_evaluation_run ──────────────────────────────────────────────────────


class TestWriteEvaluationRun:
    def test_creates_expected_directory(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path)
        expected_dir = tmp_path / "evaluation_runs" / "eval-abc"
        assert expected_dir.is_dir()

    def test_summary_json_exists(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path)
        assert (tmp_path / "evaluation_runs" / "eval-abc" / "summary.json").is_file()

    def test_case_results_json_exists(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path)
        assert (tmp_path / "evaluation_runs" / "eval-abc" / "case_results.json").is_file()

    def test_report_md_exists_by_default(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path)
        assert (tmp_path / "evaluation_runs" / "eval-abc" / "report.md").is_file()

    def test_report_md_absent_when_disabled(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path, generate_report=False)
        assert not (tmp_path / "evaluation_runs" / "eval-abc" / "report.md").exists()

    def test_returns_report_bundle(self, tmp_path):
        run = _make_evaluation_run_result()
        result = write_evaluation_run(run, tmp_path)
        assert isinstance(result, ReportBundle)

    def test_bundle_run_id_matches(self, tmp_path):
        run = _make_evaluation_run_result("my-run-id")
        bundle = write_evaluation_run(run, tmp_path)
        assert bundle.metadata.run_id == "my-run-id"

    def test_bundle_kind_is_evaluation_run(self, tmp_path):
        run = _make_evaluation_run_result()
        bundle = write_evaluation_run(run, tmp_path)
        assert bundle.metadata.kind == "evaluation_run"

    def test_artifact_files_with_report(self, tmp_path):
        run = _make_evaluation_run_result()
        bundle = write_evaluation_run(run, tmp_path, generate_report=True)
        assert "summary.json" in bundle.metadata.artifact_files
        assert "case_results.json" in bundle.metadata.artifact_files
        assert "report.md" in bundle.metadata.artifact_files

    def test_artifact_files_without_report(self, tmp_path):
        run = _make_evaluation_run_result()
        bundle = write_evaluation_run(run, tmp_path, generate_report=False)
        assert "summary.json" in bundle.metadata.artifact_files
        assert "case_results.json" in bundle.metadata.artifact_files
        assert "report.md" not in bundle.metadata.artifact_files

    def test_report_path_is_none_without_report(self, tmp_path):
        run = _make_evaluation_run_result()
        bundle = write_evaluation_run(run, tmp_path, generate_report=False)
        assert bundle.report_path is None

    def test_report_path_is_set_with_report(self, tmp_path):
        run = _make_evaluation_run_result("eval-xyz")
        bundle = write_evaluation_run(run, tmp_path, generate_report=True)
        assert bundle.report_path is not None
        assert "eval-xyz" in bundle.report_path
        assert bundle.report_path.endswith("report.md")

    def test_summary_json_is_valid_json(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path)
        content = (tmp_path / "evaluation_runs" / "eval-abc" / "summary.json").read_text()
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_summary_run_id_preserved_in_json(self, tmp_path):
        run = _make_evaluation_run_result("eval-abc")
        write_evaluation_run(run, tmp_path)
        content = json.loads(
            (tmp_path / "evaluation_runs" / "eval-abc" / "summary.json").read_text()
        )
        assert content["run_id"] == "eval-abc"

    def test_case_results_json_is_a_list(self, tmp_path):
        run = _make_evaluation_run_result()
        write_evaluation_run(run, tmp_path)
        content = json.loads(
            (tmp_path / "evaluation_runs" / run.summary.run_id / "case_results.json").read_text()
        )
        assert isinstance(content, list)

    def test_case_results_length_matches_run(self, tmp_path):
        run = _make_evaluation_run_result()
        write_evaluation_run(run, tmp_path)
        content = json.loads(
            (tmp_path / "evaluation_runs" / run.summary.run_id / "case_results.json").read_text()
        )
        assert len(content) == len(run.results)

    def test_summary_json_content_is_deterministic(self, tmp_path):
        run = _make_evaluation_run_result("eval-det")
        write_evaluation_run(run, tmp_path)
        first = (tmp_path / "evaluation_runs" / "eval-det" / "summary.json").read_text()

        tmp2 = tmp_path / "second"
        write_evaluation_run(run, tmp2)
        second = (tmp2 / "evaluation_runs" / "eval-det" / "summary.json").read_text()
        assert json.loads(first) == json.loads(second)

    def test_artifact_write_error_on_bad_path(self, tmp_path):
        """Block the output by placing a file where the directory should be."""
        run = _make_evaluation_run_result("eval-abc")
        blocker = tmp_path / "evaluation_runs"
        blocker.write_text("not a directory")
        with pytest.raises(ArtifactWriteError):
            write_evaluation_run(run, tmp_path)


# ── write_safety_run ──────────────────────────────────────────────────────────


class TestWriteSafetyRun:
    def test_creates_expected_directory(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        assert (tmp_path / "safety_runs" / "s-001").is_dir()

    def test_summary_json_exists(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        assert (tmp_path / "safety_runs" / "s-001" / "summary.json").is_file()

    def test_summary_total_is_correct(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        content = json.loads(
            (tmp_path / "safety_runs" / "s-001" / "summary.json").read_text()
        )
        assert content["total"] == 2

    def test_summary_passed_is_correct(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        content = json.loads(
            (tmp_path / "safety_runs" / "s-001" / "summary.json").read_text()
        )
        assert content["passed"] == 2

    def test_case_results_json_exists(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        assert (tmp_path / "safety_runs" / "s-001" / "case_results.json").is_file()

    def test_case_results_length_matches(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        content = json.loads(
            (tmp_path / "safety_runs" / "s-001" / "case_results.json").read_text()
        )
        assert len(content) == 2

    def test_case_result_status_is_string(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        content = json.loads(
            (tmp_path / "safety_runs" / "s-001" / "case_results.json").read_text()
        )
        for case in content:
            assert isinstance(case["actual_status"], str)
            assert isinstance(case["expected_status"], str)

    def test_suite_id_used_as_directory_name(self, tmp_path):
        results, summary = _make_safety_suite()
        bundle = write_safety_run(results, summary, tmp_path, suite_id="my-suite")
        assert (tmp_path / "safety_runs" / "my-suite").is_dir()
        assert bundle.metadata.run_id == "my-suite"

    def test_suite_id_generated_when_not_provided(self, tmp_path):
        results, summary = _make_safety_suite()
        bundle = write_safety_run(results, summary, tmp_path)
        assert bundle.metadata.run_id != ""
        assert (tmp_path / "safety_runs" / bundle.metadata.run_id).is_dir()

    def test_report_md_written_by_default(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        assert (tmp_path / "safety_runs" / "s-001" / "report.md").is_file()

    def test_bundle_kind_is_safety_run(self, tmp_path):
        results, summary = _make_safety_suite()
        bundle = write_safety_run(results, summary, tmp_path, suite_id="s-001")
        assert bundle.metadata.kind == "safety_run"

    def test_nested_assessment_serialized_in_case_results(self, tmp_path):
        results, summary = _make_safety_suite()
        write_safety_run(results, summary, tmp_path, suite_id="s-001")
        content = json.loads(
            (tmp_path / "safety_runs" / "s-001" / "case_results.json").read_text()
        )
        for case in content:
            assert "assessment" in case
            assert "status" in case["assessment"]


# ── write_comparison_run ──────────────────────────────────────────────────────


class TestWriteComparisonRun:
    def test_creates_expected_directory(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        assert (tmp_path / "comparison_runs" / "cmp-001").is_dir()

    def test_summary_json_exists(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        assert (tmp_path / "comparison_runs" / "cmp-001" / "summary.json").is_file()

    def test_summary_total_cases_correct(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        content = json.loads(
            (tmp_path / "comparison_runs" / "cmp-001" / "summary.json").read_text()
        )
        assert content["total_cases"] == 2

    def test_summary_improved_case_ids_correct(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        content = json.loads(
            (tmp_path / "comparison_runs" / "cmp-001" / "summary.json").read_text()
        )
        assert content["improved_case_ids"] == ["cmp-001"]

    def test_case_results_json_exists(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        assert (tmp_path / "comparison_runs" / "cmp-001" / "case_results.json").is_file()

    def test_case_results_length_matches(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        content = json.loads(
            (tmp_path / "comparison_runs" / "cmp-001" / "case_results.json").read_text()
        )
        assert len(content) == 2

    def test_verdict_is_string_in_case_results(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        content = json.loads(
            (tmp_path / "comparison_runs" / "cmp-001" / "case_results.json").read_text()
        )
        for case in content:
            assert isinstance(case["verdict"], str)

    def test_run_id_used_as_directory_name(self, tmp_path):
        run = _make_comparison_run_result()
        bundle = write_comparison_run(run, tmp_path, run_id="my-cmp-run")
        assert (tmp_path / "comparison_runs" / "my-cmp-run").is_dir()
        assert bundle.metadata.run_id == "my-cmp-run"

    def test_run_id_generated_when_not_provided(self, tmp_path):
        run = _make_comparison_run_result()
        bundle = write_comparison_run(run, tmp_path)
        assert bundle.metadata.run_id != ""
        assert (tmp_path / "comparison_runs" / bundle.metadata.run_id).is_dir()

    def test_report_md_written_by_default(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        assert (tmp_path / "comparison_runs" / "cmp-001" / "report.md").is_file()

    def test_bundle_kind_is_comparison_run(self, tmp_path):
        run = _make_comparison_run_result()
        bundle = write_comparison_run(run, tmp_path, run_id="cmp-001")
        assert bundle.metadata.kind == "comparison_run"

    def test_artifact_files_with_report(self, tmp_path):
        run = _make_comparison_run_result()
        bundle = write_comparison_run(run, tmp_path, run_id="cmp-001", generate_report=True)
        assert "summary.json" in bundle.metadata.artifact_files
        assert "case_results.json" in bundle.metadata.artifact_files
        assert "report.md" in bundle.metadata.artifact_files

    def test_score_delta_preserved_in_case_results(self, tmp_path):
        run = _make_comparison_run_result()
        write_comparison_run(run, tmp_path, run_id="cmp-001")
        content = json.loads(
            (tmp_path / "comparison_runs" / "cmp-001" / "case_results.json").read_text()
        )
        first_case = next(c for c in content if c["case_id"] == "cmp-001")
        assert abs(first_case["score_delta"] - 0.25) < 1e-6


# ── Structural / isolation ─────────────────────────────────────────────────────


class TestArtifactWriterStructural:
    def test_artifact_writer_does_not_import_boto3(self):
        import app.evaluation.artifact_writer as mod
        imported_names = set(vars(mod).keys())
        assert "boto3" not in imported_names

    def test_artifact_writer_does_not_import_bedrock_client(self):
        import app.evaluation.artifact_writer as mod
        imported_names = set(vars(mod).keys())
        assert "bedrock_service" not in imported_names
        assert "kb_service" not in imported_names

    def test_artifact_writer_does_not_import_cloudwatch(self):
        import app.evaluation.artifact_writer as mod
        imported_names = set(vars(mod).keys())
        assert "cloudwatch_service" not in imported_names
        assert "cloudwatch_metrics_service" not in imported_names
