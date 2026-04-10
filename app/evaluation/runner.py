"""
F-2 evaluation harness — batch evaluation runner.

Orchestrates a complete local evaluation run: loads the F-1 dataset, accepts
candidate outputs for each case, scores each pair, and aggregates the results
into a typed EvaluationRunSummary.

Candidate outputs may be provided as pre-loaded CaseOutput objects or as paths
to JSON files conforming to the CaseOutput schema — no live pipeline execution
is required.

Public surface:
  CandidateOutputMap   — type alias: dict[case_id, CaseOutput | Path]
  EvaluationRunResult  — returned by run_evaluation(); contains results + summary
  run_evaluation(...)  — execute a full evaluation batch
  RunnerError          — raised when a candidate is missing or cannot be loaded
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from app.evaluation.loader import EvaluationDataset, load_dataset
from app.evaluation.scorer import PASS_THRESHOLD, score_case
from app.schemas.evaluation_models import (
    DimensionScore,
    EvaluationResult,
    EvaluationRunSummary,
)
from app.schemas.output_models import CaseOutput
from app.utils.id_utils import generate_session_id

# Map of case_id → CaseOutput instance or path to a JSON file.
CandidateOutputMap = dict[str, Union[CaseOutput, Path]]

EVALUATION_VERSION = "f2-v1"


class RunnerError(Exception):
    """Raised when the runner cannot load a candidate output or finds a missing case."""


def _load_candidate(source: CaseOutput | Path, case_id: str) -> CaseOutput:
    """Resolve a candidate entry to a CaseOutput, loading from file if needed."""
    if isinstance(source, CaseOutput):
        return source
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RunnerError(
            f"Cannot read candidate file for case {case_id!r}: {exc}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunnerError(
            f"Malformed JSON in candidate file for case {case_id!r}: {exc}"
        ) from exc
    try:
        return CaseOutput(**data)
    except Exception as exc:
        raise RunnerError(
            f"Candidate for case {case_id!r} failed CaseOutput validation: {exc}"
        ) from exc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _per_metric_averages(results: list[EvaluationResult]) -> dict[str, float]:
    """Compute mean score per dimension across all results."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for result in results:
        for ds in result.dimension_scores:
            totals[ds.metric_name] = totals.get(ds.metric_name, 0.0) + ds.score
            counts[ds.metric_name] = counts.get(ds.metric_name, 0) + 1
    return {
        metric: round(totals[metric] / counts[metric], 6)
        for metric in sorted(totals)
    }


@dataclass(frozen=True)
class EvaluationRunResult:
    """
    Full output of run_evaluation().

    results — one EvaluationResult per evaluated case, sorted by case_id.
    summary — aggregated EvaluationRunSummary for the run.
    """

    results: tuple[EvaluationResult, ...]
    summary: EvaluationRunSummary


def run_evaluation(
    candidates: CandidateOutputMap,
    *,
    dataset: EvaluationDataset | None = None,
    dataset_dir: Path | None = None,
    run_id: str | None = None,
    pass_threshold: float = PASS_THRESHOLD,
) -> EvaluationRunResult:
    """
    Execute a full local evaluation batch.

    Parameters
    ----------
    candidates:
        Map of case_id → CaseOutput instance or Path to a candidate JSON file.
        Must contain an entry for every case_id in the dataset.
    dataset:
        Pre-loaded EvaluationDataset.  If None, load_dataset(dataset_dir) is called.
    dataset_dir:
        Override the default dataset path.  Ignored when dataset is supplied.
    run_id:
        Stable identifier for this run.  If None, a fresh session-style ID is generated.
    pass_threshold:
        Overall-score threshold used by the scorer (default PASS_THRESHOLD = 0.75).

    Returns
    -------
    EvaluationRunResult with one EvaluationResult per case and an aggregated summary.
    Raises RunnerError if any candidate is missing or cannot be loaded.
    """
    if dataset is None:
        dataset = load_dataset(dataset_dir)

    effective_run_id = run_id if run_id else generate_session_id()
    run_timestamp = _utc_now_iso()

    # Validate that every dataset case has a candidate entry before scoring.
    missing = [
        pair.case.case_id
        for pair in dataset
        if pair.case.case_id not in candidates
    ]
    if missing:
        raise RunnerError(
            f"No candidate output provided for case(s): {sorted(missing)}"
        )

    results: list[EvaluationResult] = []

    for pair in dataset:
        case_id = pair.case.case_id
        candidate = _load_candidate(candidates[case_id], case_id)
        scoring = score_case(candidate, pair.expected, pass_threshold=pass_threshold)

        dim_scores: list[DimensionScore] = list(scoring.dimension_scores)

        result = EvaluationResult(
            case_id=case_id,
            run_id=effective_run_id,
            evaluation_version=EVALUATION_VERSION,
            overall_score=scoring.overall_score,
            pass_fail=scoring.pass_fail,
            dimension_scores=dim_scores,
            timestamp=run_timestamp,
        )
        results.append(result)

    passed = sum(1 for r in results if r.pass_fail)
    failed = len(results) - passed
    avg_score = (
        sum(r.overall_score for r in results) / len(results) if results else 0.0
    )

    summary = EvaluationRunSummary(
        run_id=effective_run_id,
        total_cases=len(results),
        passed_cases=passed,
        failed_cases=failed,
        average_score=round(avg_score, 6),
        per_metric_averages=_per_metric_averages(results),
        timestamp=run_timestamp,
    )

    return EvaluationRunResult(
        results=tuple(results),
        summary=summary,
    )
