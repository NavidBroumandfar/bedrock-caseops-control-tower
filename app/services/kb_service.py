"""
Bedrock Knowledge Base retrieval service — B-1.

Thin wrapper around the bedrock-agent-runtime boto3 client.
Implements the RetrievalProvider contract defined in B-0.

Public surface:
  BedrockKBService      — the service class; callers use retrieve()
  RetrievalServiceError — raised on any provider-side or config failure

Raw Bedrock response shapes are never exposed to callers.
All translation from the AWS response structure to EvidenceChunk happens
inside this module.
"""

import hashlib
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.retrieval_models import (
    EvidenceChunk,
    RetrievalRequest,
    RetrievalResult,
)

# Fallback when RETRIEVAL_MAX_RESULTS env var is absent.
_DEFAULT_MAX_RESULTS = 5

# Character limit for the citation-safe excerpt derived from chunk text.
_EXCERPT_MAX_CHARS = 200


class RetrievalServiceError(Exception):
    """Raised when the Bedrock KB Retrieve call cannot be completed."""


def _resolve_max_results(constructor_value: int | None, env_raw: str | None) -> int:
    """
    Resolve and validate the max_results configuration value.

    Constructor override takes precedence over the environment variable.
    Falls back to _DEFAULT_MAX_RESULTS when neither is supplied.
    Raises RetrievalServiceError (not ValueError / TypeError) so callers
    see a consistent error type regardless of which config path failed.
    """
    if constructor_value is not None:
        if not isinstance(constructor_value, int) or constructor_value < 1:
            raise RetrievalServiceError(
                f"max_results must be a positive integer, got: {constructor_value!r}"
            )
        return constructor_value

    if env_raw is not None:
        try:
            parsed = int(env_raw)
        except (ValueError, TypeError) as exc:
            raise RetrievalServiceError(
                f"RETRIEVAL_MAX_RESULTS must be a positive integer, "
                f"got: {env_raw!r}"
            ) from exc
        if parsed < 1:
            raise RetrievalServiceError(
                f"RETRIEVAL_MAX_RESULTS must be a positive integer, "
                f"got: {parsed!r}"
            )
        return parsed

    return _DEFAULT_MAX_RESULTS


class BedrockKBService:
    """
    Retrieval service backed by Amazon Bedrock Knowledge Bases.

    Satisfies the RetrievalProvider protocol — callers interact only through
    retrieve(request) → RetrievalResult.

    All configuration is read from environment variables at instantiation time.
    Explicit constructor overrides are accepted so the service remains
    testable without live AWS credentials.

    Required configuration:
      BEDROCK_KB_ID           — Bedrock Knowledge Base identifier
      AWS_REGION              — AWS region (default: us-east-1)
      RETRIEVAL_MAX_RESULTS   — maximum chunks to fetch (default: 5)
    """

    def __init__(
        self,
        *,
        kb_id: str | None = None,
        region: str | None = None,
        max_results: int | None = None,
        client: Any = None,
    ) -> None:
        resolved_kb_id = kb_id or os.getenv("BEDROCK_KB_ID", "")
        if not resolved_kb_id:
            raise RetrievalServiceError(
                "BEDROCK_KB_ID must be set via environment variable or "
                "the kb_id constructor argument."
            )
        self._kb_id = resolved_kb_id
        self._max_results = _resolve_max_results(
            max_results,
            os.getenv("RETRIEVAL_MAX_RESULTS"),
        )
        self._client = client or boto3.client(
            "bedrock-agent-runtime",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )

    # ── public interface ───────────────────────────────────────────────────────

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """
        Query the Bedrock Knowledge Base and return typed evidence chunks.

        Returns RetrievalResult with status "empty" when the KB yields no
        results — never raises on an empty set.
        Raises RetrievalServiceError on any provider-side failure.
        """
        query = self._build_query(request)
        raw_results = self._call_kb(query)
        try:
            chunks = [
                _map_result_to_chunk(item, idx)
                for idx, item in enumerate(raw_results)
            ]
        except Exception as exc:
            raise RetrievalServiceError(
                f"Failed to map Bedrock KB response to evidence chunks: {exc}"
            ) from exc

        if not chunks:
            return RetrievalResult(
                document_id=request.document_id,
                evidence_chunks=[],
                retrieval_status="empty",
                retrieved_count=0,
                warning="Bedrock KB returned no results for this query.",
            )

        return RetrievalResult(
            document_id=request.document_id,
            evidence_chunks=chunks,
            retrieval_status="success",
            retrieved_count=len(chunks),
        )

    # ── private helpers ────────────────────────────────────────────────────────

    def _build_query(self, request: RetrievalRequest) -> str:
        """
        Derive a query string from the request.

        Uses request.query_text when present; otherwise falls back to a
        deterministic string built from stable request fields so retrieval
        can always proceed without requiring an explicit query.
        """
        if request.query_text:
            return request.query_text
        return f"{request.source_type} document: {request.source_filename}"

    def _call_kb(self, query: str) -> list[dict]:
        """
        Invoke the Bedrock Knowledge Base Retrieve API and return raw items.

        Raises RetrievalServiceError on any SDK-level failure so boto3
        exceptions never propagate to callers.
        """
        try:
            response = self._client.retrieve(
                knowledgeBaseId=self._kb_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": self._max_results,
                    }
                },
            )
            return response.get("retrievalResults", [])
        except (BotoCoreError, ClientError) as exc:
            raise RetrievalServiceError(
                f"Bedrock KB Retrieve API call failed: {exc}"
            ) from exc


