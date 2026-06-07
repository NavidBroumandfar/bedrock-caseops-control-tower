"""
D-1 unit tests — output schemas (Citation and CaseOutput).

Coverage:

  Citation:
  - valid Citation constructs without error
  - fields are preserved as supplied
  - non-finite relevance_score raises ValueError (NaN)
  - non-finite relevance_score raises ValueError (inf)

  CaseOutput:
  - valid CaseOutput constructs without error
  - all required fields are present
  - confidence_score below 0.0 raises ValidationError
  - confidence_score above 1.0 raises ValidationError
  - non-finite confidence_score raises ValidationError (NaN)
  - non-finite confidence_score raises ValidationError (inf)
  - CaseOutput serializes to JSON cleanly
  - serialized JSON contains all expected top-level keys
  - citations list may be empty (empty-retrieval path)
  - escalation_reason may be None when escalation_required is False
  - CaseOutput with citations round-trips through JSON
  - session_id defaults to None when not supplied (D-1 optional)
  - session_id serializes correctly when explicitly set
  - no boto3 import in output_models module

No AWS credentials or live calls are required.
"""

import json
import math

import pytest
from pydantic import ValidationError

from app.schemas.analysis_models import GroundedClaim
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.validation_models import ClaimValidation


# ── shared builders ───────────────────────────────────────────────────────────


def _make_citation(**overrides) -> Citation:
    defaults = {
        "chunk_id": "chunk-001",
        "source_id": "s3://caseops-kb/fda/doc.txt::chunk-001",
        "source_label": "FDA Warning Letter 2024-WL-0032",
        "excerpt": "...no written procedures for equipment cleaning...",
        "relevance_score": 0.91,
    }
    return Citation(**(defaults | overrides))


def _make_grounded_claim(**overrides) -> GroundedClaim:
    defaults = {
        "claim_id": "finding-1",
        "claim_type": "finding",
        "text": "Facility failed to maintain written procedures for equipment cleaning.",
        "supporting_chunk_ids": ["chunk-001"],
    }
    return GroundedClaim(**(defaults | overrides))


def _make_claim_validation(**overrides) -> ClaimValidation:
    defaults = {
        "claim_id": "finding-1",
        "supported": True,
        "supporting_chunk_ids": ["chunk-001"],
        "unsupported_reason": None,
    }
    return ClaimValidation(**(defaults | overrides))


def _make_case_output(**overrides) -> CaseOutput:
    defaults = {
        "document_id": "doc-20260404-a1b2c3d4",
        "source_filename": "warning_letter.txt",
        "source_type": "FDA",
        "severity": "High",
        "category": "Regulatory / Manufacturing Deficiency",
        "summary": "Facility failed to maintain written procedures for equipment cleaning.",
        "recommendations": [
            "Initiate CAPA for cleaning validation gaps.",
            "Notify compliance team within 48 hours.",
        ],
        "grounded_claims": [_make_grounded_claim()],
        "citations": [_make_citation()],
        "confidence_score": 0.87,
        "unsupported_claims": [],
        "claim_validations": [_make_claim_validation()],
        "escalation_required": False,
        "escalation_reason": None,
        "validated_by": "tool-executor-agent-v1",
        "timestamp": "2026-04-05T12:00:00+00:00",
    }
    return CaseOutput(**(defaults | overrides))


# ── Citation: valid construction ──────────────────────────────────────────────


def test_citation_valid_construction() -> None:
    citation = _make_citation()
    assert isinstance(citation, Citation)


def test_citation_fields_preserved() -> None:
    citation = _make_citation()
    assert citation.chunk_id == "chunk-001"
    assert citation.source_id == "s3://caseops-kb/fda/doc.txt::chunk-001"
    assert citation.source_label == "FDA Warning Letter 2024-WL-0032"
    assert citation.excerpt == "...no written procedures for equipment cleaning..."
    assert citation.relevance_score == 0.91


def test_citation_nan_relevance_score_raises() -> None:
    with pytest.raises(ValidationError, match="relevance_score"):
        _make_citation(relevance_score=math.nan)


def test_citation_inf_relevance_score_raises() -> None:
    with pytest.raises(ValidationError, match="relevance_score"):
        _make_citation(relevance_score=math.inf)


# ── CaseOutput: valid construction ────────────────────────────────────────────


def test_case_output_valid_construction() -> None:
    output = _make_case_output()
    assert isinstance(output, CaseOutput)


def test_case_output_all_required_fields_present() -> None:
    output = _make_case_output()
    assert output.document_id == "doc-20260404-a1b2c3d4"
    assert output.source_filename == "warning_letter.txt"
    assert output.source_type == "FDA"
    assert output.severity == "High"
    assert output.category == "Regulatory / Manufacturing Deficiency"
    assert isinstance(output.summary, str)
    assert isinstance(output.recommendations, list)
    assert isinstance(output.grounded_claims, list)
    assert isinstance(output.citations, list)
    assert isinstance(output.confidence_score, float)
    assert isinstance(output.unsupported_claims, list)
    assert isinstance(output.claim_validations, list)
    assert isinstance(output.escalation_required, bool)
    assert isinstance(output.validated_by, str)
    assert isinstance(output.timestamp, str)


