"""Unit tests for deployment preflight helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.deployment_preflight import (
    _check_guardrails_env,
    _check_required_env,
    _check_sam_cli,
    run_preflight,
)


def test_check_required_env_reports_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_REGION", "BEDROCK_KB_ID", "BEDROCK_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)

    result = _check_required_env()

    assert result.status == "fail"
    assert result.details == {"missing": ["AWS_REGION", "BEDROCK_KB_ID", "BEDROCK_MODEL_ID"]}


def test_check_required_env_masks_kb_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("BEDROCK_KB_ID", "KBSECRET")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-id")

    result = _check_required_env()

    assert result.status == "ok"
    assert result.details["bedrock_kb_id"] == "set"


def test_guardrails_enabled_requires_guardrail_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_GUARDRAILS", "true")
    monkeypatch.delenv("CASEOPS_GUARDRAIL_ID", raising=False)

    result = _check_guardrails_env()

    assert result.status == "fail"
    assert "CASEOPS_GUARDRAIL_ID" in result.message


def test_sam_check_can_warn_when_not_required() -> None:
    with patch("scripts.deployment_preflight.shutil.which", return_value=None):
        result = _check_sam_cli(require_sam=False)

    assert result.status == "warn"


def test_run_preflight_includes_skipped_kb_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-test")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-id")
    template = tmp_path / "template.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n", encoding="utf-8")

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/test",
    }
    mock_cfn = MagicMock()
    mock_cfn.validate_template.return_value = {
        "Parameters": [],
        "Capabilities": [],
    }

    def client_factory(service_name: str, **_: object):
        return {"sts": mock_sts, "cloudformation": mock_cfn}[service_name]

    with (
        patch("scripts.deployment_preflight.shutil.which", return_value=None),
        patch("scripts.deployment_preflight.boto3.client", side_effect=client_factory),
    ):
        checks = run_preflight(template_path=template, require_sam=False, check_kb=False)

    assert any(check.name == "bedrock_knowledge_base" and check.status == "warn" for check in checks)
    assert any(check.name == "cloudformation_template" and check.status == "ok" for check in checks)
