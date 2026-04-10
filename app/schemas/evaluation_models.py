"""
Pydantic models for the F-0 evaluation contracts layer.

These schemas define the typed foundation for automated evaluation of pipeline outputs
against reference expectations.  No runner logic, live AWS calls, or dataset population
belong here — this is the contract layer only.

EvaluationCase           — reference descriptor for one document case used in evaluation.
ExpectedOutput           — the reference judgment a pipeline run is scored against.
RetrievalExpectation     — contract for retrieval-quality evaluation of a given case.
CitationExpectation      — contract for citation-quality evaluation of a given case (G-1).
DimensionScore           — a single scored metric dimension; reused across all score types.
EvaluationResult         — the evaluated result of one pipeline run against one reference case.
EvaluationRunSummary     — aggregated results across all cases in one evaluation run.
OutputQualityScoringResult — G-2 composite output-quality result combining F-2 and G-1 sub-scores.
ComparisonVerdict        — I-2 verdict classifying how a case changed between baseline and optimized.
EvaluationMetricDatum    — J-0 typed contract for a single CloudWatch Metrics datum.
"""

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

# Valid CloudWatch metric unit strings accepted by put_metric_data.
_CW_UNIT = Literal[
    "Seconds", "Microseconds", "Milliseconds",
    "Bytes", "Kilobytes", "Megabytes", "Gigabytes", "Terabytes",
    "Bits", "Kilobits", "Megabits", "Gigabits", "Terabits",
    "Percent", "Count",
    "Bytes/Second", "Kilobytes/Second", "Megabytes/Second",
    "Gigabytes/Second", "Terabytes/Second",
    "Bits/Second", "Kilobits/Second", "Megabits/Second",
    "Gigabits/Second", "Terabits/Second",
    "Count/Second", "None",
]

from app.schemas.analysis_models import SeverityLevel


# ── ComparisonVerdict ──────────────────────────────────────────────────────────

# Verdict classifying how a case changed between baseline and optimized.
# Used by the I-2 comparison runner and stored in ComparisonCaseResult.
#   "improved"  — optimized score exceeds baseline score by more than the comparison epsilon.
#   "regressed" — optimized score is below baseline score by more than the comparison epsilon.
#   "unchanged" — the absolute delta is within the comparison epsilon; no meaningful change.
ComparisonVerdict = Literal["improved", "regressed", "unchanged"]


# ── EvaluationCase ─────────────────────────────────────────────────────────────


class EvaluationCase(BaseModel):
    """
    Reference descriptor for a single document case used in evaluation.

    Describes the input side of an evaluation fixture: which document, where it
    came from, and any operator-supplied context.  F-1 will populate instances of
    this model from reference fixture files.

    case_id         — stable identifier for this evaluation case across runs.
    source_filename — original filename of the document under evaluation.
    source_type     — document origin category (FDA / CISA / Incident / Other).
    document_date   — date of the source document in YYYY-MM-DD format.
    submitter_note  — optional operator note attached at intake time.
    case_description — optional free-text description of what this case tests.
    tags            — optional labels for filtering (e.g. "escalation", "adversarial").
    """

    case_id: str
    source_filename: str
    source_type: str
    document_date: str          # YYYY-MM-DD
    submitter_note: str | None = None
    case_description: str | None = None
    tags: list[str] = []

    @field_validator("document_date")
    @classmethod
    def must_be_iso_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"document_date must be YYYY-MM-DD, got: {value!r}")
        return value

    @field_validator("case_id", "source_filename", "source_type")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be a non-empty string")
        return value


# ── ExpectedOutput ─────────────────────────────────────────────────────────────


class ExpectedOutput(BaseModel):
    """
    Reference judgment for a single evaluation case.

    Defines what a correct pipeline run should produce.  Comparison logic in F-2
    will score an actual CaseOutput against this reference.

    case_id                        — ties this expectation to an EvaluationCase.
    expected_severity              — the severity level a correct run should assign.
    expected_category              — substring or exact match for the category field.
    expected_escalation_required   — whether escalation should be triggered.
    expected_summary_facts         — key facts that must appear in the summary.
    expected_recommendation_keywords — terms that must appear in at least one recommendation.
    forbidden_claims               — strings that must NOT appear in the output.
    """

    case_id: str
    expected_severity: SeverityLevel
    expected_category: str
    expected_escalation_required: bool
    expected_summary_facts: list[str] = []
    expected_recommendation_keywords: list[str] = []
    forbidden_claims: list[str] = []

    @field_validator("case_id", "expected_category")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be a non-empty string")
        return value


# ── RetrievalExpectation ───────────────────────────────────────────────────────


class RetrievalExpectation(BaseModel):
    """
    Contract for retrieval-quality evaluation of a single case.

    Captures the minimum acceptable retrieval behavior so that G-0 retrieval
    quality metrics have a typed reference to score against.

    case_id                  — ties this expectation to an EvaluationCase.
    minimum_expected_chunks  — the run must retrieve at least this many chunks.
    expected_source_labels   — source labels that must appear in the retrieved chunks.
    required_evidence_terms  — terms that must appear in at least one retrieved chunk.
    """

    case_id: str
    minimum_expected_chunks: int = 1
    expected_source_labels: list[str] = []
    required_evidence_terms: list[str] = []

    @field_validator("minimum_expected_chunks")
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                f"minimum_expected_chunks must be at least 1, got: {value!r}"
            )
        return value

    @field_validator("case_id")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case_id must be a non-empty string")
        return value


