"""
Unit tests for the Lambda deployment entry point.

All AWS-facing runtime dependencies are patched or backed by existing local
models.  These tests validate the Lambda boundary contract without live AWS.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.lambda_handler import _normalise_event, lambda_handler
from app.schemas.intake_models import IntakeRecord, IntakeResult
from app.schemas.lambda_models import LambdaPipelineRequest
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.safety_models import (
    IssueSource,
    SafetyAssessment,
    SafetyIssue,
    SafetyIssueCode,
    SafetyIssueSeverity,
    SafetyStatus,
)
from app.workflows.runtime_safety import RuntimeSafetyResult

_DOC_ID = "doc-20260606-lambda01"
_SESSION_ID = "sess-lambda1"


@pytest.fixture(autouse=True)
def lambda_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("CASEOPS_LAMBDA_INPUT_DIR", str(tmp_path / "inputs"))
    monkeypatch.setenv("BEDROCK_KB_ID", "kb-test-123")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CASEOPS_ENABLE_GUARDRAILS", "false")
    monkeypatch.setenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", "false")
    monkeypatch.setenv("CASEOPS_ENABLE_CLOUDWATCH", "false")
    monkeypatch.delenv("S3_DOCUMENT_BUCKET", raising=False)
    monkeypatch.delenv("S3_OUTPUT_BUCKET", raising=False)


def _make_intake_record() -> IntakeRecord:
    return IntakeRecord(
        document_id=_DOC_ID,
        original_filename="advisory.txt",
        extension=".txt",
        absolute_path=f"/tmp/{_DOC_ID}/advisory.txt",
        file_size_bytes=128,
        intake_timestamp="2026-06-06T00:00:00+00:00",
        source_type="FDA",
        document_date="2026-03-30",
        submitter_note="runtime test",
    )


def _make_intake_result() -> IntakeResult:
    return IntakeResult(
        document_id=_DOC_ID,
        artifact_path=f"/tmp/outputs/intake/{_DOC_ID}.json",
        record=_make_intake_record(),
        storage=None,
    )


def _make_case_output() -> CaseOutput:
    return CaseOutput(
        document_id=_DOC_ID,
        source_filename="advisory.txt",
        source_type="FDA",
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to establish adequate written procedures.",
        recommendations=["Initiate CAPA immediately."],
        citations=[
            Citation(
                chunk_id="chunk-1",
                source_id="s3://kb/fda/test.txt::0",
                source_label="FDA Test Document",
                excerpt="test excerpt",
                relevance_score=0.88,
            )
        ],
        confidence_score=0.87,
        unsupported_claims=[],
        escalation_required=False,
        escalation_reason=None,
        validated_by="tool-executor-agent-v1",
        session_id=_SESSION_ID,
        timestamp="2026-06-06T00:00:00+00:00",
    )


def _make_safety_assessment(status: SafetyStatus = SafetyStatus.ALLOW) -> SafetyAssessment:
    issues = []
    if status == SafetyStatus.BLOCK:
        issues = [
            SafetyIssue(
                issue_code=SafetyIssueCode.GUARDRAIL_INTERVENTION,
                severity=SafetyIssueSeverity.ERROR,
                message="Guardrail blocked generated output",
                blocking=True,
                source=IssueSource.GUARDRAILS,
            )
        ]
    return SafetyAssessment(
        document_id=_DOC_ID,
        issues=issues,
        has_blocking_issue=bool(issues),
        requires_escalation=status in (SafetyStatus.ESCALATE, SafetyStatus.BLOCK),
        status=status,
        notes="lambda safety test",
        timestamp="2026-06-06T00:00:00+00:00",
    )


def _make_safety_result(tmp_path: Path, status: SafetyStatus = SafetyStatus.ALLOW) -> RuntimeSafetyResult:
    return RuntimeSafetyResult(
        assessment=_make_safety_assessment(status),
        artifact_path=tmp_path / f"{_DOC_ID}.safety.json",
    )


def test_lambda_request_requires_exactly_one_document_source() -> None:
    with pytest.raises(ValidationError):
        LambdaPipelineRequest.model_validate(
            {
                "source_type": "FDA",
                "document_date": "2026-03-30",
                "document": {"filename": "a.txt", "text": "content"},
                "s3": {"bucket": "bucket", "key": "key.txt"},
            }
        )


def test_normalise_event_decodes_api_gateway_base64_body() -> None:
    payload = {
        "source_type": "FDA",
        "document_date": "2026-03-30",
        "document": {"filename": "advisory.txt", "text": "content"},
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    assert _normalise_event({"body": encoded, "isBase64Encoded": True}) == payload


def test_lambda_handler_runs_inline_document_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs_dir = tmp_path / "inputs"
    outputs_dir = tmp_path / "outputs"
    monkeypatch.setenv("CASEOPS_LAMBDA_INPUT_DIR", str(inputs_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(outputs_dir))
    output_path = outputs_dir / f"{_DOC_ID}.json"

    event = {
        "source_type": "FDA",
        "document_date": "2026-03-30",
        "submitter_note": "runtime test",
        "document": {"filename": "advisory.txt", "text": "FDA advisory content."},
    }

    with (
        patch("app.lambda_handler.run_intake", return_value=_make_intake_result()) as run_intake_mock,
        patch("app.lambda_handler.build_pipeline_dependencies", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("app.lambda_handler.run_pipeline", return_value=_make_case_output()) as run_pipeline_mock,
        patch("app.lambda_handler.run_operator_input_safety_check", return_value=None),
        patch("app.lambda_handler.run_case_output_safety_check", return_value=_make_safety_result(tmp_path)),
        patch("app.lambda_handler.write_case_output", return_value=output_path) as write_output_mock,
    ):
        response = lambda_handler(event, None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["status"] == "ok"
    assert body["document_id"] == _DOC_ID
    assert body["case_output"]["document_id"] == _DOC_ID
    assert (inputs_dir / "advisory.txt").read_text(encoding="utf-8") == "FDA advisory content."
    run_intake_mock.assert_called_once()
    run_pipeline_mock.assert_called_once()
    write_output_mock.assert_called_once()


def test_lambda_handler_returns_422_when_output_safety_blocks(tmp_path: Path) -> None:
    event = {
        "source_type": "FDA",
        "document_date": "2026-03-30",
        "document": {"filename": "advisory.txt", "text": "FDA advisory content."},
    }

    with (
        patch("app.lambda_handler.run_intake", return_value=_make_intake_result()),
        patch("app.lambda_handler.build_pipeline_dependencies", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("app.lambda_handler.run_pipeline", return_value=_make_case_output()),
        patch("app.lambda_handler.run_operator_input_safety_check", return_value=None),
        patch(
            "app.lambda_handler.run_case_output_safety_check",
            return_value=_make_safety_result(tmp_path, SafetyStatus.BLOCK),
        ),
        patch("app.lambda_handler.write_case_output") as write_output_mock,
        patch("builtins.print") as print_mock,
    ):
        response = lambda_handler(event, None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 422
    assert body["status"] == "blocked"
    assert body["safety_status"] == "block"
    emitted = json.loads(print_mock.call_args.args[0])
    assert emitted["event"] == "safety_blocked"
    assert emitted["safety_status"] == "block"
    write_output_mock.assert_not_called()


def test_lambda_handler_bad_event_returns_400() -> None:
    response = lambda_handler({"source_type": "FDA", "document_date": "2026-03-30"}, None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 400
    assert body["error_type"] == "bad_request"
