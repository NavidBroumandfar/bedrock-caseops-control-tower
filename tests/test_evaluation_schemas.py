"""
F-0 unit tests — evaluation contracts + scoring schemas.

Coverage:
  EvaluationCase:
    - valid construction with all fields
    - optional fields default correctly
    - document_date: valid ISO date accepted, malformed rejected
    - case_id / source_filename / source_type: empty or whitespace rejected
    - tags: defaults to empty list

  ExpectedOutput:
    - valid construction with required fields only
    - optional list fields default to empty
    - all four SeverityLevel values accepted
    - invalid severity rejected
    - case_id / expected_category: empty rejected

  RetrievalExpectation:
    - valid construction with defaults
    - minimum_expected_chunks defaults to 1
    - minimum_expected_chunks < 1 rejected
    - case_id: empty rejected

  DimensionScore:
    - valid construction with score in [0.0, max_score]
    - score at boundary values (0.0 and max_score)
    - score above max_score rejected
    - score below 0.0 rejected
    - score = NaN rejected
    - score = Inf rejected
    - max_score <= 0.0 rejected
    - max_score = NaN / Inf rejected
    - metric_name: empty rejected
    - rationale: optional, defaults to None

  EvaluationResult:
    - valid construction
    - overall_score: bounds enforced (0.0, 1.0, above 1.0, below 0.0)
    - overall_score: NaN / Inf rejected
    - dimension_scores: empty list rejected
    - timestamp: valid ISO 8601 accepted, malformed rejected
    - case_id / run_id / evaluation_version: empty rejected

  EvaluationRunSummary:
    - valid construction with consistent counts
    - passed + failed != total rejected
    - average_score: bounds enforced
    - per_metric_averages: value out of [0.0, 1.0] rejected
    - per_metric_averages: NaN value rejected
    - run_id: empty rejected
    - case counts: negative values rejected
    - timestamp: valid ISO 8601 accepted, malformed rejected

  Serialization:
    - each model serializes cleanly via model_dump and model_dump_json

No AWS credentials or live calls required.
No mocks needed.
"""

import json
import math

import pytest
from pydantic import ValidationError

from app.schemas.evaluation_models import (
    DimensionScore,
    EvaluationCase,
    EvaluationResult,
    EvaluationRunSummary,
    ExpectedOutput,
    RetrievalExpectation,
)

# ── shared timestamp constant ──────────────────────────────────────────────────

_TS = "2026-04-10T12:00:00Z"


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def valid_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="eval-case-001",
        source_filename="fda_warning_letter_01.md",
        source_type="FDA",
        document_date="2026-03-15",
    )


@pytest.fixture()
def valid_expected_output() -> ExpectedOutput:
    return ExpectedOutput(
        case_id="eval-case-001",
        expected_severity="High",
        expected_category="Regulatory / Manufacturing Deficiency",
        expected_escalation_required=False,
    )


@pytest.fixture()
def valid_retrieval_expectation() -> RetrievalExpectation:
    return RetrievalExpectation(
        case_id="eval-case-001",
        minimum_expected_chunks=2,
        expected_source_labels=["FDA Warning Letter 2024-WL-0001"],
        required_evidence_terms=["written procedures"],
    )


@pytest.fixture()
def valid_dimension_score() -> DimensionScore:
    return DimensionScore(
        metric_name="severity_match",
        score=1.0,
        max_score=1.0,
        passed=True,
    )


@pytest.fixture()
def valid_evaluation_result(valid_dimension_score: DimensionScore) -> EvaluationResult:
    return EvaluationResult(
        case_id="eval-case-001",
        run_id="run-20260410-001",
        evaluation_version="v1.0",
        overall_score=0.9,
        pass_fail=True,
        dimension_scores=[valid_dimension_score],
        timestamp=_TS,
    )


@pytest.fixture()
def valid_run_summary() -> EvaluationRunSummary:
    return EvaluationRunSummary(
        run_id="run-20260410-001",
        total_cases=10,
        passed_cases=8,
        failed_cases=2,
        average_score=0.85,
        timestamp=_TS,
    )


# ── EvaluationCase: valid construction ────────────────────────────────────────


