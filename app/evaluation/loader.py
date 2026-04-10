"""
F-2 evaluation harness — dataset loader.

Loads EvaluationCase and ExpectedOutput fixtures from the F-1 reference dataset
under data/evaluation/.  Returns matched pairs sorted by case_id for deterministic
ordering across runs.

Also exposes load_retrieval_expectations() for G-0 retrieval quality scoring,
which extracts the _retrieval_expectation blocks embedded in expected fixtures.

Also exposes load_citation_expectations() for G-1 citation quality scoring,
which extracts the _citation_expectation blocks embedded in expected fixtures.

Public surface:
  EvaluationDataset                 — container returned by load_dataset()
  load_dataset(dataset_dir)         — load and validate all fixtures; raises on any error
  load_retrieval_expectations(...)  — load RetrievalExpectation objects from expected fixtures
  load_citation_expectations(...)   — load CitationExpectation objects from expected fixtures
  DatasetLoadError                  — raised when fixtures are missing, mismatched, or malformed
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.schemas.evaluation_models import (
    CitationExpectation,
    EvaluationCase,
    ExpectedOutput,
    RetrievalExpectation,
)

# Default path relative to the repo root; callers may override for tests.
_DEFAULT_DATASET_DIR = Path(__file__).parent.parent.parent / "data" / "evaluation"


class DatasetLoadError(Exception):
    """Raised when the F-1 dataset cannot be loaded due to missing, mismatched,
    or structurally invalid fixture files."""


@dataclass(frozen=True)
class EvaluationPair:
    """A matched (EvaluationCase, ExpectedOutput) for a single case_id."""

    case: EvaluationCase
    expected: ExpectedOutput


@dataclass(frozen=True)
class EvaluationDataset:
    """All matched pairs loaded from the F-1 reference dataset, sorted by case_id."""

    pairs: tuple[EvaluationPair, ...]

    def __len__(self) -> int:
        return len(self.pairs)

    def __iter__(self):
        return iter(self.pairs)

    def get(self, case_id: str) -> EvaluationPair | None:
        """Return the pair for the given case_id, or None if not found."""
        for pair in self.pairs:
            if pair.case.case_id == case_id:
                return pair
        return None


def _load_json(path: Path) -> dict:
    """Load a JSON file, raising DatasetLoadError on any parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetLoadError(f"Cannot read fixture file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetLoadError(
            f"Malformed JSON in fixture file {path}: {exc}"
        ) from exc


def _load_cases(cases_dir: Path) -> dict[str, EvaluationCase]:
    """Load all EvaluationCase fixtures from cases_dir.  Keys are case_id values."""
    if not cases_dir.is_dir():
        raise DatasetLoadError(
            f"Expected cases directory not found: {cases_dir}"
        )

    json_files = sorted(cases_dir.glob("*.json"))
    if not json_files:
        raise DatasetLoadError(f"No JSON fixtures found in {cases_dir}")

    cases: dict[str, EvaluationCase] = {}
    for path in json_files:
        raw = _load_json(path)
        # Strip private metadata keys (prefixed with "_") before schema validation.
        data = {k: v for k, v in raw.items() if not k.startswith("_")}
        try:
            case = EvaluationCase(**data)
        except Exception as exc:
            raise DatasetLoadError(
                f"Fixture {path.name} failed EvaluationCase validation: {exc}"
            ) from exc
        cases[case.case_id] = case

    return cases


def _load_expected(expected_dir: Path) -> dict[str, ExpectedOutput]:
    """Load all ExpectedOutput fixtures from expected_dir.  Keys are case_id values."""
    if not expected_dir.is_dir():
        raise DatasetLoadError(
            f"Expected expected directory not found: {expected_dir}"
        )

    json_files = sorted(expected_dir.glob("*.json"))
    if not json_files:
        raise DatasetLoadError(f"No JSON fixtures found in {expected_dir}")

    expected: dict[str, ExpectedOutput] = {}
    for path in json_files:
        raw = _load_json(path)
        # Strip private metadata keys and the nested _retrieval_expectation block.
        data = {k: v for k, v in raw.items() if not k.startswith("_")}
        try:
            output = ExpectedOutput(**data)
        except Exception as exc:
            raise DatasetLoadError(
                f"Fixture {path.name} failed ExpectedOutput validation: {exc}"
            ) from exc
        expected[output.case_id] = output

    return expected


