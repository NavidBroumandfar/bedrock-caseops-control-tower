"""
Repeatable SAM deployment helper for CaseOps Lambda stacks.

The helper keeps environment-specific values out of committed SAM config files
while still making dev and staging deploys reproducible from `.env` or shell
environment variables.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


_ENVIRONMENT_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass(frozen=True)
class DeployConfig:
    """Resolved deployment settings passed to SAM."""

    environment_name: str
    stack_name: str
    region: str
    bedrock_kb_id: str
    bedrock_model_id: str
    bedrock_model_arn: str | None
    enable_guardrails: bool
    guardrail_id: str | None
    guardrail_version: str
    run_build: bool
    confirm_changeset: bool


def main() -> int:
    load_dotenv(find_dotenv(usecwd=True), override=False)

    parser = argparse.ArgumentParser(
        description="Build and deploy a CaseOps SAM stack with env-backed parameters."
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("CASEOPS_DEPLOY_ENVIRONMENT", "dev"),
        help="Environment suffix for stack names and template EnvironmentName.",
    )
    parser.add_argument("--stack-name", help="CloudFormation stack name.")
    parser.add_argument("--region", help="AWS region. Defaults to AWS_REGION.")
    parser.add_argument("--kb-id", help="Bedrock Knowledge Base ID.")
    parser.add_argument("--model-id", help="Bedrock model ID.")
    parser.add_argument("--model-arn", help="Optional explicit model or inference-profile ARN.")
    parser.add_argument(
        "--enable-guardrails",
        action="store_true",
        default=None,
        help="Enable runtime Bedrock Guardrails for the deployed Lambda.",
    )
    parser.add_argument(
        "--disable-guardrails",
        action="store_true",
        help="Force Guardrails off even if environment variables enable them.",
    )
    parser.add_argument("--guardrail-id", help="Bedrock Guardrail ID.")
    parser.add_argument("--guardrail-version", help="Bedrock Guardrail version.")
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip `sam build` and deploy the current built template/artifacts.",
    )
    parser.add_argument(
        "--confirm-changeset",
        action="store_true",
        help="Let SAM prompt before executing the generated changeset.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SAM commands without executing them.",
    )
    args = parser.parse_args()

    try:
        config = resolve_deploy_config(args)
    except ValueError as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1

    commands = build_sam_commands(config)
    if args.dry_run:
        for command in commands:
            print(shlex.join(command))
        return 0

    for command in commands:
        print(f"$ {shlex.join(command)}")
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            return result.returncode

    return 0


def resolve_deploy_config(args: argparse.Namespace) -> DeployConfig:
    """Resolve deployment config from CLI args, environment-specific env, and defaults."""
    environment_name = _normalize_environment_name(args.environment)
    env_prefix = environment_name.upper().replace("-", "_")

    region = _first_non_empty(
        args.region,
        os.getenv(f"{env_prefix}_AWS_REGION"),
        os.getenv("CASEOPS_DEPLOY_REGION"),
        os.getenv("AWS_REGION"),
    )
    kb_id = _first_non_empty(
        args.kb_id,
        os.getenv(f"{env_prefix}_BEDROCK_KB_ID"),
        os.getenv("BEDROCK_KB_ID"),
    )
    model_id = _first_non_empty(
        args.model_id,
        os.getenv(f"{env_prefix}_BEDROCK_MODEL_ID"),
        os.getenv("BEDROCK_MODEL_ID"),
    )
    model_arn = _first_non_empty(
        args.model_arn,
        os.getenv(f"{env_prefix}_BEDROCK_MODEL_ARN"),
        os.getenv("BEDROCK_MODEL_ARN"),
    )

    missing = [
        name
        for name, value in {
            "AWS_REGION": region,
            "BEDROCK_KB_ID": kb_id,
            "BEDROCK_MODEL_ID": model_id,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"missing required deployment values: {', '.join(missing)}")

    enable_guardrails = _resolve_guardrails_enabled(args, env_prefix)
    guardrail_id = _first_non_empty(
        args.guardrail_id,
        os.getenv(f"{env_prefix}_CASEOPS_GUARDRAIL_ID"),
        os.getenv("CASEOPS_GUARDRAIL_ID"),
    )
    guardrail_version = _first_non_empty(
        args.guardrail_version,
        os.getenv(f"{env_prefix}_CASEOPS_GUARDRAIL_VERSION"),
        os.getenv("CASEOPS_GUARDRAIL_VERSION"),
        "1",
    )
    if enable_guardrails and not guardrail_id:
        raise ValueError("GuardrailId is required when Guardrails are enabled")

    return DeployConfig(
        environment_name=environment_name,
        stack_name=args.stack_name or f"bedrock-caseops-control-tower-{environment_name}",
        region=region,
        bedrock_kb_id=kb_id,
        bedrock_model_id=model_id,
        bedrock_model_arn=model_arn,
        enable_guardrails=enable_guardrails,
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
        run_build=not args.no_build,
        confirm_changeset=args.confirm_changeset,
    )


def build_sam_commands(config: DeployConfig) -> list[list[str]]:
    """Return the SAM build/deploy commands for a resolved config."""
    commands: list[list[str]] = []
    if config.run_build:
        commands.append(["sam", "build"])

    parameter_overrides = [
        f"EnvironmentName={config.environment_name}",
        f"BedrockKbId={config.bedrock_kb_id}",
        f"BedrockModelId={config.bedrock_model_id}",
        f"EnableGuardrails={str(config.enable_guardrails).lower()}",
    ]
    if config.bedrock_model_arn:
        parameter_overrides.append(f"BedrockModelArn={config.bedrock_model_arn}")
    if config.enable_guardrails:
        parameter_overrides.extend(
            [
                f"GuardrailId={config.guardrail_id}",
                f"GuardrailVersion={config.guardrail_version}",
            ]
        )

    deploy_command = [
        "sam",
        "deploy",
        "--stack-name",
        config.stack_name,
        "--region",
        config.region,
        "--resolve-s3",
        "--capabilities",
        "CAPABILITY_IAM",
        "CAPABILITY_AUTO_EXPAND",
        "--parameter-overrides",
        *parameter_overrides,
        "--no-fail-on-empty-changeset",
    ]
    if not config.confirm_changeset:
        deploy_command.append("--no-confirm-changeset")

    commands.append(deploy_command)
    return commands


def _resolve_guardrails_enabled(args: argparse.Namespace, env_prefix: str) -> bool:
    if args.disable_guardrails:
        return False
    if args.enable_guardrails is True:
        return True

    raw = _first_non_empty(
        os.getenv(f"{env_prefix}_CASEOPS_ENABLE_GUARDRAILS"),
        os.getenv("CASEOPS_ENABLE_GUARDRAILS"),
        "false",
    )
    return _parse_bool(raw, name="CASEOPS_ENABLE_GUARDRAILS")


def _normalize_environment_name(value: str) -> str:
    environment_name = value.strip().lower()
    if not environment_name:
        raise ValueError("environment name is required")
    if not _ENVIRONMENT_RE.fullmatch(environment_name):
        raise ValueError("environment name must contain only lowercase letters, numbers, and hyphens")
    return environment_name


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None


def _parse_bool(raw: str, *, name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got {raw!r}")


if __name__ == "__main__":
    raise SystemExit(main())
