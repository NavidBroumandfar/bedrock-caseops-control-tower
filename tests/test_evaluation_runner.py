"""
F-2 unit tests — batch evaluation runner.

Coverage:
  run_evaluation():
    - evaluates a single case end-to-end
    - evaluates multiple cases end-to-end
    - returns typed EvaluationResult per case
    - returns typed EvaluationRunSummary
    - results are sorted by case_id (deterministic order)
    - accepts CaseOutput objects directly (in-memory dict path)
    - accepts Path to JSON candidate files
    - stable deterministic output on repeated calls with the same inputs
    - uses provided run_id when given
    - generates a run_id when none provided
    - respects custom pass_threshold
    - accepts a pre-loaded EvaluationDataset (no disk re-read)

  RunnerError:
    - raised when a candidate is missing for a case
    - raised when a candidate JSON file cannot be read
    - raised when a candidate JSON is malformed
    - raised when a candidate JSON fails CaseOutput validation

  EvaluationRunSummary fields:
    - total_cases, passed_cases, failed_cases are consistent
    - average_score is the mean of all overall_scores
    - per_metric_averages contains all six scoring dimensions

  EvaluationResult fields:
    - case_id matches the dataset case
    - run_id matches the run
    - evaluation_version is set
    - overall_score is in [0.0, 1.0]
    - dimension_scores is non-empty
    - timestamp is an ISO 8601 string

No AWS credentials or live calls required.
"""

import json
from pathlib import Path

import pytest

from app.evaluation.loader import EvaluationDataset, EvaluationPair, load_dataset
from app.evaluation.runner import RunnerError, run_evaluation
from app.evaluation.scorer import PASS_THRESHOLD
from app.schemas.evaluation_models import EvaluationCase, ExpectedOutput
from app.schemas.output_models import CaseOutput

_REPO_ROOT = Path(__file__).parent.parent
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "candidate_outputs"
_REAL_DATASET_DIR = _REPO_ROOT / "data" / "evaluation"


# ── fixture helpers ────────────────────────────────────────────────────────────


def _make_case(case_id: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        source_filename="test.md",
        source_type="FDA",
        document_date="2025-01-01",
    )


def _make_expected(
    case_id: str,
    severity="High",
    category="Regulatory",
    escalation=True,
) -> ExpectedOutput:
    return ExpectedOutput(
        case_id=case_id,
        expected_severity=severity,
        expected_category=category,
        expected_escalation_required=escalation,
    )


def _make_candidate(
    severity="High",
    category="Regulatory",
    escalation_required=True,
    summary="quality CAPA corrective",
    recommendations=None,
    forbidden_text=None,
) -> CaseOutput:
    if recommendations is None:
        recommendations = ["Initiate CAPA corrective action per FDA compliance guidelines."]
    return CaseOutput(
        document_id="doc-test-runner-001",
        source_filename="test.md",
        source_type="FDA",
        severity=severity,
        category=category,
        summary=summary if not forbidden_text else forbidden_text,
        recommendations=recommendations,
        citations=[],
        confidence_score=0.85,
        unsupported_claims=[],
        escalation_required=escalation_required,
        escalation_reason="Severity is High." if escalation_required else None,
        validated_by="validation-agent-v1",
        timestamp="2025-01-01T00:00:00+00:00",
    )


def _make_single_dataset(case_id: str = "case-alpha", **expected_kwargs) -> EvaluationDataset:
    pair = EvaluationPair(
        case=_make_case(case_id),
        expected=_make_expected(case_id, **expected_kwargs),
    )
    return EvaluationDataset(pairs=(pair,))


def _make_multi_dataset(*case_ids: str) -> EvaluationDataset:
    pairs = tuple(
        EvaluationPair(case=_make_case(cid), expected=_make_expected(cid))
        for cid in sorted(case_ids)
    )
    return EvaluationDataset(pairs=pairs)


# ── single case end-to-end ────────────────────────────────────────────────────


