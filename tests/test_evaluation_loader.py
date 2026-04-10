"""
F-2 unit tests — evaluation dataset loader.

Coverage:
  load_dataset():
    - loads all F-1 fixtures successfully (integration against real data/evaluation/)
    - returns EvaluationDataset with correct count
    - pairs are sorted by case_id (deterministic ordering)
    - get() returns the correct pair or None

  _load_cases / _load_expected (exercised via load_dataset):
    - raises DatasetLoadError when cases/ directory is missing
    - raises DatasetLoadError when expected/ directory is missing
    - raises DatasetLoadError when cases/ directory is empty
    - raises DatasetLoadError when expected/ directory is empty
    - raises DatasetLoadError when a case fixture is malformed JSON
    - raises DatasetLoadError when an expected fixture is malformed JSON
    - raises DatasetLoadError when a case fixture fails schema validation
    - raises DatasetLoadError when an expected fixture fails schema validation

  Pair matching:
    - raises DatasetLoadError when a case has no matching expected output
    - raises DatasetLoadError when an expected output has no matching case

  Isolation:
    - loading twice returns identical results (no runtime state)

No AWS credentials or live calls required.
"""

import json
import shutil
from pathlib import Path

import pytest

from app.evaluation.loader import DatasetLoadError, EvaluationDataset, load_dataset

# Locate the real F-1 dataset shipped with the repo.
_REPO_ROOT = Path(__file__).parent.parent
_REAL_DATASET_DIR = _REPO_ROOT / "data" / "evaluation"
_EXPECTED_CASE_COUNT = 7  # eval-fda-001/002, eval-cisa-001/002, eval-incident-001/002, eval-edge-001


# ── real dataset ──────────────────────────────────────────────────────────────


