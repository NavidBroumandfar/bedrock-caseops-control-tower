"""
C-1 unit tests — Analysis Agent.

Coverage:
  - AnalysisAgent: valid evidence → delegates to provider, returns AnalysisOutput
  - AnalysisAgent: empty evidence list → raises AnalysisAgentError without calling provider
  - AnalysisAgent: empty evidence error message includes document_id
  - AnalysisAgent: does not import or use boto3 directly
  - AnalysisAgent: forwards document_id to provider unchanged
  - AnalysisAgent: forwards evidence_chunks to provider unchanged
  - AnalysisAgent: provider called exactly once per run()
  - AnalysisAgent: single evidence chunk is valid input
  - AnalysisAgent: provider-side BedrockServiceError propagates unchanged
  - AnalysisAgent: returned AnalysisOutput has all required fields
  - AnalysisAgent: returned AnalysisOutput document_id matches caller input
  - Contract: any AnalysisProvider-compatible object accepted as dependency

No AWS credentials or live calls required.
The provider dependency is replaced by a MagicMock in all tests.
"""

import pytest
from unittest.mock import MagicMock

from app.agents.analysis_agent import AnalysisAgent, AnalysisAgentError
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk
from app.services.bedrock_service import BedrockServiceError

# ── shared helpers ──────────────────────────────────────────────────────────────


def _make_analysis_output(document_id: str = "doc-20260404-a1b2c3d4") -> AnalysisOutput:
    return AnalysisOutput(
        document_id=document_id,
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to establish adequate written procedures for equipment cleaning.",
        recommendations=["Initiate CAPA.", "Notify compliance team."],
    )


def _make_chunks(count: int = 2) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id=f"chunk-00{i + 1}",
            text=f"Evidence passage {i + 1}.",
            source_id=f"s3://kb/doc{i + 1}.txt",
            source_label=f"Source Doc {i + 1}",
            excerpt=f"Evidence passage {i + 1}.",
            relevance_score=round(0.9 - i * 0.1, 2),
        )
        for i in range(count)
    ]


_DOC_ID = "doc-20260404-a1b2c3d4"


@pytest.fixture()
def fake_provider() -> MagicMock:
    """MagicMock provider that returns a valid AnalysisOutput."""
    provider = MagicMock()
    provider.analyze.return_value = _make_analysis_output(_DOC_ID)
    return provider


@pytest.fixture()
def agent(fake_provider: MagicMock) -> AnalysisAgent:
    return AnalysisAgent(provider=fake_provider)


# ── successful run ──────────────────────────────────────────────────────────────


def test_run_returns_analysis_output(agent: AnalysisAgent) -> None:
    result = agent.run(_DOC_ID, _make_chunks())
    assert isinstance(result, AnalysisOutput)


def test_run_output_document_id_matches(agent: AnalysisAgent) -> None:
    result = agent.run(_DOC_ID, _make_chunks())
    assert result.document_id == _DOC_ID


def test_run_output_has_all_required_fields(agent: AnalysisAgent) -> None:
    result = agent.run(_DOC_ID, _make_chunks())
    assert hasattr(result, "document_id")
    assert hasattr(result, "severity")
    assert hasattr(result, "category")
    assert hasattr(result, "summary")
    assert hasattr(result, "recommendations")


def test_run_output_severity_is_valid(agent: AnalysisAgent) -> None:
    result = agent.run(_DOC_ID, _make_chunks())
    assert result.severity in ("Critical", "High", "Medium", "Low")


def test_run_with_single_chunk_is_valid(agent: AnalysisAgent) -> None:
    """A single evidence chunk is a valid (non-empty) input."""
    result = agent.run(_DOC_ID, _make_chunks(count=1))
    assert isinstance(result, AnalysisOutput)


# ── provider delegation ─────────────────────────────────────────────────────────