class TestSingleCaseEvaluation:
    def test_returns_one_result(self):
        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert len(run_result.results) == 1

    def test_result_has_correct_case_id(self):
        dataset = _make_single_dataset("my-case")
        candidates = {"my-case": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert run_result.results[0].case_id == "my-case"

    def test_result_is_typed_evaluation_result(self):
        from app.schemas.evaluation_models import EvaluationResult

        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert isinstance(run_result.results[0], EvaluationResult)

    def test_summary_is_typed_evaluation_run_summary(self):
        from app.schemas.evaluation_models import EvaluationRunSummary

        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert isinstance(run_result.summary, EvaluationRunSummary)

    def test_result_has_non_empty_dimension_scores(self):
        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert len(run_result.results[0].dimension_scores) > 0

    def test_result_evaluation_version_set(self):
        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert run_result.results[0].evaluation_version != ""

    def test_result_timestamp_is_iso8601(self):
        from datetime import datetime

        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        ts = run_result.results[0].timestamp
        # Should parse without error.
        datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_overall_score_in_unit_interval(self):
        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert 0.0 <= run_result.results[0].overall_score <= 1.0

    def test_passing_candidate_pass_fail_true(self):
        dataset = _make_single_dataset(severity="High", escalation=True)
        candidates = {"case-alpha": _make_candidate(severity="High", escalation_required=True)}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert run_result.results[0].pass_fail is True

    def test_failing_candidate_pass_fail_false(self):
        dataset = _make_single_dataset(severity="High", escalation=True)
        candidates = {
            "case-alpha": _make_candidate(
                severity="Low",  # wrong severity → hard gate fail
                escalation_required=False,
            )
        }
        run_result = run_evaluation(candidates, dataset=dataset)
        assert run_result.results[0].pass_fail is False


# ── multiple cases end-to-end ─────────────────────────────────────────────────


class TestMultiCaseEvaluation:
    def test_all_results_returned(self):
        dataset = _make_multi_dataset("case-a", "case-b", "case-c")
        candidates = {
            "case-a": _make_candidate(),
            "case-b": _make_candidate(),
            "case-c": _make_candidate(),
        }
        run_result = run_evaluation(candidates, dataset=dataset)
        assert len(run_result.results) == 3

    def test_results_sorted_by_case_id(self):
        dataset = _make_multi_dataset("case-z", "case-a", "case-m")
        candidates = {
            "case-z": _make_candidate(),
            "case-a": _make_candidate(),
            "case-m": _make_candidate(),
        }
        run_result = run_evaluation(candidates, dataset=dataset)
        ids = [r.case_id for r in run_result.results]
        # Dataset is sorted at load time; results follow that order.
        assert ids == sorted(ids)

    def test_summary_total_cases_correct(self):
        dataset = _make_multi_dataset("case-a", "case-b", "case-c")
        candidates = {cid: _make_candidate() for cid in ("case-a", "case-b", "case-c")}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert run_result.summary.total_cases == 3

    def test_summary_passed_failed_sum_equals_total(self):
        dataset = _make_multi_dataset("case-a", "case-b", "case-c")
        candidates = {cid: _make_candidate() for cid in ("case-a", "case-b", "case-c")}
        run_result = run_evaluation(candidates, dataset=dataset)
        s = run_result.summary
        assert s.passed_cases + s.failed_cases == s.total_cases

    def test_summary_average_score_is_mean(self):
        dataset = _make_multi_dataset("case-a", "case-b")
        candidates = {cid: _make_candidate() for cid in ("case-a", "case-b")}
        run_result = run_evaluation(candidates, dataset=dataset)
        results = run_result.results
        expected_avg = sum(r.overall_score for r in results) / len(results)
        assert abs(run_result.summary.average_score - expected_avg) < 1e-6

    def test_summary_per_metric_averages_has_all_dimensions(self):
        from app.evaluation.scorer import (
            DIM_CATEGORY, DIM_ESCALATION, DIM_FORBIDDEN,
            DIM_KEYWORD_COVERAGE, DIM_SEVERITY, DIM_SUMMARY_FACTS,
        )

        dataset = _make_multi_dataset("case-a", "case-b")
        candidates = {cid: _make_candidate() for cid in ("case-a", "case-b")}
        run_result = run_evaluation(candidates, dataset=dataset)
        averages = run_result.summary.per_metric_averages
        for dim in [DIM_SEVERITY, DIM_CATEGORY, DIM_ESCALATION,
                    DIM_SUMMARY_FACTS, DIM_KEYWORD_COVERAGE, DIM_FORBIDDEN]:
            assert dim in averages


# ── run_id behaviour ──────────────────────────────────────────────────────────


class TestRunId:
    def test_provided_run_id_used(self):
        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset, run_id="my-run-123")
        assert run_result.results[0].run_id == "my-run-123"
        assert run_result.summary.run_id == "my-run-123"

    def test_auto_generated_run_id_when_none(self):
        dataset = _make_single_dataset()
        candidates = {"case-alpha": _make_candidate()}
        run_result = run_evaluation(candidates, dataset=dataset)
        assert run_result.results[0].run_id != ""
        assert run_result.summary.run_id == run_result.results[0].run_id

    def test_all_results_share_same_run_id(self):
        dataset = _make_multi_dataset("case-a", "case-b")
        candidates = {cid: _make_candidate() for cid in ("case-a", "case-b")}
        run_result = run_evaluation(candidates, dataset=dataset)
        run_ids = {r.run_id for r in run_result.results}
        assert len(run_ids) == 1


