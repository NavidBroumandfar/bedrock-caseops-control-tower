"""
Analysis Agent — C-1.

Accepts retrieved evidence and produces a typed AnalysisOutput by delegating
to an injected AnalysisProvider.

Public surface:
  AnalysisAgent       — the agent class; callers use run()
  AnalysisAgentError  — raised when the agent cannot proceed (e.g. empty evidence)

Architecture contract:
  - Agents do not call AWS services directly
  - The provider dependency is injected at construction time
  - Empty evidence is an explicit, rejected case — the agent must not request
    analysis from a model when there is no grounded context to work from
"""

from app.schemas.analysis_contract import AnalysisProvider
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk


class AnalysisAgentError(Exception):
    """
    Raised when the AnalysisAgent cannot proceed.

    Distinct from BedrockServiceError (raised inside the service layer).
    This covers precondition failures in the agent layer — such as receiving
    an empty evidence list that would produce a groundless analysis.
    """


class AnalysisAgent:
    """
    Analysis Agent: consumes grounded retrieval evidence and produces AnalysisOutput.

    The agent is a thin orchestrator: it validates preconditions and delegates
    all model interaction to the injected AnalysisProvider.  It does not call
    boto3 or any AWS client directly.

    Usage:
        agent = AnalysisAgent(provider=BedrockAnalysisService())
        output = agent.run(document_id="doc-xxx", evidence_chunks=[...])

    The Supervisor is responsible for routing empty-retrieval cases to the
    escalation path before this agent is called.
    """

    def __init__(self, provider: AnalysisProvider) -> None:
        self._provider = provider

    def run(
        self,
        document_id: str,
        evidence_chunks: list[EvidenceChunk],
    ) -> AnalysisOutput:
        """
        Produce an AnalysisOutput from retrieved evidence chunks.

        Raises AnalysisAgentError when evidence_chunks is empty — analysis
        without grounded evidence is explicitly forbidden.  Provider-side
        failures propagate unchanged to the caller.
        """
        if not evidence_chunks:
            raise AnalysisAgentError(
                f"AnalysisAgent requires at least one evidence chunk to proceed; "
                f"received an empty list for document_id={document_id!r}. "
                "Route empty-retrieval cases to the escalation path before calling this agent."
            )

        return self._provider.analyze(document_id, evidence_chunks)
