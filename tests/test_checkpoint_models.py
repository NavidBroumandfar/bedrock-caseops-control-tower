"""
J-2 unit tests — Phase2CheckpointResult and Phase2ReadinessBlock typed contracts.

Coverage:

  Phase2ReadinessBlock:
    - valid block constructs without error
    - layer_name is required and must be non-empty
    - whitespace-only layer_name raises ValueError
    - is_ready field is a bool (True / False)
    - completed_subphases is preserved
    - notes defaults to empty string
    - model is immutable

  Phase2CheckpointResult — valid construction:
    - valid model with complete_blocked status constructs without error
    - checkpoint_id is required and non-empty
    - phase_version is required and non-empty
    - created_at accepts both offset-aware and Z-suffix ISO 8601
    - completed_phases list is preserved
    - total_tests_offline is preserved
    - readiness list is preserved
    - external_blockers list is preserved
    - engineering_complete flag is preserved
    - live_aws_validated flag is preserved
    - status is preserved
    - notes defaults to empty string

  Phase2CheckpointResult — validation failures:
    - empty checkpoint_id raises ValueError
    - whitespace-only checkpoint_id raises ValueError
    - empty phase_version raises ValueError
    - invalid created_at raises ValueError
    - negative total_tests_offline raises ValueError
    - status='complete' with live_aws_validated=False raises ValueError (consistency guard)
    - status='complete_blocked' with engineering_complete=False raises ValueError
    - status='complete' with engineering_complete=False raises ValueError

  Phase2CheckpointResult — serialization:
    - model_dump(mode='json') produces a dict with all required keys
    - status is serialized as a string
    - engineering_complete and live_aws_validated are serialized as bools

  ArtifactKind extension:
    - 'checkpoint' is now a valid ArtifactKind value in artifact_models

  Structural / isolation:
    - checkpoint_models does not import boto3
    - checkpoint_models does not import any AWS service module
"""

from __future__ import annotations

import sys

import pytest

