"""
Unit tests for S3Service.

Uses moto to intercept boto3 calls — no real AWS credentials or network
traffic required.  Every test that touches S3 runs inside an active
mock_aws() context backed by an in-memory bucket.

Coverage:
  - upload_source_document: file reaches the expected S3 key
  - upload_intake_artifact: JSON reaches the expected S3 key
  - upload_case_output: final output JSON reaches the expected S3 key
  - metadata is attached to uploads
  - key structure matches the documented layout (ARCHITECTURE.md §10)
  - StorageError is raised for an empty bucket name
  - StorageError is raised when the target bucket does not exist
"""

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app.services.s3_service import S3Service, StorageError

# ── constants ─────────────────────────────────────────────────────────────────

BUCKET = "test-caseops-documents"
REGION = "us-east-1"
DOCUMENT_ID = "doc-20260330-abcd1234"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Point boto3 at moto's virtual AWS endpoint.

    Without these, moto may attempt to resolve real credentials and fail in
    environments where ~/.aws/credentials is absent.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture()
def s3_mock(fake_aws_credentials: None):
    """
    Activate the moto S3 mock and create the test bucket.

    Yields the boto3 S3 client so tests can inspect bucket contents directly.
    The mock is torn down when the fixture exits.
    """
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture()
def service(s3_mock) -> S3Service:
    """S3Service pointing at the moto-backed bucket."""
    return S3Service(bucket_name=BUCKET, region=REGION)


@pytest.fixture()
def txt_file(tmp_path: Path) -> Path:
    """A minimal .txt source document."""
    f = tmp_path / "advisory.txt"
    f.write_text("FDA advisory content.", encoding="utf-8")
    return f


@pytest.fixture()
def artifact_file(tmp_path: Path) -> Path:
    """A minimal intake artifact JSON file."""
    f = tmp_path / f"{DOCUMENT_ID}.json"
    f.write_text('{"document_id": "' + DOCUMENT_ID + '"}', encoding="utf-8")
    return f


@pytest.fixture()
def output_file(tmp_path: Path) -> Path:
    """A minimal case output JSON file."""
    f = tmp_path / f"{DOCUMENT_ID}.json"
    f.write_text('{"document_id": "' + DOCUMENT_ID + '", "severity": "High"}', encoding="utf-8")
    return f


# ── upload_source_document ────────────────────────────────────────────────────

def test_upload_source_document_returns_expected_key(
    service: S3Service, txt_file: Path
) -> None:
    key = service.upload_source_document(txt_file, DOCUMENT_ID, "FDA")
    assert key == f"documents/{DOCUMENT_ID}/raw/advisory.txt"


def test_upload_source_document_object_exists_in_bucket(
    service: S3Service, txt_file: Path, s3_mock
) -> None:
    key = service.upload_source_document(txt_file, DOCUMENT_ID, "FDA")

    response = s3_mock.get_object(Bucket=BUCKET, Key=key)
    body = response["Body"].read().decode("utf-8")
    assert body == "FDA advisory content."


def test_upload_source_document_metadata_attached(
    service: S3Service, txt_file: Path, s3_mock
) -> None:
    key = service.upload_source_document(txt_file, DOCUMENT_ID, "FDA")

    head = s3_mock.head_object(Bucket=BUCKET, Key=key)
    meta = head["Metadata"]
    assert meta["document_id"] == DOCUMENT_ID
    assert meta["source_type"] == "FDA"


# ── upload_intake_artifact ────────────────────────────────────────────────────

def test_upload_intake_artifact_returns_expected_key(
    service: S3Service, artifact_file: Path
) -> None:
    key = service.upload_intake_artifact(artifact_file, DOCUMENT_ID, "CISA")
    assert key == f"artifacts/intake/{DOCUMENT_ID}.json"


def test_upload_intake_artifact_object_exists_in_bucket(
    service: S3Service, artifact_file: Path, s3_mock
) -> None:
    key = service.upload_intake_artifact(artifact_file, DOCUMENT_ID, "CISA")

    response = s3_mock.get_object(Bucket=BUCKET, Key=key)
    body = response["Body"].read().decode("utf-8")
    assert DOCUMENT_ID in body


def test_upload_intake_artifact_metadata_attached(
    service: S3Service, artifact_file: Path, s3_mock
) -> None:
    key = service.upload_intake_artifact(artifact_file, DOCUMENT_ID, "CISA")

    head = s3_mock.head_object(Bucket=BUCKET, Key=key)
    meta = head["Metadata"]
    assert meta["document_id"] == DOCUMENT_ID
    assert meta["source_type"] == "CISA"


# ── upload_case_output ────────────────────────────────────────────────────────


def test_upload_case_output_returns_expected_key(
    service: S3Service, output_file: Path
) -> None:
    """Key must be outputs/{document_id}/case_output.json per ARCHITECTURE.md §10."""
    key = service.upload_case_output(output_file, DOCUMENT_ID)
    assert key == f"outputs/{DOCUMENT_ID}/case_output.json"


def test_upload_case_output_object_exists_in_bucket(
    service: S3Service, output_file: Path, s3_mock
) -> None:
    key = service.upload_case_output(output_file, DOCUMENT_ID)

    response = s3_mock.get_object(Bucket=BUCKET, Key=key)
    body = response["Body"].read().decode("utf-8")
    assert DOCUMENT_ID in body


def test_upload_case_output_object_content_matches_local_file(
    service: S3Service, output_file: Path, s3_mock
) -> None:
    """S3 must receive exactly the bytes that were written locally."""
    local_content = output_file.read_text(encoding="utf-8")
    key = service.upload_case_output(output_file, DOCUMENT_ID)

    response = s3_mock.get_object(Bucket=BUCKET, Key=key)
    s3_content = response["Body"].read().decode("utf-8")
    assert s3_content == local_content


def test_upload_case_output_metadata_attached(
    service: S3Service, output_file: Path, s3_mock
) -> None:
    key = service.upload_case_output(output_file, DOCUMENT_ID)

    head = s3_mock.head_object(Bucket=BUCKET, Key=key)
    meta = head["Metadata"]
    assert meta["document_id"] == DOCUMENT_ID


def test_upload_case_output_raises_storage_error_on_bad_bucket(
    fake_aws_credentials: None, output_file: Path
) -> None:
    with mock_aws():
        service = S3Service(bucket_name="bucket-that-does-not-exist", region=REGION)
        with pytest.raises(StorageError):
            service.upload_case_output(output_file, DOCUMENT_ID)


# ── error handling ────────────────────────────────────────────────────────────

def test_empty_bucket_name_raises_storage_error(fake_aws_credentials: None) -> None:
    """S3Service must reject an empty bucket name before making any AWS call."""
    with mock_aws():
        with pytest.raises(StorageError, match="bucket name must not be empty"):
            S3Service(bucket_name="", region=REGION)


def test_upload_to_nonexistent_bucket_raises_storage_error(
    fake_aws_credentials: None, txt_file: Path
) -> None:
    """
    If the bucket does not exist, the upload should raise StorageError
    rather than propagating a raw botocore exception.
    """
    with mock_aws():
        # Intentionally do NOT create the bucket.
        service = S3Service(bucket_name="bucket-that-does-not-exist", region=REGION)
        with pytest.raises(StorageError):
            service.upload_source_document(txt_file, DOCUMENT_ID, "FDA")