# ── candidate from file ────────────────────────────────────────────────────────


class TestCandidateFromFile:
    def test_loads_strong_pass_fixture(self):
        """The strong_pass fixture is for eval-fda-001 from the real F-1 dataset.
        We use a minimal single-case dataset matching that structure here."""
        fixture_path = _FIXTURES_DIR / "strong_pass.json"
        pair = EvaluationPair(
            case=_make_case("eval-fda-001"),
            expected=_make_expected("eval-fda-001", severity="High", escalation=True),
        )
        dataset = EvaluationDataset(pairs=(pair,))
        run_result = run_evaluation({"eval-fda-001": fixture_path}, dataset=dataset)
        assert run_result.results[0].case_id == "eval-fda-001"
        assert 0.0 <= run_result.results[0].overall_score <= 1.0

    def test_loads_thin_edge_fixture(self):
        fixture_path = _FIXTURES_DIR / "thin_edge.json"
        pair = EvaluationPair(
            case=_make_case("eval-edge-001"),
            expected=_make_expected("eval-edge-001", severity="Low", escalation=False),
        )
        dataset = EvaluationDataset(pairs=(pair,))
        run_result = run_evaluation({"eval-edge-001": fixture_path}, dataset=dataset)
        assert run_result.results[0].case_id == "eval-edge-001"

    def test_missing_candidate_file_raises_runner_error(self, tmp_path):
        dataset = _make_single_dataset()
        nonexistent = tmp_path / "does_not_exist.json"
        with pytest.raises(RunnerError, match="Cannot read candidate file"):
            run_evaluation({"case-alpha": nonexistent}, dataset=dataset)

    def test_malformed_json_file_raises_runner_error(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ not json }", encoding="utf-8")
        dataset = _make_single_dataset()
        with pytest.raises(RunnerError, match="Malformed JSON"):
            run_evaluation({"case-alpha": bad_file}, dataset=dataset)

    def test_invalid_case_output_schema_raises_runner_error(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"document_id": "x"}), encoding="utf-8")
        dataset = _make_single_dataset()
        with pytest.raises(RunnerError, match="CaseOutput validation"):
            run_evaluation({"case-alpha": bad_file}, dataset=dataset)


# ── missing candidates ─────────────────────────────────────────────────────────


