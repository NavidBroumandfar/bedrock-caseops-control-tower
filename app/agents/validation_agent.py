"""
Validation / Critic Agent — C-2.

Audits an AnalysisOutput against the original EvidenceChunks and produces a typed
ValidationOutput by delegating to an injected ValidationProvider.

Public surface:
  ValidationAgent       — the agent class; callers use run()
  ValidationAgentError  — raised when the agent cannot proceed (precondition failure)

Architecture contract:
  - Agents do not call AWS services directly
  - The provider dependency is injected at construction time
  - Empty evidence is an explicit case: the agent handles it conservatively without
    calling the provider — no groundless validation is performed, and no fake clean
    pass is returned
"""

from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk
from app.schemas.validation_contract import ValidationProvider
from app.schemas.validation_models import ValidationOutput


class ValidationAgentError(Exception):
    """
    Raised when the ValidationAgent cannot proceed due to a precondition failure.

    Distinct from BedrockServiceError (raised inside the service layer).
    """


class ValidationAgent:
    """
    Validation / Critic Agent: audits an AnalysisOutput against retrieved evidence.

    The agent is a thin orchestrator: it guards against empty evidence and delegates
    all model interaction to the injected ValidationProvider.  It does not call
    boto3 or any AWS client directly.

    Usage:
        agent = ValidationAgent(provider=BedrockValidationService())
        result = agent.run(
            document_id="doc-xxx",
            analysis_output=analysis_output,
            evidence_chunks=[...],
        )

    Empty evidence handling:
        When evidence_chunks is empty the agent returns a conservative fail result
        without calling the provider.  All claims are unverifiable by definition when
        there is no evidence to audit against.  The Supervisor is responsible for
        routing empty-retrieval cases before invoking this agent, but the agent must
        not silently pass a groundless analysis.
    """

    def __init__(self, provider: ValidationProvider) -> None:
        self._provider = provider

    def run(
        self,
        document_id: str,
        analysis_output: AnalysisOutput,
        evidence_chunks: list[EvidenceChunk],
    ) -> ValidationOutput:
        """
        Audit analysis_output against evidence_chunks and return a typed ValidationOutput.

        If evidence_chunks is empty the agent returns a conservative fail result
        immediately — the provider is never called with no evidence to validate against.
        Provider-side failures propagate unchanged to the caller.
        """
        if not evidence_chunks:
            # Conservative: no evidence means every claim is unverifiable.
            # Return a typed fail result rather than raising, so the Supervisor
            # can route based on ValidationOutput fields (confidence, status) as normal.
            return ValidationOutput(
                document_id=document_id,
                confidence_score=0.0,
                unsupported_claims=[
                    "No evidence chunks provided — all claims are unverifiable."
                ],
                validation_status="fail",
                warning=(
                    "Validation skipped model call: no evidence chunks were available. "
                    "All analysis claims are treated as unsupported."
                ),
            )

        return self._provider.validate(document_id, analysis_output, evidence_chunks)
