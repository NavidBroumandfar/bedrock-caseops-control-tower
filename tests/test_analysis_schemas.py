"""
C-0 unit tests — analysis output schemas.

Coverage:
  - AnalysisOutput: valid construction with all four severity levels
  - AnalysisOutput: all required fields present in a valid instance
  - SeverityLevel: accepts Critical, High, Medium, Low only
  - SeverityLevel: rejects any value outside the four allowed literals
  - summary: empty string rejected
  - summary: whitespace-only string rejected
  - summary: leading/trailing whitespace stripped and stored normalized
  - recommendations: empty list is valid (no recommendations is a legal state)
  - recommendations: non-empty list with valid strings accepted
  - recommendations: empty string inside list rejected
  - recommendations: whitespace-only string inside list rejected
  - recommendations: mixed valid and empty items — whole model rejected
  - JSON serialization: model_dump returns a plain dict with expected keys
  - JSON serialization: model_dump_json round-trips through json.loads cleanly
  - CaseOutput shape compatibility: AnalysisOutput fields are a subset of CaseOutput
    fields needed by D-phase (document_id, severity, category, summary, recommendations)

No AWS credentials or live calls required.
No mocks needed.
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.analysis_models import AnalysisOutput, SeverityLevel


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def valid_output() -> AnalysisOutput:
    return AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to establish written procedures for equipment cleaning.",
        recommendations=[
            "Initiate CAPA for cleaning validation gaps.",
            "Escalate to compliance team within 48 hours.",
        ],
    )


# ── valid construction ─────────────────────────────────────────────────────────


def test_analysis_output_valid_construction(valid_output: AnalysisOutput) -> None:
    assert valid_output.document_id == "doc-20260404-a1b2c3d4"
    assert valid_output.severity == "High"
    assert valid_output.category == "Regulatory / Manufacturing Deficiency"
    assert "written procedures" in valid_output.summary
    assert len(valid_output.recommendations) == 2


def test_analysis_output_all_required_fields_present(valid_output: AnalysisOutput) -> None:
    """All five required fields must be accessible on a valid instance."""
    assert hasattr(valid_output, "document_id")
    assert hasattr(valid_output, "severity")
    assert hasattr(valid_output, "category")
    assert hasattr(valid_output, "summary")
    assert hasattr(valid_output, "recommendations")


def test_analysis_output_empty_recommendations_is_valid() -> None:
    """An empty recommendations list is a legal state — zero recommendations is representable."""
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="Low",
        category="Informational",
        summary="No immediate action required based on retrieved evidence.",
        recommendations=[],
    )
    assert output.recommendations == []


# ── SeverityLevel: accepted values ────────────────────────────────────────────


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low"])
def test_severity_accepts_all_valid_values(severity: str) -> None:
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity=severity,  # type: ignore[arg-type]
        category="Test",
        summary="Valid summary for parametrized severity test.",
        recommendations=["Take action."],
    )
    assert output.severity == severity


# ── SeverityLevel: rejected values ────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_severity",
    [
        "critical",       # wrong case
        "CRITICAL",       # all-caps
        "high",           # wrong case
        "moderate",       # not in schema
        "Unknown",        # invented value
        "",               # empty
        "None",           # string null
    ],
)
def test_severity_rejects_invalid_values(bad_severity: str) -> None:
    with pytest.raises(ValidationError):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity=bad_severity,  # type: ignore[arg-type]
            category="Test",
            summary="Valid summary.",
            recommendations=[],
        )


# ── summary validation ────────────────────────────────────────────────────────


def test_summary_empty_string_rejected() -> None:
    with pytest.raises(ValidationError, match="summary"):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity="Medium",
            category="Test",
            summary="",
            recommendations=[],
        )


def test_summary_whitespace_only_rejected() -> None:
    with pytest.raises(ValidationError, match="summary"):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity="Medium",
            category="Test",
            summary="   ",
            recommendations=[],
        )


def test_summary_tabs_and_newlines_only_rejected() -> None:
    with pytest.raises(ValidationError, match="summary"):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity="Medium",
            category="Test",
            summary="\t\n\r",
            recommendations=[],
        )


def test_summary_leading_trailing_whitespace_stripped() -> None:
    """summary is normalized: leading/trailing whitespace is stripped on input."""
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="Low",
        category="Test",
        summary="  Some readable text.  ",
        recommendations=[],
    )
    assert output.summary == "Some readable text."


def test_summary_internal_content_preserved() -> None:
    """Internal content must not be modified by the validator."""
    text = "First sentence. Second sentence with detail."
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="High",
        category="Test",
        summary=text,
        recommendations=[],
    )
    assert output.summary == text


# ── recommendations validation ────────────────────────────────────────────────


def test_recommendations_valid_single_item() -> None:
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="High",
        category="Test",
        summary="Actionable finding identified.",
        recommendations=["Initiate CAPA within 48 hours."],
    )
    assert len(output.recommendations) == 1


def test_recommendations_valid_multiple_items() -> None:
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="Critical",
        category="Safety",
        summary="Multiple critical deficiencies identified.",
        recommendations=[
            "Halt production immediately.",
            "Notify FDA within 24 hours.",
            "Conduct root cause analysis.",
        ],
    )
    assert len(output.recommendations) == 3


def test_recommendations_empty_string_in_list_rejected() -> None:
    with pytest.raises(ValidationError, match="recommendations"):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity="Medium",
            category="Test",
            summary="Valid summary.",
            recommendations=["Valid item.", ""],
        )


def test_recommendations_whitespace_only_item_rejected() -> None:
    with pytest.raises(ValidationError, match="recommendations"):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity="Medium",
            category="Test",
            summary="Valid summary.",
            recommendations=["   "],
        )


def test_recommendations_mixed_valid_and_empty_rejected() -> None:
    """A single empty item anywhere in the list must reject the whole model."""
    with pytest.raises(ValidationError, match="recommendations"):
        AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity="High",
            category="Test",
            summary="Valid summary.",
            recommendations=["Good action.", "", "Another good action."],
        )


# ── JSON serialization ─────────────────────────────────────────────────────────


def test_analysis_output_model_dump_returns_dict(valid_output: AnalysisOutput) -> None:
    data = valid_output.model_dump()
    assert isinstance(data, dict)


def test_analysis_output_model_dump_expected_keys(valid_output: AnalysisOutput) -> None:
    data = valid_output.model_dump()
    expected_keys = {"document_id", "severity", "category", "summary", "recommendations"}
    assert expected_keys == set(data.keys())


def test_analysis_output_model_dump_values_match(valid_output: AnalysisOutput) -> None:
    data = valid_output.model_dump()
    assert data["document_id"] == valid_output.document_id
    assert data["severity"] == valid_output.severity
    assert data["category"] == valid_output.category
    assert data["summary"] == valid_output.summary
    assert data["recommendations"] == valid_output.recommendations


def test_analysis_output_model_dump_json_round_trips(valid_output: AnalysisOutput) -> None:
    raw = valid_output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == valid_output.document_id
    assert parsed["severity"] == valid_output.severity
    assert parsed["summary"] == valid_output.summary
    assert isinstance(parsed["recommendations"], list)


def test_analysis_output_json_serializes_empty_recommendations() -> None:
    output = AnalysisOutput(
        document_id="doc-20260404-a1b2c3d4",
        severity="Low",
        category="Informational",
        summary="No significant findings.",
        recommendations=[],
    )
    raw = output.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["recommendations"] == []


def test_analysis_output_json_serializes_recommendations_list(
    valid_output: AnalysisOutput,
) -> None:
    raw = valid_output.model_dump_json()
    parsed = json.loads(raw)
    assert len(parsed["recommendations"]) == len(valid_output.recommendations)
    for actual, expected in zip(parsed["recommendations"], valid_output.recommendations):
        assert actual == expected


# ── CaseOutput shape compatibility ────────────────────────────────────────────


def test_analysis_output_fields_are_subset_of_case_output_shape(
    valid_output: AnalysisOutput,
) -> None:
    """
    AnalysisOutput fields must be a subset of the CaseOutput shape defined in
    ARCHITECTURE.md §10.  This test guards against C-0 introducing field names
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
    analysis_field_names = set(valid_output.model_dump().keys())
    # Every AnalysisOutput field must appear in the planned CaseOutput shape.
    assert analysis_field_names.issubset(case_output_field_names), (
        f"AnalysisOutput fields not in CaseOutput shape: "
        f"{analysis_field_names - case_output_field_names}"
    )


def test_analysis_output_severity_literal_compatible_with_case_output() -> None:
    """
    The severity string stored in AnalysisOutput must be directly usable
    as the severity field of future CaseOutput without transformation.
    """
    for severity in ("Critical", "High", "Medium", "Low"):
        output = AnalysisOutput(
            document_id="doc-20260404-a1b2c3d4",
            severity=severity,  # type: ignore[arg-type]
            category="Test",
            summary="Severity compatibility check.",
            recommendations=[],
        )
        # Verify it round-trips through JSON and remains the same string value.
        dumped = json.loads(output.model_dump_json())
        assert dumped["severity"] == severity