# ── CitationExpectation ────────────────────────────────────────────────────────


class CitationExpectation(BaseModel):
    """
    Contract for citation-quality evaluation of a single case (G-1).

    Captures what correct citation behavior looks like so that the G-1 citation
    scorer has a typed reference to evaluate against.  All fields are optional
    or have safe defaults so that existing fixtures remain backward-compatible.

    case_id                   — ties this expectation to an EvaluationCase.
    citations_required        — True when the case output must include citations;
                                False for cases where citations are intentionally absent.
    expected_source_labels    — source labels that must appear in citation source_label fields
                                (case-insensitive exact match after normalization).
    required_excerpt_terms    — terms that must appear as substrings across the concatenated
                                citation excerpts (case-insensitive).
    minimum_citation_count    — candidate must have at least this many citations when
                                citations_required is True; ignored when False.
    """

    case_id: str
    citations_required: bool = True
    expected_source_labels: list[str] = []
    required_excerpt_terms: list[str] = []
    minimum_citation_count: int = 1

    @field_validator("case_id")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case_id must be a non-empty string")
        return value

    @field_validator("minimum_citation_count")
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                f"minimum_citation_count must be at least 1, got: {value!r}"
            )
        return value


# ── DimensionScore ─────────────────────────────────────────────────────────────


class DimensionScore(BaseModel):
    """
    A single scored metric dimension.

    Reused by EvaluationResult to hold per-dimension scores.  The scoring range
    convention is [0.0, max_score]; passed is True when score >= max_score * pass_threshold.
    Callers set passed explicitly so that pass thresholds can be metric-specific.

    metric_name — stable identifier for this dimension (e.g. "severity_match").
    score       — raw score in [0.0, max_score].
    max_score   — ceiling for this dimension; defaults to 1.0 (normalized convention).
    passed      — whether this dimension is considered passing.
    rationale   — optional explanation of why this score was assigned.
    """

    metric_name: str
    score: float
    max_score: float = 1.0
    passed: bool
    rationale: str | None = None

    @field_validator("metric_name")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("metric_name must be a non-empty string")
        return value

    @field_validator("max_score")
    @classmethod
    def max_score_must_be_positive(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value) or value <= 0.0:
            raise ValueError(
                f"max_score must be a positive finite number, got: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def score_must_be_in_range(self) -> "DimensionScore":
        if math.isnan(self.score) or math.isinf(self.score):
            raise ValueError(
                f"score must be a finite number, got: {self.score!r}"
            )
        if not (0.0 <= self.score <= self.max_score):
            raise ValueError(
                f"score ({self.score!r}) must be in [0.0, max_score ({self.max_score!r})]"
            )
        return self


# ── EvaluationResult ──────────────────────────────────────────────────────────


class EvaluationResult(BaseModel):
    """
    Result of evaluating one pipeline run against one reference case.

    Produced by the F-2 automated scoring runner and consumed by J-1 reporting.
    overall_score is the aggregate across all dimension_scores; pass_fail reflects
    whether every dimension passed.

    case_id            — the EvaluationCase this result corresponds to.
    run_id             — identifier for the evaluation run batch this result belongs to.
    evaluation_version — version string for the evaluation harness or ruleset.
    overall_score      — aggregate normalized score in [0.0, 1.0].
    pass_fail          — True only when all dimension_scores have passed=True.
    dimension_scores   — per-metric breakdown; must not be empty.
    notes              — optional free-text observation from the evaluator.
    timestamp          — ISO 8601 UTC timestamp when this result was produced.
    """

    case_id: str
    run_id: str
    evaluation_version: str
    overall_score: float
    pass_fail: bool
    dimension_scores: list[DimensionScore]
    notes: str | None = None
    timestamp: str

    @field_validator("case_id", "run_id", "evaluation_version")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be a non-empty string")
        return value

    @field_validator("overall_score")
    @classmethod
    def overall_score_must_be_in_unit_interval(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"overall_score must be a finite float, got: {value!r}"
            )
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"overall_score must be between 0.0 and 1.0 inclusive, got: {value!r}"
            )
        return value

    @field_validator("dimension_scores")
    @classmethod
    def dimension_scores_must_not_be_empty(
        cls, items: list[DimensionScore]
    ) -> list[DimensionScore]:
        if not items:
            raise ValueError(
                "dimension_scores must contain at least one DimensionScore entry"
            )
        return items

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_iso8601(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"timestamp must be a valid ISO 8601 datetime string, got: {value!r}"
            )
        return value


# ── EvaluationRunSummary ───────────────────────────────────────────────────────


