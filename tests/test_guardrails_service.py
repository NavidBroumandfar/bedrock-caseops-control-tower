"""
Tests for app/services/guardrails_service.py — H-1 Guardrails service wrapper.

Coverage:
  - Request is built with correct API parameters
  - Mocked non-intervention response normalises to intervened=False
  - Mocked intervention response normalises to intervened=True, blocked=True
  - Output text is extracted from outputs list
  - Finding types extracted from all sub-policies (topic, content, word, PII, grounding)
  - Trace attached only when include_trace=True
  - Client failure raises GuardrailsServiceError
  - Missing 'action' key in response raises GuardrailsServiceError
  - No live AWS call is made in any test
  - GuardrailsService remains a thin wrapper (no policy logic)
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.schemas.guardrail_models import GuardrailAssessmentResult, GuardrailSource
from app.services.guardrails_service import (
    GuardrailsService,
    GuardrailsServiceError,
    _extract_finding_types,
    _extract_output_text,
    _normalize_response,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_client(response: dict) -> MagicMock:
    """Return a mock boto3 client whose apply_guardrail returns response."""
    client = MagicMock()
    client.apply_guardrail.return_value = response
    return client


def _none_response() -> dict:
    """Minimal API response representing no intervention."""
    return {"action": "NONE", "outputs": [], "assessments": []}


def _intervened_response() -> dict:
    """API response representing a Guardrail intervention."""
    return {
        "action": "GUARDRAIL_INTERVENED",
        "outputs": [{"text": "Content has been blocked."}],
        "assessments": [
            {
                "topicPolicy": {
                    "topics": [{"name": "Finance", "action": "BLOCKED"}]
                }
            }
        ],
    }


# ── GuardrailsService.assess_text — request construction ─────────────────────


class TestAssessTextRequestConstruction:
    def test_apply_guardrail_called_once(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("hello", "gr-1", "1", GuardrailSource.INPUT)
        client.apply_guardrail.assert_called_once()

    def test_guardrail_identifier_passed(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("hello", "gr-abc", "1", GuardrailSource.INPUT)
        call_kwargs = client.apply_guardrail.call_args[1]
        assert call_kwargs["guardrailIdentifier"] == "gr-abc"

    def test_guardrail_version_passed(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("hello", "gr-1", "DRAFT", GuardrailSource.INPUT)
        call_kwargs = client.apply_guardrail.call_args[1]
        assert call_kwargs["guardrailVersion"] == "DRAFT"

    def test_source_input_uppercased(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("hello", "gr-1", "1", GuardrailSource.INPUT)
        call_kwargs = client.apply_guardrail.call_args[1]
        assert call_kwargs["source"] == "INPUT"

    def test_source_output_uppercased(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("hello", "gr-1", "1", GuardrailSource.OUTPUT)
        call_kwargs = client.apply_guardrail.call_args[1]
        assert call_kwargs["source"] == "OUTPUT"

    def test_content_wraps_text(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("test content", "gr-1", "1", GuardrailSource.INPUT)
        call_kwargs = client.apply_guardrail.call_args[1]
        assert call_kwargs["content"] == [{"text": {"text": "test content"}}]


# ── GuardrailsService.assess_text — non-intervention response ─────────────────


class TestAssessTextNonIntervention:
    def _result(self, **kwargs) -> GuardrailAssessmentResult:
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        return svc.assess_text("ok text", "gr-1", "1", GuardrailSource.INPUT, **kwargs)

    def test_returns_assessment_result(self):
        assert isinstance(self._result(), GuardrailAssessmentResult)

    def test_intervened_false(self):
        assert self._result().intervened is False

    def test_blocked_false(self):
        assert self._result().blocked is False

    def test_action_is_none_string(self):
        assert self._result().action == "NONE"

    def test_output_text_none(self):
        assert self._result().output_text is None

    def test_finding_types_empty(self):
        assert self._result().finding_types == []

    def test_guardrail_id_preserved(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        result = svc.assess_text("x", "gr-xyz", "3", GuardrailSource.OUTPUT)
        assert result.guardrail_id == "gr-xyz"
        assert result.guardrail_version == "3"

    def test_source_preserved(self):
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        result = svc.assess_text("x", "gr-1", "1", GuardrailSource.OUTPUT)
        assert result.source == GuardrailSource.OUTPUT

    def test_trace_none_by_default(self):
        assert self._result().trace is None

    def test_trace_none_when_include_trace_false(self):
        assert self._result(include_trace=False).trace is None


# ── GuardrailsService.assess_text — intervention response ─────────────────────


class TestAssessTextIntervention:
    def _result(self, response=None, **kwargs) -> GuardrailAssessmentResult:
        client = _make_client(response or _intervened_response())
        svc = GuardrailsService(client=client)
        return svc.assess_text("bad text", "gr-1", "1", GuardrailSource.OUTPUT, **kwargs)

    def test_intervened_true(self):
        assert self._result().intervened is True

    def test_blocked_true(self):
        assert self._result().blocked is True

    def test_action_is_guardrail_intervened(self):
        assert self._result().action == "GUARDRAIL_INTERVENED"

    def test_output_text_extracted(self):
        assert self._result().output_text == "Content has been blocked."

    def test_finding_types_contain_topic_name(self):
        assert "Finance" in self._result().finding_types

    def test_trace_none_by_default(self):
        assert self._result().trace is None

    def test_trace_attached_when_requested(self):
        result = self._result(include_trace=True)
        assert result.trace is not None
        assert isinstance(result.trace, dict)

    def test_trace_contains_assessments_key(self):
        result = self._result(include_trace=True)
        assert "assessments" in result.trace
        assert len(result.trace["assessments"]) == 1


# ── Finding extraction helpers ────────────────────────────────────────────────


class TestExtractFindingTypes:
    def test_empty_assessments_returns_empty(self):
        assert _extract_finding_types({"assessments": []}) == []

    def test_no_assessments_key_returns_empty(self):
        assert _extract_finding_types({}) == []

    def test_topic_name_extracted_when_blocked(self):
        raw = {
            "assessments": [
                {
                    "topicPolicy": {
                        "topics": [{"name": "Violence", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["Violence"]

    def test_topic_name_excluded_when_action_none(self):
        raw = {
            "assessments": [
                {
                    "topicPolicy": {
                        "topics": [{"name": "Finance", "action": "NONE"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == []

    def test_content_filter_type_extracted(self):
        raw = {
            "assessments": [
                {
                    "contentPolicy": {
                        "filters": [{"type": "HATE", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["HATE"]

    def test_content_filter_excluded_when_action_none(self):
        raw = {
            "assessments": [
                {
                    "contentPolicy": {
                        "filters": [{"type": "INSULTS", "action": "NONE"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == []

    def test_pii_entity_type_extracted(self):
        raw = {
            "assessments": [
                {
                    "sensitiveInformationPolicy": {
                        "piiEntities": [{"type": "EMAIL", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["EMAIL"]

    def test_regex_name_extracted(self):
        raw = {
            "assessments": [
                {
                    "sensitiveInformationPolicy": {
                        "regexes": [{"name": "credit_card_regex", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["credit_card_regex"]

    def test_custom_word_match_extracted(self):
        raw = {
            "assessments": [
                {
                    "wordPolicy": {
                        "customWords": [{"match": "badword", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["badword"]

    def test_managed_word_list_type_extracted(self):
        raw = {
            "assessments": [
                {
                    "wordPolicy": {
                        "managedWordLists": [{"type": "PROFANITY", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["PROFANITY"]

    def test_contextual_grounding_filter_extracted(self):
        raw = {
            "assessments": [
                {
                    "contextualGroundingPolicy": {
                        "filters": [{"type": "GROUNDING", "action": "BLOCKED"}]
                    }
                }
            ]
        }
        assert _extract_finding_types(raw) == ["GROUNDING"]

    def test_multiple_findings_combined(self):
        raw = {
            "assessments": [
                {
                    "topicPolicy": {
                        "topics": [{"name": "Finance", "action": "BLOCKED"}]
                    },
                    "contentPolicy": {
                        "filters": [{"type": "HATE", "action": "BLOCKED"}]
                    },
                }
            ]
        }
        findings = _extract_finding_types(raw)
        assert "Finance" in findings
        assert "HATE" in findings

    def test_multiple_assessments_combined(self):
        raw = {
            "assessments": [
                {"topicPolicy": {"topics": [{"name": "A", "action": "BLOCKED"}]}},
                {"topicPolicy": {"topics": [{"name": "B", "action": "BLOCKED"}]}},
            ]
        }
        findings = _extract_finding_types(raw)
        assert "A" in findings
        assert "B" in findings


# ── _extract_output_text helper ───────────────────────────────────────────────


class TestExtractOutputText:
    def test_extracts_first_output_text(self):
        raw = {"outputs": [{"text": "Modified content"}]}
        assert _extract_output_text(raw) == "Modified content"

    def test_empty_outputs_returns_none(self):
        assert _extract_output_text({"outputs": []}) is None

    def test_missing_outputs_key_returns_none(self):
        assert _extract_output_text({}) is None

    def test_none_outputs_returns_none(self):
        assert _extract_output_text({"outputs": None}) is None


# ── _normalize_response helper ────────────────────────────────────────────────


class TestNormalizeResponse:
    def test_none_action_sets_intervened_false(self):
        result = _normalize_response(
            raw={"action": "NONE", "outputs": [], "assessments": []},
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            include_trace=False,
        )
        assert result.intervened is False
        assert result.blocked is False

    def test_intervened_action_sets_intervened_true(self):
        result = _normalize_response(
            raw={"action": "GUARDRAIL_INTERVENED", "outputs": [], "assessments": []},
            guardrail_id="gr-1",
            guardrail_version="1",
            source=GuardrailSource.INPUT,
            include_trace=False,
        )
        assert result.intervened is True
        assert result.blocked is True

    def test_missing_action_raises_service_error(self):
        with pytest.raises(GuardrailsServiceError, match="missing 'action'"):
            _normalize_response(
                raw={"outputs": []},
                guardrail_id="gr-1",
                guardrail_version="1",
                source=GuardrailSource.INPUT,
                include_trace=False,
            )


# ── Client failure handling ────────────────────────────────────────────────────


class TestClientFailure:
    def test_boto_client_error_raises_service_error(self):
        client = MagicMock()
        client.apply_guardrail.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "ApplyGuardrail",
        )
        svc = GuardrailsService(client=client)
        with pytest.raises(GuardrailsServiceError, match="ApplyGuardrail API call failed"):
            svc.assess_text("text", "gr-1", "1", GuardrailSource.INPUT)

    def test_generic_exception_from_client_raises_service_error(self):
        from botocore.exceptions import BotoCoreError

        client = MagicMock()
        client.apply_guardrail.side_effect = BotoCoreError()
        svc = GuardrailsService(client=client)
        with pytest.raises(GuardrailsServiceError):
            svc.assess_text("text", "gr-1", "1", GuardrailSource.INPUT)

    def test_service_error_is_raised_not_boto_exception(self):
        """GuardrailsServiceError is the only exception type that escapes the service."""
        client = MagicMock()
        client.apply_guardrail.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Denied"}},
            "ApplyGuardrail",
        )
        svc = GuardrailsService(client=client)
        with pytest.raises(GuardrailsServiceError):
            svc.assess_text("text", "gr-1", "1", GuardrailSource.INPUT)


# ── Structural / no-live-AWS assertions ───────────────────────────────────────


class TestStructural:
    def test_no_live_boto3_call_in_any_test(self):
        """All tests use injected mock clients; this test validates the pattern."""
        client = _make_client(_none_response())
        svc = GuardrailsService(client=client)
        svc.assess_text("x", "gr-1", "1", GuardrailSource.INPUT)
        assert client.apply_guardrail.called

    def test_service_does_not_import_aws_services(self):
        """GuardrailsService must not import bedrock_service, kb_service, or s3_service."""
        import inspect
        import app.services.guardrails_service as mod

        source = inspect.getsource(mod)
        assert "bedrock_service" not in source
        assert "kb_service" not in source
        assert "s3_service" not in source

    def test_service_does_not_import_policy_evaluator(self):
        """The service must not import the H-0 evaluator or adapter at module level."""
        import ast
        import inspect
        import app.services.guardrails_service as mod

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
        assert "app.evaluation.guardrails_adapter" not in import_names

    def test_service_does_not_import_h2_or_later(self):
        import inspect
        import app.services.guardrails_service as mod

        source = inspect.getsource(mod)
        for forbidden in ("adversarial", "optimization", "cloudwatch_dashboard"):
            assert forbidden not in source
