"""
Validation contract — the narrow interface that all validation implementations must satisfy.

ValidationProvider is a structural Protocol: any class with a matching validate()
method satisfies the contract without inheriting from this class.

C-2 implements this protocol via BedrockValidationService in app/services/bedrock_service.py.
Test code may use a MagicMock or a purpose-built fake that matches this signature.
"""

from typing import Protocol, runtime_checkable

from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk
from app.schemas.validation_models import ValidationOutput


@runtime_checkable
class ValidationProvider(Protocol):
    """
    Contract for all validation implementations.

    Input:  document_id (str) + analysis_output (AnalysisOutput) + evidence_chunks (list[EvidenceChunk])
    Output: ValidationOutput

    Implementations must raise on unrecoverable failure — returning a partial or fabricated
    result is not acceptable.  The ValidationAgent owns the empty-evidence guard; the provider
    may assume it is called with a meaningful evidence list.
    """

    def validate(
        self,
        document_id: str,
        analysis_output: AnalysisOutput,
        evidence_chunks: list[EvidenceChunk],
    ) -> ValidationOutput:
        ...
