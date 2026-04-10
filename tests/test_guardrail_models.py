"""
Tests for app/schemas/guardrail_models.py — H-1 Guardrails schema contracts.

Coverage:
  - GuardrailSource enum values and string coercion
  - GuardrailAssessmentResult required fields validate correctly
  - GuardrailAssessmentResult optional fields have correct defaults
  - Field validators reject invalid inputs
  - Intervention vs non-intervention states
  - trace field attachment and omission
  - finding_types list behaviour
  - Round-trip serialisation
"""

import pytest
from pydantic import ValidationError

from app.schemas.guardrail_models import GuardrailAssessmentResult, GuardrailSource


# ── GuardrailSource ────────────────────────────────────────────────────────────


class TestGuardrailSource:
    def test_input_value(self):
        assert GuardrailSource.INPUT.value == "input"

    def test_output_value(self):
        assert GuardrailSource.OUTPUT.value == "output"

    def test_is_str_enum(self):
        assert isinstance(GuardrailSource.INPUT, str)

    def test_coerce_from_string(self):
        assert GuardrailSource("input") == GuardrailSource.INPUT
        assert GuardrailSource("output") == GuardrailSource.OUTPUT

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError):
            GuardrailSource("model")

    def test_enum_members(self):
        members = {m.value for m in GuardrailSource}
        assert members == {"input", "output"}


# ── GuardrailAssessmentResult — minimal valid construction ─────────────────────


class TestGuardrailAssessmentResultMinimal:
    def _minimal(self, **overrides) -> dict:
        base = {
            "guardrail_id": "gr-abc123",
            "guardrail_version": "1",
            "source": GuardrailSource.INPUT,
            "intervened": False,
        }
        base.update(overrides)
        return base

    def test_minimal_valid(self):
        result = GuardrailAssessmentResult(**self._minimal())
        assert result.guardrail_id == "gr-abc123"
        assert result.guardrail_version == "1"
        assert result.source == GuardrailSource.INPUT
        assert result.intervened is False

    def test_default_action_is_none(self):
        result = GuardrailAssessmentResult(**self._minimal())
        assert result.action is None

    def test_default_output_text_is_none(self):
        result = GuardrailAssessmentResult(**self._minimal())
        assert result.output_text is None

    def test_default_blocked_is_false(self):
        result = GuardrailAssessmentResult(**self._minimal())
        assert result.blocked is False

    def test_default_finding_types_empty(self):
        result = GuardrailAssessmentResult(**self._minimal())
        assert result.finding_types == []

    def test_default_trace_is_none(self):
        result = GuardrailAssessmentResult(**self._minimal())
        assert result.trace is None

    def test_source_output_accepted(self):
        result = GuardrailAssessmentResult(**self._minimal(source=GuardrailSource.OUTPUT))
        assert result.source == GuardrailSource.OUTPUT

    def test_source_coerced_from_string(self):
        result = GuardrailAssessmentResult(**self._minimal(source="output"))
        assert result.source == GuardrailSource.OUTPUT


# ── GuardrailAssessmentResult — intervention state ────────────────────────────


class TestGuardrailAssessmentResultIntervened:
    def _intervened(self, **overrides) -> GuardrailAssessmentResult:
        data = {
            "guardrail_id": "gr-safety",
            "guardrail_version": "2",
            "source": GuardrailSource.OUTPUT,
            "intervened": True,
            "action": "GUARDRAIL_INTERVENED",
            "output_text": "Content blocked by policy.",
            "blocked": True,
            "finding_types": ["HATE", "VIOLENCE"],
        }
        data.update(overrides)
        return GuardrailAssessmentResult(**data)

    def test_intervened_true(self):
        assert self._intervened().intervened is True

    def test_blocked_true(self):
        assert self._intervened().blocked is True

    def test_action_set(self):
        assert self._intervened().action == "GUARDRAIL_INTERVENED"

    def test_output_text_set(self):
        assert self._intervened().output_text == "Content blocked by policy."

    def test_finding_types_present(self):
        assert self._intervened().finding_types == ["HATE", "VIOLENCE"]

    def test_finding_types_empty_list_accepted(self):
        result = self._intervened(finding_types=[])
        assert result.finding_types == []

    def test_finding_types_single_item(self):
        result = self._intervened(finding_types=["TOPIC_blocked"])
        assert result.finding_types == ["TOPIC_blocked"]


# ── GuardrailAssessmentResult — trace field ────────────────────────────────────


