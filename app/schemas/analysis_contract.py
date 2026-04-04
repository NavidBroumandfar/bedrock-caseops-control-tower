"""
Analysis contract — the narrow interface that all analysis implementations must satisfy.

AnalysisProvider is a structural Protocol: any class with a matching analyze()
method satisfies the contract without inheriting from this class.

C-1 implements this protocol via BedrockAnalysisService in app/services/bedrock_service.py.
Test code may use a MagicMock or a purpose-built fake that matches this signature.
"""

from typing import Protocol, runtime_checkable

from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk


@runtime_checkable
class AnalysisProvider(Protocol):
    """
    Contract for all analysis implementations.

    Input:  document_id (str) + evidence_chunks (list[EvidenceChunk])
    Output: AnalysisOutput

    Implementations must raise on failure — returning a partial or empty result
    is not acceptable.  The AnalysisAgent owns the empty-evidence guard; the
    provider may assume it receives at least one chunk.
    """

    def analyze(
        self,
        document_id: str,
        evidence_chunks: list[EvidenceChunk],
    ) -> AnalysisOutput:
        ...
