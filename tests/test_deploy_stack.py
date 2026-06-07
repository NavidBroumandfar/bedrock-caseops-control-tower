"""Unit tests for the repeatable SAM deployment helper."""

from argparse import Namespace

import pytest

from scripts.deploy_stack import build_sam_commands, resolve_deploy_config


def _args(**overrides: object) -> Namespace:
    values = {
        "environment": "dev",
        "stack_name": None,
        "region": None,
        "kb_id": None,
        "model_id": None,
        "model_arn": None,
        "enable_guardrails": None,
        "disable_guardrails": False,
        "guardrail_id": None,
        "guardrail_version": None,
        "no_build": False,
        "confirm_changeset": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_deploy_helper_builds_dev_sam_commands_from_base_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-dev")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-dev")
    monkeypatch.delenv("CASEOPS_ENABLE_GUARDRAILS", raising=False)

    config = resolve_deploy_config(_args())
    commands = build_sam_commands(config)

    assert commands[0] == ["sam", "build"]
    deploy_command = commands[1]
    assert deploy_command[:2] == ["sam", "deploy"]
    assert "bedrock-caseops-control-tower-dev" in deploy_command
    assert "EnvironmentName=dev" in deploy_command
    assert "BedrockKbId=kb-dev" in deploy_command
    assert "BedrockModelId=model-dev" in deploy_command
    assert "EnableGuardrails=false" in deploy_command
    assert not any(value.startswith("GuardrailId=") for value in deploy_command)
    assert "--no-confirm-changeset" in deploy_command


def test_deploy_helper_prefers_environment_specific_staging_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-dev")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-dev")
    monkeypatch.setenv("STAGING_BEDROCK_KB_ID", "kb-staging")
    monkeypatch.setenv("STAGING_BEDROCK_MODEL_ID", "model-staging")

    config = resolve_deploy_config(_args(environment="staging"))

    assert config.stack_name == "bedrock-caseops-control-tower-staging"
    assert config.bedrock_kb_id == "kb-staging"
    assert config.bedrock_model_id == "model-staging"


def test_deploy_helper_requires_guardrail_id_when_guardrails_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-dev")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-dev")
    monkeypatch.setenv("CASEOPS_ENABLE_GUARDRAILS", "true")
    monkeypatch.delenv("CASEOPS_GUARDRAIL_ID", raising=False)

    with pytest.raises(ValueError, match="GuardrailId is required"):
        resolve_deploy_config(_args())


def test_deploy_helper_includes_guardrail_parameters_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-dev")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-dev")

    config = resolve_deploy_config(
        _args(
            enable_guardrails=True,
            guardrail_id="guardrail-123",
            guardrail_version="7",
            no_build=True,
        )
    )
    commands = build_sam_commands(config)

    assert len(commands) == 1
    deploy_command = commands[0]
    assert "EnableGuardrails=true" in deploy_command
    assert "GuardrailId=guardrail-123" in deploy_command
    assert "GuardrailVersion=7" in deploy_command
