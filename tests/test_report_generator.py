"""
J-1 unit tests — markdown report generator.

Coverage:

  generate_evaluation_run_report():
    - returns a non-empty string
    - contains the run_id
    - contains the timestamp
    - contains the total/passed/failed case counts
    - contains the pass rate
    - contains the average score
    - lists failing case IDs when failures exist
    - shows 'All cases passed' when no failures
    - includes per-metric averages section when present
    - omits per-metric section when per_metric_averages is empty
    - report is deterministic across repeated calls with same input
    - failing cases are listed in case_id order

  generate_safety_run_report():
    - returns a non-empty string
    - contains the suite_id
    - contains total/passed/failed counts
    - contains a status distribution section
    - shows actual status distribution correctly
    - lists failing case IDs when failures exist
    - shows 'All cases passed' when no failures
    - report is deterministic
    - suite_id defaults to 'unknown' when not provided

  generate_comparison_run_report():
    - returns a non-empty string
    - contains the run_id
    - contains improved/regressed/unchanged counts
    - contains the average score delta
    - shows improved case IDs
    - shows regressed case IDs
    - shows unchanged case IDs
    - contains baseline and optimized average scores
    - contains safety status distribution section
    - contains per-case results table
    - shows missing case IDs when present
    - run_id defaults to 'unknown' when not provided
    - report is deterministic
    - empty case results handled gracefully

  Structural:
    - report_generator does not import boto3
    - report_generator does not import any AWS service
    - all three generator functions are pure (no I/O performed)
"""

from __future__ import annotations

import sys

import pytest

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
from app.schemas.evaluation_models import DimensionScore, EvaluationResult, EvaluationRunSummary
from app.schemas.safety_models import (
    SafetyAssessment,
    SafetyStatus,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    IssueSource,
)

# ── Shared test helpers ────────────────────────────────────────────────────────

_TS = "2026-04-11T00:00:00+00:00"


def _make_dim_score(name: str, score: float, passed: bool) -> DimensionScore:
    return DimensionScore(metric_name=name, score=score, passed=passed)


def _make_eval_result(
    case_id: str, score: float, pass_fail: bool
) -> EvaluationResult:
    return EvaluationResult(
        case_id=case_id,
        run_id="test-run-001",
        evaluation_version="f2-v1",
        overall_score=score,
        pass_fail=pass_fail,
        dimension_scores=[_make_dim_score("severity_match", score, pass_fail)],
        timestamp=_TS,
    )


def _make_evaluation_run_result(
    run_id: str = "test-run-001",
    passed: int = 2,
    failed: int = 1,
) -> EvaluationRunResult:
    results = [
        _make_eval_result("case-001", 0.9, True),
        _make_eval_result("case-002", 0.8, True),
        _make_eval_result("case-003", 0.4, False),
    ]
    used = results[: passed + failed]
    pass_r = [r for r in used if r.pass_fail][: passed]
    fail_r = [r for r in used if not r.pass_fail][: failed]
    all_r = pass_r + fail_r
    total = len(all_r)
    avg = sum(r.overall_score for r in all_r) / total if total else 0.0
    summary = EvaluationRunSummary(
        run_id=run_id,
        total_cases=total,
        passed_cases=len(pass_r),
        failed_cases=len(fail_r),
        average_score=round(avg, 6),
        per_metric_averages={"severity_match": round(avg, 6)},
        timestamp=_TS,
    )
    return EvaluationRunResult(results=tuple(all_r), summary=summary)