def test_run_delegates_to_provider_once(agent: AnalysisAgent, fake_provider: MagicMock) -> None:
    """The agent must call provider.analyze exactly once per run()."""
    chunks = _make_chunks()
    agent.run(_DOC_ID, chunks)
    fake_provider.analyze.assert_called_once()


def test_run_forwards_document_id_to_provider(
    agent: AnalysisAgent, fake_provider: MagicMock
) -> None:
    chunks = _make_chunks()
    agent.run(_DOC_ID, chunks)
    call_args = fake_provider.analyze.call_args
    assert call_args.args[0] == _DOC_ID


def test_run_forwards_evidence_chunks_to_provider(
    agent: AnalysisAgent, fake_provider: MagicMock
) -> None:
    chunks = _make_chunks(count=3)
    agent.run(_DOC_ID, chunks)
    call_args = fake_provider.analyze.call_args
    assert call_args.args[1] == chunks


def test_run_delegates_with_correct_positional_args(
    agent: AnalysisAgent, fake_provider: MagicMock
) -> None:
    chunks = _make_chunks()
    agent.run(_DOC_ID, chunks)
    fake_provider.analyze.assert_called_once_with(_DOC_ID, chunks)


# ── empty evidence guard ─────────────────────────────────────────────────────────


def test_empty_evidence_raises_analysis_agent_error(agent: AnalysisAgent) -> None:
    with pytest.raises(AnalysisAgentError):
        agent.run(_DOC_ID, [])


def test_empty_evidence_does_not_call_provider(
    agent: AnalysisAgent, fake_provider: MagicMock
) -> None:
    """Provider must not be called when evidence is empty — no groundless analysis."""
    with pytest.raises(AnalysisAgentError):
        agent.run(_DOC_ID, [])
    fake_provider.analyze.assert_not_called()


def test_empty_evidence_error_includes_document_id() -> None:
    """The error message must identify which document was being processed."""
    provider = MagicMock()
    agent = AnalysisAgent(provider=provider)
    with pytest.raises(AnalysisAgentError, match=_DOC_ID):
        agent.run(_DOC_ID, [])


# ── no direct AWS calls ─────────────────────────────────────────────────────────


def test_agent_module_does_not_import_boto3() -> None:
    """
    The analysis agent module must not import boto3.
    All AWS interaction belongs in the service layer — agents are AWS-free.
    """
    import app.agents.analysis_agent as agent_module
    assert not hasattr(agent_module, "boto3"), (
        "AnalysisAgent must not import boto3 directly; "
        "AWS interaction belongs in app/services/."
    )


# ── provider error propagation ──────────────────────────────────────────────────


def test_provider_bedrock_service_error_propagates_unchanged() -> None:
    provider = MagicMock()
    provider.analyze.side_effect = BedrockServiceError("Model call failed")
    agent = AnalysisAgent(provider=provider)
    with pytest.raises(BedrockServiceError, match="Model call failed"):
        agent.run(_DOC_ID, _make_chunks())


def test_provider_generic_error_propagates_unchanged() -> None:
    provider = MagicMock()
    provider.analyze.side_effect = RuntimeError("Unexpected provider failure")
    agent = AnalysisAgent(provider=provider)
    with pytest.raises(RuntimeError, match="Unexpected provider failure"):
        agent.run(_DOC_ID, _make_chunks())


# ── contract: any AnalysisProvider-compatible object is accepted ────────────────


def test_agent_accepts_any_provider_compatible_object() -> None:
    """
    AnalysisAgent accepts any object with a matching analyze() signature,
    not just BedrockAnalysisService — the protocol is structural.
    """
    class InlineProvider:
        def analyze(self, document_id: str, evidence_chunks: list) -> AnalysisOutput:
            return _make_analysis_output(document_id)

    agent = AnalysisAgent(provider=InlineProvider())
    result = agent.run(_DOC_ID, _make_chunks())
    assert isinstance(result, AnalysisOutput)
    assert result.document_id == _DOC_ID
