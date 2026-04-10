"""
I-2 baseline vs. optimized comparison workflow.

Answers: "Did the optimized configuration improve output quality and safety?"

This module is a pure offline comparison runner.  It loads baseline and optimized
candidate outputs from two directories, scores both sides using existing F/G/H
evaluation layers, computes per-case deltas, and aggregates a comparison summary.

No new scoring logic is introduced.  The module composes:
  - G-2 score_output_quality()  for quality scoring (F-2 + G-1 + final checks)
  - H-0 evaluate_safety()       for safety evaluation

Input contract:
  baseline_dir  — directory of JSON files, each a CaseOutput; filename stem = case_id.
  optimized_dir — same structure; must cover the same case_ids as baseline_dir.
  dataset_dir   — dataset directory containing cases/ and expected/ subdirectories.
                  Defaults to data/evaluation/.  Must include _citation_expectation
                  blocks in expected fixtures for citation scoring to be meaningful;
                  if a block is absent the runner uses a permissive default.

Verdict classification (per case):
  "improved"  — optimized G-2 score exceeds baseline by > COMPARISON_DELTA_EPSILON.
  "regressed" — optimized G-2 score is below baseline by > COMPARISON_DELTA_EPSILON.
  "unchanged" — absolute delta <= COMPARISON_DELTA_EPSILON.

Missing cases are tracked in ComparisonRunResult but do not raise an error; only
cases present in both directories are scored.

Public surface:
  COMPARISON_DELTA_EPSILON  — minimum score delta to be called an improvement or regression.
  ComparisonCaseResult      — per-case comparison result (frozen dataclass).
  ComparisonSummary         — aggregate comparison summary (frozen dataclass).
  ComparisonRunResult       — full run container (frozen dataclass).
  ComparisonAlignmentError  — raised when no cases can be compared (both dirs empty or absent).
  run_comparison(...)       — load, score, and compare baseline vs. optimized outputs.

Separation rules:
  - No boto3, no Bedrock client, no live AWS calls.
  - No CLI, Converse inference, or runtime pipeline imports.
  - Imports only: evaluation layer (G-2, H-0), schemas, and loader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.evaluation.loader import load_citation_expectations, load_dataset
from app.evaluation.output_quality_scorer import (
    OUTPUT_QUALITY_PASS_THRESHOLD,
    OutputQualityScoringResult,
    score_output_quality,
)
from app.evaluation.safety_policy import DEFAULT_POLICY, evaluate_safety
from app.schemas.evaluation_models import CitationExpectation, ComparisonVerdict
from app.schemas.output_models import CaseOutput
from app.schemas.safety_models import FailurePolicy, SafetyStatus

# Minimum absolute score delta required for a verdict of "improved" or "regressed".
# Deltas within this band are classified as "unchanged" to absorb floating-point noise.
COMPARISON_DELTA_EPSILON: float = 0.005

# Default dataset directory (data/evaluation/ relative to repo root).
_DEFAULT_DATASET_DIR: Path = (
    Path(__file__).parent.parent.parent / "data" / "evaluation"
)

# Default CitationExpectation used when the expected fixture has no _citation_expectation
# block.  Setting citations_required=False means citation absence is never penalised for
# unknown cases — callers who need stricter citation checks should ensure their fixture
# files include explicit _citation_expectation blocks.
_DEFAULT_CITATION_EXPECTATION_TEMPLATE = {
    "citations_required": False,
    "expected_source_labels": [],
    "required_excerpt_terms": [],
    "minimum_citation_count": 1,
}


class ComparisonAlignmentError(Exception):
    """
    Raised when the comparison runner cannot produce any results.

    This is raised only when:
    - Both baseline_dir and optimized_dir are absent or empty, OR
    - The dataset is empty.

    Individual missing cases (one side absent for a case) are tracked in
    ComparisonRunResult.missing_baseline_case_ids / missing_optimized_case_ids
    and never raise this error.
    """


# ── Result dataclasses ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComparisonCaseResult:
    """
    Per-case comparison result: baseline output vs. optimized output.

    case_id                — the evaluation case this result corresponds to.
    baseline_score         — G-2 overall quality score for the baseline output (0.0–1.0).
    optimized_score        — G-2 overall quality score for the optimized output (0.0–1.0).
    score_delta            — optimized_score minus baseline_score; positive means improvement.
    baseline_pass          — G-2 pass_fail for the baseline output.
    optimized_pass         — G-2 pass_fail for the optimized output.
    baseline_safety_status — H-0 SafetyStatus value (string) for the baseline output.
    optimized_safety_status — H-0 SafetyStatus value (string) for the optimized output.
    safety_status_changed  — True when baseline and optimized safety statuses differ.
    verdict                — ComparisonVerdict: "improved", "regressed", or "unchanged".
    """

    case_id: str
    baseline_score: float
    optimized_score: float
    score_delta: float
    baseline_pass: bool
    optimized_pass: bool
    baseline_safety_status: str
    optimized_safety_status: str
    safety_status_changed: bool
    verdict: ComparisonVerdict


@dataclass(frozen=True)
class ComparisonSummary:
    """
    Aggregate comparison summary across all evaluated cases.

    total_cases                    — number of cases scored (cases with both baseline and optimized).
    baseline_average_score         — mean G-2 score across all baseline outputs.
    optimized_average_score        — mean G-2 score across all optimized outputs.
    average_score_delta            — optimized_average_score minus baseline_average_score.
    baseline_pass_count            — number of baseline outputs with pass_fail=True.
    optimized_pass_count           — number of optimized outputs with pass_fail=True.
    baseline_safety_distribution   — mapping of SafetyStatus value → count for baseline.
    optimized_safety_distribution  — mapping of SafetyStatus value → count for optimized.
    improved_case_ids              — case_ids where verdict == "improved", in case_id order.
    regressed_case_ids             — case_ids where verdict == "regressed", in case_id order.
    unchanged_case_ids             — case_ids where verdict == "unchanged", in case_id order.
    """

    total_cases: int
    baseline_average_score: float
    optimized_average_score: float
    average_score_delta: float
    baseline_pass_count: int
    optimized_pass_count: int
    baseline_safety_distribution: dict[str, int]
    optimized_safety_distribution: dict[str, int]
    improved_case_ids: tuple[str, ...]
    regressed_case_ids: tuple[str, ...]
    unchanged_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class ComparisonRunResult:
    """
    Full output of run_comparison().

    case_results               — one ComparisonCaseResult per scored case, sorted by case_id.
    summary                    — aggregate ComparisonSummary.
    missing_baseline_case_ids  — case_ids from the dataset that had no baseline file.
    missing_optimized_case_ids — case_ids from the dataset that had no optimized file.
    """

    case_results: tuple[ComparisonCaseResult, ...]
    summary: ComparisonSummary
    missing_baseline_case_ids: tuple[str, ...]
    missing_optimized_case_ids: tuple[str, ...]


# ── Internal helpers ───────────────────────────────────────────────────────────


def _scan_directory(directory: Path) -> dict[str, Path]:
    """
    Return a mapping of case_id → Path for all *.json files in directory.

    The case_id is the file stem (filename without the .json extension).
    Files that are not valid JSON are not filtered here — errors surface at load time.
    Returns an empty dict when the directory does not exist.
    """
    if not directory.is_dir():
        return {}
    return {p.stem: p for p in sorted(directory.glob("*.json"))}


def _load_case_output(path: Path, case_id: str) -> CaseOutput:
    """Load a CaseOutput from a JSON file, raising ValueError on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"Cannot read candidate file for case {case_id!r}: {exc}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed JSON in candidate file for case {case_id!r}: {exc}"
        ) from exc
    try:
        return CaseOutput.model_validate(data)
    except Exception as exc:
        raise ValueError(
            f"Candidate for case {case_id!r} failed CaseOutput validation: {exc}"
        ) from exc