# ── response mapping ───────────────────────────────────────────────────────────
#
# These functions translate the bedrock-agent-runtime response shape into the
# B-0 EvidenceChunk contract.  All provider-specific field names are contained
# here and nowhere else in the codebase.
#
# Relevant Bedrock response fields (per retrievalResults item):
#   item["content"]["text"]                  — retrieved passage text
#   item["location"]["type"]                 — S3 | WEB | CUSTOM | …
#   item["location"]["s3Location"]["uri"]    — s3://bucket/key  (when type=S3)
#   item["score"]                            — relevance score  (0.0 – 1.0)


def _map_result_to_chunk(item: dict, index: int) -> EvidenceChunk:
    """Translate one Bedrock retrievalResult entry into an EvidenceChunk."""
    text = item.get("content", {}).get("text", "")
    source_id = _extract_source_id(item)
    return EvidenceChunk(
        chunk_id=_make_chunk_id(source_id, index),
        text=text,
        source_id=source_id,
        source_label=_derive_source_label(source_id),
        excerpt=_make_excerpt(text),
        relevance_score=float(item.get("score", 0.0)),
    )


def _extract_source_id(item: dict) -> str:
    """
    Return a stable identifier for the chunk's source location.

    S3 locations use the full S3 URI as the identifier since it is unique,
    stable, and directly auditable.  Other location types fall back to the
    type name so the field is always non-empty.
    """
    location = item.get("location", {})
    loc_type = location.get("type", "")
    if loc_type == "S3":
        return location.get("s3Location", {}).get("uri", "")
    return loc_type.lower() if loc_type else "unknown"


def _derive_source_label(source_id: str) -> str:
    """
    Produce a human-readable label from a source identifier.

    For S3 URIs (s3://bucket/prefix/filename.txt) returns the filename
    component, which is short enough to surface in citations.
    Falls back to the raw source_id for non-S3 identifiers.
    """
    if source_id.startswith("s3://"):
        filename = source_id.rstrip("/").split("/")[-1]
        return filename if filename else source_id
    return source_id


def _make_chunk_id(source_id: str, index: int) -> str:
    """
    Generate a short, deterministic chunk_id for an evidence chunk.

    The ID is stable for a given source_id + positional index pair, so
    repeated calls with the same KB response produce the same chunk IDs.
    """
    raw = f"{source_id}::{index}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"chunk-{digest}"


def _make_excerpt(text: str, max_chars: int = _EXCERPT_MAX_CHARS) -> str:
    """
    Derive a citation-safe excerpt from the full chunk text.

    Clips at the last word boundary before max_chars to avoid mid-word
    truncation, then appends an ellipsis if the text was shortened.
    """
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    last_space = clipped.rfind(" ")
    if last_space > 0:
        clipped = clipped[:last_space]
    return f"{clipped}..."


# Enforce protocol satisfaction at import time.
# A failure here means BedrockKBService has drifted from the RetrievalProvider contract.
assert isinstance(BedrockKBService.__new__(BedrockKBService), RetrievalProvider), (
    "BedrockKBService does not satisfy the RetrievalProvider protocol"
)
