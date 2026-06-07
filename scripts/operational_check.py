"""
Operational validation helper for deployed CaseOps Lambda stacks.

This script is a Phase 10 release-gate building block. It checks the AWS stack,
deployed outputs, Lambda response payload, S3 output archive, and CloudWatch log
streams that operators need before calling a deployment healthy.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import find_dotenv, load_dotenv


_HEALTHY_STACK_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
_REQUIRED_OUTPUT_KEYS = {
    "FunctionName",
    "FunctionArn",
    "DocumentBucketName",
    "OutputBucketName",
    "PipelineLogGroupName",
}


@dataclass(frozen=True)
class OperationalCheck:
    """One operational validation check result."""

    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class LambdaResponseEvidence:
    """Parsed evidence from an `aws lambda invoke` response file."""

    aws_status_code: int
    app_status: str | None
    document_id: str | None
    session_id: str | None
    s3_archive: str | None
    safety_status: str | None


def main() -> int:
    load_dotenv(find_dotenv(usecwd=True), override=False)

    parser = argparse.ArgumentParser(
        description="Validate deployed CaseOps stack health and invocation evidence."
    )
    parser.add_argument(
        "--stack-name",
        default=os.getenv("CASEOPS_STACK_NAME", "bedrock-caseops-control-tower-dev"),
        help="CloudFormation stack name to inspect.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "us-east-1"),
        help="AWS region for CloudFormation, S3, and CloudWatch Logs.",
    )
    parser.add_argument(
        "--response-file",
        default="outputs/lambda-response.json",
        help="Path to an aws lambda invoke response payload.",
    )
    parser.add_argument(
        "--skip-response",
        action="store_true",
        help="Only check deployed AWS resources, not a Lambda response file or archive.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    args = parser.parse_args()

    checks = run_operational_checks(
        stack_name=args.stack_name,
        region=args.region,
        response_file=None if args.skip_response else Path(args.response_file),
    )
    payload = {
        "status": "fail" if any(check.status == "fail" for check in checks) else "ok",
        "checks": [asdict(check) for check in checks],
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_text_report(checks)

    return 1 if payload["status"] == "fail" else 0


def run_operational_checks(
    *,
    stack_name: str,
    region: str,
    response_file: Path | None,
) -> list[OperationalCheck]:
    """Run deployment health checks and return structured results."""
    checks: list[OperationalCheck] = []
    outputs: dict[str, str] = {}
    evidence: LambdaResponseEvidence | None = None

    stack_check, outputs = _check_stack_outputs(stack_name=stack_name, region=region)
    checks.append(stack_check)

    if response_file is None:
        checks.append(
            OperationalCheck(
                name="lambda_response",
                status="warn",
                message="Skipped Lambda response evidence checks.",
            )
        )
    else:
        response_check, evidence = _check_lambda_response(response_file)
        checks.append(response_check)

    if evidence and evidence.s3_archive:
        checks.append(_check_s3_archive(evidence.s3_archive, region=region))
    elif response_file is not None:
        checks.append(
            OperationalCheck(
                name="s3_archive",
                status="fail",
                message="Lambda response did not include an s3_archive location.",
            )
        )

    function_name = outputs.get("FunctionName")
    if function_name:
        checks.append(_check_lambda_log_stream(function_name=function_name, region=region))
    else:
        checks.append(
            OperationalCheck(
                name="lambda_logs",
                status="fail",
                message="Cannot check Lambda logs because FunctionName output is missing.",
            )
        )

    pipeline_log_group = outputs.get("PipelineLogGroupName")
    if pipeline_log_group and evidence and evidence.session_id:
        checks.append(
            _check_pipeline_log_stream(
                log_group=pipeline_log_group,
                session_id=evidence.session_id,
                region=region,
            )
        )
    elif response_file is not None:
        checks.append(
            OperationalCheck(
                name="pipeline_logs",
                status="fail",
                message="Cannot check pipeline logs without PipelineLogGroupName and session_id.",
            )
        )

    return checks


def _check_stack_outputs(*, stack_name: str, region: str) -> tuple[OperationalCheck, dict[str, str]]:
    try:
        response = boto3.client("cloudformation", region_name=region).describe_stacks(
            StackName=stack_name
        )
        stack = response["Stacks"][0]
    except (BotoCoreError, ClientError, KeyError, IndexError) as exc:
        return (
            OperationalCheck(
                name="cloudformation_stack",
                status="fail",
                message=f"Could not describe stack {stack_name}: {exc}",
            ),
            {},
        )

    stack_status = stack.get("StackStatus")
    outputs = {
        item.get("OutputKey"): item.get("OutputValue")
        for item in stack.get("Outputs", [])
        if item.get("OutputKey") and item.get("OutputValue")
    }
    missing_outputs = sorted(_REQUIRED_OUTPUT_KEYS - set(outputs))
    if stack_status not in _HEALTHY_STACK_STATUSES:
        return (
            OperationalCheck(
                name="cloudformation_stack",
                status="fail",
                message="Stack is not in a healthy complete status.",
                details={"stack_status": stack_status, "missing_outputs": missing_outputs},
            ),
            outputs,
        )
    if missing_outputs:
        return (
            OperationalCheck(
                name="cloudformation_stack",
                status="fail",
                message="Stack is missing required outputs.",
                details={"stack_status": stack_status, "missing_outputs": missing_outputs},
            ),
            outputs,
        )

    return (
        OperationalCheck(
            name="cloudformation_stack",
            status="ok",
            message="Stack is healthy and required outputs are present.",
            details={"stack_status": stack_status, "outputs": outputs},
        ),
        outputs,
    )


def _check_lambda_response(response_file: Path) -> tuple[OperationalCheck, LambdaResponseEvidence | None]:
    if not response_file.exists():
        return (
            OperationalCheck(
                name="lambda_response",
                status="fail",
                message=f"Lambda response file not found: {response_file}",
            ),
            None,
        )

    try:
        payload = json.loads(response_file.read_text(encoding="utf-8"))
        body = payload.get("body")
        parsed_body = json.loads(body) if isinstance(body, str) else body
        if not isinstance(parsed_body, dict):
            raise ValueError("response body must be a JSON object")
        evidence = LambdaResponseEvidence(
            aws_status_code=int(payload.get("statusCode", 0)),
            app_status=parsed_body.get("status"),
            document_id=parsed_body.get("document_id"),
            session_id=parsed_body.get("session_id"),
            s3_archive=parsed_body.get("s3_archive"),
            safety_status=parsed_body.get("safety_status"),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return (
            OperationalCheck(
                name="lambda_response",
                status="fail",
                message=f"Lambda response file could not be parsed: {exc}",
            ),
            None,
        )

    missing = [
        name
        for name, value in {
            "document_id": evidence.document_id,
            "session_id": evidence.session_id,
            "s3_archive": evidence.s3_archive,
            "safety_status": evidence.safety_status,
        }.items()
        if not value
    ]
    if evidence.aws_status_code != 200 or evidence.app_status != "ok" or missing:
        return (
            OperationalCheck(
                name="lambda_response",
                status="fail",
                message="Lambda response did not include successful application evidence.",
                details={**asdict(evidence), "missing": missing},
            ),
            evidence,
        )

    return (
        OperationalCheck(
            name="lambda_response",
            status="ok",
            message="Lambda response returned AWS status 200 and application status ok.",
            details=asdict(evidence),
        ),
        evidence,
    )


def _check_s3_archive(s3_uri: str, *, region: str) -> OperationalCheck:
    try:
        bucket, key = _parse_s3_uri(s3_uri)
        response = boto3.client("s3", region_name=region).head_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return OperationalCheck(
            name="s3_archive",
            status="fail",
            message=f"Could not verify S3 archive: {exc}",
            details={"s3_archive": s3_uri},
        )

    return OperationalCheck(
        name="s3_archive",
        status="ok",
        message="S3 archive object exists.",
        details={
            "s3_archive": s3_uri,
            "content_length": response.get("ContentLength"),
            "last_modified": response.get("LastModified"),
            "etag": response.get("ETag"),
        },
    )


def _check_lambda_log_stream(*, function_name: str, region: str) -> OperationalCheck:
    log_group = f"/aws/lambda/{function_name}"
    try:
        streams = boto3.client("logs", region_name=region).describe_log_streams(
            logGroupName=log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=1,
        ).get("logStreams", [])
    except (BotoCoreError, ClientError) as exc:
        return OperationalCheck(
            name="lambda_logs",
            status="fail",
            message=f"Could not inspect Lambda log group {log_group}: {exc}",
        )

    if not streams:
        return OperationalCheck(
            name="lambda_logs",
            status="fail",
            message=f"Lambda log group {log_group} has no log streams.",
        )

    stream = streams[0]
    return OperationalCheck(
        name="lambda_logs",
        status="ok",
        message="Lambda service log stream exists.",
        details={
            "log_group": log_group,
            "log_stream": stream.get("logStreamName"),
            "last_event_timestamp": stream.get("lastEventTimestamp"),
        },
    )


def _check_pipeline_log_stream(*, log_group: str, session_id: str, region: str) -> OperationalCheck:
    log_stream = f"caseops-session/{session_id}"
    try:
        streams = boto3.client("logs", region_name=region).describe_log_streams(
            logGroupName=log_group,
            logStreamNamePrefix=log_stream,
            limit=1,
        ).get("logStreams", [])
    except (BotoCoreError, ClientError) as exc:
        return OperationalCheck(
            name="pipeline_logs",
            status="fail",
            message=f"Could not inspect pipeline log stream {log_stream}: {exc}",
        )

    if not streams:
        return OperationalCheck(
            name="pipeline_logs",
            status="fail",
            message=f"Pipeline log stream {log_stream} was not found.",
            details={"log_group": log_group, "log_stream": log_stream},
        )

    return OperationalCheck(
        name="pipeline_logs",
        status="ok",
        message="Structured pipeline log stream lookup succeeded.",
        details={"log_group": log_group, "log_stream": log_stream},
    )


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _print_text_report(checks: list[OperationalCheck]) -> None:
    print("CaseOps operational validation")
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.message}")
        if check.details:
            for key, value in check.details.items():
                print(f"      {key}: {value}")

    if any(check.status == "fail" for check in checks):
        print("[fail] Operational validation found blocking issues.")
    else:
        print("[ok] Operational validation passed.")


if __name__ == "__main__":
    raise SystemExit(main())
