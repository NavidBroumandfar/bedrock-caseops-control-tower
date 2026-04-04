"""
B-0 unit tests — retrieval contracts + evidence schemas.

Coverage:
  - EvidenceChunk: validation success and field presence
  - EvidenceChunk: invalid relevance_score (NaN, Inf, missing required field)
  - RetrievalResult: retrieved_count consistency enforcement
  - RetrievalResult: empty retrieval representable without exceptions
  - RetrievalResult: warning is optional
  - RetrievalRequest: minimal and full field construction
  - RetrievalRequest: unknown source_type rejected
  - FakeRetrievalProvider: returns typed RetrievalResult (non-empty and empty)
  - FakeRetrievalProvider: document_id passthrough
  - FakeRetrievalProvider: satisfies RetrievalProvider Protocol
  - FakeRetrievalProvider: evidence chunks carry valid citation fields
  - JSON serialization: EvidenceChunk and RetrievalResult serialize cleanly

No AWS credentials or live calls required.
"""

import json
import math

import pytest
from pydantic import ValidationError

from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.retrieval_models import (
    EvidenceChunk,
    RetrievalRequest,
    RetrievalResult,
)
from tests.fakes.fake_retrieval import FakeRetrievalProvider


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def valid_chunk() -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="chunk-abc",
        text="Full retrieved passage text from the source document.",
        source_id="s3://caseops-kb/fda/doc.txt::chunk-abc",
        source_label="FDA Warning Letter 2024-WL-0001",
        excerpt="...retrieved passage from source...",
        relevance_score=0.85,
    )


@pytest.fixture()
def minimal_request() -> RetrievalRequest:
    return RetrievalRequest(
        document_id="doc-20260404-a1b2c3d4",
        source_type="FDA",
        source_filename="warning_letter.txt",
    )


# ── EvidenceChunk: validation success ─────────────────────────────────────────

def test_evidence_chunk_valid(valid_chunk: EvidenceChunk) -> None:
    assert valid_chunk.chunk_id == "chunk-abc"
    assert valid_chunk.relevance_score == 0.85


def test_evidence_chunk_all_citation_fields_present(valid_chunk: EvidenceChunk) -> None:
    """Citations are first-class; all citation fields must be populated."""
    assert valid_chunk.text
    assert valid_chunk.source_id
    assert valid_chunk.source_label
    assert valid_chunk.excerpt


def test_evidence_chunk_relevance_score_zero_is_valid() -> None:
    chunk = EvidenceChunk(
        chunk_id="chunk-zero",
        text="text",
        source_id="src",
        source_label="label",
        excerpt="excerpt",
        relevance_score=0.0,
    )
    assert chunk.relevance_score == 0.0


# ── EvidenceChunk: invalid relevance_score ────────────────────────────────────

def test_evidence_chunk_rejects_nan_relevance_score() -> None:
    with pytest.raises(ValidationError, match="relevance_score"):
        EvidenceChunk(
            chunk_id="chunk-x",
            text="text",
            source_id="src",
            source_label="label",
            excerpt="excerpt",
            relevance_score=math.nan,
        )


def test_evidence_chunk_rejects_inf_relevance_score() -> None:
    with pytest.raises(ValidationError, match="relevance_score"):
        EvidenceChunk(
            chunk_id="chunk-x",
            text="text",
            source_id="src",
            source_label="label",
            excerpt="excerpt",
            relevance_score=math.inf,
        )


def test_evidence_chunk_rejects_negative_inf_relevance_score() -> None:
    with pytest.raises(ValidationError, match="relevance_score"):
        EvidenceChunk(
            chunk_id="chunk-x",
            text="text",
            source_id="src",
            source_label="label",
            excerpt="excerpt",
            relevance_score=-math.inf,
        )


def test_evidence_chunk_rejects_missing_required_field() -> None:
    """source_label is required; omitting it must raise ValidationError."""
    with pytest.raises(ValidationError):
        EvidenceChunk(
            chunk_id="chunk-x",
            text="text",
            source_id="src",
            # source_label intentionally omitted
            excerpt="excerpt",
            relevance_score=0.5,
        )