from app.schemas.checkpoint_models import (
    Phase2CheckpointResult,
    Phase2CheckpointStatus,
    Phase2ReadinessBlock,
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

_VALID_TS = "2026-04-11T12:00:00+00:00"


def _valid_readiness_block(**overrides) -> Phase2ReadinessBlock:
    defaults: dict = {
        "layer_name": "evaluation",
        "is_ready": True,
        "completed_subphases": ["F-0", "F-1", "F-2"],
        "notes": "Offline harness complete.",
    }
    defaults.update(overrides)
    return Phase2ReadinessBlock(**defaults)


def _valid_result(**overrides) -> Phase2CheckpointResult:
    defaults: dict = {
        "checkpoint_id": "chk-001",
        "created_at": _VALID_TS,
        "phase_version": "phase2-v2",
        "completed_phases": ["F", "G", "H", "I", "J-0", "J-1", "J-2"],
        "total_tests_offline": 2100,
        "readiness": [_valid_readiness_block()],
        "external_blockers": ["Titan Embeddings V2 throttling"],
        "engineering_complete": True,
        "live_aws_validated": False,
        "status": "complete_blocked",
        "notes": "",
    }
    defaults.update(overrides)
    return Phase2CheckpointResult(**defaults)


# ── Phase2ReadinessBlock — valid construction ──────────────────────────────────


class TestPhase2ReadinessBlockValid:
    def test_valid_block_constructs(self):
        block = _valid_readiness_block()
        assert block.layer_name == "evaluation"
        assert block.is_ready is True

    def test_is_ready_false_is_valid(self):
        block = _valid_readiness_block(is_ready=False)
        assert block.is_ready is False

    def test_completed_subphases_preserved(self):
        block = _valid_readiness_block(completed_subphases=["H-0", "H-1"])
        assert block.completed_subphases == ["H-0", "H-1"]

    def test_notes_default_is_empty_string(self):
        block = Phase2ReadinessBlock(
            layer_name="safety",
            is_ready=True,
            completed_subphases=["H-0"],
        )
        assert block.notes == ""

    def test_notes_is_preserved(self):
        block = _valid_readiness_block(notes="All checks pass.")
        assert block.notes == "All checks pass."

    def test_empty_subphases_list_is_valid(self):
        block = _valid_readiness_block(completed_subphases=[])
        assert block.completed_subphases == []


class TestPhase2ReadinessBlockValidation:
    def test_empty_layer_name_raises(self):
        with pytest.raises(Exception):
            _valid_readiness_block(layer_name="")

    def test_whitespace_layer_name_raises(self):
        with pytest.raises(Exception):
            _valid_readiness_block(layer_name="   ")


class TestPhase2ReadinessBlockImmutability:
    def test_layer_name_is_immutable(self):
        block = _valid_readiness_block()
        with pytest.raises(Exception):
            block.layer_name = "new_name"  # type: ignore[misc]

    def test_is_ready_is_immutable(self):
        block = _valid_readiness_block()
        with pytest.raises(Exception):
            block.is_ready = False  # type: ignore[misc]


# ── Phase2CheckpointResult — valid construction ────────────────────────────────


class TestPhase2CheckpointResultValid:
    def test_valid_complete_blocked_constructs(self):
        result = _valid_result()
        assert result.status == "complete_blocked"
        assert result.engineering_complete is True
        assert result.live_aws_validated is False

    def test_valid_incomplete_constructs(self):
        result = _valid_result(engineering_complete=False, status="incomplete")
        assert result.status == "incomplete"
        assert result.engineering_complete is False

    def test_valid_complete_with_live_aws_constructs(self):
        result = _valid_result(live_aws_validated=True, status="complete")
        assert result.status == "complete"
        assert result.live_aws_validated is True

    def test_checkpoint_id_preserved(self):
        result = _valid_result(checkpoint_id="my-chk-001")
        assert result.checkpoint_id == "my-chk-001"

    def test_phase_version_preserved(self):
        result = _valid_result()
        assert result.phase_version == "phase2-v2"

    def test_created_at_offset_aware_accepted(self):
        result = _valid_result(created_at="2026-04-11T10:30:00+00:00")
        assert "2026-04-11" in result.created_at

    def test_created_at_z_suffix_accepted(self):
        result = _valid_result(created_at="2026-04-11T10:30:00Z")
        assert "2026-04-11" in result.created_at

    def test_completed_phases_preserved(self):
        result = _valid_result(completed_phases=["F", "G"])
        assert result.completed_phases == ["F", "G"]

    def test_total_tests_offline_preserved(self):
        result = _valid_result(total_tests_offline=2005)
        assert result.total_tests_offline == 2005

    def test_total_tests_offline_zero_is_valid(self):
        result = _valid_result(total_tests_offline=0)
        assert result.total_tests_offline == 0

    def test_readiness_list_preserved(self):
        blocks = [_valid_readiness_block(), _valid_readiness_block(layer_name="safety")]
        result = _valid_result(readiness=blocks)
        assert len(result.readiness) == 2

    def test_external_blockers_preserved(self):
        result = _valid_result(external_blockers=["blocker-a", "blocker-b"])
        assert result.external_blockers == ["blocker-a", "blocker-b"]

    def test_empty_external_blockers_is_valid(self):
        result = _valid_result(
            external_blockers=[],
            live_aws_validated=True,
            status="complete",
        )
        assert result.external_blockers == []

    def test_notes_defaults_to_empty_string(self):
        result = _valid_result()
        assert result.notes == ""

    def test_notes_preserved(self):
        result = _valid_result(notes="Minor latency warning.")
        assert result.notes == "Minor latency warning."


# ── Phase2CheckpointResult — validation failures ───────────────────────────────


class TestPhase2CheckpointResultValidation:
    def test_empty_checkpoint_id_raises(self):
        with pytest.raises(Exception):
            _valid_result(checkpoint_id="")

    def test_whitespace_checkpoint_id_raises(self):
        with pytest.raises(Exception):
            _valid_result(checkpoint_id="  ")

    def test_empty_phase_version_raises(self):
        with pytest.raises(Exception):
            _valid_result(phase_version="")

    def test_invalid_created_at_raises(self):
        with pytest.raises(Exception):
            _valid_result(created_at="not-a-date")

    def test_negative_total_tests_raises(self):
        with pytest.raises(Exception):
            _valid_result(total_tests_offline=-1)

    def test_complete_status_with_live_aws_false_raises(self):
        """status='complete' is disallowed when live_aws_validated=False."""
        with pytest.raises(Exception):
            _valid_result(
                engineering_complete=True,
                live_aws_validated=False,
                status="complete",
            )

    def test_complete_blocked_with_engineering_false_raises(self):
        """status='complete_blocked' requires engineering_complete=True."""
        with pytest.raises(Exception):
            _valid_result(
                engineering_complete=False,
                live_aws_validated=False,
                status="complete_blocked",
            )

    def test_complete_with_engineering_false_raises(self):
        """status='complete' requires engineering_complete=True."""
        with pytest.raises(Exception):
            _valid_result(
                engineering_complete=False,
                live_aws_validated=True,
                status="complete",
            )


# ── Phase2CheckpointResult — immutability ─────────────────────────────────────


class TestPhase2CheckpointResultImmutability:
    def test_checkpoint_id_is_immutable(self):
        result = _valid_result()
        with pytest.raises(Exception):
            result.checkpoint_id = "new-id"  # type: ignore[misc]

    def test_status_is_immutable(self):
        result = _valid_result()
        with pytest.raises(Exception):
            result.status = "complete"  # type: ignore[misc]


# ── Phase2CheckpointResult — serialization ────────────────────────────────────


class TestPhase2CheckpointResultSerialization:
    def test_model_dump_produces_dict(self):
        result = _valid_result()
        data = result.model_dump(mode="json")
        assert isinstance(data, dict)

    def test_all_required_keys_present(self):
        result = _valid_result()
        data = result.model_dump(mode="json")
        required = {
            "checkpoint_id", "created_at", "phase_version",
            "completed_phases", "total_tests_offline",
            "readiness", "external_blockers",
            "engineering_complete", "live_aws_validated", "status", "notes",
        }
        assert required.issubset(set(data.keys()))

    def test_status_serialized_as_string(self):
        result = _valid_result()
        data = result.model_dump(mode="json")
        assert isinstance(data["status"], str)
        assert data["status"] == "complete_blocked"

    def test_engineering_complete_serialized_as_bool(self):
        result = _valid_result()
        data = result.model_dump(mode="json")
        assert isinstance(data["engineering_complete"], bool)

    def test_live_aws_validated_serialized_as_bool(self):
        result = _valid_result()
        data = result.model_dump(mode="json")
        assert isinstance(data["live_aws_validated"], bool)
        assert data["live_aws_validated"] is False

    def test_readiness_serialized_as_list_of_dicts(self):
        result = _valid_result()
        data = result.model_dump(mode="json")
        assert isinstance(data["readiness"], list)
        assert all(isinstance(b, dict) for b in data["readiness"])


# ── ArtifactKind extension — 'checkpoint' ─────────────────────────────────────


class TestArtifactKindCheckpointExtension:
    def test_checkpoint_is_valid_artifact_kind(self):
        from app.schemas.artifact_models import ArtifactMetadata

        meta = ArtifactMetadata(
            run_id="chk-001",
            kind="checkpoint",
            created_at=_VALID_TS,
            artifact_dir="checkpoints/chk-001",
            artifact_files=["checkpoint.json", "report.md"],
        )
        assert meta.kind == "checkpoint"

    def test_invalid_kind_still_raises(self):
        from app.schemas.artifact_models import ArtifactMetadata
        import pytest

        with pytest.raises(Exception):
            ArtifactMetadata(
                run_id="chk-001",
                kind="unknown_kind",
                created_at=_VALID_TS,
                artifact_dir="checkpoints/chk-001",
                artifact_files=["checkpoint.json"],
            )


# ── Structural / isolation ─────────────────────────────────────────────────────


class TestCheckpointModelsStructural:
    def test_checkpoint_models_does_not_import_boto3(self):
        import app.schemas.checkpoint_models as mod

        assert "boto3" not in sys.modules or "boto3" not in vars(mod)

    def test_checkpoint_models_does_not_import_aws_service(self):
        import app.schemas.checkpoint_models as mod

        names = set(vars(mod).keys())
        assert "bedrock_service" not in names
        assert "cloudwatch_service" not in names
        assert "s3_service" not in names
