"""
Deployment preflight for the CaseOps SAM/Lambda foundation.

This script checks the operator prerequisites that must be true before a live
`sam deploy` is worth attempting. It intentionally does not deploy anything.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True)
class PreflightCheck:
    """One deployment preflight check result."""

    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate local and AWS prerequisites before SAM deployment."
    )
    parser.add_argument(
        "--template",
        default="template.yaml",
        help="SAM/CloudFormation template path.",
    )
    parser.add_argument(
        "--skip-sam",
        action="store_true",
        help="Do not require the SAM CLI. Useful for environments that only run AWS-side template validation.",
    )
    parser.add_argument(
        "--skip-kb",
        action="store_true",
        help="Skip live Bedrock Knowledge Base describe/list checks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True), override=False)

    checks = run_preflight(
        template_path=Path(args.template),
        require_sam=not args.skip_sam,
        check_kb=not args.skip_kb,
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


def run_preflight(
    *,
    template_path: Path,
    require_sam: bool = True,
    check_kb: bool = True,
) -> list[PreflightCheck]:
    """Run deployment preflight checks and return structured results."""
    checks: list[PreflightCheck] = []
    checks.append(_check_required_env())
    checks.append(_check_guardrails_env())
    checks.append(_check_sam_cli(require_sam=require_sam))
    checks.append(_check_aws_identity())
    checks.append(_check_cloudformation_template(template_path))

    if check_kb:
        checks.append(_check_knowledge_base())
    else:
        checks.append(
            PreflightCheck(
                name="bedrock_knowledge_base",
                status="warn",
                message="Skipped live Knowledge Base check.",
            )
        )

    return checks


def _check_required_env() -> PreflightCheck:
    required = ("AWS_REGION", "BEDROCK_KB_ID", "BEDROCK_MODEL_ID")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        return PreflightCheck(
            name="required_environment",
            status="fail",
            message="Missing required deployment variables.",
            details={"missing": missing},
        )

    return PreflightCheck(
        name="required_environment",
        status="ok",
        message="Required deployment variables are set.",
        details={
            "aws_region": os.getenv("AWS_REGION"),
            "bedrock_model_id": os.getenv("BEDROCK_MODEL_ID"),
            "bedrock_kb_id": "set",
        },
    )


def _check_guardrails_env() -> PreflightCheck:
    enabled = os.getenv("CASEOPS_ENABLE_GUARDRAILS", "false").strip().lower()
    if enabled not in {"true", "false"}:
        return PreflightCheck(
            name="guardrails_environment",
            status="fail",
            message="CASEOPS_ENABLE_GUARDRAILS must be 'true' or 'false'.",
            details={"value": enabled},
        )
    if enabled == "true" and not os.getenv("CASEOPS_GUARDRAIL_ID", "").strip():
        return PreflightCheck(
            name="guardrails_environment",
            status="fail",
            message="CASEOPS_GUARDRAIL_ID is required when Guardrails are enabled.",
        )
    return PreflightCheck(
        name="guardrails_environment",
        status="ok",
        message="Guardrails deployment environment is consistent.",
        details={"enabled": enabled == "true"},
    )


def _check_sam_cli(*, require_sam: bool) -> PreflightCheck:
    sam_path = shutil.which("sam")
    if not sam_path:
        status = "fail" if require_sam else "warn"
        return PreflightCheck(
            name="sam_cli",
            status=status,
            message="SAM CLI is not installed or not on PATH.",
        )

    try:
        result = subprocess.run(
            ["sam", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        version = (result.stdout or result.stderr).strip()
    except OSError as exc:
        return PreflightCheck(
            name="sam_cli",
            status="fail" if require_sam else "warn",
            message=f"SAM CLI could not be executed: {exc}",
        )

    return PreflightCheck(
        name="sam_cli",
        status="ok",
        message="SAM CLI is available.",
        details={"path": sam_path, "version": version},
    )


def _check_aws_identity() -> PreflightCheck:
    region = os.getenv("AWS_REGION", "us-east-1")
    try:
        identity = boto3.client("sts", region_name=region).get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        return PreflightCheck(
            name="aws_identity",
            status="fail",
            message=f"Could not resolve AWS caller identity: {exc}",
        )

    return PreflightCheck(
        name="aws_identity",
        status="ok",
        message="AWS caller identity resolved.",
        details={
            "account": identity.get("Account"),
            "arn": identity.get("Arn"),
        },
    )


def _check_cloudformation_template(template_path: Path) -> PreflightCheck:
    if not template_path.exists():
        return PreflightCheck(
            name="cloudformation_template",
            status="fail",
            message=f"Template not found: {template_path}",
        )

    region = os.getenv("AWS_REGION", "us-east-1")
    try:
        template_body = template_path.read_text(encoding="utf-8")
        response = boto3.client("cloudformation", region_name=region).validate_template(
            TemplateBody=template_body
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        return PreflightCheck(
            name="cloudformation_template",
            status="fail",
            message=f"CloudFormation template validation failed: {exc}",
        )

    return PreflightCheck(
        name="cloudformation_template",
        status="ok",
        message="CloudFormation accepted the SAM template.",
        details={
            "parameter_count": len(response.get("Parameters", [])),
            "capabilities": response.get("Capabilities", []),
        },
    )


def _check_knowledge_base() -> PreflightCheck:
    region = os.getenv("AWS_REGION", "us-east-1")
    kb_id = os.getenv("BEDROCK_KB_ID", "").strip()
    if not kb_id:
        return PreflightCheck(
            name="bedrock_knowledge_base",
            status="fail",
            message="BEDROCK_KB_ID is required for the Knowledge Base check.",
        )

    try:
        client = boto3.client("bedrock-agent", region_name=region)
        kb = client.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        sources = client.list_data_sources(knowledgeBaseId=kb_id).get(
            "dataSourceSummaries",
            [],
        )
    except (BotoCoreError, ClientError) as exc:
        return PreflightCheck(
            name="bedrock_knowledge_base",
            status="fail",
            message=f"Could not describe configured Knowledge Base: {exc}",
        )

    kb_status = kb.get("status", "")
    status = "ok" if kb_status == "ACTIVE" else "warn"
    return PreflightCheck(
        name="bedrock_knowledge_base",
        status=status,
        message="Configured Knowledge Base is reachable.",
        details={
            "knowledge_base_id": kb_id,
            "knowledge_base_name": kb.get("name"),
            "knowledge_base_status": kb_status,
            "data_source_count": len(sources),
        },
    )


def _print_text_report(checks: list[PreflightCheck]) -> None:
    print("CaseOps deployment preflight")
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.message}")
        if check.details:
            for key, value in check.details.items():
                print(f"      {key}: {value}")
    if any(check.status == "fail" for check in checks):
        print("[fail] Deployment preflight found blocking issues.")
    else:
        print("[ok] Deployment preflight passed.")


if __name__ == "__main__":
    raise SystemExit(main())