class EvaluationRunSummary(BaseModel):
    """
    Aggregated results across all evaluated cases in one evaluation run.

    Produced by the F-2 runner after all EvaluationResults have been collected.
    Consumed by J-0 (CloudWatch dashboard) and J-1 (reporting artifacts).

    run_id              — matches the run_id on all contributing EvaluationResults.
    total_cases         — number of cases included in this run.
    passed_cases        — cases where pass_fail was True.
    failed_cases        — cases where pass_fail was False.
    average_score       — mean of all overall_scores across the run; in [0.0, 1.0].
    per_metric_averages — optional map of metric_name → mean score across the run.
    timestamp           — ISO 8601 UTC timestamp when the summary was produced.
    """

    run_id: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    average_score: float
    per_metric_averages: dict[str, float] = {}
    timestamp: str

    @field_validator("run_id")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run_id must be a non-empty string")
        return value

    @field_validator("total_cases", "passed_cases", "failed_cases")
    @classmethod
    def must_be_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"case count must be non-negative, got: {value!r}")
        return value

    @field_validator("average_score")
    @classmethod
    def average_score_must_be_in_unit_interval(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"average_score must be a finite float, got: {value!r}"
            )
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"average_score must be between 0.0 and 1.0 inclusive, got: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def case_counts_must_be_consistent(self) -> "EvaluationRunSummary":
        if self.passed_cases + self.failed_cases != self.total_cases:
            raise ValueError(
                f"passed_cases ({self.passed_cases}) + failed_cases ({self.failed_cases}) "
                f"must equal total_cases ({self.total_cases})"
            )
        return self

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_iso8601(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"timestamp must be a valid ISO 8601 datetime string, got: {value!r}"
            )
        return value

    @field_validator("per_metric_averages")
    @classmethod
    def per_metric_values_must_be_valid(
        cls, mapping: dict[str, float]
    ) -> dict[str, float]:
        for key, value in mapping.items():
            if math.isnan(value) or math.isinf(value):
                raise ValueError(
                    f"per_metric_averages[{key!r}] must be a finite float, got: {value!r}"
                )
            if not (0.0 <= value <= 1.0):
                raise ValueError(
                    f"per_metric_averages[{key!r}] must be in [0.0, 1.0], got: {value!r}"
                )
        return mapping


# ── OutputQualityScoringResult ─────────────────────────────────────────────────


class OutputQualityScoringResult(BaseModel):
    """
    G-2 composite output-quality scoring result.

    Returned by output_quality_scorer.score_output_quality().  Combines the
    reused F-2 core case alignment score and G-1 citation quality score with
    a small set of final-output-only checks.

    core_case_alignment_score      — overall score from the F-2 scorer (0.0–1.0).
    citation_quality_score         — overall score from the G-1 citation scorer (0.0–1.0).
    dimension_scores               — per-dimension DimensionScores for final-output checks
                                     (summary_nonempty, recommendations_present_when_expected,
                                     unsupported_claims_clean).
    overall_score                  — mean of all five component scores; in [0.0, 1.0].
    pass_fail                      — True when hard-gate dims passed AND overall_score
                                     >= pass_threshold.
    pass_threshold                 — threshold used for this result (default OUTPUT_QUALITY_PASS_THRESHOLD).
    notes                          — optional free-text observation.
    """

    core_case_alignment_score: float
    citation_quality_score: float
    dimension_scores: list[DimensionScore]
    overall_score: float
    pass_fail: bool
    pass_threshold: float
    notes: str | None = None

    @field_validator("core_case_alignment_score", "citation_quality_score", "overall_score")
    @classmethod
    def must_be_in_unit_interval(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"score must be a finite float, got: {value!r}")
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"score must be in [0.0, 1.0], got: {value!r}")
        return value

    @field_validator("dimension_scores")
    @classmethod
    def dimension_scores_must_not_be_empty(
        cls, items: list[DimensionScore]
    ) -> list[DimensionScore]:
        if not items:
            raise ValueError(
                "dimension_scores must contain at least one DimensionScore entry"
            )
        return items

    def get(self, metric_name: str) -> DimensionScore | None:
        """Return the DimensionScore for the given metric_name, or None."""
        for ds in self.dimension_scores:
            if ds.metric_name == metric_name:
                return ds
        return None


# ── EvaluationMetricDatum ──────────────────────────────────────────────────────


class EvaluationMetricDatum(BaseModel):
    """
    Typed contract for a single CloudWatch Metrics datum (J-0).

    Represents one metric data point produced by the evaluation pipeline.
    Used by the J-0 metrics translator to construct CloudWatch put_metric_data
    payloads and by tests to verify metric translation correctness without
    live AWS calls.

    metric_name — CloudWatch metric name (e.g. "EvalPassCount").
    value       — numeric value for this datum; must be finite (negative allowed).
    unit        — CloudWatch metric unit string (e.g. "Count", "None" for scores/ratios).
    namespace   — CloudWatch namespace this metric belongs to.
    dimensions  — optional dimension key-value pairs (e.g. {"Environment": "development"}).
    """

    metric_name: str
    value: float
    unit: _CW_UNIT
    namespace: str
    dimensions: dict[str, str] = {}

    @field_validator("metric_name", "namespace")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be a non-empty string")
        return value

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"value must be a finite number, got: {value!r}"
            )
        return value