class TestMissingCandidates:
    def test_missing_candidate_for_case_raises(self):
        dataset = _make_multi_dataset("case-a", "case-b")
        # Only provide one of the two required candidates.
        candidates = {"case-a": _make_candidate()}
        with pytest.raises(RunnerError, match="case-b"):
            run_evaluation(candidates, dataset=dataset)


# ── pass_threshold ─────────────────────────────────────────────────────────────


class TestPassThreshold:
    def test_custom_threshold_propagated(self):
        dataset = _make_single_dataset(severity="High", escalation=True)
        # Candidate is correct on hard gates but scoring may not reach 0.99.
        candidates = {"case-alpha": _make_candidate(severity="High", escalation_required=True)}
        run_result = run_evaluation(candidates, dataset=dataset, pass_threshold=0.99)
        # Result should reflect that the threshold was applied (may or may not pass — that's OK,
        # we just verify the runner accepted the param and did not crash).
        assert isinstance(run_result.results[0].pass_fail, bool)


# ── determinism ────────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_inputs_same_scores(self):
        dataset = _make_multi_dataset("case-a", "case-b")
        candidates = {cid: _make_candidate() for cid in ("case-a", "case-b")}
        run_a = run_evaluation(candidates, dataset=dataset, run_id="fixed")
        run_b = run_evaluation(candidates, dataset=dataset, run_id="fixed")
        scores_a = [r.overall_score for r in run_a.results]
        scores_b = [r.overall_score for r in run_b.results]
        assert scores_a == scores_b


# ── real dataset smoke test ───────────────────────────────────────────────────


class TestRealDatasetSmoke:
    """Light smoke test: load the real F-1 dataset and evaluate the strong_pass
    and thin_edge fixtures to confirm the harness works end-to-end with real data."""

    def _load_case_output(self, path: Path) -> CaseOutput:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("_note", None)
        return CaseOutput(**data)

    def test_strong_pass_scores_above_threshold_for_fda_001(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        fda_pair = dataset.get("eval-fda-001")
        assert fda_pair is not None

        candidate = self._load_case_output(_FIXTURES_DIR / "strong_pass.json")
        single_dataset = EvaluationDataset(pairs=(fda_pair,))
        run_result = run_evaluation({"eval-fda-001": candidate}, dataset=single_dataset)
        assert run_result.results[0].overall_score >= PASS_THRESHOLD
        assert run_result.results[0].pass_fail is True

    def test_weak_fail_does_not_pass_for_fda_001(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        fda_pair = dataset.get("eval-fda-001")
        assert fda_pair is not None

        candidate = self._load_case_output(_FIXTURES_DIR / "weak_fail.json")
        single_dataset = EvaluationDataset(pairs=(fda_pair,))
        run_result = run_evaluation({"eval-fda-001": candidate}, dataset=single_dataset)
        assert run_result.results[0].pass_fail is False

    def test_forbidden_claim_fails_for_fda_001(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        fda_pair = dataset.get("eval-fda-001")
        assert fda_pair is not None

        candidate = self._load_case_output(_FIXTURES_DIR / "forbidden_claim.json")
        single_dataset = EvaluationDataset(pairs=(fda_pair,))
        run_result = run_evaluation({"eval-fda-001": candidate}, dataset=single_dataset)
        assert run_result.results[0].pass_fail is False

    def test_thin_edge_passes_for_edge_001(self):
        dataset = load_dataset(_REAL_DATASET_DIR)
        edge_pair = dataset.get("eval-edge-001")
        assert edge_pair is not None

        candidate = self._load_case_output(_FIXTURES_DIR / "thin_edge.json")
        single_dataset = EvaluationDataset(pairs=(edge_pair,))
        run_result = run_evaluation({"eval-edge-001": candidate}, dataset=single_dataset)
        # Thin edge: severity=Low correct, escalation=False correct, no forbidden claims,
        # one expected fact ("notice") present, no expected keywords (N/A → 1.0).
        assert run_result.results[0].pass_fail is True
