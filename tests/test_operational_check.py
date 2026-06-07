"""Unit tests for the Phase 10 operational validation helper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.operational_check import (
    _check_lambda_response,
    _parse_s3_uri,
    run_operational_checks,
)


def _write_lambda_response(path: Path) -> None:
    body = {
        "status": "ok",
        "document_id": "doc-20260606-563ebe43",
        "session_id": "sess-618a4da7",
        "safety_status": "escalate",
        "s3_archive": "s3://caseops-output/outputs/doc-20260606-563ebe43/case_output.json",
    }
    path.write_text(
        json.dumps({"statusCode": 200, "body": json.dumps(body)}),
        encoding="utf-8",
    )


class _FakeCloudFormation:
    def __init__(self, *, outputs: list[dict[str, str]] | None = None) -> None:
        self.outputs = outputs or [
            {"OutputKey": "FunctionName", "OutputValue": "caseops-pipeline-dev"},
            {"OutputKey": "FunctionArn", "OutputValue": "arn:aws:lambda:test"},
            {"OutputKey": "DocumentBucketName", "OutputValue": "caseops-docs"},
            {"OutputKey": "OutputBucketName", "OutputValue": "caseops-output"},
            {"OutputKey": "PipelineLogGroupName", "OutputValue": "/caseops/pipeline/dev"},
        ]

    def describe_stacks(self, StackName: str) -> dict[str, object]:  # noqa: N803
        return {
            "Stacks": [
                {
                    "StackName": StackName,
                    "StackStatus": "CREATE_COMPLETE",
                    "Outputs": self.outputs,
                }
            ]
        }


class _FakeS3:
    def head_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
        return {"ContentLength": 4968, "ETag": '"etag"', "LastModified": "2026-06-06T22:14:23Z"}


class _FakeLogs:
    def describe_log_streams(self, **kwargs: object) -> dict[str, object]:
        if kwargs.get("logStreamNamePrefix") == "caseops-session/sess-618a4da7":
            return {"logStreams": [{"logStreamName": "caseops-session/sess-618a4da7"}]}
        return {
            "logStreams": [
                {
                    "logStreamName": "2026/06/06/[$LATEST]abc",
                    "lastEventTimestamp": 1780784057748,
                }
            ]
        }


def test_parse_s3_uri_returns_bucket_and_key() -> None:
    assert _parse_s3_uri("s3://bucket/path/to/object.json") == (
        "bucket",
        "path/to/object.json",
    )


def test_parse_s3_uri_rejects_non_s3_uri() -> None:
    with pytest.raises(ValueError, match="invalid S3 URI"):
        _parse_s3_uri("https://example.com/object.json")


def test_check_lambda_response_extracts_release_gate_evidence(tmp_path: Path) -> None:
    response_file = tmp_path / "lambda-response.json"
    _write_lambda_response(response_file)

    check, evidence = _check_lambda_response(response_file)

    assert check.status == "ok"
    assert evidence is not None
    assert evidence.aws_status_code == 200
    assert evidence.app_status == "ok"
    assert evidence.document_id == "doc-20260606-563ebe43"
    assert evidence.session_id == "sess-618a4da7"


def test_run_operational_checks_passes_with_stack_response_archive_and_logs(
    tmp_path: Path,
) -> None:
    response_file = tmp_path / "lambda-response.json"
    _write_lambda_response(response_file)

    def client_factory(service_name: str, **_: object):
        return {
            "cloudformation": _FakeCloudFormation(),
            "s3": _FakeS3(),
            "logs": _FakeLogs(),
        }[service_name]

    with patch("scripts.operational_check.boto3.client", side_effect=client_factory):
        checks = run_operational_checks(
            stack_name="bedrock-caseops-control-tower-dev",
            region="us-east-2",
            response_file=response_file,
        )

    assert checks
    assert {check.status for check in checks} == {"ok"}
    assert {check.name for check in checks} == {
        "cloudformation_stack",
        "lambda_response",
        "s3_archive",
        "lambda_logs",
        "pipeline_logs",
    }


def test_run_operational_checks_fails_when_required_stack_output_is_missing(
    tmp_path: Path,
) -> None:
    response_file = tmp_path / "lambda-response.json"
    _write_lambda_response(response_file)

    def client_factory(service_name: str, **_: object):
        return {
            "cloudformation": _FakeCloudFormation(
                outputs=[
                    {"OutputKey": "FunctionName", "OutputValue": "caseops-pipeline-dev"},
                    {"OutputKey": "OutputBucketName", "OutputValue": "caseops-output"},
                ]
            ),
            "s3": _FakeS3(),
            "logs": _FakeLogs(),
        }[service_name]

    with patch("scripts.operational_check.boto3.client", side_effect=client_factory):
        checks = run_operational_checks(
            stack_name="bedrock-caseops-control-tower-dev",
            region="us-east-2",
            response_file=response_file,
        )

    stack_check = next(check for check in checks if check.name == "cloudformation_stack")
    assert stack_check.status == "fail"
    assert "FunctionArn" in stack_check.details["missing_outputs"]


def test_check_lambda_response_fails_when_application_status_is_not_ok(tmp_path: Path) -> None:
    response_file = tmp_path / "lambda-response.json"
    response_file.write_text(
        json.dumps({"statusCode": 200, "body": json.dumps({"status": "blocked"})}),
        encoding="utf-8",
    )

    check, evidence = _check_lambda_response(response_file)

    assert check.status == "fail"
    assert evidence is not None
    assert evidence.app_status == "blocked"
