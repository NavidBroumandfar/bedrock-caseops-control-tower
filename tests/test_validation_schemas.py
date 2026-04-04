"""
C-2 unit tests — validation output schemas.

Coverage:
  - ValidationOutput: valid construction with all three validation statuses
  - ValidationOutput: all required fields accessible on a valid instance
  - ValidationOutput: warning field is optional and defaults to None
  - ValidationOutput: non-None warning is preserved
  - ValidationStatus: "pass", "warning", "fail" accepted
  - ValidationStatus: any value outside the three literals rejected
  - confidence_score: 0.0 accepted (lower bound)
  - confidence_score: 1.0 accepted (upper bound)
  - confidence_score: 0.5 accepted (midpoint)
  - confidence_score: below 0.0 rejected
  - confidence_score: above 1.0 rejected
  - confidence_score: NaN rejected
  - confidence_score: infinity rejected
  - unsupported_claims: empty list is valid
  - unsupported_claims: non-empty list is valid and preserved
  - JSON serialization: model_dump returns a plain dict with expected keys
  - JSON serialization: model_dump_json round-trips through json.loads cleanly
  - JSON serialization: validation_status serializes as a plain string
  - CaseOutput shape compatibility: ValidationOutput fields are a subset of the
    planned CaseOutput shape (document_id, confidence_score, unsupported_claims)

No AWS credentials or live calls required.
No mocks needed.
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.validation_models import ValidationOutput, ValidationStatus


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def valid_output() -> ValidationOutput:
    return ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.87,
        unsupported_claims=[],
        validation_status="pass",
    )


# ── valid construction ─────────────────────────────────────────────────────────


def test_validation_output_valid_construction(valid_output: ValidationOutput) -> None:
    assert valid_output.document_id == "doc-20260404-a1b2c3d4"
    assert valid_output.confidence_score == pytest.approx(0.87)
    assert valid_output.unsupported_claims == []
    assert valid_output.validation_status == "pass"
    assert valid_output.warning is None


def test_validation_output_all_required_fields_present(valid_output: ValidationOutput) -> None:
    assert hasattr(valid_output, "document_id")
    assert hasattr(valid_output, "confidence_score")
    assert hasattr(valid_output, "unsupported_claims")
    assert hasattr(valid_output, "validation_status")
    assert hasattr(valid_output, "warning")


def test_warning_defaults_to_none(valid_output: ValidationOutput) -> None:
    assert valid_output.warning is None


def test_warning_preserved_when_provided() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.5,
        unsupported_claims=["Claim lacks direct citation."],
        validation_status="warning",
        warning="Partial evidence support detected.",
    )
    assert output.warning == "Partial evidence support detected."


# ── ValidationStatus: accepted values ────────────────────────────────────────


@pytest.mark.parametrize("status", ["pass", "warning", "fail"])
def test_validation_status_accepts_all_valid_values(status: str) -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.7,
        unsupported_claims=[],
        validation_status=status,  # type: ignore[arg-type]
    )
    assert output.validation_status == status


# ── ValidationStatus: rejected values ────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_status",
    [
        "Pass",         # wrong case
        "PASS",         # all-caps
        "passed",       # wrong word
        "unclear",      # invented value
        "",             # empty
        "None",         # string null
        "ok",           # informal synonym
    ],
)
def test_validation_status_rejects_invalid_values(bad_status: str) -> None:
    with pytest.raises(ValidationError):
        ValidationOutput(
            document_id="doc-20260404-a1b2c3d4",
            confidence_score=0.7,
            unsupported_claims=[],
            validation_status=bad_status,  # type: ignore[arg-type]
        )


# ── confidence_score bounds ────────────────────────────────────────────────────


def test_confidence_score_zero_accepted() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.0,
        unsupported_claims=["All claims lack evidence support."],
        validation_status="fail",
    )
    assert output.confidence_score == pytest.approx(0.0)


def test_confidence_score_one_accepted() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=1.0,
        unsupported_claims=[],
        validation_status="pass",
    )
    assert output.confidence_score == pytest.approx(1.0)


def test_confidence_score_midpoint_accepted() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.5,
        unsupported_claims=[],
        validation_status="warning",
    )
    assert output.confidence_score == pytest.approx(0.5)


def test_confidence_score_below_zero_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        ValidationOutput(
            document_id="doc-20260404-a1b2c3d4",
            confidence_score=-0.01,
            unsupported_claims=[],
            validation_status="fail",
        )


def test_confidence_score_above_one_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        ValidationOutput(
            document_id="doc-20260404-a1b2c3d4",
            confidence_score=1.01,
            unsupported_claims=[],
            validation_status="pass",
        )


def test_confidence_score_nan_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        ValidationOutput(
            document_id="doc-20260404-a1b2c3d4",
            confidence_score=float("nan"),
            unsupported_claims=[],
            validation_status="fail",
        )


def test_confidence_score_positive_infinity_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        ValidationOutput(
            document_id="doc-20260404-a1b2c3d4",
            confidence_score=float("inf"),
            unsupported_claims=[],
            validation_status="fail",
        )


def test_confidence_score_negative_infinity_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        ValidationOutput(
            document_id="doc-20260404-a1b2c3d4",
            confidence_score=float("-inf"),
            unsupported_claims=[],
            validation_status="fail",
        )


# ── unsupported_claims ────────────────────────────────────────────────────────


def test_empty_unsupported_claims_is_valid(valid_output: ValidationOutput) -> None:
    assert valid_output.unsupported_claims == []


def test_non_empty_unsupported_claims_preserved() -> None:
    claims = [
        "The summary references a 21 CFR section not present in any evidence chunk.",
        "Recommendation 2 cites a deadline not mentioned in retrieved passages.",
    ]
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.45,
        unsupported_claims=claims,
        validation_status="fail",
    )
    assert output.unsupported_claims == claims
    assert len(output.unsupported_claims) == 2


def test_single_unsupported_claim_preserved() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.65,
        unsupported_claims=["One claim lacks direct evidence."],
        validation_status="warning",
    )
    assert len(output.unsupported_claims) == 1


# ── JSON serialization ─────────────────────────────────────────────────────────


def test_model_dump_returns_dict(valid_output: ValidationOutput) -> None:
    data = valid_output.model_dump()
    assert isinstance(data, dict)


def test_model_dump_expected_keys(valid_output: ValidationOutput) -> None:
    data = valid_output.model_dump()
    expected_keys = {
        "document_id",
        "confidence_score",
        "unsupported_claims",
        "validation_status",
        "warning",
    }
    assert expected_keys == set(data.keys())


def test_model_dump_values_match(valid_output: ValidationOutput) -> None:
    data = valid_output.model_dump()
    assert data["document_id"] == valid_output.document_id
    assert data["confidence_score"] == pytest.approx(valid_output.confidence_score)
    assert data["unsupported_claims"] == valid_output.unsupported_claims
    assert data["validation_status"] == valid_output.validation_status
    assert data["warning"] == valid_output.warning


def test_model_dump_json_round_trips(valid_output: ValidationOutput) -> None:
    raw = valid_output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == valid_output.document_id
    assert parsed["confidence_score"] == pytest.approx(valid_output.confidence_score)
    assert isinstance(parsed["unsupported_claims"], list)
    assert parsed["validation_status"] == valid_output.validation_status


def test_validation_status_serializes_as_plain_string(valid_output: ValidationOutput) -> None:
    """validation_status must round-trip as a plain string, not an enum wrapper."""
    raw = valid_output.model_dump_json()
    parsed = json.loads(raw)
    assert isinstance(parsed["validation_status"], str)
    assert parsed["validation_status"] == "pass"


def test_model_dump_json_with_unsupported_claims() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.3,
        unsupported_claims=["Claim A.", "Claim B."],
        validation_status="fail",
    )
    raw = output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["unsupported_claims"] == ["Claim A.", "Claim B."]


def test_model_dump_json_warning_null_when_none(valid_output: ValidationOutput) -> None:
    raw = valid_output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["warning"] is None


def test_model_dump_json_warning_preserved_when_set() -> None:
    output = ValidationOutput(
        document_id="doc-20260404-a1b2c3d4",
        confidence_score=0.5,
        unsupported_claims=[],
        validation_status="warning",
        warning="Ambiguous support for recommendation 3.",
    )
    raw = output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["warning"] == "Ambiguous support for recommendation 3."


# ── CaseOutput shape compatibility ────────────────────────────────────────────


def test_validation_output_fields_are_subset_of_case_output_shape(
    valid_output: ValidationOutput,
) -> None:
    """
    ValidationOutput fields must be a subset of the CaseOutput shape defined in
    ARCHITECTURE.md §10.  This test guards against C-2 introducing field names
    that would conflict with or be renamed in D-phase CaseOutput.

    CaseOutput shape (from ARCHITECTURE.md §10):
      document_id, source_filename, source_type, severity, category, summary,
      recommendations, citations, confidence_score, unsupported_claims,
      escalation_required, escalation_reason, validated_by, session_id, timestamp
    """
    case_output_field_names = {
        "document_id",
        "source_filename",
        "source_type",
        "severity",
        "category",
        "summary",
        "recommendations",
        "citations",
        "confidence_score",
        "unsupported_claims",
        "escalation_required",
        "escalation_reason",
        "validated_by",
        "session_id",
        "timestamp",
    }
    validation_field_names = set(valid_output.model_dump().keys())
    # warning and validation_status are C-2-internal: the Tool Executor consumes
    # validation_status to compute escalation_required, but it does not pass the
    # raw validation_status string into CaseOutput.
    fields_to_check = validation_field_names - {"warning", "validation_status"}
    assert fields_to_check.issubset(case_output_field_names), (
        f"ValidationOutput fields not in CaseOutput shape: "
        f"{fields_to_check - case_output_field_names}"
    )
