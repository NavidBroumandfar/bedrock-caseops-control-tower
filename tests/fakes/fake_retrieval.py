"""
Deterministic fake retrieval implementation — for tests only.

FakeRetrievalProvider satisfies the RetrievalProvider contract with
predictable, in-memory results. It makes no AWS calls and has no
external dependencies.

Use in tests to verify contract behavior without a live Knowledge Base.
B-1 will replace this with a real Bedrock Knowledge Base implementation.
"""

from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.retrieval_models import (
    EvidenceChunk,
    RetrievalRequest,
    RetrievalResult,
)

# Pre-built chunks derived from public FDA sample data.
# Source references are illustrative — no real KB is contacted.
_SAMPLE_CHUNKS: list[EvidenceChunk] = [
    EvidenceChunk(
        chunk_id="chunk-001",
        text=(
            "The facility failed to establish adequate written procedures for "
            "equipment cleaning and maintenance as required by 21 CFR 211.67."
        ),
        source_id="s3://caseops-kb/fda/warning-letter-2024-wl-0032.txt::chunk-001",
        source_label="FDA Warning Letter 2024-WL-0032",
        excerpt="...no written procedures for equipment cleaning...",
        relevance_score=0.91,
    ),
    EvidenceChunk(
        chunk_id="chunk-002",
        text=(
            "Inspectors observed that batch records were incomplete and lacked "
            "critical in-process controls documentation."
        ),
        source_id="s3://caseops-kb/fda/warning-letter-2024-wl-0032.txt::chunk-002",
        source_label="FDA Warning Letter 2024-WL-0032",
        excerpt="...batch records were incomplete...",
        relevance_score=0.78,
    ),
]


class FakeRetrievalProvider:
    """
    Deterministic fake that satisfies RetrievalProvider.

    Behavior is controlled by the return_empty constructor flag:
      return_empty=False (default) — returns two pre-built EvidenceChunks
      return_empty=True            — returns an empty result with a warning
    """

    def __init__(self, *, return_empty: bool = False) -> None:
        self._return_empty = return_empty

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if self._return_empty:
            return RetrievalResult(
                document_id=request.document_id,
                evidence_chunks=[],
                retrieval_status="empty",
                retrieved_count=0,
                warning="No matching chunks found in the knowledge base.",
            )

        chunks = list(_SAMPLE_CHUNKS)
        return RetrievalResult(
            document_id=request.document_id,
            evidence_chunks=chunks,
            retrieval_status="success",
            retrieved_count=len(chunks),
            warning=None,
        )


# Enforce that FakeRetrievalProvider satisfies the Protocol at import time.
# A TypeError here means the fake has drifted from the contract.
assert isinstance(FakeRetrievalProvider(), RetrievalProvider), (
    "FakeRetrievalProvider does not satisfy the RetrievalProvider protocol"
)
