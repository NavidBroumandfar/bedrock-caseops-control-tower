"""
C-2 unit tests — Validation Agent and Bedrock Converse validation service.

Coverage:

  ValidationAgent:
  - valid evidence + valid provider → returns ValidationOutput
  - returned ValidationOutput has all required fields
  - returned ValidationOutput document_id matches caller input
  - does not import or use boto3 directly
  - delegates to provider exactly once per run()
  - forwards document_id to provider unchanged
  - forwards analysis_output to provider unchanged
  - forwards evidence_chunks to provider unchanged
  - empty evidence → returns conservative fail result without calling provider
  - empty evidence result has confidence_score 0.0
  - empty evidence result has validation_status "fail"
  - empty evidence result has non-empty unsupported_claims
  - empty evidence result has warning field set
  - provider-side BedrockServiceError propagates unchanged
  - provider-side generic error propagates unchanged
  - any ValidationProvider-compatible object accepted as dependency

  BedrockValidationService:
  - valid Converse response → ValidationOutput with all fields
  - satisfies ValidationProvider protocol
  - model_id from constructor takes precedence over env var
  - model_id falls back to env var when no constructor value
  - ClientError → BedrockServiceError (with chained cause)
  - BotoCoreError → BedrockServiceError
  - unexpected response shape → BedrockServiceError
  - non-JSON model output → BedrockServiceError
  - JSON array (not dict) → BedrockServiceError
  - missing required key → BedrockServiceError
  - invalid confidence_score (out of range) → BedrockServiceError
  - invalid validation_status → BedrockServiceError
  - markdown code-fenced JSON parsed cleanly
  - markdown fence without language tag parsed cleanly
  - document_id injected into ValidationOutput (not echoed by model)
  - all three validation statuses accepted
  - empty unsupported_claims list is valid
  - non-empty unsupported_claims preserved
  - optional warning field preserved when present
  - optional warning field defaults to None when absent from model response
  - converse called with the configured modelId
  - system prompt forwarded to client
  - user message forwarded as single user-role message

  Prompt:
  - validation system prompt contains critic role instruction
  - validation system prompt contains evidence-only constraint
  - validation system prompt names all three validation statuses
  - validation system prompt names confidence_score key
  - validation system prompt names unsupported_claims key
  - validation user message contains document_id
  - validation user message contains analysis severity
  - validation user message contains analysis summary
  - validation user message contains evidence chunk text
  - validation user message contains evidence source labels
  - validation user message contains all recommendations

  Parsing:
  - _parse_validation_output returns ValidationOutput for valid JSON
  - _parse_validation_output raises BedrockServiceError for invalid JSON
  - _parse_validation_output raises BedrockServiceError for missing required key
  - _parse_validation_output raises BedrockServiceError when confidence_score out of bounds

No AWS credentials or live calls required.
All boto3 interaction is replaced by an injected MagicMock client.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from app.agents.validation_agent import ValidationAgent, ValidationAgentError
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk
from app.schemas.validation_contract import ValidationProvider
from app.schemas.validation_models import ValidationOutput
from app.services.bedrock_service import (
    BedrockServiceError,
    BedrockValidationService,
    _build_validation_system_prompt,
    _build_validation_user_message,
    _parse_validation_output,
)

# ── shared helpers ──────────────────────────────────────────────────────────────


def _make_converse_response(text: str) -> dict:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text}],
            }
        }
    }


def _make_mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    client.converse.return_value = _make_converse_response(response_text)
    return client


def _valid_json_response(
    confidence_score: float = 0.87,
    unsupported_claims: list[str] | None = None,
    validation_status: str = "pass",
    warning: str | None = None,
) -> str:
    payload: dict = {
        "confidence_score": confidence_score,
        "unsupported_claims": unsupported_claims if unsupported_claims is not None else [],
        "validation_status": validation_status,
    }
    if warning is not None:
        payload["warning"] = warning
    return json.dumps(payload)


def _make_analysis_output(document_id: str = "doc-20260404-a1b2c3d4") -> AnalysisOutput:
    return AnalysisOutput(
        document_id=document_id,
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to establish adequate written procedures for equipment cleaning.",
        recommendations=[
            "Initiate CAPA for cleaning validation gaps.",
            "Notify compliance team within 48 hours.",
        ],
    )


def _make_chunks(count: int = 2) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id=f"chunk-00{i + 1}",
            text=f"Evidence passage {i + 1} with relevant regulatory content.",
            source_id=f"s3://caseops-kb/doc{i + 1}.txt",
            source_label=f"Source Document {i + 1}",
            excerpt=f"Evidence passage {i + 1}.",
            relevance_score=round(0.9 - i * 0.1, 2),
        )
        for i in range(count)
    ]


_DOC_ID = "doc-20260404-a1b2c3d4"
_SAMPLE_CHUNKS = _make_chunks()
_SAMPLE_ANALYSIS = _make_analysis_output(_DOC_ID)


@pytest.fixture()
def service() -> BedrockValidationService:
    return BedrockValidationService(
        model_id="test-model-id",
        client=_make_mock_client(_valid_json_response()),
    )


@pytest.fixture()
def fake_provider() -> MagicMock:
    provider = MagicMock()
    provider.validate.return_value = ValidationOutput(
        document_id=_DOC_ID,
        confidence_score=0.87,
        unsupported_claims=[],
        validation_status="pass",
    )
    return provider


@pytest.fixture()
def agent(fake_provider: MagicMock) -> ValidationAgent:
    return ValidationAgent(provider=fake_provider)


# ── ValidationAgent: successful run ────────────────────────────────────────────


def test_agent_run_returns_validation_output(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert isinstance(result, ValidationOutput)


def test_agent_run_output_document_id_matches(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.document_id == _DOC_ID


def test_agent_run_output_has_all_required_fields(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert hasattr(result, "document_id")
    assert hasattr(result, "confidence_score")
    assert hasattr(result, "unsupported_claims")
    assert hasattr(result, "validation_status")
    assert hasattr(result, "warning")


def test_agent_run_with_single_chunk_is_valid(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _make_chunks(count=1))
    assert isinstance(result, ValidationOutput)


# ── ValidationAgent: provider delegation ────────────────────────────────────────


def test_agent_delegates_to_provider_once(
    agent: ValidationAgent, fake_provider: MagicMock
) -> None:
    agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    fake_provider.validate.assert_called_once()


def test_agent_forwards_document_id_to_provider(
    agent: ValidationAgent, fake_provider: MagicMock
) -> None:
    agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    call_args = fake_provider.validate.call_args
    assert call_args.args[0] == _DOC_ID


def test_agent_forwards_analysis_output_to_provider(
    agent: ValidationAgent, fake_provider: MagicMock
) -> None:
    agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    call_args = fake_provider.validate.call_args
    assert call_args.args[1] is _SAMPLE_ANALYSIS


def test_agent_forwards_evidence_chunks_to_provider(
    agent: ValidationAgent, fake_provider: MagicMock
) -> None:
    chunks = _make_chunks(count=3)
    agent.run(_DOC_ID, _SAMPLE_ANALYSIS, chunks)
    call_args = fake_provider.validate.call_args
    assert call_args.args[2] == chunks


def test_agent_delegates_with_correct_positional_args(
    agent: ValidationAgent, fake_provider: MagicMock
) -> None:
    agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    fake_provider.validate.assert_called_once_with(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


# ── ValidationAgent: empty evidence ────────────────────────────────────────────


def test_empty_evidence_returns_validation_output_not_raises(
    agent: ValidationAgent,
) -> None:
    """Empty evidence must not raise — agent returns a conservative typed result."""
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    assert isinstance(result, ValidationOutput)


def test_empty_evidence_does_not_call_provider(
    agent: ValidationAgent, fake_provider: MagicMock
) -> None:
    """Provider must not be called when evidence is empty."""
    agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    fake_provider.validate.assert_not_called()


def test_empty_evidence_result_confidence_is_zero(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    assert result.confidence_score == pytest.approx(0.0)


def test_empty_evidence_result_status_is_fail(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    assert result.validation_status == "fail"


def test_empty_evidence_result_has_unsupported_claims(agent: ValidationAgent) -> None:
    """Must not return an empty unsupported_claims list — all claims are unverifiable."""
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    assert len(result.unsupported_claims) > 0


def test_empty_evidence_result_has_warning(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    assert result.warning is not None
    assert len(result.warning) > 0


def test_empty_evidence_result_document_id_is_set(agent: ValidationAgent) -> None:
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, [])
    assert result.document_id == _DOC_ID


# ── ValidationAgent: no direct AWS calls ────────────────────────────────────────


def test_agent_module_does_not_import_boto3() -> None:
    """
    The validation agent module must not import boto3.
    All AWS interaction belongs in the service layer — agents are AWS-free.
    """
    import app.agents.validation_agent as agent_module
    assert not hasattr(agent_module, "boto3"), (
        "ValidationAgent must not import boto3 directly; "
        "AWS interaction belongs in app/services/."
    )


# ── ValidationAgent: error propagation ─────────────────────────────────────────


def test_provider_bedrock_service_error_propagates_unchanged() -> None:
    provider = MagicMock()
    provider.validate.side_effect = BedrockServiceError("Validation model call failed")
    agent = ValidationAgent(provider=provider)
    with pytest.raises(BedrockServiceError, match="Validation model call failed"):
        agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_provider_generic_error_propagates_unchanged() -> None:
    provider = MagicMock()
    provider.validate.side_effect = RuntimeError("Unexpected provider failure")
    agent = ValidationAgent(provider=provider)
    with pytest.raises(RuntimeError, match="Unexpected provider failure"):
        agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


# ── ValidationAgent: contract compatibility ─────────────────────────────────────


def test_agent_accepts_any_validation_provider_compatible_object() -> None:
    """
    ValidationAgent accepts any object with a matching validate() signature —
    the protocol is structural, not inheritance-based.
    """
    class InlineProvider:
        def validate(
            self,
            document_id: str,
            analysis_output: AnalysisOutput,
            evidence_chunks: list[EvidenceChunk],
        ) -> ValidationOutput:
            return ValidationOutput(
                document_id=document_id,
                confidence_score=0.9,
                unsupported_claims=[],
                validation_status="pass",
            )

    agent = ValidationAgent(provider=InlineProvider())
    result = agent.run(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert isinstance(result, ValidationOutput)
    assert result.document_id == _DOC_ID


# ── BedrockValidationService: constructor ──────────────────────────────────────


def test_model_id_from_constructor() -> None:
    svc = BedrockValidationService(model_id="my-model", client=MagicMock())
    assert svc._model_id == "my-model"


def test_model_id_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env-model")
    svc = BedrockValidationService(client=MagicMock())
    assert svc._model_id == "env-model"


def test_model_id_constructor_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env-model")
    svc = BedrockValidationService(model_id="explicit-model", client=MagicMock())
    assert svc._model_id == "explicit-model"


# ── BedrockValidationService: protocol ─────────────────────────────────────────


def test_service_satisfies_validation_provider_protocol() -> None:
    svc = BedrockValidationService(model_id="test", client=MagicMock())
    assert isinstance(svc, ValidationProvider)


# ── BedrockValidationService: successful validation ─────────────────────────────


def test_validate_returns_validation_output(service: BedrockValidationService) -> None:
    result = service.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert isinstance(result, ValidationOutput)


def test_validate_document_id_injected(service: BedrockValidationService) -> None:
    """document_id comes from the caller, not the model response."""
    result = service.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.document_id == _DOC_ID


def test_validate_confidence_score_mapped(service: BedrockValidationService) -> None:
    result = service.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.confidence_score == pytest.approx(0.87)


def test_validate_validation_status_mapped(service: BedrockValidationService) -> None:
    result = service.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.validation_status == "pass"


def test_validate_empty_unsupported_claims_is_valid(service: BedrockValidationService) -> None:
    result = service.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.unsupported_claims == []


def test_validate_non_empty_unsupported_claims_preserved() -> None:
    claims = ["Claim A lacks evidence.", "Claim B is speculative."]
    client = _make_mock_client(
        _valid_json_response(
            confidence_score=0.35,
            unsupported_claims=claims,
            validation_status="fail",
        )
    )
    svc = BedrockValidationService(model_id="test", client=client)
    result = svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.unsupported_claims == claims


def test_validate_warning_preserved_when_present() -> None:
    client = _make_mock_client(
        _valid_json_response(
            confidence_score=0.65,
            validation_status="warning",
            warning="Weak support for recommendation 2.",
        )
    )
    svc = BedrockValidationService(model_id="test", client=client)
    result = svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.warning == "Weak support for recommendation 2."


def test_validate_warning_is_none_when_absent() -> None:
    """warning key absent from model response → ValidationOutput.warning is None."""
    client = _make_mock_client(_valid_json_response())  # no warning key
    svc = BedrockValidationService(model_id="test", client=client)
    result = svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.warning is None


@pytest.mark.parametrize("status", ["pass", "warning", "fail"])
def test_validate_all_statuses_accepted(status: str) -> None:
    score = {"pass": 0.9, "warning": 0.65, "fail": 0.3}[status]
    client = _make_mock_client(
        _valid_json_response(confidence_score=score, validation_status=status)
    )
    svc = BedrockValidationService(model_id="test", client=client)
    result = svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert result.validation_status == status


# ── BedrockValidationService: converse call forwarding ─────────────────────────


def test_converse_called_with_correct_model_id() -> None:
    mock_client = _make_mock_client(_valid_json_response())
    svc = BedrockValidationService(model_id="specific-model-id", client=mock_client)
    svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    call_kwargs = mock_client.converse.call_args.kwargs
    assert call_kwargs["modelId"] == "specific-model-id"


def test_converse_called_with_system_prompt() -> None:
    mock_client = _make_mock_client(_valid_json_response())
    svc = BedrockValidationService(model_id="test", client=mock_client)
    svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    call_kwargs = mock_client.converse.call_args.kwargs
    assert "system" in call_kwargs
    assert len(call_kwargs["system"]) > 0
    assert "text" in call_kwargs["system"][0]


def test_converse_called_with_single_user_message() -> None:
    mock_client = _make_mock_client(_valid_json_response())
    svc = BedrockValidationService(model_id="test", client=mock_client)
    svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    call_kwargs = mock_client.converse.call_args.kwargs
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


# ── BedrockValidationService: error translation ─────────────────────────────────


def test_client_error_raises_bedrock_service_error() -> None:
    mock_client = MagicMock()
    mock_client.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "Converse",
    )
    svc = BedrockValidationService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError, match="Bedrock Converse API call failed"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_botocore_error_raises_bedrock_service_error() -> None:
    mock_client = MagicMock()
    mock_client.converse.side_effect = BotoCoreError()
    svc = BedrockValidationService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_service_error_chains_original_exception() -> None:
    original = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Invalid model ID"}},
        "Converse",
    )
    mock_client = MagicMock()
    mock_client.converse.side_effect = original
    svc = BedrockValidationService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError) as exc_info:
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert exc_info.value.__cause__ is original


def test_unexpected_response_shape_raises_bedrock_service_error() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = {"unexpected": "shape"}
    svc = BedrockValidationService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError, match="Unexpected Bedrock Converse response shape"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_non_json_model_output_raises_bedrock_service_error() -> None:
    client = _make_mock_client("This is plain text, not JSON at all.")
    svc = BedrockValidationService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="not valid JSON"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_json_array_not_dict_raises_bedrock_service_error() -> None:
    client = _make_mock_client('["list", "not", "dict"]')
    svc = BedrockValidationService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="expected a JSON object"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_missing_required_key_raises_bedrock_service_error() -> None:
    incomplete = json.dumps({
        "confidence_score": 0.8,
        # unsupported_claims and validation_status intentionally absent
    })
    client = _make_mock_client(incomplete)
    svc = BedrockValidationService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="missing required keys"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_invalid_confidence_score_raises_bedrock_service_error() -> None:
    """A confidence_score outside [0.0, 1.0] must fail ValidationOutput validation."""
    invalid = json.dumps({
        "confidence_score": 1.5,
        "unsupported_claims": [],
        "validation_status": "pass",
    })
    client = _make_mock_client(invalid)
    svc = BedrockValidationService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="ValidationOutput validation"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


def test_invalid_validation_status_raises_bedrock_service_error() -> None:
    """A validation_status outside the three allowed literals must fail validation."""
    invalid = json.dumps({
        "confidence_score": 0.7,
        "unsupported_claims": [],
        "validation_status": "unknown",
    })
    client = _make_mock_client(invalid)
    svc = BedrockValidationService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="ValidationOutput validation"):
        svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)


# ── BedrockValidationService: markdown fence handling ──────────────────────────


def test_markdown_fenced_json_parsed_cleanly() -> None:
    fenced = f"```json\n{_valid_json_response()}\n```"
    client = _make_mock_client(fenced)
    svc = BedrockValidationService(model_id="test", client=client)
    result = svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert isinstance(result, ValidationOutput)


def test_markdown_fence_without_language_tag_parsed() -> None:
    fenced = f"```\n{_valid_json_response()}\n```"
    client = _make_mock_client(fenced)
    svc = BedrockValidationService(model_id="test", client=client)
    result = svc.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert isinstance(result, ValidationOutput)


# ── Prompt: system prompt content ──────────────────────────────────────────────


def test_validation_system_prompt_contains_critic_role() -> None:
    prompt = _build_validation_system_prompt()
    assert "critic" in prompt.lower()


def test_validation_system_prompt_contains_evidence_only_constraint() -> None:
    prompt = _build_validation_system_prompt()
    assert "NOT" in prompt or "not" in prompt.lower()


def test_validation_system_prompt_names_all_three_statuses() -> None:
    prompt = _build_validation_system_prompt()
    for status in ("pass", "warning", "fail"):
        assert status in prompt


def test_validation_system_prompt_names_confidence_score_key() -> None:
    prompt = _build_validation_system_prompt()
    assert "confidence_score" in prompt


def test_validation_system_prompt_names_unsupported_claims_key() -> None:
    prompt = _build_validation_system_prompt()
    assert "unsupported_claims" in prompt


def test_validation_system_prompt_names_validation_status_key() -> None:
    prompt = _build_validation_system_prompt()
    assert "validation_status" in prompt


# ── Prompt: user message content ───────────────────────────────────────────────


def test_validation_user_message_contains_document_id() -> None:
    msg = _build_validation_user_message(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert _DOC_ID in msg


def test_validation_user_message_contains_severity() -> None:
    msg = _build_validation_user_message(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert _SAMPLE_ANALYSIS.severity in msg


def test_validation_user_message_contains_summary() -> None:
    msg = _build_validation_user_message(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    assert _SAMPLE_ANALYSIS.summary in msg


def test_validation_user_message_contains_all_recommendations() -> None:
    msg = _build_validation_user_message(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    for rec in _SAMPLE_ANALYSIS.recommendations:
        assert rec in msg


def test_validation_user_message_contains_all_chunk_texts() -> None:
    msg = _build_validation_user_message(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    for chunk in _SAMPLE_CHUNKS:
        assert chunk.text in msg


def test_validation_user_message_contains_source_labels() -> None:
    msg = _build_validation_user_message(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    for chunk in _SAMPLE_CHUNKS:
        assert chunk.source_label in msg


# ── _parse_validation_output unit tests ────────────────────────────────────────


def test_parse_valid_json_returns_validation_output() -> None:
    result = _parse_validation_output(_DOC_ID, _valid_json_response())
    assert isinstance(result, ValidationOutput)
    assert result.document_id == _DOC_ID


def test_parse_injects_document_id() -> None:
    result = _parse_validation_output("doc-injected", _valid_json_response())
    assert result.document_id == "doc-injected"


def test_parse_invalid_json_raises_bedrock_service_error() -> None:
    with pytest.raises(BedrockServiceError, match="not valid JSON"):
        _parse_validation_output(_DOC_ID, "not-json-at-all")


def test_parse_missing_key_raises_bedrock_service_error() -> None:
    incomplete = json.dumps({"confidence_score": 0.8})
    with pytest.raises(BedrockServiceError, match="missing required keys"):
        _parse_validation_output(_DOC_ID, incomplete)


def test_parse_out_of_bounds_confidence_raises_bedrock_service_error() -> None:
    bad = json.dumps({
        "confidence_score": -0.5,
        "unsupported_claims": [],
        "validation_status": "fail",
    })
    with pytest.raises(BedrockServiceError, match="ValidationOutput validation"):
        _parse_validation_output(_DOC_ID, bad)


# ── contract: JSON round-trip ───────────────────────────────────────────────────


def test_validation_output_round_trips_json(service: BedrockValidationService) -> None:
    result = service.validate(_DOC_ID, _SAMPLE_ANALYSIS, _SAMPLE_CHUNKS)
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == _DOC_ID
    assert parsed["confidence_score"] == pytest.approx(0.87)
    assert isinstance(parsed["unsupported_claims"], list)
    assert parsed["validation_status"] == "pass"