def load_retrieval_expectations(
    dataset_dir: Path | None = None,
) -> dict[str, RetrievalExpectation]:
    """
    Load RetrievalExpectation objects from the F-1 expected fixtures.

    Each expected fixture may embed a ``_retrieval_expectation`` block.
    This function extracts and validates those blocks, returning a mapping
    of case_id → RetrievalExpectation for all cases that have one.

    Cases without a ``_retrieval_expectation`` block (e.g. thin edge cases)
    are silently omitted from the returned dict — callers should handle absence.

    Raises DatasetLoadError if any present block fails schema validation.
    """
    root = dataset_dir if dataset_dir is not None else _DEFAULT_DATASET_DIR
    expected_dir = root / "expected"

    if not expected_dir.is_dir():
        raise DatasetLoadError(
            f"Expected expected directory not found: {expected_dir}"
        )

    json_files = sorted(expected_dir.glob("*.json"))
    if not json_files:
        raise DatasetLoadError(f"No JSON fixtures found in {expected_dir}")

    expectations: dict[str, RetrievalExpectation] = {}
    for path in json_files:
        raw = _load_json(path)
        block = raw.get("_retrieval_expectation")
        if block is None:
            continue
        data = {k: v for k, v in block.items() if not k.startswith("_")}
        try:
            expectation = RetrievalExpectation(**data)
        except Exception as exc:
            raise DatasetLoadError(
                f"Fixture {path.name} has an invalid _retrieval_expectation block: {exc}"
            ) from exc
        expectations[expectation.case_id] = expectation

    return expectations


def load_citation_expectations(
    dataset_dir: Path | None = None,
) -> dict[str, CitationExpectation]:
    """
    Load CitationExpectation objects from the F-1 expected fixtures.

    Each expected fixture may embed a ``_citation_expectation`` block.
    This function extracts and validates those blocks, returning a mapping
    of case_id → CitationExpectation for all cases that have one.

    Cases without a ``_citation_expectation`` block are silently omitted from
    the returned dict — callers should handle absence (treat as N/A).

    Raises DatasetLoadError if any present block fails schema validation.
    """
    root = dataset_dir if dataset_dir is not None else _DEFAULT_DATASET_DIR
    expected_dir = root / "expected"

    if not expected_dir.is_dir():
        raise DatasetLoadError(
            f"Expected expected directory not found: {expected_dir}"
        )

    json_files = sorted(expected_dir.glob("*.json"))
    if not json_files:
        raise DatasetLoadError(f"No JSON fixtures found in {expected_dir}")

    expectations: dict[str, CitationExpectation] = {}
    for path in json_files:
        raw = _load_json(path)
        block = raw.get("_citation_expectation")
        if block is None:
            continue
        data = {k: v for k, v in block.items() if not k.startswith("_")}
        try:
            expectation = CitationExpectation(**data)
        except Exception as exc:
            raise DatasetLoadError(
                f"Fixture {path.name} has an invalid _citation_expectation block: {exc}"
            ) from exc
        expectations[expectation.case_id] = expectation

    return expectations


def load_dataset(dataset_dir: Path | None = None) -> EvaluationDataset:
    """
    Load the F-1 evaluation dataset from dataset_dir (defaults to data/evaluation/).

    Enforces:
    - Both cases/ and expected/ subdirectories must exist and be non-empty.
    - Every case_id in cases/ must have a matching case_id in expected/.
    - Every case_id in expected/ must have a matching case_id in cases/.
    - All fixtures must parse as valid JSON and conform to their schemas.

    Returns an EvaluationDataset with pairs sorted by case_id (deterministic order).
    Raises DatasetLoadError for any violation.
    """
    root = dataset_dir if dataset_dir is not None else _DEFAULT_DATASET_DIR

    cases = _load_cases(root / "cases")
    expected = _load_expected(root / "expected")

    cases_ids = set(cases.keys())
    expected_ids = set(expected.keys())

    missing_expected = cases_ids - expected_ids
    if missing_expected:
        raise DatasetLoadError(
            f"Cases with no matching expected output: {sorted(missing_expected)}"
        )

    missing_cases = expected_ids - cases_ids
    if missing_cases:
        raise DatasetLoadError(
            f"Expected outputs with no matching case: {sorted(missing_cases)}"
        )

    pairs = tuple(
        EvaluationPair(case=cases[cid], expected=expected[cid])
        for cid in sorted(cases_ids)
    )
    return EvaluationDataset(pairs=pairs)