class TestGuardrailAssessmentResultTrace:
    def test_trace_none_by_default(self):
        result = GuardrailAssessmentResult(
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            intervened=False,
        )
        assert result.trace is None

    def test_trace_dict_attached(self):
        trace_payload = {"topicPolicy": {"topics": [{"name": "Finance", "action": "BLOCKED"}]}}
        result = GuardrailAssessmentResult(
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            intervened=True,
            trace=trace_payload,
        )
        assert result.trace == trace_payload

    def test_trace_empty_dict_accepted(self):
        result = GuardrailAssessmentResult(
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            intervened=False,
            trace={},
        )
        assert result.trace == {}


# ── GuardrailAssessmentResult — validators ────────────────────────────────────


class TestGuardrailAssessmentResultValidators:
    def test_empty_guardrail_id_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            GuardrailAssessmentResult(
                guardrail_id="   ",
                guardrail_version="1",
                source=GuardrailSource.INPUT,
                intervened=False,
            )
        assert "guardrail_id" in str(exc_info.value)

    def test_empty_guardrail_version_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            GuardrailAssessmentResult(
                guardrail_id="gr-1",
                guardrail_version="  ",
                source=GuardrailSource.INPUT,
                intervened=False,
            )
        assert "guardrail_version" in str(exc_info.value)

    def test_missing_guardrail_id_raises(self):
        with pytest.raises(ValidationError):
            GuardrailAssessmentResult(
                guardrail_version="1",
                source=GuardrailSource.INPUT,
                intervened=False,
            )

    def test_missing_source_raises(self):
        with pytest.raises(ValidationError):
            GuardrailAssessmentResult(
                guardrail_id="gr-1",
                guardrail_version="1",
                intervened=False,
            )

    def test_invalid_source_string_raises(self):
        with pytest.raises(ValidationError):
            GuardrailAssessmentResult(
                guardrail_id="gr-1",
                guardrail_version="1",
                source="INVALID",
                intervened=False,
            )


# ── Round-trip serialisation ───────────────────────────────────────────────────


class TestGuardrailAssessmentResultSerialization:
    def test_model_dump_round_trip(self):
        result = GuardrailAssessmentResult(
            guardrail_id="gr-test",
            guardrail_version="DRAFT",
            source=GuardrailSource.OUTPUT,
            intervened=True,
            action="GUARDRAIL_INTERVENED",
            output_text="Blocked.",
            blocked=True,
            finding_types=["PII_EMAIL"],
            trace={"raw": "data"},
        )
        dumped = result.model_dump()
        rebuilt = GuardrailAssessmentResult(**dumped)
        assert rebuilt == result

    def test_model_dump_source_is_string_value(self):
        result = GuardrailAssessmentResult(
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            intervened=False,
        )
        dumped = result.model_dump()
        assert dumped["source"] == "input"

    def test_model_json_serialisable(self):
        import json

        result = GuardrailAssessmentResult(
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            intervened=False,
        )
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["guardrail_id"] == "gr-1"
        assert parsed["source"] == "input"


# ── safety_models extension: new enum members ─────────────────────────────────


class TestSafetyModelsExtension:
    """Verify the H-1 additions to safety_models are present and correct."""

    def test_guardrails_issue_source_exists(self):
        from app.schemas.safety_models import IssueSource

        assert IssueSource.GUARDRAILS.value == "guardrails"

    def test_guardrail_intervention_issue_code_exists(self):
        from app.schemas.safety_models import SafetyIssueCode

        assert SafetyIssueCode.GUARDRAIL_INTERVENTION.value == "guardrail_intervention"

    def test_issue_source_still_has_all_h0_members(self):
        from app.schemas.safety_models import IssueSource

        expected = {
            "validation",
            "retrieval",
            "citation_quality",
            "output_quality",
            "schema",
            "policy",
            "guardrails",
        }
        actual = {m.value for m in IssueSource}
        assert expected == actual

    def test_safety_issue_code_includes_h1_member(self):
        from app.schemas.safety_models import SafetyIssueCode

        codes = {m.value for m in SafetyIssueCode}
        assert "guardrail_intervention" in codes

    def test_guardrails_source_usable_in_safety_issue(self):
        from app.schemas.safety_models import (
            IssueSource,
            SafetyIssue,
            SafetyIssueCode,
            SafetyIssueSeverity,
        )

        issue = SafetyIssue(
            issue_code=SafetyIssueCode.GUARDRAIL_INTERVENTION,
            severity=SafetyIssueSeverity.ERROR,
            message="Guardrail blocked content",
            blocking=True,
            source=IssueSource.GUARDRAILS,
        )
        assert issue.source == IssueSource.GUARDRAILS
        assert issue.issue_code == SafetyIssueCode.GUARDRAIL_INTERVENTION