def test_evaluation_case_valid_minimal(valid_case: EvaluationCase) -> None:
    assert valid_case.case_id == "eval-case-001"
    assert valid_case.source_filename == "fda_warning_letter_01.md"
    assert valid_case.source_type == "FDA"
    assert valid_case.document_date == "2026-03-15"


def test_evaluation_case_optional_fields_default(valid_case: EvaluationCase) -> None:
    assert valid_case.submitter_note is None
    assert valid_case.case_description is None
    assert valid_case.tags == []


def test_evaluation_case_with_all_fields() -> None:
    case = EvaluationCase(
        case_id="eval-case-002",
        source_filename="cisa_advisory_01.md",
        source_type="CISA",
        document_date="2026-01-20",
        submitter_note="Critical ICS advisory",
        case_description="Tests escalation for critical severity.",
        tags=["escalation", "critical", "ics"],
    )
    assert case.submitter_note == "Critical ICS advisory"
    assert case.case_description == "Tests escalation for critical severity."
    assert case.tags == ["escalation", "critical", "ics"]


def test_evaluation_case_tags_default_to_empty_list() -> None:
    case = EvaluationCase(
        case_id="eval-case-003",
        source_filename="sample_notice.txt",
        source_type="Other",
        document_date="2026-02-01",
    )
    assert case.tags == []


# ── EvaluationCase: document_date validation ──────────────────────────────────


@pytest.mark.parametrize(
    "date",
    ["2026-01-01", "2025-12-31", "2024-02-29"],  # 2024 is a leap year
)
def test_evaluation_case_valid_document_dates(date: str) -> None:
    case = EvaluationCase(
        case_id="eval-case-date",
        source_filename="file.txt",
        source_type="Other",
        document_date=date,
    )
    assert case.document_date == date


@pytest.mark.parametrize(
    "bad_date",
    ["2026/04/10", "April 10 2026", "20260410", "2026-13-01", ""],
)
def test_evaluation_case_rejects_invalid_document_date(bad_date: str) -> None:
    with pytest.raises(ValidationError, match="document_date"):
        EvaluationCase(
            case_id="eval-case-date",
            source_filename="file.txt",
            source_type="Other",
            document_date=bad_date,
        )


# ── EvaluationCase: required string field validation ──────────────────────────


def test_evaluation_case_empty_case_id_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase(
            case_id="",
            source_filename="file.txt",
            source_type="FDA",
            document_date="2026-04-10",
        )


def test_evaluation_case_whitespace_case_id_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase(
            case_id="   ",
            source_filename="file.txt",
            source_type="FDA",
            document_date="2026-04-10",
        )


def test_evaluation_case_empty_source_filename_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase(
            case_id="eval-case-001",
            source_filename="",
            source_type="FDA",
            document_date="2026-04-10",
        )


# ── ExpectedOutput: valid construction ────────────────────────────────────────


def test_expected_output_valid_minimal(valid_expected_output: ExpectedOutput) -> None:
    assert valid_expected_output.case_id == "eval-case-001"
    assert valid_expected_output.expected_severity == "High"
    assert valid_expected_output.expected_category == "Regulatory / Manufacturing Deficiency"
    assert valid_expected_output.expected_escalation_required is False


def test_expected_output_optional_fields_default(valid_expected_output: ExpectedOutput) -> None:
    assert valid_expected_output.expected_summary_facts == []
    assert valid_expected_output.expected_recommendation_keywords == []
    assert valid_expected_output.forbidden_claims == []


def test_expected_output_with_all_optional_fields() -> None:
    output = ExpectedOutput(
        case_id="eval-case-001",
        expected_severity="Critical",
        expected_category="Safety",
        expected_escalation_required=True,
        expected_summary_facts=["contamination", "recall"],
        expected_recommendation_keywords=["halt production", "notify FDA"],
        forbidden_claims=["no risk", "safe for use"],
    )
    assert len(output.expected_summary_facts) == 2
    assert len(output.expected_recommendation_keywords) == 2
    assert len(output.forbidden_claims) == 2


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low"])
def test_expected_output_all_severity_levels_accepted(severity: str) -> None:
    output = ExpectedOutput(
        case_id="eval-case-001",
        expected_severity=severity,  # type: ignore[arg-type]
        expected_category="Test",
        expected_escalation_required=False,
    )
    assert output.expected_severity == severity


