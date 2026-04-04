"""
C-1 unit tests — Bedrock Converse analysis service.

Coverage:
  - BedrockAnalysisService: valid Converse response → AnalysisOutput with all fields
  - BedrockAnalysisService: satisfies AnalysisProvider protocol
  - BedrockAnalysisService: model_id from constructor takes precedence over env var
  - BedrockAnalysisService: model_id falls back to env var when no constructor value
  - BedrockAnalysisService: ClientError → BedrockServiceError (with chained cause)
  - BedrockAnalysisService: BotoCoreError → BedrockServiceError
  - BedrockAnalysisService: unexpected response shape → BedrockServiceError
  - BedrockAnalysisService: non-JSON model output → BedrockServiceError
  - BedrockAnalysisService: JSON array (not dict) → BedrockServiceError
  - BedrockAnalysisService: missing required key → BedrockServiceError
  - BedrockAnalysisService: invalid severity value → BedrockServiceError
  - BedrockAnalysisService: markdown code-fenced JSON parsed cleanly
  - BedrockAnalysisService: markdown fence without language tag parsed cleanly
  - BedrockAnalysisService: document_id injected into AnalysisOutput (not echoed by model)
  - BedrockAnalysisService: all four severity levels accepted
  - BedrockAnalysisService: empty recommendations list is valid
  - BedrockAnalysisService: multiple recommendations preserved in order
  - BedrockAnalysisService: converse called with the configured modelId
  - BedrockAnalysisService: system prompt forwarded to client
  - BedrockAnalysisService: user message forwarded as single user-role message
  - Prompt: user message contains document_id
  - Prompt: user message contains evidence chunk text
  - Prompt: user message contains source labels
  - Prompt: system prompt specifies evidence-only constraint
  - Prompt: system prompt names all four severity values
  - Parsing: _extract_json returns plain JSON unchanged
  - Parsing: _extract_json strips ```json ... ``` fence
  - Parsing: _extract_json strips plain ``` ... ``` fence
  - Parsing: _parse_analysis_output returns AnalysisOutput for valid JSON
  - Parsing: _parse_analysis_output raises BedrockServiceError for invalid JSON
  - Contract: AnalysisOutput round-trips through JSON cleanly

No AWS credentials or live calls required.
All boto3 interaction is replaced by an injected MagicMock client.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from app.schemas.analysis_contract import AnalysisProvider
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk
from app.services.bedrock_service import (
    BedrockAnalysisService,
    BedrockServiceError,
    _build_system_prompt,
    _build_user_message,
    _extract_json,
    _parse_analysis_output,
)

# ── shared helpers ──────────────────────────────────────────────────────────────


def _make_converse_response(text: str) -> dict:
    """Build a minimal Bedrock Converse response shape."""
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
    severity: str = "High",
    category: str = "Regulatory / Manufacturing Deficiency",
    summary: str = "Facility failed to establish adequate procedures for equipment cleaning.",
    recommendations: list[str] | None = None,
) -> str:
    return json.dumps({
        "severity": severity,
        "category": category,
        "summary": summary,
        "recommendations": recommendations if recommendations is not None else [
            "Initiate CAPA for cleaning validation gaps.",
            "Notify compliance team within 48 hours.",
        ],
    })


_SAMPLE_CHUNKS: list[EvidenceChunk] = [
    EvidenceChunk(
        chunk_id="chunk-001",
        text=(
            "The facility failed to establish adequate written procedures "
            "for equipment cleaning as required by 21 CFR 211.67."
        ),
        source_id="s3://caseops-kb/fda/warning-letter-2024-wl-0032.txt",
        source_label="FDA Warning Letter 2024-WL-0032",
        excerpt="...no written procedures for equipment cleaning...",
        relevance_score=0.91,
    ),
    EvidenceChunk(
        chunk_id="chunk-002",
        text="Batch records were incomplete and lacked in-process controls documentation.",
        source_id="s3://caseops-kb/fda/warning-letter-2024-wl-0032.txt",
        source_label="FDA Warning Letter 2024-WL-0032",
        excerpt="...batch records were incomplete...",
        relevance_score=0.78,
    ),
]

_DOC_ID = "doc-20260404-a1b2c3d4"


@pytest.fixture()
def service() -> BedrockAnalysisService:
    return BedrockAnalysisService(
        model_id="test-model-id",
        client=_make_mock_client(_valid_json_response()),
    )


# ── constructor ─────────────────────────────────────────────────────────────────


def test_model_id_from_constructor() -> None:
    svc = BedrockAnalysisService(model_id="my-model", client=MagicMock())
    assert svc._model_id == "my-model"


def test_model_id_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env-model")
    svc = BedrockAnalysisService(client=MagicMock())
    assert svc._model_id == "env-model"


def test_model_id_constructor_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env-model")
    svc = BedrockAnalysisService(model_id="explicit-model", client=MagicMock())
    assert svc._model_id == "explicit-model"


# ── protocol ────────────────────────────────────────────────────────────────────


def test_service_satisfies_analysis_provider_protocol() -> None:
    svc = BedrockAnalysisService(model_id="test", client=MagicMock())
    assert isinstance(svc, AnalysisProvider)


# ── successful analysis ─────────────────────────────────────────────────────────


def test_analyze_returns_analysis_output(service: BedrockAnalysisService) -> None:
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert isinstance(result, AnalysisOutput)


def test_analyze_document_id_injected(service: BedrockAnalysisService) -> None:
    """document_id comes from the caller, not the model response."""
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert result.document_id == _DOC_ID


def test_analyze_severity_mapped(service: BedrockAnalysisService) -> None:
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert result.severity == "High"


def test_analyze_category_mapped(service: BedrockAnalysisService) -> None:
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert result.category == "Regulatory / Manufacturing Deficiency"


def test_analyze_summary_mapped(service: BedrockAnalysisService) -> None:
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert "procedures" in result.summary


def test_analyze_recommendations_mapped(service: BedrockAnalysisService) -> None:
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert len(result.recommendations) == 2


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low"])
def test_analyze_all_severity_levels_accepted(severity: str) -> None:
    client = _make_mock_client(_valid_json_response(severity=severity))
    svc = BedrockAnalysisService(model_id="test", client=client)
    result = svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert result.severity == severity


def test_analyze_empty_recommendations_is_valid() -> None:
    client = _make_mock_client(_valid_json_response(recommendations=[]))
    svc = BedrockAnalysisService(model_id="test", client=client)
    result = svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert result.recommendations == []


def test_analyze_multiple_recommendations_preserved_in_order() -> None:
    recs = ["Action one.", "Action two.", "Action three."]
    client = _make_mock_client(_valid_json_response(recommendations=recs))
    svc = BedrockAnalysisService(model_id="test", client=client)
    result = svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert result.recommendations == recs


# ── converse call forwarding ────────────────────────────────────────────────────


def test_converse_called_with_correct_model_id() -> None:
    mock_client = _make_mock_client(_valid_json_response())
    svc = BedrockAnalysisService(model_id="specific-model-id", client=mock_client)
    svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    call_kwargs = mock_client.converse.call_args.kwargs
    assert call_kwargs["modelId"] == "specific-model-id"


def test_converse_called_with_system_prompt() -> None:
    mock_client = _make_mock_client(_valid_json_response())
    svc = BedrockAnalysisService(model_id="test", client=mock_client)
    svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    call_kwargs = mock_client.converse.call_args.kwargs
    assert "system" in call_kwargs
    assert len(call_kwargs["system"]) > 0
    assert "text" in call_kwargs["system"][0]


def test_converse_called_with_single_user_message() -> None:
    mock_client = _make_mock_client(_valid_json_response())
    svc = BedrockAnalysisService(model_id="test", client=mock_client)
    svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    call_kwargs = mock_client.converse.call_args.kwargs
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


# ── error translation ───────────────────────────────────────────────────────────


def test_client_error_raises_bedrock_service_error() -> None:
    mock_client = MagicMock()
    mock_client.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "Converse",
    )
    svc = BedrockAnalysisService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError, match="Bedrock Converse API call failed"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_botocore_error_raises_bedrock_service_error() -> None:
    mock_client = MagicMock()
    mock_client.converse.side_effect = BotoCoreError()
    svc = BedrockAnalysisService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_service_error_chains_original_exception() -> None:
    """The original boto3 exception must be accessible via __cause__."""
    original = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Invalid model ID"}},
        "Converse",
    )
    mock_client = MagicMock()
    mock_client.converse.side_effect = original
    svc = BedrockAnalysisService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError) as exc_info:
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert exc_info.value.__cause__ is original


def test_unexpected_response_shape_raises_bedrock_service_error() -> None:
    """A response with no 'output' key must raise BedrockServiceError immediately."""
    mock_client = MagicMock()
    mock_client.converse.return_value = {"unexpected": "shape"}
    svc = BedrockAnalysisService(model_id="test", client=mock_client)
    with pytest.raises(BedrockServiceError, match="Unexpected Bedrock Converse response shape"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_non_json_model_output_raises_bedrock_service_error() -> None:
    client = _make_mock_client("This is plain text, not JSON at all.")
    svc = BedrockAnalysisService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="not valid JSON"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_json_array_not_dict_raises_bedrock_service_error() -> None:
    """A JSON array at the top level must be rejected — the model must return an object."""
    client = _make_mock_client('["list", "not", "dict"]')
    svc = BedrockAnalysisService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="expected a JSON object"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_missing_required_key_raises_bedrock_service_error() -> None:
    incomplete = json.dumps({
        "severity": "High",
        "category": "Test",
        # summary and recommendations intentionally absent
    })
    client = _make_mock_client(incomplete)
    svc = BedrockAnalysisService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="missing required keys"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_invalid_severity_raises_bedrock_service_error() -> None:
    """A severity value outside the four allowed literals must fail validation."""
    invalid = json.dumps({
        "severity": "Extreme",
        "category": "Test",
        "summary": "A valid summary.",
        "recommendations": [],
    })
    client = _make_mock_client(invalid)
    svc = BedrockAnalysisService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="AnalysisOutput validation"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


def test_empty_summary_raises_bedrock_service_error() -> None:
    """An empty summary must fail AnalysisOutput validation and surface as BedrockServiceError."""
    invalid = json.dumps({
        "severity": "High",
        "category": "Test",
        "summary": "",
        "recommendations": [],
    })
    client = _make_mock_client(invalid)
    svc = BedrockAnalysisService(model_id="test", client=client)
    with pytest.raises(BedrockServiceError, match="AnalysisOutput validation"):
        svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)


# ── markdown code fence handling ────────────────────────────────────────────────


def test_markdown_fenced_json_parsed_cleanly() -> None:
    fenced = f"```json\n{_valid_json_response()}\n```"
    client = _make_mock_client(fenced)
    svc = BedrockAnalysisService(model_id="test", client=client)
    result = svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert isinstance(result, AnalysisOutput)


def test_markdown_fence_without_language_tag_parsed() -> None:
    fenced = f"```\n{_valid_json_response()}\n```"
    client = _make_mock_client(fenced)
    svc = BedrockAnalysisService(model_id="test", client=client)
    result = svc.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    assert isinstance(result, AnalysisOutput)


# ── prompt building ─────────────────────────────────────────────────────────────


def test_user_message_contains_document_id() -> None:
    msg = _build_user_message(_DOC_ID, _SAMPLE_CHUNKS)
    assert _DOC_ID in msg


def test_user_message_contains_all_chunk_texts() -> None:
    msg = _build_user_message(_DOC_ID, _SAMPLE_CHUNKS)
    for chunk in _SAMPLE_CHUNKS:
        assert chunk.text in msg


def test_user_message_contains_source_labels() -> None:
    msg = _build_user_message(_DOC_ID, _SAMPLE_CHUNKS)
    for chunk in _SAMPLE_CHUNKS:
        assert chunk.source_label in msg


def test_system_prompt_contains_evidence_only_constraint() -> None:
    prompt = _build_system_prompt()
    assert "ONLY" in prompt


def test_system_prompt_names_all_severity_values() -> None:
    prompt = _build_system_prompt()
    for severity in ("Critical", "High", "Medium", "Low"):
        assert severity in prompt


def test_system_prompt_names_all_required_keys() -> None:
    prompt = _build_system_prompt()
    for key in ("severity", "category", "summary", "recommendations"):
        assert key in prompt


# ── _extract_json unit tests ────────────────────────────────────────────────────


def test_extract_json_plain_json_returned_unchanged() -> None:
    raw = '{"key": "value"}'
    assert _extract_json(raw) == raw


def test_extract_json_strips_json_language_fence() -> None:
    raw = '```json\n{"key": "value"}\n```'
    assert _extract_json(raw) == '{"key": "value"}'


def test_extract_json_strips_plain_fence() -> None:
    raw = '```\n{"key": "value"}\n```'
    assert _extract_json(raw) == '{"key": "value"}'


def test_extract_json_strips_surrounding_whitespace() -> None:
    raw = '  {"key": "value"}  '
    assert _extract_json(raw) == '{"key": "value"}'


# ── _parse_analysis_output unit tests ──────────────────────────────────────────


def test_parse_valid_json_returns_analysis_output() -> None:
    result = _parse_analysis_output(_DOC_ID, _valid_json_response())
    assert isinstance(result, AnalysisOutput)
    assert result.document_id == _DOC_ID


def test_parse_injects_document_id() -> None:
    """document_id is set by the caller; the model payload does not include it."""
    result = _parse_analysis_output("doc-injected", _valid_json_response())
    assert result.document_id == "doc-injected"


def test_parse_invalid_json_raises_bedrock_service_error() -> None:
    with pytest.raises(BedrockServiceError, match="not valid JSON"):
        _parse_analysis_output(_DOC_ID, "not-json-at-all")


# ── contract: JSON round-trip ───────────────────────────────────────────────────


def test_analysis_output_round_trips_json(service: BedrockAnalysisService) -> None:
    result = service.analyze(_DOC_ID, _SAMPLE_CHUNKS)
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == _DOC_ID
    assert parsed["severity"] == "High"
    assert isinstance(parsed["recommendations"], list)
    assert len(parsed["recommendations"]) == 2
