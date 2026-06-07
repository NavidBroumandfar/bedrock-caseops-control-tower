"""Smoke tests for Phase 7 deployment assets."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_sam_template_declares_lambda_handler_and_required_iam_actions() -> None:
    template = (_REPO_ROOT / "template.yaml").read_text(encoding="utf-8")

    assert "Handler: app.lambda_handler.lambda_handler" in template
    assert "Runtime: python3.12" in template
    for action in (
        "s3:GetObject",
        "s3:PutObject",
        "bedrock:InvokeModel",
        "bedrock:Retrieve",
        "bedrock:ApplyGuardrail",
        "logs:PutLogEvents",
        "cloudwatch:PutMetricData",
    ):
        assert action in template


def test_sam_template_declares_phase_10_monitoring_resources() -> None:
    template = (_REPO_ROOT / "template.yaml").read_text(encoding="utf-8")

    for resource in (
        "CaseOpsSafetyBlockMetricFilter",
        "CaseOpsApplicationErrorMetricFilter",
        "CaseOpsArchiveFailureMetricFilter",
        "CaseOpsLambdaErrorsAlarm",
        "CaseOpsLambdaThrottlesAlarm",
        "CaseOpsLambdaDurationAlarm",
        "CaseOpsApplicationErrorsAlarm",
        "CaseOpsArchiveFailuresAlarm",
        "CaseOpsSafetyBlocksAlarm",
    ):
        assert resource in template

    assert 'FilterPattern: \'{ $.event = "safety_blocked" }\'' in template
    assert 'FilterPattern: \'{ $.event = "lambda_error" }\'' in template
    assert 'FilterPattern: \'{ $.event = "lambda_error" && $.error_type = "StorageError" }\'' in template


def test_pull_request_ci_runs_pytest() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )

    assert "pull_request:" in workflow
    assert "python -m pytest -q" in workflow


def test_production_readiness_doc_declares_release_gate_and_cleanup() -> None:
    doc = (_REPO_ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8"
    )

    assert "Required Release Gate" in doc
    assert "Rollback" in doc
    assert "Cleanup" in doc
    assert "scripts/operational_check.py" in doc