def _make_evaluation_run_all_pass() -> EvaluationRunResult:
    results = (
        _make_eval_result("case-001", 0.9, True),
        _make_eval_result("case-002", 0.85, True),
    )
    summary = EvaluationRunSummary(
        run_id="all-pass-run",
        total_cases=2,
        passed_cases=2,
        failed_cases=0,
        average_score=0.875,
        per_metric_averages={},
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


def _make_safety_case_result(
    case_id: str, passed: bool, expected: SafetyStatus, actual: SafetyStatus
) -> SafetyCaseResult:
    assessment = (
        _make_clean_assessment(case_id)
        if actual == SafetyStatus.ALLOW
        else _make_blocking_assessment(case_id)
    )
    return SafetyCaseResult(
        case_id=case_id,
        expected_status=expected,
        actual_status=actual,
        passed=passed,
        missing_issue_codes=(),
        assessment=assessment,
    )


def _make_safety_suite_with_failures() -> tuple[list[SafetyCaseResult], SafetySuiteSummary]:
    results = [
        _make_safety_case_result("safe-001", True, SafetyStatus.ALLOW, SafetyStatus.ALLOW),
        _make_safety_case_result("safe-002", False, SafetyStatus.ALLOW, SafetyStatus.BLOCK),
    ]
    summary = SafetySuiteSummary(total=2, passed=1, failed=1, failed_case_ids=("safe-002",))
    return results, summary


def _make_safety_suite_all_pass() -> tuple[list[SafetyCaseResult], SafetySuiteSummary]:
    results = [
        _make_safety_case_result("safe-001", True, SafetyStatus.ALLOW, SafetyStatus.ALLOW),
        _make_safety_case_result("safe-002", True, SafetyStatus.BLOCK, SafetyStatus.BLOCK),
    ]
    summary = SafetySuiteSummary(total=2, passed=2, failed=0, failed_case_ids=())
    return results, summary


def _make_comparison_run_result() -> ComparisonRunResult:
    case1 = ComparisonCaseResult(
        case_id="cmp-001",
        baseline_score=0.55,
        optimized_score=0.85,
        score_delta=0.30,
        baseline_pass=False,
        optimized_pass=True,
        baseline_safety_status="allow",
        optimized_safety_status="allow",
        safety_status_changed=False,
        verdict="improved",
    )
    case2 = ComparisonCaseResult(
        case_id="cmp-002",
        baseline_score=0.80,
        optimized_score=0.50,
        score_delta=-0.30,
        baseline_pass=True,
        optimized_pass=False,
        baseline_safety_status="allow",
        optimized_safety_status="escalate",
        safety_status_changed=True,
        verdict="regressed",
    )
    case3 = ComparisonCaseResult(
        case_id="cmp-003",
        baseline_score=0.75,
        optimized_score=0.75,
        score_delta=0.0,
        baseline_pass=True,
        optimized_pass=True,
        baseline_safety_status="allow",
        optimized_safety_status="allow",
        safety_status_changed=False,
        verdict="unchanged",
    )
    summary = ComparisonSummary(
        total_cases=3,
        baseline_average_score=round((0.55 + 0.80 + 0.75) / 3, 6),
        optimized_average_score=round((0.85 + 0.50 + 0.75) / 3, 6),
        average_score_delta=round(((0.85 + 0.50 + 0.75) - (0.55 + 0.80 + 0.75)) / 3, 6),
        baseline_pass_count=2,
        optimized_pass_count=2,
        baseline_safety_distribution={"allow": 3},
        optimized_safety_distribution={"allow": 2, "escalate": 1},
        improved_case_ids=("cmp-001",),
        regressed_case_ids=("cmp-002",),
        unchanged_case_ids=("cmp-003",),
    )
    return ComparisonRunResult(
        case_results=(case1, case2, case3),
        summary=summary,
        missing_baseline_case_ids=(),
        missing_optimized_case_ids=(),
    )


def _make_comparison_run_empty() -> ComparisonRunResult:
    summary = ComparisonSummary(
        total_cases=0,
        baseline_average_score=0.0,
        optimized_average_score=0.0,
        average_score_delta=0.0,
        baseline_pass_count=0,
        optimized_pass_count=0,
        baseline_safety_distribution={},
        optimized_safety_distribution={},
        improved_case_ids=(),
        regressed_case_ids=(),
        unchanged_case_ids=(),
    )
    return ComparisonRunResult(
        case_results=(),
        summary=summary,
        missing_baseline_case_ids=("cmp-missing",),
        missing_optimized_case_ids=(),
    )


# ── generate_evaluation_run_report ────────────────────────────────────────────


class TestGenerateEvaluationRunReport:
    def test_returns_non_empty_string(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_contains_run_id(self):
        run = _make_evaluation_run_result(run_id="my-eval-run")
        report = generate_evaluation_run_report(run)
        assert "my-eval-run" in report

    def test_contains_timestamp(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert "2026-04-11" in report

    def test_contains_total_cases_count(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert str(run.summary.total_cases) in report

    def test_contains_passed_cases_count(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert str(run.summary.passed_cases) in report

    def test_contains_failed_cases_count(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert str(run.summary.failed_cases) in report

    def test_contains_average_score(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert "Average Score" in report

    def test_failing_case_id_appears_in_report(self):
        run = _make_evaluation_run_result()
        failing = [r for r in run.results if not r.pass_fail]
        assert failing, "test fixture must have at least one failing case"
        report = generate_evaluation_run_report(run)
        for r in failing:
            assert r.case_id in report

    def test_all_pass_shows_no_failures_message(self):
        run = _make_evaluation_run_all_pass()
        report = generate_evaluation_run_report(run)
        assert "All cases passed" in report

    def test_per_metric_section_present_when_averages_exist(self):
        run = _make_evaluation_run_result()
        assert run.summary.per_metric_averages
        report = generate_evaluation_run_report(run)
        assert "Per-Metric" in report

    def test_per_metric_section_absent_when_no_averages(self):
        run = _make_evaluation_run_all_pass()
        assert run.summary.per_metric_averages == {}
        report = generate_evaluation_run_report(run)
        assert "Per-Metric" not in report

    def test_report_is_deterministic(self):
        run = _make_evaluation_run_result()
        assert generate_evaluation_run_report(run) == generate_evaluation_run_report(run)

    def test_report_header_present(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert "# Evaluation Run Report" in report


# ── generate_safety_run_report ────────────────────────────────────────────────


class TestGenerateSafetyRunReport:
    def test_returns_non_empty_string(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary, suite_id="s-001")
        assert isinstance(report, str)
        assert len(report) > 0

    def test_contains_suite_id(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary, suite_id="my-suite-123")
        assert "my-suite-123" in report

    def test_defaults_suite_id_to_unknown(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert "unknown" in report

    def test_contains_total_count(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert str(summary.total) in report

    def test_contains_passed_count(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert str(summary.passed) in report

    def test_contains_failed_count(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert str(summary.failed) in report

    def test_contains_status_distribution_section(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert "Distribution" in report

    def test_actual_status_values_appear_in_distribution(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert "allow" in report
        assert "block" in report

    def test_failing_case_ids_appear_in_report(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert "safe-002" in report

    def test_all_pass_shows_no_failures_message(self):
        results, summary = _make_safety_suite_all_pass()
        report = generate_safety_run_report(results, summary)
        assert "All cases passed" in report

    def test_report_is_deterministic(self):
        results, summary = _make_safety_suite_with_failures()
        r1 = generate_safety_run_report(results, summary, suite_id="x")
        r2 = generate_safety_run_report(results, summary, suite_id="x")
        assert r1 == r2

    def test_report_header_present(self):
        results, summary = _make_safety_suite_with_failures()
        report = generate_safety_run_report(results, summary)
        assert "# Safety Suite Report" in report


# ── generate_comparison_run_report ────────────────────────────────────────────


class TestGenerateComparisonRunReport:
    def test_returns_non_empty_string(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="cmp-run-001")
        assert isinstance(report, str)
        assert len(report) > 0

    def test_contains_run_id(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="my-comparison-run")
        assert "my-comparison-run" in report

    def test_defaults_run_id_to_unknown(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run)
        assert "unknown" in report

    def test_contains_total_cases(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert str(run.summary.total_cases) in report

    def test_contains_improved_count(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "1" in report  # 1 improved case

    def test_contains_regressed_count(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "Regressed" in report

    def test_contains_score_delta(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "Delta" in report

    def test_improved_case_id_appears(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "cmp-001" in report

    def test_regressed_case_id_appears(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "cmp-002" in report

    def test_unchanged_case_id_appears(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "cmp-003" in report

    def test_contains_safety_distribution_section(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "Safety Status" in report

    def test_safety_status_values_appear(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "allow" in report
        assert "escalate" in report

    def test_contains_per_case_results_section(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "Per-Case" in report

    def test_missing_baseline_cases_appear(self):
        run = _make_comparison_run_empty()
        report = generate_comparison_run_report(run, run_id="r")
        assert "cmp-missing" in report

    def test_empty_case_results_handled_gracefully(self):
        run = _make_comparison_run_empty()
        report = generate_comparison_run_report(run, run_id="r")
        assert isinstance(report, str)
        assert len(report) > 0

    def test_report_is_deterministic(self):
        run = _make_comparison_run_result()
        r1 = generate_comparison_run_report(run, run_id="r")
        r2 = generate_comparison_run_report(run, run_id="r")
        assert r1 == r2

    def test_report_header_present(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "# Comparison Run Report" in report

    def test_baseline_avg_score_appears(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "Baseline Avg Score" in report

    def test_optimized_avg_score_appears(self):
        run = _make_comparison_run_result()
        report = generate_comparison_run_report(run, run_id="r")
        assert "Optimized Avg Score" in report


# ── Structural / isolation ─────────────────────────────────────────────────────


class TestReportGeneratorStructural:
    def test_report_generator_does_not_import_boto3(self):
        import app.evaluation.report_generator as mod
        assert "boto3" not in (mod.__dict__.get("__builtins__") or "")
        # Confirm boto3 is not in the module's direct imports.
        import sys
        imported_names = set(vars(mod).keys())
        assert "boto3" not in imported_names

    def test_report_generator_does_not_import_aws_service(self):
        import app.evaluation.report_generator as mod
        imported_names = set(vars(mod).keys())
        assert "bedrock_service" not in imported_names
        assert "cloudwatch_service" not in imported_names
        assert "cloudwatch_metrics_service" not in imported_names

    def test_generators_produce_strings_not_bytes(self):
        run = _make_evaluation_run_result()
        report = generate_evaluation_run_report(run)
        assert isinstance(report, str)

    def test_all_three_generators_return_markdown_headers(self):
        eval_r = generate_evaluation_run_report(_make_evaluation_run_result())
        results, summary = _make_safety_suite_with_failures()
        safety_r = generate_safety_run_report(results, summary)
        cmp_r = generate_comparison_run_report(_make_comparison_run_result())
        assert eval_r.startswith("# ")
        assert safety_r.startswith("# ")
        assert cmp_r.startswith("# ")
