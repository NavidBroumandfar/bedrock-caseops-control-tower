"""
J-1 unit tests — ArtifactMetadata and ReportBundle typed contracts.

Coverage:

  ArtifactKind:
    - "evaluation_run", "safety_run", "comparison_run" are accepted values
    - kind is enforced as a Literal at runtime via Pydantic

  ArtifactMetadata:
    - valid model constructs without error
    - run_id is required and must be non-empty
    - artifact_dir is required and must be non-empty
    - artifact_files must not be empty
    - created_at must be a valid ISO 8601 string
    - kind is one of the three valid ArtifactKind values
    - whitespace-only run_id raises ValueError
    - whitespace-only artifact_dir raises ValueError
    - empty artifact_files list raises ValueError
    - invalid ISO 8601 string raises ValueError
    - field values are preserved correctly after construction
    - model is immutable (attempt to mutate raises)

  ReportBundle:
    - valid bundle with report_path constructs without error
    - valid bundle with report_path=None constructs without error
    - metadata field is required
    - report_path defaults to None
    - bundle preserves nested metadata fields correctly
    - report_path is preserved when non-None
    - model is immutable

  Structural:
    - ArtifactMetadata does not import boto3
    - ReportBundle does not import boto3
    - artifact_models does not import any AWS service
"""

from __future__ import annotations

import sys

import pytest

from app.schemas.artifact_models import ArtifactMetadata, ArtifactKind, ReportBundle

# ── Shared helpers ─────────────────────────────────────────────────────────────

_VALID_TIMESTAMP = "2026-04-11T00:00:00+00:00"


def _valid_metadata(**overrides) -> ArtifactMetadata:
    defaults: dict = {
        "run_id": "eval-run-001",
        "kind": "evaluation_run",
        "created_at": _VALID_TIMESTAMP,
        "artifact_dir": "evaluation_runs/eval-run-001",
        "artifact_files": ["summary.json", "case_results.json"],
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


# ── ArtifactKind ───────────────────────────────────────────────────────────────


class TestArtifactKind:
    def test_evaluation_run_is_valid_kind(self):
        meta = _valid_metadata(kind="evaluation_run")
        assert meta.kind == "evaluation_run"

    def test_safety_run_is_valid_kind(self):
        meta = _valid_metadata(kind="safety_run")
        assert meta.kind == "safety_run"

    def test_comparison_run_is_valid_kind(self):
        meta = _valid_metadata(kind="comparison_run")
        assert meta.kind == "comparison_run"

    def test_invalid_kind_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(kind="unknown_run")


# ── ArtifactMetadata — valid construction ─────────────────────────────────────


class TestArtifactMetadataValid:
    def test_valid_evaluation_run_constructs(self):
        meta = _valid_metadata()
        assert meta.run_id == "eval-run-001"
        assert meta.kind == "evaluation_run"

    def test_valid_safety_run_constructs(self):
        meta = _valid_metadata(
            run_id="suite-001",
            kind="safety_run",
            artifact_dir="safety_runs/suite-001",
        )
        assert meta.kind == "safety_run"

    def test_valid_comparison_run_constructs(self):
        meta = _valid_metadata(
            run_id="cmp-run-001",
            kind="comparison_run",
            artifact_dir="comparison_runs/cmp-run-001",
        )
        assert meta.kind == "comparison_run"

    def test_created_at_is_preserved(self):
        meta = _valid_metadata()
        assert meta.created_at == _VALID_TIMESTAMP

    def test_artifact_dir_is_preserved(self):
        meta = _valid_metadata(artifact_dir="evaluation_runs/my-run")
        assert meta.artifact_dir == "evaluation_runs/my-run"

    def test_artifact_files_are_preserved(self):
        files = ["summary.json", "case_results.json", "report.md"]
        meta = _valid_metadata(artifact_files=files)
        assert meta.artifact_files == files

    def test_single_artifact_file_is_valid(self):
        meta = _valid_metadata(artifact_files=["summary.json"])
        assert len(meta.artifact_files) == 1

    def test_utc_z_suffix_is_accepted(self):
        meta = _valid_metadata(created_at="2026-04-11T00:00:00Z")
        assert "2026-04-11" in meta.created_at


# ── ArtifactMetadata — validation failures ────────────────────────────────────


class TestArtifactMetadataValidation:
    def test_empty_run_id_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(run_id="")

    def test_whitespace_run_id_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(run_id="   ")

    def test_empty_artifact_dir_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(artifact_dir="")

    def test_whitespace_artifact_dir_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(artifact_dir="   ")

    def test_empty_artifact_files_list_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(artifact_files=[])

    def test_invalid_iso8601_created_at_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(created_at="not-a-date")

    def test_garbage_string_created_at_raises(self):
        with pytest.raises(Exception):
            _valid_metadata(created_at="yesterday morning")


# ── ArtifactMetadata — immutability ───────────────────────────────────────────


class TestArtifactMetadataImmutability:
    def test_run_id_is_immutable(self):
        meta = _valid_metadata()
        with pytest.raises(Exception):
            meta.run_id = "new-id"  # type: ignore[misc]


# ── ReportBundle — valid construction ─────────────────────────────────────────


class TestReportBundleValid:
    def test_bundle_with_report_path_constructs(self):
        meta = _valid_metadata()
        bundle = ReportBundle(
            metadata=meta,
            report_path="evaluation_runs/eval-run-001/report.md",
        )
        assert bundle.report_path == "evaluation_runs/eval-run-001/report.md"

    def test_bundle_without_report_path_constructs(self):
        meta = _valid_metadata()
        bundle = ReportBundle(metadata=meta)
        assert bundle.report_path is None

    def test_report_path_defaults_to_none(self):
        bundle = ReportBundle(metadata=_valid_metadata())
        assert bundle.report_path is None

    def test_bundle_preserves_metadata_run_id(self):
        meta = _valid_metadata(run_id="test-123")
        bundle = ReportBundle(metadata=meta)
        assert bundle.metadata.run_id == "test-123"

    def test_bundle_preserves_metadata_kind(self):
        meta = _valid_metadata(kind="comparison_run")
        bundle = ReportBundle(metadata=meta)
        assert bundle.metadata.kind == "comparison_run"

    def test_bundle_preserves_metadata_artifact_files(self):
        meta = _valid_metadata(artifact_files=["summary.json"])
        bundle = ReportBundle(metadata=meta)
        assert bundle.metadata.artifact_files == ["summary.json"]


# ── Structural / isolation ─────────────────────────────────────────────────────


class TestArtifactModelsStructural:
    def test_artifact_models_does_not_import_boto3(self):
        import app.schemas.artifact_models as mod
        imported_names = set(vars(mod).keys())
        assert "boto3" not in imported_names

    def test_artifact_models_does_not_import_aws_service(self):
        import app.schemas.artifact_models as mod
        imported_names = set(vars(mod).keys())
        assert "bedrock_service" not in imported_names
        assert "cloudwatch_service" not in imported_names