# ── RetrievalResult: retrieved_count consistency ──────────────────────────────

def test_retrieval_result_valid_with_one_chunk(valid_chunk: EvidenceChunk) -> None:
    result = RetrievalResult(
        document_id="doc-20260404-a1b2c3d4",
        evidence_chunks=[valid_chunk],
        retrieval_status="success",
        retrieved_count=1,
    )
    assert result.retrieved_count == 1
    assert len(result.evidence_chunks) == 1


def test_retrieval_result_count_mismatch_raises(valid_chunk: EvidenceChunk) -> None:
    """retrieved_count inconsistent with evidence_chunks length must be rejected."""
    with pytest.raises(ValidationError, match="retrieved_count"):
        RetrievalResult(
            document_id="doc-20260404-a1b2c3d4",
            evidence_chunks=[valid_chunk],
            retrieval_status="success",
            retrieved_count=3,  # wrong — only one chunk provided
        )


def test_retrieval_result_empty_count_mismatch_raises() -> None:
    with pytest.raises(ValidationError, match="retrieved_count"):
        RetrievalResult(
            document_id="doc-20260404-a1b2c3d4",
            evidence_chunks=[],
            retrieval_status="success",
            retrieved_count=1,  # wrong — list is empty
        )


# ── RetrievalResult: empty retrieval ──────────────────────────────────────────

def test_retrieval_result_empty_without_exception() -> None:
    """Empty retrieval must be representable without raising."""
    result = RetrievalResult(
        document_id="doc-20260404-a1b2c3d4",
        evidence_chunks=[],
        retrieval_status="empty",
        retrieved_count=0,
        warning="No chunks found in the knowledge base.",
    )
    assert result.retrieval_status == "empty"
    assert result.evidence_chunks == []
    assert result.retrieved_count == 0
    assert result.warning == "No chunks found in the knowledge base."


def test_retrieval_result_warning_defaults_to_none(valid_chunk: EvidenceChunk) -> None:
    result = RetrievalResult(
        document_id="doc-20260404-a1b2c3d4",
        evidence_chunks=[valid_chunk],
        retrieval_status="success",
        retrieved_count=1,
    )
    assert result.warning is None


def test_retrieval_result_error_status_is_valid() -> None:
    """Error status must be representable for fault isolation."""
    result = RetrievalResult(
        document_id="doc-20260404-a1b2c3d4",
        evidence_chunks=[],
        retrieval_status="error",
        retrieved_count=0,
        warning="KB retrieval failed — downstream escalation required.",
    )
    assert result.retrieval_status == "error"


# ── RetrievalRequest ───────────────────────────────────────────────────────────

def test_retrieval_request_minimal_defaults(minimal_request: RetrievalRequest) -> None:
    assert minimal_request.document_id.startswith("doc-")
    assert minimal_request.source_type == "FDA"
    assert minimal_request.query_text is None
    assert minimal_request.source_document_s3_key is None


def test_retrieval_request_with_optional_fields() -> None:
    req = RetrievalRequest(
        document_id="doc-20260404-a1b2c3d4",
        source_type="CISA",
        source_filename="advisory.txt",
        source_document_s3_key="documents/doc-20260404-a1b2c3d4/raw/advisory.txt",
        query_text="What are the critical vulnerabilities described?",
    )
    assert req.source_document_s3_key is not None
    assert req.query_text is not None


def test_retrieval_request_all_source_types_valid() -> None:
    for source_type in ("FDA", "CISA", "Incident", "Other"):
        req = RetrievalRequest(
            document_id="doc-20260404-a1b2c3d4",
            source_type=source_type,  # type: ignore[arg-type]
            source_filename="file.txt",
        )
        assert req.source_type == source_type


def test_retrieval_request_rejects_unknown_source_type() -> None:
    with pytest.raises(ValidationError):
        RetrievalRequest(
            document_id="doc-20260404-a1b2c3d4",
            source_type="UnknownOrg",  # type: ignore[arg-type]
            source_filename="file.txt",
        )


