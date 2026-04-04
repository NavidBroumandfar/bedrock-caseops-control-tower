"""
Retrieval contract — the narrow interface that all retrieval implementations must satisfy.

RetrievalProvider is a structural Protocol: any class with a matching retrieve()
method satisfies the contract without inheriting from this class.

B-1 will implement this protocol against Amazon Bedrock Knowledge Bases.
Test code may use FakeRetrievalProvider from tests/fakes/fake_retrieval.py.
"""

from typing import Protocol, runtime_checkable

from app.schemas.retrieval_models import RetrievalRequest, RetrievalResult


@runtime_checkable
class RetrievalProvider(Protocol):
    """
    Contract for all retrieval implementations.

    Input:  RetrievalRequest  — document context and optional explicit query.
    Output: RetrievalResult   — evidence chunks, status, and count.

    Implementations must not raise on empty results; return status="empty"
    with an empty evidence_chunks list instead.
    """

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        ...