def _classify_verdict(delta: float) -> ComparisonVerdict:
    """Classify a score delta into a ComparisonVerdict."""
    if delta > COMPARISON_DELTA_EPSILON:
        return "improved"
    if delta < -COMPARISON_DELTA_EPSILON:
        return "regressed"
    return "unchanged"


def _build_safety_distribution(results: list[ComparisonCaseResult], side: str) -> dict[str, int]:
    """
    Build a SafetyStatus → count distribution from a list of case results.

    side must be "baseline" or "optimized".
    """
    distribution: dict[str, int] = {}
    for r in results:
        status = r.baseline_safety_status if side == "baseline" else r.optimized_safety_status
        distribution[status] = distribution.get(status, 0) + 1
    return distribution


def _build_summary(case_results: list[ComparisonCaseResult]) -> ComparisonSummary:
    """Aggregate a list of ComparisonCaseResult objects into a ComparisonSummary."""
    total = len(case_results)

    if total == 0:
        return ComparisonSummary(
            total_cases=0,
            baseline_average_score=0.0,
            optimized_average_score=0.0,
            average_score_delta=0.0,
            baseline_pass_count=0,
            optimized_pass_count=0,
            baseline_safety_distribution={},
            optimized_safety_distribution={},
            improved_case_ids=(),
            regressed_case_ids=(),
            unchanged_case_ids=(),
        )

    baseline_avg = sum(r.baseline_score for r in case_results) / total
    optimized_avg = sum(r.optimized_score for r in case_results) / total

    improved = tuple(r.case_id for r in case_results if r.verdict == "improved")
    regressed = tuple(r.case_id for r in case_results if r.verdict == "regressed")
    unchanged = tuple(r.case_id for r in case_results if r.verdict == "unchanged")

    return ComparisonSummary(
        total_cases=total,
        baseline_average_score=round(baseline_avg, 6),
        optimized_average_score=round(optimized_avg, 6),
        average_score_delta=round(optimized_avg - baseline_avg, 6),
        baseline_pass_count=sum(1 for r in case_results if r.baseline_pass),
        optimized_pass_count=sum(1 for r in case_results if r.optimized_pass),
        baseline_safety_distribution=_build_safety_distribution(case_results, "baseline"),
        optimized_safety_distribution=_build_safety_distribution(case_results, "optimized"),
        improved_case_ids=improved,
        regressed_case_ids=regressed,
        unchanged_case_ids=unchanged,
    )


