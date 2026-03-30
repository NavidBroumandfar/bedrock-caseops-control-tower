"""
S3 storage adapter for the CaseOps intake pipeline.

Thin wrapper around boto3.  All S3 key structure decisions live here;
callers pass local paths and document IDs, not raw keys.

S3 key layout (matches ARCHITECTURE.md §5 and §10)
───────────────────────────────────────────────────
  documents/{document_id}/raw/{filename}    ← original source document
  artifacts/intake/{document_id}.json       ← intake artifact JSON

Metadata tags attached to every upload:
  document_id   — the assigned document ID
  source_type   — FDA | CISA | Incident | Other
"""

import os
from pathlib import Path

import boto3
import boto3.exceptions
from botocore.exceptions import BotoCoreError, ClientError


class StorageError(Exception):
    """Raised when an S3 operation cannot be completed."""


class S3Service:
    """
    Thin adapter around the boto3 S3 client.

    Instantiate with a bucket name; all upload methods resolve the correct
    S3 key and attach standard metadata automatically.
    """

    def __init__(self, bucket_name: str, region: str | None = None) -> None:
        if not bucket_name or not bucket_name.strip():
            raise StorageError("S3 bucket name must not be empty.")
        self._bucket = bucket_name
        self._client = boto3.client(
            "s3",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )

    # ── public interface ──────────────────────────────────────────────────────

    def upload_source_document(
        self,
        local_path: Path,
        document_id: str,
        source_type: str,
    ) -> str:
        """
        Upload the original source document to S3.

        Key format: documents/{document_id}/raw/{filename}
        Returns the S3 key that was written.
        """
        s3_key = f"documents/{document_id}/raw/{local_path.name}"
        self._upload_file(
            local_path=local_path,
            s3_key=s3_key,
            metadata={"document_id": document_id, "source_type": source_type},
        )
        return s3_key

    def upload_intake_artifact(
        self,
        local_path: Path,
        document_id: str,
        source_type: str,
    ) -> str:
        """
        Upload the intake artifact JSON to S3.

        Key format: artifacts/intake/{document_id}.json
        Returns the S3 key that was written.
        """
        s3_key = f"artifacts/intake/{document_id}.json"
        self._upload_file(
            local_path=local_path,
            s3_key=s3_key,
            metadata={"document_id": document_id, "source_type": source_type},
        )
        return s3_key

    # ── private helpers ───────────────────────────────────────────────────────

    def _upload_file(
        self,
        local_path: Path,
        s3_key: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Upload a single local file to S3 at the given key."""
        try:
            kwargs: dict = {
                "Filename": str(local_path),
                "Bucket": self._bucket,
                "Key": s3_key,
            }
            if metadata:
                kwargs["ExtraArgs"] = {"Metadata": metadata}
            self._client.upload_file(**kwargs)
        except (BotoCoreError, ClientError, boto3.exceptions.S3UploadFailedError) as exc:
            raise StorageError(
                f"Failed to upload {local_path.name!r} "
                f"to s3://{self._bucket}/{s3_key}: {exc}"
            ) from exc
