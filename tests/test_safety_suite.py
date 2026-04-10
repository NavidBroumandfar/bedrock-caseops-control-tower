"""
H-2 unit tests — adversarial and edge-case safety evaluation suite.

Coverage:

  Fixture loading:
    - load_safety_fixture loads a single JSON fixture correctly
    - all 10 adversarial fixture files are present and loadable
    - fixture fields are correctly typed after loading
    - expected_status values are valid SafetyStatus members
    - expected_issue_codes values are valid SafetyIssueCode members
    - missing _case_id raises ValueError
    - missing _evaluation_path raises ValueError
    - missing _expected_status raises ValueError
    - invalid _expected_status value raises ValueError from enum
    - load_safety_suite loads all fixtures in stable alphabetical order
    - load_safety_suite returns 10 cases for the default suite directory
    - repeated load_safety_suite calls return fixtures in the same order

  Adversarial case evaluations:
    - schema_failure_raw → status BLOCK, code schema_or_contract_failure
    - unsupported_claims_block → status BLOCK, code unsupported_claims_present
    - missing_citations_block → status BLOCK, code missing_citations_when_required
    - low_confidence_escalate → status ESCALATE, code low_confidence_output
    - empty_retrieval_warn → status WARN, code empty_or_weak_retrieval
    - escalation_required_escalate → status ESCALATE, code escalation_policy_triggered
    - guardrail_intervention_block → status BLOCK, code guardrail_intervention
    - guardrail_non_intervention_allow → status ALLOW, no issues
    - combined_block_overrides_escalate → status BLOCK (blocking wins over escalation)
    - clean_allow_case → status ALLOW, no issues

  SafetyCaseResult fields:
    - passed=True when actual_status == expected_status and no missing_issue_codes
    - passed=False when actual_status != expected_status
    - passed=False when expected issue code is absent from assessment
    - missing_issue_codes tuple is empty when all expected codes present
    - missing_issue_codes contains absent codes when they are missing
    - assessment field is a valid SafetyAssessment on each result
    - case_id on result matches the fixture case_id

  Suite runner batch execution:
    - run_safety_suite returns a list of SafetyCaseResult and SafetySuiteSummary
    - results list length equals number of fixture files loaded
    - all 10 adversarial cases pass (passed=True on each)
    - SafetySuiteSummary.total == 10
    - SafetySuiteSummary.passed == 10
    - SafetySuiteSummary.failed == 0
    - SafetySuiteSummary.failed_case_ids is empty when all pass
    - summary reflects a failure correctly when a case fails (using a custom bad fixture)
    - case order in results matches stable alphabetical fixture load order
    - batch run is deterministic (two runs on same dir return same statuses)

  Structural / isolation constraints:
    - safety_suite module does not import boto3
    - safety_suite module does not import pipeline_workflow
    - safety_suite module does not import cli
    - safety_suite module does not import bedrock_service or kb_service
    - evaluate_case routes to evaluate_safety_from_raw for 'raw' path
    - evaluate_case routes to evaluate_safety for 'typed' path
    - evaluate_case routes to guardrail_result_to_assessment for 'guardrail' path
    - evaluate_case raises ValueError for unknown evaluation_path
    - evaluate_case with 'guardrail' path raises ValueError when _guardrail_result is None
    - SafetyCaseFixture is a frozen dataclass
    - SafetyCaseResult is a frozen dataclass
    - SafetySuiteSummary is a frozen dataclass
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.safety_suite import (
    DEFAULT_SUITE_DIR,
    SafetyCaseFixture,
    SafetyCaseResult,
    SafetySuiteSummary,
    evaluate_case,
    load_safety_fixture,
    load_safety_suite,
    run_safety_suite,
)
from app.schemas.safety_models import SafetyIssueCode, SafetyStatus


# ── Helpers ────────────────────────────────────────────────────────────────────


def _fixture_path(case_id_prefix: str) -> Path:
    """Return the path to the fixture file whose name starts with case_id_prefix."""
    matches = sorted(DEFAULT_SUITE_DIR.glob(f"{case_id_prefix}_*.json"))
    assert matches, f"No fixture file found with prefix '{case_id_prefix}' in {DEFAULT_SUITE_DIR}"
    return matches[0]


def _load_by_case_id(case_id: str) -> SafetyCaseFixture:
    """Load a fixture by its _case_id value from the default suite directory."""
    all_fixtures = load_safety_suite()
    for f in all_fixtures:
        if f.case_id == case_id:
            return f
    raise KeyError(f"No fixture with case_id={case_id!r} found in suite.")


# ── Fixture loading ────────────────────────────────────────────────────────────


class TestFixtureLoading:
    def test_default_suite_dir_exists(self) -> None:
        assert DEFAULT_SUITE_DIR.is_dir()

    def test_suite_contains_ten_fixtures(self) -> None:
        fixtures = load_safety_suite()
        assert len(fixtures) == 10

    def test_load_single_fixture_returns_correct_type(self) -> None:
        path = _fixture_path("01")
        fixture = load_safety_fixture(path)
        assert isinstance(fixture, SafetyCaseFixture)

    def test_fixture_case_id_is_string(self) -> None:
        fixture = load_safety_fixture(_fixture_path("01"))
        assert isinstance(fixture.case_id, str)
        assert fixture.case_id

    def test_fixture_description_is_string(self) -> None:
        fixture = load_safety_fixture(_fixture_path("01"))
        assert isinstance(fixture.description, str)

    def test_fixture_expected_status_is_safety_status(self) -> None:
        for f in load_safety_suite():
            assert isinstance(f.expected_status, SafetyStatus), (
                f"Case {f.case_id}: expected_status is not a SafetyStatus"
            )

    def test_fixture_expected_issue_codes_are_valid(self) -> None:
        for f in load_safety_suite():
            for code in f.expected_issue_codes:
                assert isinstance(code, SafetyIssueCode), (
                    f"Case {f.case_id}: issue code {code!r} is not a SafetyIssueCode"
                )

    def test_fixture_evaluation_path_is_known(self) -> None:
        valid_paths = {"raw", "typed", "guardrail"}
        for f in load_safety_suite():
            assert f.evaluation_path in valid_paths, (
                f"Case {f.case_id}: unknown evaluation_path {f.evaluation_path!r}"
            )

    def test_load_suite_is_stable_order(self) -> None:
        first = [f.case_id for f in load_safety_suite()]
        second = [f.case_id for f in load_safety_suite()]
        assert first == second

    def test_load_suite_alphabetical_order(self) -> None:
        fixtures = load_safety_suite()
        ids = [f.case_id for f in fixtures]
        # Schema failure should come before clean_allow_case (01_ vs 10_)
        assert ids.index("schema_failure_raw") < ids.index("clean_allow_case")

    def test_missing_case_id_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"_evaluation_path": "raw", "_expected_status": "block"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="_case_id"):
            load_safety_fixture(bad)

    def test_missing_evaluation_path_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"_case_id": "x", "_expected_status": "block"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="_evaluation_path"):
            load_safety_fixture(bad)

    def test_missing_expected_status_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"_case_id": "x", "_evaluation_path": "raw"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="_expected_status"):
            load_safety_fixture(bad)

    def test_invalid_expected_status_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {"_case_id": "x", "_evaluation_path": "raw", "_expected_status": "invalid_value"}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_safety_fixture(bad)


# ── Individual adversarial case evaluations ────────────────────────────────────


class TestAdversarialCases:
    """One test method per adversarial case — evaluates through the suite runner."""

    @staticmethod
    def _eval(case_id: str) -> SafetyCaseResult:
        fixture = _load_by_case_id(case_id)
        return evaluate_case(fixture)

    # Case 01: schema failure
    def test_schema_failure_raw_blocks(self) -> None:
        result = self._eval("schema_failure_raw")
        assert result.actual_status == SafetyStatus.BLOCK

    def test_schema_failure_raw_has_correct_issue_code(self) -> None:
        result = self._eval("schema_failure_raw")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE in codes

    def test_schema_failure_raw_passes_suite_expectation(self) -> None:
        result = self._eval("schema_failure_raw")
        assert result.passed

    def test_schema_failure_raw_has_blocking_issue(self) -> None:
        result = self._eval("schema_failure_raw")
        assert result.assessment.has_blocking_issue

    # Case 02: unsupported claims
    def test_unsupported_claims_block_blocks(self) -> None:
        result = self._eval("unsupported_claims_block")
        assert result.actual_status == SafetyStatus.BLOCK

    def test_unsupported_claims_block_has_correct_issue_code(self) -> None:
        result = self._eval("unsupported_claims_block")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT in codes

    def test_unsupported_claims_block_issue_is_blocking(self) -> None:
        result = self._eval("unsupported_claims_block")
        blocking = [i for i in result.assessment.issues if i.blocking]
        assert any(
            i.issue_code == SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT
            for i in blocking
        )

    def test_unsupported_claims_block_passes_suite_expectation(self) -> None:
        result = self._eval("unsupported_claims_block")
        assert result.passed

    # Case 03: missing citations
    def test_missing_citations_block_blocks(self) -> None:
        result = self._eval("missing_citations_block")
        assert result.actual_status == SafetyStatus.BLOCK

    def test_missing_citations_block_has_correct_issue_code(self) -> None:
        result = self._eval("missing_citations_block")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.MISSING_CITATIONS_WHEN_REQUIRED in codes

    def test_missing_citations_block_passes_suite_expectation(self) -> None:
        result = self._eval("missing_citations_block")
        assert result.passed

    # Case 04: low confidence
    def test_low_confidence_escalates(self) -> None:
        result = self._eval("low_confidence_escalate")
        assert result.actual_status == SafetyStatus.ESCALATE

    def test_low_confidence_has_correct_issue_code(self) -> None:
        result = self._eval("low_confidence_escalate")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.LOW_CONFIDENCE_OUTPUT in codes

    def test_low_confidence_is_not_blocking(self) -> None:
        result = self._eval("low_confidence_escalate")
        assert not result.assessment.has_blocking_issue

    def test_low_confidence_passes_suite_expectation(self) -> None:
        result = self._eval("low_confidence_escalate")
        assert result.passed

    # Case 05: empty retrieval
    def test_empty_retrieval_warns(self) -> None:
        result = self._eval("empty_retrieval_warn")
        assert result.actual_status == SafetyStatus.WARN

    def test_empty_retrieval_has_correct_issue_code(self) -> None:
        result = self._eval("empty_retrieval_warn")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL in codes

    def test_empty_retrieval_is_not_blocking(self) -> None:
        result = self._eval("empty_retrieval_warn")
        assert not result.assessment.has_blocking_issue

    def test_empty_retrieval_passes_suite_expectation(self) -> None:
        result = self._eval("empty_retrieval_warn")
        assert result.passed

    # Case 06: escalation required
    def test_escalation_required_escalates(self) -> None:
        result = self._eval("escalation_required_escalate")
        assert result.actual_status == SafetyStatus.ESCALATE

    def test_escalation_required_has_correct_issue_code(self) -> None:
        result = self._eval("escalation_required_escalate")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.ESCALATION_POLICY_TRIGGERED in codes

    def test_escalation_required_is_not_blocking(self) -> None:
        result = self._eval("escalation_required_escalate")
        assert not result.assessment.has_blocking_issue

    def test_escalation_required_passes_suite_expectation(self) -> None:
        result = self._eval("escalation_required_escalate")
        assert result.passed

    # Case 07: guardrail intervention
    def test_guardrail_intervention_blocks(self) -> None:
        result = self._eval("guardrail_intervention_block")
        assert result.actual_status == SafetyStatus.BLOCK

    def test_guardrail_intervention_has_correct_issue_code(self) -> None:
        result = self._eval("guardrail_intervention_block")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.GUARDRAIL_INTERVENTION in codes

    def test_guardrail_intervention_has_blocking_issue(self) -> None:
        result = self._eval("guardrail_intervention_block")
        assert result.assessment.has_blocking_issue

    def test_guardrail_intervention_passes_suite_expectation(self) -> None:
        result = self._eval("guardrail_intervention_block")
        assert result.passed

    # Case 08: guardrail non-intervention
    def test_guardrail_non_intervention_allows(self) -> None:
        result = self._eval("guardrail_non_intervention_allow")
        assert result.actual_status == SafetyStatus.ALLOW

    def test_guardrail_non_intervention_has_no_issues(self) -> None:
        result = self._eval("guardrail_non_intervention_allow")
        assert result.assessment.issues == []

    def test_guardrail_non_intervention_has_no_blocking_issue(self) -> None:
        result = self._eval("guardrail_non_intervention_allow")
        assert not result.assessment.has_blocking_issue

    def test_guardrail_non_intervention_passes_suite_expectation(self) -> None:
        result = self._eval("guardrail_non_intervention_allow")
        assert result.passed

    # Case 09: combined (block overrides escalate)
    def test_combined_resolves_to_block(self) -> None:
        result = self._eval("combined_block_overrides_escalate")
        assert result.actual_status == SafetyStatus.BLOCK

    def test_combined_has_unsupported_claims_code(self) -> None:
        result = self._eval("combined_block_overrides_escalate")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.UNSUPPORTED_CLAIMS_PRESENT in codes

    def test_combined_has_escalation_code(self) -> None:
        result = self._eval("combined_block_overrides_escalate")
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.ESCALATION_POLICY_TRIGGERED in codes

    def test_combined_has_blocking_issue(self) -> None:
        result = self._eval("combined_block_overrides_escalate")
        assert result.assessment.has_blocking_issue

    def test_combined_passes_suite_expectation(self) -> None:
        result = self._eval("combined_block_overrides_escalate")
        assert result.passed

    # Case 10: clean allow
    def test_clean_allow_case_allows(self) -> None:
        result = self._eval("clean_allow_case")
        assert result.actual_status == SafetyStatus.ALLOW

    def test_clean_allow_case_has_no_issues(self) -> None:
        result = self._eval("clean_allow_case")
        assert result.assessment.issues == []

    def test_clean_allow_case_not_blocking(self) -> None:
        result = self._eval("clean_allow_case")
        assert not result.assessment.has_blocking_issue

    def test_clean_allow_case_not_escalated(self) -> None:
        result = self._eval("clean_allow_case")
        assert not result.assessment.requires_escalation

    def test_clean_allow_case_passes_suite_expectation(self) -> None:
        result = self._eval("clean_allow_case")
        assert result.passed


# ── SafetyCaseResult field contracts ──────────────────────────────────────────


class TestSafetyCaseResultFields:
    def test_result_case_id_matches_fixture(self) -> None:
        fixture = _load_by_case_id("clean_allow_case")
        result = evaluate_case(fixture)
        assert result.case_id == fixture.case_id

    def test_result_expected_status_matches_fixture(self) -> None:
        fixture = _load_by_case_id("clean_allow_case")
        result = evaluate_case(fixture)
        assert result.expected_status == fixture.expected_status

    def test_passed_true_when_status_matches_and_codes_present(self) -> None:
        result = evaluate_case(_load_by_case_id("clean_allow_case"))
        assert result.passed is True

    def test_passed_false_when_status_mismatch(self, tmp_path: Path) -> None:
        # Inject a fixture that expects BLOCK for a clean case — status won't match.
        bad_fixture_data = {
            "_description": "Deliberately wrong expectation",
            "_case_id": "wrong_expectation",
            "_expected_status": "block",
            "_expected_issue_codes": [],
            "_evaluation_path": "typed",
            "_retrieval_chunk_count": None,
            "_guardrail_result": None,
            "_document_id": None,
            "input": {
                "document_id": "doc-wrong",
                "source_filename": "test.txt",
                "source_type": "FDA",
                "severity": "Low",
                "category": "Regulatory",
                "summary": "No issues found.",
                "recommendations": ["Continue monitoring."],
                "citations": [
                    {
                        "source_id": "kb-src-001",
                        "source_label": "Source",
                        "excerpt": "Nothing unusual.",
                        "relevance_score": 0.7,
                    }
                ],
                "confidence_score": 0.90,
                "unsupported_claims": [],
                "escalation_required": False,
                "escalation_reason": None,
                "validated_by": "test-agent",
                "session_id": "sess-test",
                "timestamp": "2026-04-10T12:00:00+00:00",
            },
        }
        p = tmp_path / "wrong.json"
        p.write_text(json.dumps(bad_fixture_data), encoding="utf-8")
        fixture = load_safety_fixture(p)
        result = evaluate_case(fixture)
        assert result.passed is False

    def test_missing_issue_codes_empty_when_all_present(self) -> None:
        result = evaluate_case(_load_by_case_id("schema_failure_raw"))
        assert result.missing_issue_codes == ()

    def test_missing_issue_codes_contains_absent_code(self, tmp_path: Path) -> None:
        fixture_data = {
            "_description": "Expects a code that will not appear",
            "_case_id": "missing_code_test",
            "_expected_status": "allow",
            "_expected_issue_codes": ["guardrail_intervention"],
            "_evaluation_path": "typed",
            "_retrieval_chunk_count": None,
            "_guardrail_result": None,
            "_document_id": None,
            "input": {
                "document_id": "doc-missing-code",
                "source_filename": "test.txt",
                "source_type": "FDA",
                "severity": "Low",
                "category": "Regulatory",
                "summary": "Clean output.",
                "recommendations": ["Review."],
                "citations": [
                    {
                        "source_id": "kb-1",
                        "source_label": "Source",
                        "excerpt": "Text.",
                        "relevance_score": 0.8,
                    }
                ],
                "confidence_score": 0.90,
                "unsupported_claims": [],
                "escalation_required": False,
                "escalation_reason": None,
                "validated_by": "agent",
                "session_id": "sess",
                "timestamp": "2026-04-10T12:00:00+00:00",
            },
        }
        p = tmp_path / "missing_code.json"
        p.write_text(json.dumps(fixture_data), encoding="utf-8")
        fixture = load_safety_fixture(p)
        result = evaluate_case(fixture)
        assert SafetyIssueCode.GUARDRAIL_INTERVENTION in result.missing_issue_codes

    def test_assessment_is_safety_assessment_type(self) -> None:
        from app.schemas.safety_models import SafetyAssessment
        result = evaluate_case(_load_by_case_id("clean_allow_case"))
        assert isinstance(result.assessment, SafetyAssessment)


# ── Batch suite runner ─────────────────────────────────────────────────────────


class TestSuiteRunner:
    def test_run_safety_suite_returns_list_and_summary(self) -> None:
        results, summary = run_safety_suite()
        assert isinstance(results, list)
        assert isinstance(summary, SafetySuiteSummary)

    def test_results_length_equals_fixture_count(self) -> None:
        results, _ = run_safety_suite()
        assert len(results) == 10

    def test_all_cases_pass(self) -> None:
        results, _ = run_safety_suite()
        failing = [r.case_id for r in results if not r.passed]
        assert failing == [], f"Cases failed: {failing}"

    def test_summary_total_is_ten(self) -> None:
        _, summary = run_safety_suite()
        assert summary.total == 10

    def test_summary_passed_is_ten(self) -> None:
        _, summary = run_safety_suite()
        assert summary.passed == 10

    def test_summary_failed_is_zero(self) -> None:
        _, summary = run_safety_suite()
        assert summary.failed == 0

    def test_summary_failed_case_ids_is_empty(self) -> None:
        _, summary = run_safety_suite()
        assert summary.failed_case_ids == ()

    def test_summary_total_equals_passed_plus_failed(self) -> None:
        _, summary = run_safety_suite()
        assert summary.total == summary.passed + summary.failed

    def test_batch_run_is_deterministic(self) -> None:
        results_a, _ = run_safety_suite()
        results_b, _ = run_safety_suite()
        statuses_a = [r.actual_status for r in results_a]
        statuses_b = [r.actual_status for r in results_b]
        assert statuses_a == statuses_b

    def test_results_order_matches_fixture_load_order(self) -> None:
        fixtures = load_safety_suite()
        results, _ = run_safety_suite()
        fixture_ids = [f.case_id for f in fixtures]
        result_ids = [r.case_id for r in results]
        assert result_ids == fixture_ids

    def test_run_with_custom_suite_dir(self, tmp_path: Path) -> None:
        # Copy one fixture to tmp_path and run on that minimal suite.
        src = _fixture_path("10")
        dest = tmp_path / src.name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        results, summary = run_safety_suite(suite_dir=tmp_path)
        assert summary.total == 1
        assert summary.passed == 1

    def test_summary_reflects_failure_on_wrong_expectation(self, tmp_path: Path) -> None:
        bad_fixture = {
            "_description": "Wrong expectation",
            "_case_id": "wrong_expect",
            "_expected_status": "block",
            "_expected_issue_codes": [],
            "_evaluation_path": "typed",
            "_retrieval_chunk_count": None,
            "_guardrail_result": None,
            "_document_id": None,
            "input": {
                "document_id": "doc-w",
                "source_filename": "t.txt",
                "source_type": "FDA",
                "severity": "Low",
                "category": "Regulatory",
                "summary": "Clean.",
                "recommendations": ["Review."],
                "citations": [
                    {
                        "source_id": "kb-1",
                        "source_label": "S",
                        "excerpt": "T.",
                        "relevance_score": 0.8,
                    }
                ],
                "confidence_score": 0.9,
                "unsupported_claims": [],
                "escalation_required": False,
                "escalation_reason": None,
                "validated_by": "a",
                "session_id": "s",
                "timestamp": "2026-04-10T12:00:00+00:00",
            },
        }
        p = tmp_path / "wrong.json"
        p.write_text(json.dumps(bad_fixture), encoding="utf-8")
        results, summary = run_safety_suite(suite_dir=tmp_path)
        assert summary.failed == 1
        assert "wrong_expect" in summary.failed_case_ids


# ── evaluate_case routing and error handling ───────────────────────────────────


class TestEvaluateCaseRouting:
    def test_unknown_evaluation_path_raises(self) -> None:
        # Build a fixture with an unknown path directly.
        fixture = SafetyCaseFixture(
            case_id="bad_path",
            description="unknown path",
            evaluation_path="unknown_path",
            expected_status=SafetyStatus.BLOCK,
            expected_issue_codes=(),
            input_data=None,
        )
        with pytest.raises(ValueError, match="unknown evaluation_path"):
            evaluate_case(fixture)

    def test_guardrail_path_without_guardrail_result_raises(self) -> None:
        fixture = SafetyCaseFixture(
            case_id="no_gr",
            description="missing guardrail result",
            evaluation_path="guardrail",
            expected_status=SafetyStatus.BLOCK,
            expected_issue_codes=(),
            input_data=None,
            guardrail_result=None,
        )
        with pytest.raises(ValueError, match="_guardrail_result"):
            evaluate_case(fixture)

    def test_raw_path_calls_evaluate_safety_from_raw(self) -> None:
        result = evaluate_case(_load_by_case_id("schema_failure_raw"))
        # If the raw path worked, we get a blocking schema failure assessment.
        assert result.actual_status == SafetyStatus.BLOCK
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.SCHEMA_OR_CONTRACT_FAILURE in codes

    def test_typed_path_evaluates_via_safety_policy(self) -> None:
        result = evaluate_case(_load_by_case_id("clean_allow_case"))
        assert result.actual_status == SafetyStatus.ALLOW

    def test_guardrail_path_uses_guardrail_adapter(self) -> None:
        result = evaluate_case(_load_by_case_id("guardrail_intervention_block"))
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.GUARDRAIL_INTERVENTION in codes

    def test_retrieval_chunk_count_is_forwarded(self) -> None:
        # empty_retrieval_warn has retrieval_chunk_count=0 which triggers a warn.
        result = evaluate_case(_load_by_case_id("empty_retrieval_warn"))
        assert result.actual_status == SafetyStatus.WARN
        codes = {i.issue_code for i in result.assessment.issues}
        assert SafetyIssueCode.EMPTY_OR_WEAK_RETRIEVAL in codes


# ── Structural / isolation constraints ────────────────────────────────────────


class TestStructuralConstraints:
    def test_safety_suite_does_not_import_boto3(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "import boto3" not in source
        assert "from boto3" not in source

    def test_safety_suite_does_not_import_pipeline_workflow(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "pipeline_workflow" not in source

    def test_safety_suite_does_not_import_cli(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "from app.cli" not in source
        assert "import app.cli" not in source

    def test_safety_suite_does_not_import_bedrock_service(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "bedrock_service" not in source

    def test_safety_suite_does_not_import_kb_service(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "kb_service" not in source

    def test_safety_suite_fixture_is_frozen_dataclass(self) -> None:
        fixture = _load_by_case_id("clean_allow_case")
        with pytest.raises((AttributeError, TypeError)):
            fixture.case_id = "mutated"  # type: ignore[misc]

    def test_safety_case_result_is_frozen_dataclass(self) -> None:
        result = evaluate_case(_load_by_case_id("clean_allow_case"))
        with pytest.raises((AttributeError, TypeError)):
            result.case_id = "mutated"  # type: ignore[misc]

    def test_safety_suite_summary_is_frozen_dataclass(self) -> None:
        _, summary = run_safety_suite()
        with pytest.raises((AttributeError, TypeError)):
            summary.total = 999  # type: ignore[misc]

    def test_no_live_aws_calls(self) -> None:
        # Running the full suite should not require boto3 credentials.
        # If it imported any live AWS service, this test environment would fail.
        # The fact that run_safety_suite() succeeds without AWS env vars proves isolation.
        results, summary = run_safety_suite()
        assert summary.total == 10

    def test_safety_suite_imports_h0_evaluator(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "evaluate_safety" in source
        assert "safety_policy" in source

    def test_safety_suite_imports_h1_adapter(self) -> None:
        import app.evaluation.safety_suite as module_under_test
        source = Path(module_under_test.__file__).read_text(encoding="utf-8")
        assert "guardrails_adapter" in source
        assert "guardrail_result_to_assessment" in source