# ── Public runner ──────────────────────────────────────────────────────────────


def run_comparison(
    baseline_dir: Path,
    optimized_dir: Path,
    *,
    dataset_dir: Path | None = None,
    safety_policy: FailurePolicy | None = None,
    quality_pass_threshold: float = OUTPUT_QUALITY_PASS_THRESHOLD,
) -> ComparisonRunResult:
    """
    Load, score, and compare baseline vs. optimized candidate outputs.

    For each case in the dataset that has both a baseline and an optimized candidate,
    this function:
      1. Scores both candidates with G-2 score_output_quality() (F-2 + G-1 + checks).
      2. Evaluates the safety of both candidates with H-0 evaluate_safety().
      3. Computes the score delta and classifies a verdict.
      4. Records whether the safety status changed.

    Cases present in the dataset but missing from one side are tracked in
    missing_baseline_case_ids / missing_optimized_case_ids respectively and are
    not scored.  No error is raised for individual missing cases.

    Parameters
    ----------
    baseline_dir
        Directory of baseline candidate outputs.  Files are expected to be valid
        CaseOutput JSON files, named {case_id}.json.
    optimized_dir
        Directory of optimized candidate outputs, same naming convention.
    dataset_dir
        Dataset directory with cases/ and expected/ subdirectories.
        Defaults to data/evaluation/.
    safety_policy
        FailurePolicy forwarded to H-0 evaluate_safety().  Defaults to DEFAULT_POLICY.
    quality_pass_threshold
        Pass/fail threshold forwarded to G-2 score_output_quality().
        Defaults to OUTPUT_QUALITY_PASS_THRESHOLD (0.75).

    Returns
    -------
    ComparisonRunResult

    Raises
    ------
    ComparisonAlignmentError
        When the dataset is empty (no cases to compare).
    """
    effective_policy = safety_policy if safety_policy is not None else DEFAULT_POLICY
    effective_dataset_dir = dataset_dir if dataset_dir is not None else _DEFAULT_DATASET_DIR

    # Load shared expected references.
    dataset = load_dataset(effective_dataset_dir)
    citation_expectations = load_citation_expectations(effective_dataset_dir)

    if len(dataset) == 0:
        raise ComparisonAlignmentError("Dataset is empty — no cases to compare.")

    # Scan candidate directories.
    baseline_files = _scan_directory(baseline_dir)
    optimized_files = _scan_directory(optimized_dir)

    missing_baseline: list[str] = []
    missing_optimized: list[str] = []
    case_results: list[ComparisonCaseResult] = []

    for pair in dataset:
        case_id = pair.case.case_id

        has_baseline = case_id in baseline_files
        has_optimized = case_id in optimized_files

        if not has_baseline:
            missing_baseline.append(case_id)
        if not has_optimized:
            missing_optimized.append(case_id)

        if not has_baseline or not has_optimized:
            continue

        # Load both candidate outputs.
        baseline_output = _load_case_output(baseline_files[case_id], case_id)
        optimized_output = _load_case_output(optimized_files[case_id], case_id)

        # Resolve citation expectation; fall back to permissive default if absent.
        if case_id in citation_expectations:
            citation_exp = citation_expectations[case_id]
        else:
            citation_exp = CitationExpectation(
                case_id=case_id,
                **_DEFAULT_CITATION_EXPECTATION_TEMPLATE,
            )

        # Score quality with G-2 (composes F-2 + G-1 + final checks).
        baseline_quality: OutputQualityScoringResult = score_output_quality(
            baseline_output,
            pair.expected,
            citation_exp,
            pass_threshold=quality_pass_threshold,
        )
        optimized_quality: OutputQualityScoringResult = score_output_quality(
            optimized_output,
            pair.expected,
            citation_exp,
            pass_threshold=quality_pass_threshold,
        )

        # Evaluate safety with H-0.
        baseline_safety = evaluate_safety(baseline_output, policy=effective_policy)
        optimized_safety = evaluate_safety(optimized_output, policy=effective_policy)

        baseline_safety_value = baseline_safety.status.value
        optimized_safety_value = optimized_safety.status.value

        delta = round(
            optimized_quality.overall_score - baseline_quality.overall_score, 6
        )

        case_results.append(
            ComparisonCaseResult(
                case_id=case_id,
                baseline_score=baseline_quality.overall_score,
                optimized_score=optimized_quality.overall_score,
                score_delta=delta,
                baseline_pass=baseline_quality.pass_fail,
                optimized_pass=optimized_quality.pass_fail,
                baseline_safety_status=baseline_safety_value,
                optimized_safety_status=optimized_safety_value,
                safety_status_changed=(baseline_safety_value != optimized_safety_value),
                verdict=_classify_verdict(delta),
            )
        )

    # Sort results by case_id for deterministic output order.
    case_results.sort(key=lambda r: r.case_id)

    summary = _build_summary(case_results)

    return ComparisonRunResult(
        case_results=tuple(case_results),
        summary=summary,
        missing_baseline_case_ids=tuple(sorted(missing_baseline)),
        missing_optimized_case_ids=tuple(sorted(missing_optimized)),
    )