# ── CaseOutput: confidence_score validation ───────────────────────────────────


def test_case_output_confidence_below_zero_raises() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        _make_case_output(confidence_score=-0.01)


def test_case_output_confidence_above_one_raises() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        _make_case_output(confidence_score=1.01)


def test_case_output_confidence_exactly_zero_is_valid() -> None:
    output = _make_case_output(confidence_score=0.0)
    assert output.confidence_score == 0.0


def test_case_output_confidence_exactly_one_is_valid() -> None:
    output = _make_case_output(confidence_score=1.0)
    assert output.confidence_score == 1.0


def test_case_output_nan_confidence_raises() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        _make_case_output(confidence_score=math.nan)


def test_case_output_inf_confidence_raises() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        _make_case_output(confidence_score=math.inf)


# ── CaseOutput: escalation fields ────────────────────────────────────────────


def test_case_output_escalation_reason_may_be_none() -> None:
    output = _make_case_output(escalation_required=False, escalation_reason=None)
    assert output.escalation_reason is None


def test_case_output_escalation_reason_may_have_value() -> None:
    output = _make_case_output(
        escalation_required=True,
        escalation_reason="severity is Critical",
    )
    assert output.escalation_reason == "severity is Critical"


# ── CaseOutput: citations ─────────────────────────────────────────────────────


def test_case_output_citations_may_be_empty() -> None:
    """Empty citations list is valid on the empty-retrieval path."""
    output = _make_case_output(citations=[])
    assert output.citations == []


def test_case_output_citations_are_typed() -> None:
    citation = _make_citation()
    output = _make_case_output(citations=[citation])
    assert len(output.citations) == 1
    assert isinstance(output.citations[0], Citation)


def test_case_output_grounded_claims_default_to_empty() -> None:
    output = _make_case_output(grounded_claims=[])
    assert output.grounded_claims == []


def test_case_output_grounded_claims_are_typed() -> None:
    claim = _make_grounded_claim()
    output = _make_case_output(grounded_claims=[claim])
    assert len(output.grounded_claims) == 1
    assert isinstance(output.grounded_claims[0], GroundedClaim)


def test_case_output_claim_validations_default_to_empty() -> None:
    output = _make_case_output(claim_validations=[])
    assert output.claim_validations == []


def test_case_output_claim_validations_are_typed() -> None:
    claim_validation = _make_claim_validation()
    output = _make_case_output(claim_validations=[claim_validation])
    assert len(output.claim_validations) == 1
    assert isinstance(output.claim_validations[0], ClaimValidation)


# ── CaseOutput: JSON serialization ───────────────────────────────────────────


def test_case_output_serializes_to_json() -> None:
    output = _make_case_output()
    raw = output.model_dump_json()
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


def test_case_output_serialized_json_has_expected_keys() -> None:
    output = _make_case_output()
    parsed = json.loads(output.model_dump_json())
    expected_keys = {
        "document_id",
        "source_filename",
        "source_type",
        "severity",
        "category",
        "summary",
        "recommendations",
        "grounded_claims",
        "citations",
        "confidence_score",
        "unsupported_claims",
        "claim_validations",
        "escalation_required",
        "escalation_reason",
        "validated_by",
        "timestamp",
    }
    assert expected_keys.issubset(set(parsed.keys()))


def test_case_output_round_trips_through_json() -> None:
    citation = _make_citation()
    original = _make_case_output(
        citations=[citation],
        escalation_required=True,
        escalation_reason="severity is Critical",
    )
    parsed = json.loads(original.model_dump_json())
    assert parsed["document_id"] == original.document_id
    assert parsed["severity"] == original.severity
    assert parsed["escalation_required"] is True
    assert parsed["escalation_reason"] == "severity is Critical"
    assert parsed["grounded_claims"][0]["supporting_chunk_ids"] == ["chunk-001"]
    assert parsed["claim_validations"][0]["claim_id"] == "finding-1"
    assert len(parsed["citations"]) == 1
    assert parsed["citations"][0]["chunk_id"] == citation.chunk_id
    assert parsed["citations"][0]["source_id"] == citation.source_id


# ── CaseOutput: session_id (optional in D-1) ─────────────────────────────────


def test_case_output_session_id_defaults_to_none() -> None:
    """session_id is optional in D-1; omitting it must not raise."""
    output = _make_case_output()
    assert output.session_id is None


def test_case_output_session_id_accepted_when_supplied() -> None:
    output = _make_case_output(session_id="sess-abc12345")
    assert output.session_id == "sess-abc12345"
    parsed = json.loads(output.model_dump_json())
    assert parsed["session_id"] == "sess-abc12345"


# ── no boto3 ─────────────────────────────────────────────────────────────────


def test_output_models_does_not_import_boto3() -> None:
    import app.schemas.output_models as module

    assert not hasattr(module, "boto3"), (
        "output_models must not import boto3; schemas are pure data contracts."
    )