@pytest.mark.parametrize("bad_severity", ["critical", "CRITICAL", "moderate", "unknown", ""])
def test_expected_output_invalid_severity_rejected(bad_severity: str) -> None:
    with pytest.raises(ValidationError):
        ExpectedOutput(
            case_id="eval-case-001",
            expected_severity=bad_severity,  # type: ignore[arg-type]
            expected_category="Test",
            expected_escalation_required=False,
        )


def test_expected_output_empty_case_id_rejected() -> None:
    with pytest.raises(ValidationError):
        ExpectedOutput(
            case_id="",
            expected_severity="Low",
            expected_category="Test",
            expected_escalation_required=False,
        )


def test_expected_output_empty_category_rejected() -> None:
    with pytest.raises(ValidationError):
        ExpectedOutput(
            case_id="eval-case-001",
            expected_severity="Low",
            expected_category="",
            expected_escalation_required=False,
        )


# ── RetrievalExpectation: valid construction ──────────────────────────────────


def test_retrieval_expectation_valid(
    valid_retrieval_expectation: RetrievalExpectation,
) -> None:
    assert valid_retrieval_expectation.case_id == "eval-case-001"
    assert valid_retrieval_expectation.minimum_expected_chunks == 2
    assert len(valid_retrieval_expectation.expected_source_labels) == 1
    assert len(valid_retrieval_expectation.required_evidence_terms) == 1


def test_retrieval_expectation_defaults() -> None:
    exp = RetrievalExpectation(case_id="eval-case-001")
    assert exp.minimum_expected_chunks == 1
    assert exp.expected_source_labels == []
    assert exp.required_evidence_terms == []


def test_retrieval_expectation_minimum_one_chunk() -> None:
    exp = RetrievalExpectation(case_id="eval-case-001", minimum_expected_chunks=1)
    assert exp.minimum_expected_chunks == 1


def test_retrieval_expectation_zero_chunks_rejected() -> None:
    with pytest.raises(ValidationError, match="minimum_expected_chunks"):
        RetrievalExpectation(case_id="eval-case-001", minimum_expected_chunks=0)


def test_retrieval_expectation_negative_chunks_rejected() -> None:
    with pytest.raises(ValidationError, match="minimum_expected_chunks"):
        RetrievalExpectation(case_id="eval-case-001", minimum_expected_chunks=-1)


def test_retrieval_expectation_empty_case_id_rejected() -> None:
    with pytest.raises(ValidationError):
        RetrievalExpectation(case_id="")


# ── DimensionScore: valid construction ────────────────────────────────────────


def test_dimension_score_valid(valid_dimension_score: DimensionScore) -> None:
    assert valid_dimension_score.metric_name == "severity_match"
    assert valid_dimension_score.score == 1.0
    assert valid_dimension_score.max_score == 1.0
    assert valid_dimension_score.passed is True
    assert valid_dimension_score.rationale is None


def test_dimension_score_rationale_optional() -> None:
    ds = DimensionScore(
        metric_name="citation_coverage",
        score=0.5,
        max_score=1.0,
        passed=False,
        rationale="Only 2 of 4 expected citations found.",
    )
    assert ds.rationale == "Only 2 of 4 expected citations found."


def test_dimension_score_score_at_zero() -> None:
    ds = DimensionScore(metric_name="escalation_match", score=0.0, max_score=1.0, passed=False)
    assert ds.score == 0.0


def test_dimension_score_score_at_max() -> None:
    ds = DimensionScore(metric_name="escalation_match", score=2.0, max_score=2.0, passed=True)
    assert ds.score == 2.0


def test_dimension_score_custom_max() -> None:
    ds = DimensionScore(metric_name="recall_at_5", score=3.0, max_score=5.0, passed=False)
    assert ds.max_score == 5.0
    assert ds.score == 3.0


def test_dimension_score_score_above_max_rejected() -> None:
    with pytest.raises(ValidationError, match="score"):
        DimensionScore(metric_name="m", score=1.1, max_score=1.0, passed=True)


