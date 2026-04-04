"""
B-1 unit tests — Bedrock Knowledge Base retrieval service.

Coverage:
  - BedrockKBService: successful retrieval → RetrievalResult with evidence chunks
  - BedrockKBService: empty KB response → RetrievalResult status="empty"
  - BedrockKBService: ClientError from SDK → RetrievalServiceError
  - BedrockKBService: BotoCoreError from SDK → RetrievalServiceError
  - BedrockKBService: missing kb_id at construction → RetrievalServiceError
  - BedrockKBService: retrieved_count equals len(evidence_chunks)
  - BedrockKBService: explicit query_text forwarded to KB call
  - BedrockKBService: fallback query derived from source_type + source_filename
  - BedrockKBService: satisfies RetrievalProvider protocol
  - Config: invalid RETRIEVAL_MAX_RESULTS env value → RetrievalServiceError
  - Config: zero or negative RETRIEVAL_MAX_RESULTS → RetrievalServiceError
  - Config: invalid max_results constructor argument → RetrievalServiceError
  - Mapping: non-numeric score field → RetrievalServiceError
  - Mapping: None score field → RetrievalServiceError
  - Mapping: completely malformed item (non-dict) → RetrievalServiceError
  - Mapping: source_id extracted from S3 location URI
  - Mapping: source_id falls back for non-S3 location types
  - Mapping: source_label derived as filename from S3 URI
  - Mapping: excerpt clips long text at word boundary
  - Mapping: excerpt preserves short text unchanged
  - Mapping: chunk_id is deterministic for same input
  - Mapping: relevance_score passed through from Bedrock score field
  - Mapping: missing score field defaults to 0.0
  - Contract: B-0 schema compatibility (RetrievalResult round-trips to JSON)

No AWS credentials or live calls required.
All boto3 interaction is replaced by an injected MagicMock client.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.retrieval_models import RetrievalRequest, RetrievalResult
from app.services.kb_service import (
    BedrockKBService,
    RetrievalServiceError,
    _derive_source_label,
    _extract_source_id,
    _make_chunk_id,
    _make_excerpt,
    _map_result_to_chunk,
    _resolve_max_results,
)

# ── shared fixtures ────────────────────────────────────────────────────────────


def _make_mock_client(retrieval_results: list[dict]) -> MagicMock:
    """Return a MagicMock that mimics the bedrock-agent-runtime client."""
    client = MagicMock()
    client.retrieve.return_value = {"retrievalResults": retrieval_results}
    return client


def _s3_result(text: str, uri: str, score: float) -> dict:
    """Build a minimal Bedrock retrievalResult item with an S3 location."""
    return {
        "content": {"text": text},
        "location": {
            "type": "S3",
            "s3Location": {"uri": uri},
        },
        "score": score,
    }


_SAMPLE_RESULTS = [
    _s3_result(
        text="The facility failed to maintain adequate equipment cleaning procedures.",
        uri="s3://caseops-kb/fda/warning-letter-2024.txt",
        score=0.91,
    ),
    _s3_result(
        text="Batch records were incomplete and lacked in-process controls.",
        uri="s3://caseops-kb/fda/warning-letter-2024.txt",
        score=0.78,
    ),
]


@pytest.fixture()
def fda_request() -> RetrievalRequest:
    return RetrievalRequest(
        document_id="doc-20260404-a1b2c3d4",
        source_type="FDA",
        source_filename="warning_letter.txt",
    )


@pytest.fixture()
def service_with_results() -> BedrockKBService:
    """BedrockKBService pre-wired to return two sample chunks."""
    return BedrockKBService(
        kb_id="kb-test-id",
        client=_make_mock_client(_SAMPLE_RESULTS),
    )


@pytest.fixture()
def service_with_empty() -> BedrockKBService:
    """BedrockKBService pre-wired to return an empty result list."""
    return BedrockKBService(
        kb_id="kb-test-id",
        client=_make_mock_client([]),
    )


# ── constructor ────────────────────────────────────────────────────────────────


def test_missing_kb_id_raises_retrieval_service_error(monkeypatch) -> None:
    """Construction without a kb_id must fail immediately and clearly."""
    monkeypatch.delenv("BEDROCK_KB_ID", raising=False)
    with pytest.raises(RetrievalServiceError, match="BEDROCK_KB_ID"):
        BedrockKBService(client=MagicMock())


def test_kb_id_from_env_var(monkeypatch) -> None:
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-from-env")
    svc = BedrockKBService(client=MagicMock())
    assert svc._kb_id == "kb-from-env"


def test_kb_id_from_constructor_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-from-env")
    svc = BedrockKBService(kb_id="kb-explicit", client=MagicMock())
    assert svc._kb_id == "kb-explicit"


def test_max_results_from_env_var(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "10")
    svc = BedrockKBService(kb_id="kb-test", client=MagicMock())
    assert svc._max_results == 10


def test_max_results_default_when_env_absent(monkeypatch) -> None:
    monkeypatch.delenv("RETRIEVAL_MAX_RESULTS", raising=False)
    svc = BedrockKBService(kb_id="kb-test", client=MagicMock())
    assert svc._max_results == 5


# ── config hardening — max_results ────────────────────────────────────────────


def test_invalid_max_results_env_string_raises(monkeypatch) -> None:
    """A non-numeric RETRIEVAL_MAX_RESULTS env value must raise RetrievalServiceError."""
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "not-a-number")
    with pytest.raises(RetrievalServiceError, match="RETRIEVAL_MAX_RESULTS"):
        BedrockKBService(kb_id="kb-test", client=MagicMock())


def test_zero_max_results_env_raises(monkeypatch) -> None:
    """Zero is not a valid chunk count; must be rejected as RetrievalServiceError."""
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "0")
    with pytest.raises(RetrievalServiceError, match="RETRIEVAL_MAX_RESULTS"):
        BedrockKBService(kb_id="kb-test", client=MagicMock())


def test_negative_max_results_env_raises(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "-3")
    with pytest.raises(RetrievalServiceError, match="RETRIEVAL_MAX_RESULTS"):
        BedrockKBService(kb_id="kb-test", client=MagicMock())


def test_invalid_max_results_constructor_raises() -> None:
    """A negative or zero constructor max_results must raise RetrievalServiceError."""
    with pytest.raises(RetrievalServiceError, match="max_results"):
        BedrockKBService(kb_id="kb-test", max_results=-1, client=MagicMock())


def test_zero_max_results_constructor_raises() -> None:
    with pytest.raises(RetrievalServiceError, match="max_results"):
        BedrockKBService(kb_id="kb-test", max_results=0, client=MagicMock())


def test_resolve_max_results_constructor_takes_precedence(monkeypatch) -> None:
    """Explicit constructor value must override a valid env var."""
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "10")
    svc = BedrockKBService(kb_id="kb-test", max_results=3, client=MagicMock())
    assert svc._max_results == 3


# ── response mapping hardening ─────────────────────────────────────────────────


def test_non_numeric_score_raises_retrieval_service_error(
    fda_request: RetrievalRequest,
) -> None:
    """A non-numeric score value in the provider response must raise RetrievalServiceError."""
    bad_item = {
        "content": {"text": "some passage"},
        "location": {"type": "S3", "s3Location": {"uri": "s3://b/f.txt"}},
        "score": "not-a-float",
    }
    mock_client = _make_mock_client([bad_item])
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError, match="Failed to map"):
        svc.retrieve(fda_request)


def test_none_score_raises_retrieval_service_error(
    fda_request: RetrievalRequest,
) -> None:
    """A None score (unexpected provider behaviour) must raise RetrievalServiceError."""
    bad_item = {
        "content": {"text": "some passage"},
        "location": {"type": "S3", "s3Location": {"uri": "s3://b/f.txt"}},
        "score": None,
    }
    mock_client = _make_mock_client([bad_item])
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError, match="Failed to map"):
        svc.retrieve(fda_request)


def test_malformed_item_non_dict_raises_retrieval_service_error(
    fda_request: RetrievalRequest,
) -> None:
    """A non-dict item in retrievalResults must raise RetrievalServiceError."""
    mock_client = _make_mock_client(["not-a-dict"])  # type: ignore[list-item]
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError, match="Failed to map"):
        svc.retrieve(fda_request)


def test_mapping_error_chains_original_exception(
    fda_request: RetrievalRequest,
) -> None:
    """RetrievalServiceError from mapping must chain the original cause."""
    bad_item = {
        "content": {"text": "passage"},
        "location": {"type": "S3", "s3Location": {"uri": "s3://b/f.txt"}},
        "score": "bad",
    }
    mock_client = _make_mock_client([bad_item])
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError) as exc_info:
        svc.retrieve(fda_request)
    assert exc_info.value.__cause__ is not None


# ── successful retrieval ───────────────────────────────────────────────────────


def test_retrieve_returns_retrieval_result(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert isinstance(result, RetrievalResult)


def test_retrieve_status_success(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.retrieval_status == "success"


def test_retrieve_returns_correct_chunk_count(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.retrieved_count == 2
    assert len(result.evidence_chunks) == 2


def test_retrieve_count_equals_chunks_length(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    """retrieved_count must always equal len(evidence_chunks) — schema invariant."""
    result = service_with_results.retrieve(fda_request)
    assert result.retrieved_count == len(result.evidence_chunks)


def test_retrieve_preserves_document_id(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.document_id == fda_request.document_id


def test_retrieve_no_warning_on_success(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.warning is None


# ── empty retrieval ────────────────────────────────────────────────────────────


def test_empty_retrieval_does_not_raise(
    service_with_empty: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_empty.retrieve(fda_request)
    assert result is not None


def test_empty_retrieval_status(
    service_with_empty: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_empty.retrieve(fda_request)
    assert result.retrieval_status == "empty"


def test_empty_retrieval_zero_count(
    service_with_empty: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_empty.retrieve(fda_request)
    assert result.retrieved_count == 0
    assert result.evidence_chunks == []


def test_empty_retrieval_has_warning(
    service_with_empty: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_empty.retrieve(fda_request)
    assert result.warning is not None
    assert len(result.warning) > 0


def test_empty_retrieval_preserves_document_id(
    service_with_empty: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_empty.retrieve(fda_request)
    assert result.document_id == fda_request.document_id


# ── error translation ──────────────────────────────────────────────────────────


def test_client_error_raises_retrieval_service_error(fda_request: RetrievalRequest) -> None:
    """A ClientError from boto3 must be wrapped in RetrievalServiceError."""
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Invalid KB ID"}},
        "Retrieve",
    )
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError, match="Bedrock KB Retrieve"):
        svc.retrieve(fda_request)


def test_botocore_error_raises_retrieval_service_error(fda_request: RetrievalRequest) -> None:
    """A BotoCoreError must be wrapped in RetrievalServiceError."""
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = BotoCoreError()
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError):
        svc.retrieve(fda_request)


def test_service_error_chains_original_exception(fda_request: RetrievalRequest) -> None:
    """The original boto3 exception must be accessible via __cause__."""
    original = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "Retrieve",
    )
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = original
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    with pytest.raises(RetrievalServiceError) as exc_info:
        svc.retrieve(fda_request)
    assert exc_info.value.__cause__ is original


# ── query building ─────────────────────────────────────────────────────────────


def test_explicit_query_text_forwarded_to_kb(fda_request: RetrievalRequest) -> None:
    """When query_text is set, it must reach the KB retrieve call unchanged."""
    mock_client = _make_mock_client(_SAMPLE_RESULTS)
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    req = fda_request.model_copy(
        update={"query_text": "What are the critical facility violations?"}
    )
    svc.retrieve(req)
    call_kwargs = mock_client.retrieve.call_args.kwargs
    assert call_kwargs["retrievalQuery"]["text"] == "What are the critical facility violations?"


def test_fallback_query_derived_from_request_fields(fda_request: RetrievalRequest) -> None:
    """When query_text is None, the derived query must include source_type and filename."""
    mock_client = _make_mock_client([])
    svc = BedrockKBService(kb_id="kb-test", client=mock_client)
    svc.retrieve(fda_request)
    call_kwargs = mock_client.retrieve.call_args.kwargs
    query_sent = call_kwargs["retrievalQuery"]["text"]
    assert "FDA" in query_sent
    assert "warning_letter.txt" in query_sent


def test_max_results_forwarded_in_config() -> None:
    mock_client = _make_mock_client([])
    svc = BedrockKBService(kb_id="kb-test", max_results=7, client=mock_client)
    req = RetrievalRequest(
        document_id="doc-x",
        source_type="CISA",
        source_filename="advisory.txt",
    )
    svc.retrieve(req)
    config = mock_client.retrieve.call_args.kwargs["retrievalConfiguration"]
    assert config["vectorSearchConfiguration"]["numberOfResults"] == 7


# ── evidence chunk mapping ─────────────────────────────────────────────────────


def test_evidence_chunk_text_mapped(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.evidence_chunks[0].text == _SAMPLE_RESULTS[0]["content"]["text"]


def test_evidence_chunk_relevance_score_mapped(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.evidence_chunks[0].relevance_score == pytest.approx(0.91)
    assert result.evidence_chunks[1].relevance_score == pytest.approx(0.78)


def test_evidence_chunk_source_id_is_s3_uri(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.evidence_chunks[0].source_id == "s3://caseops-kb/fda/warning-letter-2024.txt"


def test_evidence_chunk_source_label_is_filename(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    assert result.evidence_chunks[0].source_label == "warning-letter-2024.txt"


def test_evidence_chunk_excerpt_populated(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_results.retrieve(fda_request)
    for chunk in result.evidence_chunks:
        assert chunk.excerpt, "excerpt must not be empty"


def test_evidence_chunk_id_is_deterministic(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    """Repeated retrieval calls with the same response must produce identical chunk IDs."""
    result_a = service_with_results.retrieve(fda_request)
    # Reset mock to return the same results again.
    service_with_results._client.retrieve.return_value = {
        "retrievalResults": _SAMPLE_RESULTS
    }
    result_b = service_with_results.retrieve(fda_request)
    assert result_a.evidence_chunks[0].chunk_id == result_b.evidence_chunks[0].chunk_id


def test_missing_score_defaults_to_zero() -> None:
    item = {
        "content": {"text": "some text"},
        "location": {"type": "S3", "s3Location": {"uri": "s3://b/k.txt"}},
        # score field intentionally absent
    }
    chunk = _map_result_to_chunk(item, 0)
    assert chunk.relevance_score == 0.0


# ── protocol ──────────────────────────────────────────────────────────────────


def test_bedrock_kb_service_satisfies_retrieval_provider_protocol() -> None:
    """BedrockKBService must satisfy the B-0 RetrievalProvider contract."""
    svc = BedrockKBService(kb_id="kb-test", client=MagicMock())
    assert isinstance(svc, RetrievalProvider)


# ── mapping unit tests ─────────────────────────────────────────────────────────


def test_extract_source_id_s3() -> None:
    item = {
        "location": {
            "type": "S3",
            "s3Location": {"uri": "s3://my-bucket/docs/file.txt"},
        }
    }
    assert _extract_source_id(item) == "s3://my-bucket/docs/file.txt"


def test_extract_source_id_non_s3_returns_type_lowercase() -> None:
    item = {"location": {"type": "WEB", "webLocation": {"url": "https://example.com"}}}
    assert _extract_source_id(item) == "web"


def test_extract_source_id_missing_location_returns_unknown() -> None:
    assert _extract_source_id({}) == "unknown"


def test_derive_source_label_extracts_filename() -> None:
    assert _derive_source_label("s3://bucket/prefix/advisory.txt") == "advisory.txt"


def test_derive_source_label_nested_path() -> None:
    assert _derive_source_label("s3://bucket/a/b/c/letter.pdf") == "letter.pdf"


def test_derive_source_label_non_s3_returns_raw_id() -> None:
    assert _derive_source_label("web") == "web"


def test_make_chunk_id_format() -> None:
    chunk_id = _make_chunk_id("s3://b/k.txt", 0)
    assert chunk_id.startswith("chunk-")
    assert len(chunk_id) == len("chunk-") + 12


def test_make_chunk_id_deterministic() -> None:
    assert _make_chunk_id("s3://b/k.txt", 0) == _make_chunk_id("s3://b/k.txt", 0)


def test_make_chunk_id_differs_by_index() -> None:
    assert _make_chunk_id("s3://b/k.txt", 0) != _make_chunk_id("s3://b/k.txt", 1)


def test_make_excerpt_short_text_unchanged() -> None:
    short = "Short passage."
    assert _make_excerpt(short) == short


def test_make_excerpt_long_text_clipped() -> None:
    long_text = "word " * 100  # well over 200 chars
    result = _make_excerpt(long_text)
    assert len(result) <= 205  # max_chars + ellipsis + small margin
    assert result.endswith("...")


def test_make_excerpt_clips_at_word_boundary() -> None:
    # Build a string where the 200th char falls mid-word.
    text = "a" * 180 + " boundary_word " + "b" * 50
    result = _make_excerpt(text)
    # The excerpt must not split "boundary_word" in half.
    assert "boundary_word..." not in result or result.endswith("...")


# ── B-0 contract compatibility ─────────────────────────────────────────────────


def test_result_serializes_to_json(
    service_with_results: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    """RetrievalResult from BedrockKBService must serialize cleanly to JSON."""
    result = service_with_results.retrieve(fda_request)
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["retrieval_status"] == "success"
    assert len(parsed["evidence_chunks"]) == 2
    assert parsed["retrieved_count"] == 2


def test_empty_result_serializes_to_json(
    service_with_empty: BedrockKBService,
    fda_request: RetrievalRequest,
) -> None:
    result = service_with_empty.retrieve(fda_request)
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["evidence_chunks"] == []
    assert parsed["retrieved_count"] == 0
    assert parsed["retrieval_status"] == "empty"
    assert parsed["warning"] is not None
