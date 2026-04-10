"""
Tests for app/evaluation/guardrails_adapter.py — H-1 Guardrails → H-0 adapter.

Coverage:
  - Intervened result produces one blocking SafetyIssue
  - Non-intervened result produces empty issues list
  - Issue source is IssueSource.GUARDRAILS
  - Issue code is SafetyIssueCode.GUARDRAIL_INTERVENTION
  - Issue severity is SafetyIssueSeverity.ERROR on intervention
  - Issue blocking=True on intervention
  - Metadata preserved: guardrail_id, guardrail_version, source, action, finding_types
  - SafetyAssessment status=BLOCK on intervention
  - SafetyAssessment status=ALLOW on non-intervention
  - has_blocking_issue=True on intervention
  - requires_escalation=True on intervention
  - document_id preserved in assessment
  - notes preserved in assessment
  - Finding types appear in issue message and metadata
  - Deterministic: same input always produces same output
  - Adapter does not import AWS services or safety_policy
"""

import pytest

from app.evaluation.guardrails_adapter import (
    guardrail_result_to_assessment,
    guardrail_result_to_issues,
)
from app.schemas.guardrail_models import GuardrailAssessmentResult, GuardrailSource
from app.schemas.safety_models import (
    IssueSource,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_result(intervened: bool, **overrides) -> GuardrailAssessmentResult:
    data = {
        "guardrail_id": "gr-test",
        "guardrail_version": "1",
        "source": GuardrailSource.OUTPUT,
        "intervened": intervened,
        "action": "GUARDRAIL_INTERVENED" if intervened else "NONE",
        "blocked": intervened,
        "finding_types": ["HATE", "VIOLENCE"] if intervened else [],
    }
    data.update(overrides)
    return GuardrailAssessmentResult(**data)


# ── guardrail_result_to_issues — non-intervention ─────────────────────────────


class TestToIssuesNonIntervention:
    def test_returns_empty_list(self):
        result = _make_result(intervened=False)
        assert guardrail_result_to_issues(result) == []

    def test_returns_list_type(self):
        result = _make_result(intervened=False)
        issues = guardrail_result_to_issues(result)
        assert isinstance(issues, list)

    def test_no_findings_no_issues(self):
        result = _make_result(intervened=False, finding_types=[])
        assert guardrail_result_to_issues(result) == []

    def test_deterministic_empty(self):
        result = _make_result(intervened=False)
        assert guardrail_result_to_issues(result) == guardrail_result_to_issues(result)


# ── guardrail_result_to_issues — intervention ─────────────────────────────────


class TestToIssuesIntervention:
    def _issues(self, **kwargs):
        return guardrail_result_to_issues(_make_result(intervened=True, **kwargs))

    def test_returns_one_issue(self):
        assert len(self._issues()) == 1

    def test_issue_is_blocking(self):
        issue = self._issues()[0]
        assert issue.blocking is True

    def test_issue_source_is_guardrails(self):
        issue = self._issues()[0]
        assert issue.source == IssueSource.GUARDRAILS

    def test_issue_code_is_guardrail_intervention(self):
        issue = self._issues()[0]
        assert issue.issue_code == SafetyIssueCode.GUARDRAIL_INTERVENTION

    def test_issue_severity_is_error(self):
        issue = self._issues()[0]
        assert issue.severity == SafetyIssueSeverity.ERROR

    def test_issue_message_contains_guardrail_id(self):
        issue = self._issues()[0]
        assert "gr-test" in issue.message

    def test_issue_message_contains_version(self):
        issue = self._issues()[0]
        assert "1" in issue.message

    def test_issue_message_contains_source(self):
        issue = self._issues()[0]
        assert "output" in issue.message

    def test_finding_types_in_message_when_present(self):
        issues = guardrail_result_to_issues(
            _make_result(intervened=True, finding_types=["FINANCE", "PII_EMAIL"])
        )
        message = issues[0].message
        assert "FINANCE" in message
        assert "PII_EMAIL" in message

    def test_finding_types_in_metadata(self):
        issue = self._issues()[0]
        assert "finding_types" in issue.metadata
        assert "HATE" in issue.metadata["finding_types"]
        assert "VIOLENCE" in issue.metadata["finding_types"]

    def test_guardrail_id_in_metadata(self):
        issue = self._issues()[0]
        assert issue.metadata["guardrail_id"] == "gr-test"

    def test_guardrail_version_in_metadata(self):
        issue = self._issues()[0]
        assert issue.metadata["guardrail_version"] == "1"

    def test_source_in_metadata(self):
        issue = self._issues()[0]
        assert issue.metadata["source"] == "output"

    def test_action_in_metadata(self):
        issue = self._issues()[0]
        assert issue.metadata["action"] == "GUARDRAIL_INTERVENED"

    def test_empty_findings_no_findings_suffix_in_message(self):
        issues = guardrail_result_to_issues(
            _make_result(intervened=True, finding_types=[])
        )
        assert "findings:" not in issues[0].message

    def test_deterministic_same_issue_twice(self):
        result = _make_result(intervened=True)
        issues_a = guardrail_result_to_issues(result)
        issues_b = guardrail_result_to_issues(result)
        assert issues_a[0].model_dump() == issues_b[0].model_dump()


# ── guardrail_result_to_assessment — non-intervention ─────────────────────────


class TestToAssessmentNonIntervention:
    def _assessment(self, **kwargs):
        result = _make_result(intervened=False, **kwargs)
        return guardrail_result_to_assessment(result, document_id="doc-001")

    def test_status_is_allow(self):
        assert self._assessment().status == SafetyStatus.ALLOW

    def test_has_blocking_issue_false(self):
        assert self._assessment().has_blocking_issue is False

    def test_requires_escalation_false(self):
        assert self._assessment().requires_escalation is False

    def test_issues_empty(self):
        assert self._assessment().issues == []

    def test_document_id_preserved(self):
        assert self._assessment().document_id == "doc-001"

    def test_timestamp_present(self):
        ts = self._assessment().timestamp
        assert ts and len(ts) > 10

    def test_notes_none_by_default(self):
        assert self._assessment().notes is None

    def test_notes_preserved(self):
        result = _make_result(intervened=False)
        assessment = guardrail_result_to_assessment(
            result, document_id="doc-1", notes="all clear"
        )
        assert assessment.notes == "all clear"


# ── guardrail_result_to_assessment — intervention ─────────────────────────────


class TestToAssessmentIntervention:
    def _assessment(self, **kwargs):
        result = _make_result(intervened=True, **kwargs)
        return guardrail_result_to_assessment(result, document_id="doc-999")

    def test_status_is_block(self):
        assert self._assessment().status == SafetyStatus.BLOCK

    def test_has_blocking_issue_true(self):
        assert self._assessment().has_blocking_issue is True

    def test_requires_escalation_true(self):
        assert self._assessment().requires_escalation is True

    def test_issues_has_one_item(self):
        assert len(self._assessment().issues) == 1

    def test_document_id_preserved(self):
        assert self._assessment().document_id == "doc-999"

    def test_timestamp_present(self):
        ts = self._assessment().timestamp
        assert ts and len(ts) > 10

    def test_notes_preserved(self):
        result = _make_result(intervened=True)
        assessment = guardrail_result_to_assessment(
            result, document_id="doc-1", notes="guardrail triggered"
        )
        assert assessment.notes == "guardrail triggered"

    def test_issue_in_assessment_is_blocking(self):
        issue = self._assessment().issues[0]
        assert issue.blocking is True

    def test_issue_source_is_guardrails(self):
        issue = self._assessment().issues[0]
        assert issue.source == IssueSource.GUARDRAILS


# ── Integration: issue list → assessment consistency ─────────────────────────


class TestIssueAndAssessmentConsistency:
    def test_intervention_issues_match_assessment_issues(self):
        result = _make_result(intervened=True)
        issues = guardrail_result_to_issues(result)
        assessment = guardrail_result_to_assessment(result, document_id="doc-1")
        assert len(issues) == len(assessment.issues)
        assert issues[0].issue_code == assessment.issues[0].issue_code

    def test_non_intervention_issues_match_assessment_issues(self):
        result = _make_result(intervened=False)
        issues = guardrail_result_to_issues(result)
        assessment = guardrail_result_to_assessment(result, document_id="doc-1")
        assert issues == []
        assert assessment.issues == []

    def test_different_guardrail_ids_produce_different_messages(self):
        result_a = _make_result(intervened=True, guardrail_id="gr-aaa")
        result_b = _make_result(intervened=True, guardrail_id="gr-bbb")
        issues_a = guardrail_result_to_issues(result_a)
        issues_b = guardrail_result_to_issues(result_b)
        assert issues_a[0].message != issues_b[0].message
        assert "gr-aaa" in issues_a[0].message
        assert "gr-bbb" in issues_b[0].message

    def test_input_source_preserved_in_metadata(self):
        result = _make_result(intervened=True, source=GuardrailSource.INPUT)
        issues = guardrail_result_to_issues(result)
        assert issues[0].metadata["source"] == "input"

    def test_output_source_preserved_in_metadata(self):
        result = _make_result(intervened=True, source=GuardrailSource.OUTPUT)
        issues = guardrail_result_to_issues(result)
        assert issues[0].metadata["source"] == "output"


# ── Structural / separation ────────────────────────────────────────────────────


class TestAdapterStructural:
    def test_adapter_does_not_import_aws_services(self):
        """Check actual import statements — not docstring content."""
        import ast
        import inspect
        import app.evaluation.guardrails_adapter as mod

        tree = ast.parse(inspect.getsource(mod))
        import_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    import_names.add(node.module)
        for forbidden in ("boto3", "app.services.bedrock_service",
                          "app.services.kb_service", "app.services.s3_service"):
            assert forbidden not in import_names, f"Unexpected import: {forbidden!r}"

    def test_adapter_does_not_import_safety_policy(self):
        """Check actual import statements — not docstring content."""
        import ast
        import inspect
        import app.evaluation.guardrails_adapter as mod

        tree = ast.parse(inspect.getsource(mod))
        import_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    import_names.add(node.module)
        assert "app.evaluation.safety_policy" not in import_names

    def test_adapter_does_not_import_h2_modules(self):
        import ast
        import inspect
        import app.evaluation.guardrails_adapter as mod

        tree = ast.parse(inspect.getsource(mod))
        import_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    import_names.add(node.module)
        for forbidden in ("adversarial", "optimization", "cloudwatch_dashboard"):
            for name in import_names:
                assert forbidden not in name

    def test_adapter_imports_only_schema_modules(self):
        """Adapter's app.* imports must be restricted to schema modules."""
        import ast
        import inspect
        import app.evaluation.guardrails_adapter as mod

        tree = ast.parse(inspect.getsource(mod))
        allowed_app_imports = {
            "app.schemas.guardrail_models",
            "app.schemas.safety_models",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app."):
                    assert node.module in allowed_app_imports, (
                        f"Adapter imported unexpected app module: {node.module!r}"
                    )