# ── FakeRetrievalProvider ──────────────────────────────────────────────────────

def test_fake_retrieval_returns_retrieval_result(minimal_request: RetrievalRequest) -> None:
    provider = FakeRetrievalProvider()
    result = provider.retrieve(minimal_request)
    assert isinstance(result, RetrievalResult)


def test_fake_retrieval_non_empty_case(minimal_request: RetrievalRequest) -> None:
    provider = FakeRetrievalProvider()
    result = provider.retrieve(minimal_request)
    assert result.retrieval_status == "success"
    assert result.retrieved_count > 0
    assert len(result.evidence_chunks) == result.retrieved_count


def test_fake_retrieval_empty_case(minimal_request: RetrievalRequest) -> None:
    provider = FakeRetrievalProvider(return_empty=True)
    result = provider.retrieve(minimal_request)
    assert result.retrieval_status == "empty"
    assert result.evidence_chunks == []
    assert result.retrieved_count == 0
    assert result.warning is not None


def test_fake_retrieval_preserves_document_id(minimal_request: RetrievalRequest) -> None:
    """document_id in the result must match the document_id in the request."""
    provider = FakeRetrievalProvider()
    result = provider.retrieve(minimal_request)
    assert result.document_id == minimal_request.document_id


def test_fake_retrieval_empty_preserves_document_id(minimal_request: RetrievalRequest) -> None:
    provider = FakeRetrievalProvider(return_empty=True)
    result = provider.retrieve(minimal_request)
    assert result.document_id == minimal_request.document_id


def test_fake_retrieval_satisfies_protocol() -> None:
    """FakeRetrievalProvider must satisfy the RetrievalProvider Protocol."""
    provider = FakeRetrievalProvider()
    assert isinstance(provider, RetrievalProvider)


def test_fake_retrieval_chunks_carry_valid_citation_fields(
    minimal_request: RetrievalRequest,
) -> None:
    """Every evidence chunk must have non-empty citation fields."""
    provider = FakeRetrievalProvider()
    result = provider.retrieve(minimal_request)
    for chunk in result.evidence_chunks:
        assert chunk.source_id, "source_id must not be empty"
        assert chunk.source_label, "source_label must not be empty"
        assert chunk.excerpt, "excerpt must not be empty"


# ── JSON serialization ─────────────────────────────────────────────────────────

def test_evidence_chunk_serializes_to_dict(valid_chunk: EvidenceChunk) -> None:
    data = valid_chunk.model_dump()
    assert isinstance(data, dict)
    assert data["chunk_id"] == valid_chunk.chunk_id
    assert data["relevance_score"] == valid_chunk.relevance_score
    assert data["excerpt"] == valid_chunk.excerpt


def test_retrieval_result_serializes_to_json(valid_chunk: EvidenceChunk) -> None:
    result = RetrievalResult(
        document_id="doc-20260404-a1b2c3d4",
        evidence_chunks=[valid_chunk],
        retrieval_status="success",
        retrieved_count=1,
    )
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == "doc-20260404-a1b2c3d4"
    assert len(parsed["evidence_chunks"]) == 1
    assert parsed["evidence_chunks"][0]["excerpt"] == valid_chunk.excerpt
    assert parsed["evidence_chunks"][0]["source_label"] == valid_chunk.source_label


def test_retrieval_request_serializes_cleanly(minimal_request: RetrievalRequest) -> None:
    data = minimal_request.model_dump()
    assert "document_id" in data
    assert "source_type" in data
    assert "query_text" in data
    assert "source_document_s3_key" in data
    # Optional fields must be present in the dict (even as None) for predictable serialization.
    assert data["query_text"] is None
    assert data["source_document_s3_key"] is None


def test_empty_retrieval_result_serializes_cleanly() -> None:
    result = RetrievalResult(
        document_id="doc-20260404-a1b2c3d4",
        evidence_chunks=[],
        retrieval_status="empty",
        retrieved_count=0,
        warning="No results.",
    )
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["evidence_chunks"] == []
    assert parsed["retrieved_count"] == 0
    assert parsed["warning"] == "No results."