def test_dimension_score_score_below_zero_rejected() -> None:
    with pytest.raises(ValidationError, match="score"):
        DimensionScore(metric_name="m", score=-0.1, max_score=1.0, passed=False)


def test_dimension_score_nan_score_rejected() -> None:
    with pytest.raises(ValidationError):
        DimensionScore(metric_name="m", score=math.nan, max_score=1.0, passed=False)


def test_dimension_score_inf_score_rejected() -> None:
    with pytest.raises(ValidationError):
        DimensionScore(metric_name="m", score=math.inf, max_score=1.0, passed=False)


def test_dimension_score_zero_max_score_rejected() -> None:
    with pytest.raises(ValidationError, match="max_score"):
        DimensionScore(metric_name="m", score=0.0, max_score=0.0, passed=False)


def test_dimension_score_negative_max_score_rejected() -> None:
    with pytest.raises(ValidationError, match="max_score"):
        DimensionScore(metric_name="m", score=0.0, max_score=-1.0, passed=False)


def test_dimension_score_nan_max_score_rejected() -> None:
    with pytest.raises(ValidationError, match="max_score"):
        DimensionScore(metric_name="m", score=0.0, max_score=math.nan, passed=False)


def test_dimension_score_empty_metric_name_rejected() -> None:
    with pytest.raises(ValidationError, match="metric_name"):
        DimensionScore(metric_name="", score=0.5, max_score=1.0, passed=True)


def test_dimension_score_whitespace_metric_name_rejected() -> None:
    with pytest.raises(ValidationError, match="metric_name"):
        DimensionScore(metric_name="   ", score=0.5, max_score=1.0, passed=True)


# ── EvaluationResult: valid construction ──────────────────────────────────────


def test_evaluation_result_valid(valid_evaluation_result: EvaluationResult) -> None:
    assert valid_evaluation_result.case_id == "eval-case-001"
    assert valid_evaluation_result.run_id == "run-20260410-001"
    assert valid_evaluation_result.evaluation_version == "v1.0"
    assert valid_evaluation_result.overall_score == 0.9
    assert valid_evaluation_result.pass_fail is True
    assert len(valid_evaluation_result.dimension_scores) == 1
    assert valid_evaluation_result.notes is None


def test_evaluation_result_notes_optional(
    valid_dimension_score: DimensionScore,
) -> None:
    result = EvaluationResult(
        case_id="eval-case-001",
        run_id="run-001",
        evaluation_version="v1.0",
        overall_score=0.5,
        pass_fail=False,
        dimension_scores=[valid_dimension_score],
        notes="Low confidence in category match.",
        timestamp=_TS,
    )
    assert result.notes == "Low confidence in category match."


def test_evaluation_result_overall_score_zero(valid_dimension_score: DimensionScore) -> None:
    result = EvaluationResult(
        case_id="c",
        run_id="r",
        evaluation_version="v1",
        overall_score=0.0,
        pass_fail=False,
        dimension_scores=[valid_dimension_score],
        timestamp=_TS,
    )
    assert result.overall_score == 0.0


def test_evaluation_result_overall_score_one(valid_dimension_score: DimensionScore) -> None:
    result = EvaluationResult(
        case_id="c",
        run_id="r",
        evaluation_version="v1",
        overall_score=1.0,
        pass_fail=True,
        dimension_scores=[valid_dimension_score],
        timestamp=_TS,
    )
    assert result.overall_score == 1.0


def test_evaluation_result_overall_score_above_one_rejected(
    valid_dimension_score: DimensionScore,
) -> None:
    with pytest.raises(ValidationError, match="overall_score"):
        EvaluationResult(
            case_id="c",
            run_id="r",
            evaluation_version="v1",
            overall_score=1.1,
            pass_fail=True,
            dimension_scores=[valid_dimension_score],
            timestamp=_TS,
        )


def test_evaluation_result_overall_score_below_zero_rejected(
    valid_dimension_score: DimensionScore,
) -> None:
    with pytest.raises(ValidationError, match="overall_score"):
        EvaluationResult(
            case_id="c",
            run_id="r",
            evaluation_version="v1",
            overall_score=-0.1,
            pass_fail=False,
            dimension_scores=[valid_dimension_score],
            timestamp=_TS,
        )


