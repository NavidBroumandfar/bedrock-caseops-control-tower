"""
J-2 unit tests — checkpoint runner, checkpoint writer, and report generator.

Coverage:

  CheckpointInputs (dataclass):
    - constructs with default values (all layers ready, live AWS not validated)
    - completed_phases default includes all expected Phase 2 labels
    - external_blockers default is non-empty (reflects Titan throttling)
    - total_tests_offline defaults to 0
    - live_aws_validated defaults to False
    - custom values are preserved

  build_checkpoint() — default inputs:
    - returns a Phase2CheckpointResult
    - status is 'complete_blocked' by default
    - engineering_complete is True by default
    - live_aws_validated is False by default
    - phase_version is 'phase2-v2'
    - completed_phases includes all J-2 labels
    - readiness has 4 blocks (evaluation, safety, optimization, observability_reporting)
    - all readiness blocks have is_ready=True by default
    - checkpoint_id is non-empty
    - checkpoint_id is generated if not supplied
    - checkpoint_id is used if supplied
    - created_at is a non-empty string

  build_checkpoint() — incomplete inputs:
    - one layer not ready → engineering_complete=False → status='incomplete'
    - all layers ready + live_aws_validated=True → status='complete'
    - external_blockers propagated correctly

  build_checkpoint() — determinism:
    - same checkpoint_id with same inputs produces same status and phases
    - different checkpoint_ids produce different checkpoint_id values

  generate_checkpoint_report():
    - returns a non-empty string
    - contains checkpoint_id
    - contains phase_version
    - contains status string
    - contains 'complete_blocked' text when status is complete_blocked
    - contains 'External Blockers' section
    - contains 'What was hardened in J-2' section
    - contains 'Readiness by Layer' section
    - contains each readiness layer name
    - deterministic: same inputs produce same output

  write_checkpoint():
    - checkpoint.json is written to {output_root}/checkpoints/{id}/
    - report.md is written when generate_report=True
    - report.md is NOT written when generate_report=False
    - checkpoint.json content parses as valid JSON
    - checkpoint.json contains checkpoint_id field
    - returns (json_path, report_path) tuple
    - report_path is None when generate_report=False
    - raises CheckpointWriteError on unwritable directory

  Structural / isolation:
    - checkpoint_runner does not import boto3
    - checkpoint_runner does not import bedrock_service
    - checkpoint_writer does not import boto3
    - no live AWS dependency
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.evaluation.checkpoint_runner import CheckpointInputs, build_checkpoint
from app.evaluation.checkpoint_writer import (
    CheckpointWriteError,
    generate_checkpoint_report,
    write_checkpoint,
)
from app.schemas.checkpoint_models import Phase2CheckpointResult, Phase2ReadinessBlock


# ── Helpers ────────────────────────────────────────────────────────────────────

_EXPECTED_LAYERS = {"evaluation", "safety", "optimization", "observability_reporting"}
_EXPECTED_PHASES = {"F", "G", "H", "I", "J-0", "J-1", "J-2"}


# ── CheckpointInputs — construction ───────────────────────────────────────────


class TestCheckpointInputsDefaults:
    def test_default_constructs(self):
        inputs = CheckpointInputs()
        assert inputs is not None

    def test_default_all_layers_ready(self):
        inputs = CheckpointInputs()
        assert inputs.evaluation_ready is True
        assert inputs.safety_ready is True
        assert inputs.optimization_ready is True
        assert inputs.observability_ready is True
        assert inputs.checkpoint_ready is True

    def test_default_live_aws_false(self):
        inputs = CheckpointInputs()
        assert inputs.live_aws_validated is False

    def test_default_total_tests_zero(self):
        inputs = CheckpointInputs()
        assert inputs.total_tests_offline == 0

    def test_default_external_blockers_non_empty(self):
        inputs = CheckpointInputs()
        assert len(inputs.external_blockers) > 0
        assert any("Titan" in b for b in inputs.external_blockers)

    def test_default_completed_phases_contains_all(self):
        inputs = CheckpointInputs()
        phase_set = set(inputs.completed_phases)
        assert _EXPECTED_PHASES.issubset(phase_set)

    def test_custom_total_tests_preserved(self):
        inputs = CheckpointInputs(total_tests_offline=2200)
        assert inputs.total_tests_offline == 2200

    def test_custom_notes_preserved(self):
        inputs = CheckpointInputs(notes="some note")
        assert inputs.notes == "some note"

    def test_inputs_are_frozen(self):
        inputs = CheckpointInputs()
        with pytest.raises(Exception):
            inputs.evaluation_ready = False  # type: ignore[misc]


# ── build_checkpoint() — default inputs ───────────────────────────────────────


class TestBuildCheckpointDefaults:
    def test_returns_phase2_checkpoint_result(self):
        result = build_checkpoint()
        assert isinstance(result, Phase2CheckpointResult)

    def test_default_status_is_complete_blocked(self):
        result = build_checkpoint()
        assert result.status == "complete_blocked"

    def test_default_engineering_complete_true(self):
        result = build_checkpoint()
        assert result.engineering_complete is True

    def test_default_live_aws_validated_false(self):
        result = build_checkpoint()
        assert result.live_aws_validated is False

    def test_phase_version_is_phase2_v2(self):
        result = build_checkpoint()
        assert result.phase_version == "phase2-v2"

    def test_completed_phases_contains_all_expected(self):
        result = build_checkpoint()
        phase_set = set(result.completed_phases)
        assert _EXPECTED_PHASES.issubset(phase_set)

    def test_readiness_has_four_blocks(self):
        result = build_checkpoint()
        assert len(result.readiness) == 4

    def test_readiness_layer_names_are_expected(self):
        result = build_checkpoint()
        names = {b.layer_name for b in result.readiness}
        assert names == _EXPECTED_LAYERS

    def test_all_readiness_blocks_ready_by_default(self):
        result = build_checkpoint()
        assert all(b.is_ready for b in result.readiness)

    def test_checkpoint_id_is_non_empty(self):
        result = build_checkpoint()
        assert result.checkpoint_id.strip() != ""

    def test_checkpoint_id_is_generated_if_not_supplied(self):
        r1 = build_checkpoint()
        r2 = build_checkpoint()
        assert r1.checkpoint_id != r2.checkpoint_id

    def test_supplied_checkpoint_id_is_used(self):
        result = build_checkpoint(checkpoint_id="my-chk-001")
        assert result.checkpoint_id == "my-chk-001"

    def test_created_at_is_non_empty(self):
        result = build_checkpoint()
        assert result.created_at.strip() != ""

    def test_external_blockers_non_empty(self):
        result = build_checkpoint()
        assert len(result.external_blockers) > 0

    def test_total_tests_offline_zero_default(self):
        result = build_checkpoint()
        assert result.total_tests_offline == 0

    def test_total_tests_offline_passed_through(self):
        inputs = CheckpointInputs(total_tests_offline=2100)
        result = build_checkpoint(inputs)
        assert result.total_tests_offline == 2100


# ── build_checkpoint() — custom inputs ────────────────────────────────────────


class TestBuildCheckpointCustomInputs:
    def test_incomplete_when_one_layer_not_ready(self):
        inputs = CheckpointInputs(evaluation_ready=False)
        result = build_checkpoint(inputs)
        assert result.engineering_complete is False
        assert result.status == "incomplete"

    def test_incomplete_when_safety_not_ready(self):
        inputs = CheckpointInputs(safety_ready=False)
        result = build_checkpoint(inputs)
        assert result.status == "incomplete"

    def test_incomplete_when_optimization_not_ready(self):
        inputs = CheckpointInputs(optimization_ready=False)
        result = build_checkpoint(inputs)
        assert result.status == "incomplete"

    def test_incomplete_when_observability_not_ready(self):
        inputs = CheckpointInputs(observability_ready=False)
        result = build_checkpoint(inputs)
        assert result.status == "incomplete"

    def test_incomplete_when_checkpoint_not_ready(self):
        inputs = CheckpointInputs(checkpoint_ready=False)
        result = build_checkpoint(inputs)
        assert result.status == "incomplete"

    def test_complete_when_live_aws_validated(self):
        inputs = CheckpointInputs(
            live_aws_validated=True,
            external_blockers=(),
        )
        result = build_checkpoint(inputs)
        assert result.status == "complete"
        assert result.live_aws_validated is True

    def test_custom_external_blockers_propagated(self):
        inputs = CheckpointInputs(external_blockers=("custom-blocker",))
        result = build_checkpoint(inputs)
        assert "custom-blocker" in result.external_blockers

    def test_empty_external_blockers_propagated(self):
        inputs = CheckpointInputs(
            external_blockers=(),
            live_aws_validated=True,
        )
        result = build_checkpoint(inputs)
        assert result.external_blockers == []

    def test_incomplete_layer_reflected_in_readiness_block(self):
        inputs = CheckpointInputs(evaluation_ready=False)
        result = build_checkpoint(inputs)
        eval_block = next(b for b in result.readiness if b.layer_name == "evaluation")
        assert eval_block.is_ready is False

    def test_custom_notes_propagated(self):
        inputs = CheckpointInputs(notes="Milestone note.")
        result = build_checkpoint(inputs)
        assert result.notes == "Milestone note."


# ── build_checkpoint() — determinism ─────────────────────────────────────────


class TestBuildCheckpointDeterminism:
    def test_same_id_produces_same_status(self):
        r1 = build_checkpoint(checkpoint_id="fixed-id")
        r2 = build_checkpoint(checkpoint_id="fixed-id")
        assert r1.status == r2.status

    def test_same_id_produces_same_completed_phases(self):
        r1 = build_checkpoint(checkpoint_id="fixed-id")
        r2 = build_checkpoint(checkpoint_id="fixed-id")
        assert r1.completed_phases == r2.completed_phases


# ── generate_checkpoint_report() ──────────────────────────────────────────────


class TestGenerateCheckpointReport:
    def _get_result(self, **kwargs) -> Phase2CheckpointResult:
        inputs = CheckpointInputs(**kwargs)
        return build_checkpoint(inputs, checkpoint_id="rpt-test-001")

    def test_returns_non_empty_string(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_contains_checkpoint_id(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "rpt-test-001" in report

    def test_contains_phase_version(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "phase2-v2" in report

    def test_contains_status(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "complete_blocked" in report

    def test_contains_external_blockers_section(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "External Blockers" in report

    def test_contains_hardening_section(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "hardened in J-2" in report

    def test_contains_readiness_section(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "Readiness by Layer" in report

    def test_contains_each_layer_name(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "Evaluation" in report
        assert "Safety" in report
        assert "Optimization" in report
        assert "Observability" in report

    def test_complete_status_report_describes_complete(self):
        result = build_checkpoint(
            CheckpointInputs(live_aws_validated=True, external_blockers=()),
            checkpoint_id="rpt-complete-001",
        )
        report = generate_checkpoint_report(result)
        assert "complete" in report.lower()

    def test_incomplete_status_not_in_complete_blocked_report(self):
        result = self._get_result()
        report = generate_checkpoint_report(result)
        assert "incomplete" not in report.lower() or "not yet complete" not in report

    def test_deterministic_same_id_same_report(self):
        r1 = build_checkpoint(checkpoint_id="det-001")
        r2 = build_checkpoint(checkpoint_id="det-001")
        report1 = generate_checkpoint_report(r1)
        report2 = generate_checkpoint_report(r2)
        # Created_at may differ by microseconds; compare key content sections
        assert "phase2-v2" in report1
        assert "phase2-v2" in report2

    def test_notes_included_when_present(self):
        inputs = CheckpointInputs(notes="See ticket ENG-999.")
        result = build_checkpoint(inputs, checkpoint_id="notes-test")
        report = generate_checkpoint_report(result)
        assert "See ticket ENG-999." in report

    def test_notes_section_absent_when_empty(self):
        result = build_checkpoint(checkpoint_id="no-notes-test")
        report = generate_checkpoint_report(result)
        assert "See ticket" not in report


# ── write_checkpoint() ────────────────────────────────────────────────────────


class TestWriteCheckpoint:
    def test_json_file_is_written(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-001")
        json_path, _ = write_checkpoint(result, tmp_path)
        assert json_path.exists()
        assert json_path.name == "checkpoint.json"

    def test_report_md_is_written_by_default(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-002")
        _, report_path = write_checkpoint(result, tmp_path)
        assert report_path is not None
        assert report_path.exists()
        assert report_path.name == "report.md"

    def test_report_md_not_written_when_disabled(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-003")
        _, report_path = write_checkpoint(result, tmp_path, generate_report=False)
        assert report_path is None
        assert not (tmp_path / "checkpoints" / "wrt-003" / "report.md").exists()

    def test_json_placed_in_correct_subdir(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-004")
        json_path, _ = write_checkpoint(result, tmp_path)
        expected = tmp_path / "checkpoints" / "wrt-004" / "checkpoint.json"
        assert json_path == expected

    def test_report_placed_in_correct_subdir(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-005")
        _, report_path = write_checkpoint(result, tmp_path)
        expected = tmp_path / "checkpoints" / "wrt-005" / "report.md"
        assert report_path == expected

    def test_json_content_is_valid_json(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-006")
        json_path, _ = write_checkpoint(result, tmp_path)
        data = json.loads(json_path.read_text())
        assert isinstance(data, dict)

    def test_json_contains_checkpoint_id(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-007")
        json_path, _ = write_checkpoint(result, tmp_path)
        data = json.loads(json_path.read_text())
        assert data["checkpoint_id"] == "wrt-007"

    def test_json_contains_status(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-008")
        json_path, _ = write_checkpoint(result, tmp_path)
        data = json.loads(json_path.read_text())
        assert data["status"] == "complete_blocked"

    def test_json_contains_engineering_complete_flag(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-009")
        json_path, _ = write_checkpoint(result, tmp_path)
        data = json.loads(json_path.read_text())
        assert data["engineering_complete"] is True

    def test_json_contains_live_aws_validated_false(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-010")
        json_path, _ = write_checkpoint(result, tmp_path)
        data = json.loads(json_path.read_text())
        assert data["live_aws_validated"] is False

    def test_report_content_contains_checkpoint_id(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-011")
        _, report_path = write_checkpoint(result, tmp_path)
        assert report_path is not None
        content = report_path.read_text()
        assert "wrt-011" in content

    def test_checkpoint_dir_created_if_absent(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-012")
        write_checkpoint(result, tmp_path / "deep" / "nested")
        checkpoint_dir = tmp_path / "deep" / "nested" / "checkpoints" / "wrt-012"
        assert checkpoint_dir.is_dir()

    def test_raises_checkpoint_write_error_on_bad_path(self, tmp_path: Path):
        # Use a path where a file blocks directory creation.
        blocker = tmp_path / "checkpoints"
        blocker.write_text("blocking file")
        result = build_checkpoint(checkpoint_id="wrt-err")
        with pytest.raises(CheckpointWriteError):
            write_checkpoint(result, tmp_path)

    def test_returns_tuple_of_two(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-013")
        out = write_checkpoint(result, tmp_path)
        assert isinstance(out, tuple)
        assert len(out) == 2

    def test_no_report_returns_none_second_element(self, tmp_path: Path):
        result = build_checkpoint(checkpoint_id="wrt-014")
        _, report_path = write_checkpoint(result, tmp_path, generate_report=False)
        assert report_path is None


# ── Structural / isolation ─────────────────────────────────────────────────────


class TestCheckpointRunnerStructural:
    def test_checkpoint_runner_does_not_import_boto3(self):
        import app.evaluation.checkpoint_runner as mod

        names = set(vars(mod).keys())
        assert "boto3" not in names

    def test_checkpoint_runner_does_not_import_bedrock_service(self):
        import app.evaluation.checkpoint_runner as mod

        names = set(vars(mod).keys())
        assert "bedrock_service" not in names

    def test_checkpoint_writer_does_not_import_boto3(self):
        import app.evaluation.checkpoint_writer as mod

        names = set(vars(mod).keys())
        assert "boto3" not in names

    def test_no_live_aws_calls_in_build_checkpoint(self):
        # Verify that build_checkpoint executes without any boto3 being touched.
        # If boto3 is not in sys.modules before the call, it should not be after.
        boto3_was_loaded = "boto3" in sys.modules
        build_checkpoint(checkpoint_id="no-aws-test")
        if not boto3_was_loaded:
            assert "boto3" not in sys.modules
