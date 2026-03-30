"""
Unit tests for the document intake pipeline.

A-1 / A-2 coverage (preserved, now verified against IntakeResult):
  - Happy path: valid file + valid metadata → IntakeResult returned + artifact written
  - Missing file: non-existent path → IntakeError raised
  - Unsupported extension: .csv → IntakeError raised
  - Oversized file → IntakeError raised
  - Invalid document_date format → Pydantic ValidationError raised

A-3 coverage (typed IntakeResult handoff contract):
  - run_intake returns IntakeResult, not a bare string
  - local-only mode: storage is None
  - S3-enabled mode: storage populated with bucket name and both S3 keys
  - IntakeResult.record matches the written artifact content
  - IntakeResult.artifact_path points to the written local file
  - S3 upload failure propagates as IntakeError
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws
from pydantic import ValidationError

import app.services.intake_service as intake_service_module
from app.schemas.intake_models import IntakeMetadata, IntakeResult
from app.services.intake_service import IntakeError, run_intake
from app.services.s3_service import S3Service, StorageError


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def valid_metadata() -> IntakeMetadata:
    return IntakeMetadata(
        source_type="FDA",
        document_date="2026-03-30",
        submitter_note="Test submission",
    )


@pytest.fixture()
def txt_file(tmp_path: Path) -> Path:
    """A real .txt file with minimal content."""
    f = tmp_path / "sample_advisory.txt"
    f.write_text("This is a sample FDA advisory document.", encoding="utf-8")
    return f


# ── A-3: IntakeResult return type ─────────────────────────────────────────────

def test_intake_returns_intake_result(
    txt_file: Path, valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    """run_intake must return a typed IntakeResult, not a bare string."""
    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=tmp_path / "intake",
    )

    assert isinstance(result, IntakeResult)


def test_intake_result_document_id_format(
    txt_file: Path, valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    """document_id on the result must follow the doc-YYYYMMDD-xxxxxxxx format."""
    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=tmp_path / "intake",
    )

    assert result.document_id.startswith("doc-")
    parts = result.document_id.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8      # YYYYMMDD
    assert len(parts[2]) == 8      # uuid4 prefix


def test_intake_result_artifact_path_exists(
    txt_file: Path, valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    """artifact_path on the result must point to the written local file."""
    output_dir = tmp_path / "intake"

    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=output_dir,
    )

    assert Path(result.artifact_path).exists()
    assert result.artifact_path.endswith(f"{result.document_id}.json")


def test_intake_result_record_matches_artifact(
    txt_file: Path, valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    """IntakeResult.record must be consistent with the written artifact JSON."""
    output_dir = tmp_path / "intake"

    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=output_dir,
    )

    artifact = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert artifact["document_id"] == result.record.document_id
    assert artifact["source_type"] == result.record.source_type
    assert artifact["document_date"] == result.record.document_date


def test_intake_local_only_has_no_storage(
    txt_file: Path, valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    """In local-only mode (no S3Service), storage must be None."""
    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=tmp_path / "intake",
    )

    assert result.storage is None


# ── artifact content (A-1 assertions preserved) ───────────────────────────────

def test_intake_writes_artifact(
    txt_file: Path, valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    output_dir = tmp_path / "intake"

    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=output_dir,
    )

    artifact = output_dir / f"{result.document_id}.json"
    assert artifact.exists(), "Intake artifact file should be created"

    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["document_id"] == result.document_id
    assert data["original_filename"] == "sample_advisory.txt"
    assert data["extension"] == ".txt"
    assert data["source_type"] == "FDA"
    assert data["document_date"] == "2026-03-30"
    assert data["submitter_note"] == "Test submission"
    assert data["file_size_bytes"] > 0
    assert "intake_timestamp" in data
    assert "absolute_path" in data


def test_intake_md_file_is_accepted(
    tmp_path: Path, valid_metadata: IntakeMetadata
) -> None:
    md_file = tmp_path / "report.md"
    md_file.write_text("# Incident Report\n\nDetails here.", encoding="utf-8")
    output_dir = tmp_path / "intake"

    result = run_intake(
        file_path=str(md_file),
        metadata=valid_metadata,
        output_dir=output_dir,
    )

    assert result.document_id.startswith("doc-")


# ── missing file ──────────────────────────────────────────────────────────────

def test_intake_raises_for_missing_file(
    valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    missing = tmp_path / "does_not_exist.txt"

    with pytest.raises(IntakeError, match="File not found"):
        run_intake(
            file_path=str(missing),
            metadata=valid_metadata,
            output_dir=tmp_path / "intake",
        )


# ── unsupported extension ─────────────────────────────────────────────────────

def test_intake_raises_for_unsupported_extension(
    valid_metadata: IntakeMetadata, tmp_path: Path
) -> None:
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("col1,col2\n1,2", encoding="utf-8")

    with pytest.raises(IntakeError, match="Unsupported file type"):
        run_intake(
            file_path=str(csv_file),
            metadata=valid_metadata,
            output_dir=tmp_path / "intake",
        )


# ── oversized file ───────────────────────────────────────────────────────────

def test_intake_raises_for_oversized_file(
    valid_metadata: IntakeMetadata, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Lower the limit to 10 bytes so a trivial file triggers the check.
    monkeypatch.setattr(intake_service_module, "MAX_FILE_SIZE_BYTES", 10)

    big_file = tmp_path / "large_doc.txt"
    big_file.write_text("x" * 11, encoding="utf-8")  # 11 bytes > 10 byte limit

    with pytest.raises(IntakeError, match="File too large"):
        run_intake(
            file_path=str(big_file),
            metadata=valid_metadata,
            output_dir=tmp_path / "intake",
        )


# ── metadata validation ───────────────────────────────────────────────────────

def test_invalid_document_date_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="document_date"):
        IntakeMetadata(
            source_type="CISA",
            document_date="30-03-2026",    # wrong format
        )


def test_invalid_source_type_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        IntakeMetadata(
            source_type="UnknownOrg",      # not in the Literal set
            document_date="2026-03-30",
        )


# ── A-2 / A-3: S3 upload tests ───────────────────────────────────────────────
#
# These tests pass an S3Service (backed by moto) into run_intake() to verify
# that the pipeline uploads files to the correct S3 keys and that IntakeResult
# captures the bucket and key information accurately.
# No real AWS credentials or network calls are made.

_BUCKET = "test-caseops-documents"
_REGION = "us-east-1"


@pytest.fixture()
def fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure boto3 never contacts real AWS during these tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)


@pytest.fixture()
def s3_mock(fake_aws_credentials: None):
    """Active moto S3 mock with the test bucket pre-created."""
    with mock_aws():
        client = boto3.client("s3", region_name=_REGION)
        client.create_bucket(Bucket=_BUCKET)
        yield client


@pytest.fixture()
def s3_service(s3_mock) -> S3Service:
    """S3Service pointing at the moto-backed bucket."""
    return S3Service(bucket_name=_BUCKET, region=_REGION)


def test_intake_s3_result_has_storage(
    txt_file: Path,
    valid_metadata: IntakeMetadata,
    tmp_path: Path,
    s3_service: S3Service,
    s3_mock,
) -> None:
    """With S3 enabled, IntakeResult.storage must be populated."""
    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=tmp_path / "intake",
        s3_service=s3_service,
    )

    assert result.storage is not None
    assert result.storage.bucket_name == _BUCKET


def test_intake_s3_result_source_document_key(
    txt_file: Path,
    valid_metadata: IntakeMetadata,
    tmp_path: Path,
    s3_service: S3Service,
    s3_mock,
) -> None:
    """storage.source_document_key must match the expected S3 key pattern."""
    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=tmp_path / "intake",
        s3_service=s3_service,
    )

    expected_key = f"documents/{result.document_id}/raw/sample_advisory.txt"
    assert result.storage.source_document_key == expected_key

    # Verify the object actually landed there.
    response = s3_mock.get_object(Bucket=_BUCKET, Key=expected_key)
    body = response["Body"].read().decode("utf-8")
    assert "FDA advisory" in body


def test_intake_s3_result_artifact_key(
    txt_file: Path,
    valid_metadata: IntakeMetadata,
    tmp_path: Path,
    s3_service: S3Service,
    s3_mock,
) -> None:
    """storage.intake_artifact_key must match the expected S3 key pattern."""
    result = run_intake(
        file_path=str(txt_file),
        metadata=valid_metadata,
        output_dir=tmp_path / "intake",
        s3_service=s3_service,
    )

    expected_key = f"artifacts/intake/{result.document_id}.json"
    assert result.storage.intake_artifact_key == expected_key

    # Verify the artifact JSON is present in S3 and coherent.
    response = s3_mock.get_object(Bucket=_BUCKET, Key=expected_key)
    artifact = json.loads(response["Body"].read().decode("utf-8"))
    assert artifact["document_id"] == result.document_id
    assert artifact["source_type"] == "FDA"


def test_intake_s3_failure_raises_intake_error(
    txt_file: Path,
    valid_metadata: IntakeMetadata,
    tmp_path: Path,
    s3_service: S3Service,
) -> None:
    """
    If S3 upload fails, run_intake must raise IntakeError rather than
    propagating a raw StorageError or boto3 exception.
    """
    s3_service.upload_source_document = MagicMock(
        side_effect=StorageError("simulated S3 failure")
    )

    with pytest.raises(IntakeError, match="S3 upload failed"):
        run_intake(
            file_path=str(txt_file),
            metadata=valid_metadata,
            output_dir=tmp_path / "intake",
            s3_service=s3_service,
        )