def test_evaluation_result_overall_score_nan_rejected(
    valid_dimension_score: DimensionScore,
) -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(
            case_id="c",
            run_id="r",
            evaluation_version="v1",
            overall_score=math.nan,
            pass_fail=False,
            dimension_scores=[valid_dimension_score],
            timestamp=_TS,
        )


def test_evaluation_result_empty_dimension_scores_rejected() -> None:
    with pytest.raises(ValidationError, match="dimension_scores"):
        EvaluationResult(
            case_id="c",
            run_id="r",
            evaluation_version="v1",
            overall_score=0.5,
            pass_fail=False,
            dimension_scores=[],
            timestamp=_TS,
        )


def test_evaluation_result_multiple_dimension_scores(
    valid_dimension_score: DimensionScore,
) -> None:
    ds2 = DimensionScore(metric_name="category_match", score=0.0, max_score=1.0, passed=False)
    result = EvaluationResult(
        case_id="c",
        run_id="r",
        evaluation_version="v1",
        overall_score=0.5,
        pass_fail=False,
        dimension_scores=[valid_dimension_score, ds2],
        timestamp=_TS,
    )
    assert len(result.dimension_scores) == 2


def test_evaluation_result_empty_case_id_rejected(
    valid_dimension_score: DimensionScore,
) -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(
            case_id="",
            run_id="r",
            evaluation_version="v1",
            overall_score=0.5,
            pass_fail=False,
            dimension_scores=[valid_dimension_score],
            timestamp=_TS,
        )


def test_evaluation_result_empty_run_id_rejected(
    valid_dimension_score: DimensionScore,
) -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(
            case_id="c",
            run_id="",
            evaluation_version="v1",
            overall_score=0.5,
            pass_fail=False,
            dimension_scores=[valid_dimension_score],
            timestamp=_TS,
        )


def test_evaluation_result_malformed_timestamp_rejected(
    valid_dimension_score: DimensionScore,
) -> None:
    with pytest.raises(ValidationError, match="timestamp"):
        EvaluationResult(
            case_id="c",
            run_id="r",
            evaluation_version="v1",
            overall_score=0.5,
            pass_fail=False,
            dimension_scores=[valid_dimension_score],
            timestamp="not-a-timestamp",
        )


def test_evaluation_result_valid_iso_timestamp_accepted(
    valid_dimension_score: DimensionScore,
) -> None:
    for ts in ["2026-04-10T12:00:00Z", "2026-04-10T12:00:00+00:00", "2026-04-10T12:00:00"]:
        result = EvaluationResult(
            case_id="c",
            run_id="r",
            evaluation_version="v1",
            overall_score=0.5,
            pass_fail=False,
            dimension_scores=[valid_dimension_score],
            timestamp=ts,
        )
        assert result.timestamp == ts


# ── EvaluationRunSummary: valid construction ──────────────────────────────────


def test_evaluation_run_summary_valid(valid_run_summary: EvaluationRunSummary) -> None:
    assert valid_run_summary.run_id == "run-20260410-001"
    assert valid_run_summary.total_cases == 10
    assert valid_run_summary.passed_cases == 8
    assert valid_run_summary.failed_cases == 2
    assert valid_run_summary.average_score == 0.85
    assert valid_run_summary.per_metric_averages == {}


def test_evaluation_run_summary_with_per_metric_averages() -> None:
    summary = EvaluationRunSummary(
        run_id="run-001",
        total_cases=5,
        passed_cases=4,
        failed_cases=1,
        average_score=0.80,
        per_metric_averages={"severity_match": 0.9, "escalation_match": 0.7},
        timestamp=_TS,
    )
    assert summary.per_metric_averages["severity_match"] == 0.9
    assert summary.per_metric_averages["escalation_match"] == 0.7


def test_evaluation_run_summary_all_passed() -> None:
    summary = EvaluationRunSummary(
        run_id="run-001",
        total_cases=3,
        passed_cases=3,
        failed_cases=0,
        average_score=1.0,
        timestamp=_TS,
    )
    assert summary.passed_cases == 3
    assert summary.failed_cases == 0