class TestLoadRealDataset:
    def test_loads_successfully(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        assert isinstance(dataset, EvaluationDataset)

    def test_correct_case_count(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        assert len(dataset) == _EXPECTED_CASE_COUNT

    def test_pairs_sorted_by_case_id(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        ids = [pair.case.case_id for pair in dataset]
        assert ids == sorted(ids)

    def test_each_pair_case_id_matches_expected(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        for pair in dataset:
            assert pair.case.case_id == pair.expected.case_id

    def test_get_returns_correct_pair(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        pair = dataset.get("eval-fda-001")
        assert pair is not None
        assert pair.case.case_id == "eval-fda-001"
        assert pair.expected.case_id == "eval-fda-001"

    def test_get_returns_none_for_unknown_id(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        assert dataset.get("nonexistent-case") is None

    def test_deterministic_repeated_load(self):
        dataset_a = load_dataset(_REAL_DATASET_DIR)
        dataset_b = load_dataset(_REAL_DATASET_DIR)
        ids_a = [p.case.case_id for p in dataset_a]
        ids_b = [p.case.case_id for p in dataset_b]
        assert ids_a == ids_b

    def test_default_path_loads(self):
        # load_dataset() with no arg should resolve to data/evaluation/ from the repo.
        dataset = load_dataset()
        assert len(dataset) == _EXPECTED_CASE_COUNT


# ── missing directories ───────────────────────────────────────────────────────


class TestMissingDirectories:
    def test_missing_cases_dir_raises(self, tmp_path):
        (tmp_path / "expected").mkdir()
        (tmp_path / "expected" / "dummy.json").write_text(
            json.dumps({"case_id": "x", "expected_severity": "Low", "expected_category": "Regulatory", "expected_escalation_required": False}),
            encoding="utf-8",
        )
        with pytest.raises(DatasetLoadError, match="cases directory not found"):
            load_dataset(tmp_path)

    def test_missing_expected_dir_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "cases" / "dummy.json").write_text(
            json.dumps({"case_id": "x", "source_filename": "f.md", "source_type": "FDA", "document_date": "2025-01-01"}),
            encoding="utf-8",
        )
        with pytest.raises(DatasetLoadError, match="expected directory not found"):
            load_dataset(tmp_path)


# ── empty directories ─────────────────────────────────────────────────────────


class TestEmptyDirectories:
    def test_empty_cases_dir_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        with pytest.raises(DatasetLoadError, match="No JSON fixtures found"):
            load_dataset(tmp_path)

    def test_empty_expected_dir_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        case_data = {
            "case_id": "x",
            "source_filename": "f.md",
            "source_type": "FDA",
            "document_date": "2025-01-01",
        }
        (tmp_path / "cases" / "x.json").write_text(json.dumps(case_data), encoding="utf-8")
        with pytest.raises(DatasetLoadError, match="No JSON fixtures found"):
            load_dataset(tmp_path)


# ── malformed fixtures ────────────────────────────────────────────────────────


class TestMalformedFixtures:
    def _minimal_expected(self, case_id: str) -> dict:
        return {
            "case_id": case_id,
            "expected_severity": "Low",
            "expected_category": "Regulatory",
            "expected_escalation_required": False,
        }

    def _minimal_case(self, case_id: str) -> dict:
        return {
            "case_id": case_id,
            "source_filename": "f.md",
            "source_type": "FDA",
            "document_date": "2025-01-01",
        }

    def test_malformed_json_in_case_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        (tmp_path / "cases" / "bad.json").write_text("{ not json }", encoding="utf-8")
        (tmp_path / "expected" / "x.json").write_text(
            json.dumps(self._minimal_expected("x")), encoding="utf-8"
        )
        with pytest.raises(DatasetLoadError, match="Malformed JSON"):
            load_dataset(tmp_path)

    def test_malformed_json_in_expected_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        (tmp_path / "cases" / "x.json").write_text(
            json.dumps(self._minimal_case("x")), encoding="utf-8"
        )
        (tmp_path / "expected" / "bad.json").write_text("NOT JSON", encoding="utf-8")
        with pytest.raises(DatasetLoadError, match="Malformed JSON"):
            load_dataset(tmp_path)

    def test_invalid_schema_in_case_raises(self, tmp_path):
        """Case fixture missing required field source_filename."""
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        bad_case = {"case_id": "x", "source_type": "FDA", "document_date": "2025-01-01"}
        (tmp_path / "cases" / "x.json").write_text(json.dumps(bad_case), encoding="utf-8")
        (tmp_path / "expected" / "x.json").write_text(
            json.dumps(self._minimal_expected("x")), encoding="utf-8"
        )
        with pytest.raises(DatasetLoadError, match="EvaluationCase validation"):
            load_dataset(tmp_path)

    def test_invalid_schema_in_expected_raises(self, tmp_path):
        """Expected fixture with invalid severity value."""
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        (tmp_path / "cases" / "x.json").write_text(
            json.dumps(self._minimal_case("x")), encoding="utf-8"
        )
        bad_expected = {
            "case_id": "x",
            "expected_severity": "Catastrophic",  # invalid
            "expected_category": "Regulatory",
            "expected_escalation_required": False,
        }
        (tmp_path / "expected" / "x.json").write_text(json.dumps(bad_expected), encoding="utf-8")
        with pytest.raises(DatasetLoadError, match="ExpectedOutput validation"):
            load_dataset(tmp_path)


# ── pair matching ─────────────────────────────────────────────────────────────


class TestPairMatching:
    def _write_case(self, directory: Path, case_id: str) -> None:
        data = {
            "case_id": case_id,
            "source_filename": "f.md",
            "source_type": "FDA",
            "document_date": "2025-01-01",
        }
        (directory / f"{case_id}.json").write_text(json.dumps(data), encoding="utf-8")

    def _write_expected(self, directory: Path, case_id: str) -> None:
        data = {
            "case_id": case_id,
            "expected_severity": "Low",
            "expected_category": "Regulatory",
            "expected_escalation_required": False,
        }
        (directory / f"{case_id}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_case_missing_expected_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        self._write_case(tmp_path / "cases", "case-a")
        self._write_case(tmp_path / "cases", "case-b")
        self._write_expected(tmp_path / "expected", "case-a")
        # case-b has no matching expected
        with pytest.raises(DatasetLoadError, match="no matching expected output"):
            load_dataset(tmp_path)

    def test_expected_missing_case_raises(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        self._write_case(tmp_path / "cases", "case-a")
        self._write_expected(tmp_path / "expected", "case-a")
        self._write_expected(tmp_path / "expected", "case-orphan")
        # case-orphan has no matching case
        with pytest.raises(DatasetLoadError, match="no matching case"):
            load_dataset(tmp_path)

    def test_matched_pair_loads_correctly(self, tmp_path):
        (tmp_path / "cases").mkdir()
        (tmp_path / "expected").mkdir()
        self._write_case(tmp_path / "cases", "case-a")
        self._write_expected(tmp_path / "expected", "case-a")
        dataset = load_dataset(tmp_path)
        assert len(dataset) == 1
        assert dataset.get("case-a") is not None