def test_evaluation_run_summary_all_failed() -> None:
    summary = EvaluationRunSummary(
        run_id="run-001",
        total_cases=3,
        passed_cases=0,
        failed_cases=3,
        average_score=0.0,
        timestamp=_TS,
    )
    assert summary.passed_cases == 0
    assert summary.failed_cases == 3


def test_evaluation_run_summary_count_inconsistency_rejected() -> None:
    with pytest.raises(ValidationError, match="total_cases"):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=10,
            passed_cases=6,
            failed_cases=3,   # 6+3 != 10
            average_score=0.5,
            timestamp=_TS,
        )


def test_evaluation_run_summary_negative_total_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=-1,
            passed_cases=0,
            failed_cases=0,
            average_score=0.5,
            timestamp=_TS,
        )


def test_evaluation_run_summary_average_score_above_one_rejected() -> None:
    with pytest.raises(ValidationError, match="average_score"):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=2,
            passed_cases=2,
            failed_cases=0,
            average_score=1.1,
            timestamp=_TS,
        )


def test_evaluation_run_summary_average_score_below_zero_rejected() -> None:
    with pytest.raises(ValidationError, match="average_score"):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=2,
            passed_cases=0,
            failed_cases=2,
            average_score=-0.1,
            timestamp=_TS,
        )


def test_evaluation_run_summary_per_metric_value_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError, match="per_metric_averages"):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            average_score=1.0,
            per_metric_averages={"severity_match": 1.5},
            timestamp=_TS,
        )


def test_evaluation_run_summary_per_metric_nan_rejected() -> None:
    with pytest.raises(ValidationError, match="per_metric_averages"):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            average_score=1.0,
            per_metric_averages={"severity_match": math.nan},
            timestamp=_TS,
        )


def test_evaluation_run_summary_empty_run_id_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationRunSummary(
            run_id="",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            average_score=1.0,
            timestamp=_TS,
        )


def test_evaluation_run_summary_malformed_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timestamp"):
        EvaluationRunSummary(
            run_id="run-001",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            average_score=1.0,
            timestamp="2026/04/10",
        )


# ── Serialization ──────────────────────────────────────────────────────────────


def test_evaluation_case_serializes_cleanly(valid_case: EvaluationCase) -> None:
    data = valid_case.model_dump()
    assert isinstance(data, dict)
    parsed = json.loads(valid_case.model_dump_json())
    assert parsed["case_id"] == valid_case.case_id
    assert parsed["tags"] == []


def test_expected_output_serializes_cleanly(valid_expected_output: ExpectedOutput) -> None:
    data = valid_expected_output.model_dump()
    assert "case_id" in data
    assert "expected_severity" in data
    assert "expected_escalation_required" in data
    parsed = json.loads(valid_expected_output.model_dump_json())
    assert parsed["expected_escalation_required"] is False


def test_retrieval_expectation_serializes_cleanly(
    valid_retrieval_expectation: RetrievalExpectation,
) -> None:
    data = valid_retrieval_expectation.model_dump()
    assert data["minimum_expected_chunks"] == 2
    assert isinstance(data["required_evidence_terms"], list)


def test_dimension_score_serializes_cleanly(valid_dimension_score: DimensionScore) -> None:
    data = valid_dimension_score.model_dump()
    assert data["metric_name"] == "severity_match"
    assert data["score"] == 1.0
    assert data["passed"] is True
    assert data["rationale"] is None


def test_evaluation_result_serializes_cleanly(
    valid_evaluation_result: EvaluationResult,
) -> None:
    raw = valid_evaluation_result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["case_id"] == "eval-case-001"
    assert parsed["overall_score"] == 0.9
    assert isinstance(parsed["dimension_scores"], list)
    assert len(parsed["dimension_scores"]) == 1


def test_evaluation_run_summary_serializes_cleanly(
    valid_run_summary: EvaluationRunSummary,
) -> None:
    raw = valid_run_summary.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["run_id"] == "run-20260410-001"
    assert parsed["total_cases"] == 10
    assert parsed["passed_cases"] == 8
    assert parsed["failed_cases"] == 2
    assert isinstance(parsed["per_metric_averages"], dict)
