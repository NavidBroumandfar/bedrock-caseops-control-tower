"""
AWS Lambda entry point for the CaseOps pipeline.

The handler supports direct Lambda invocation and API Gateway-style events.  The
event body must match LambdaPipelineRequest:

Inline document:
  {
    "source_type": "FDA",
    "document_date": "2026-03-30",
    "submitter_note": "optional retrieval hint",
    "document": {"filename": "advisory.txt", "text": "..."}
  }

S3 document:
  {
    "source_type": "CISA",
    "document_date": "2026-03-30",
    "s3": {"bucket": "caseops-doc-bucket", "key": "incoming/advisory.txt"}
  }
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote_plus

from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from app.schemas.lambda_models import LambdaPipelineRequest
from app.schemas.safety_models import FailurePolicy, SafetyAssessment, SafetyStatus
from app.services.intake_service import IntakeError, run_intake
from app.services.s3_service import S3Service, StorageError
from app.utils.config import (
    load_guardrails_config,
    load_pipeline_config,
)
from app.utils.id_utils import generate_session_id
from app.utils.logging_utils import LoggingConfig, PipelineLogger
from app.utils.output_writer import OutputWriteError, write_case_output
from app.workflows.pipeline_workflow import PipelineWorkflowError, run_pipeline
from app.workflows.runtime_factory import (
    RuntimeDependencyError,
    build_pipeline_dependencies,
)
from app.workflows.runtime_safety import (
    RuntimeSafetyError,
    build_operator_input_text,
    run_case_output_safety_check,
    run_operator_input_safety_check,
)

_DEFAULT_INPUT_DIR = "/tmp/caseops/inputs"
_DEFAULT_OUTPUT_DIR = "/tmp/caseops/outputs"


class LambdaInputError(Exception):
    """Raised when the Lambda event cannot be validated or materialised."""


class LambdaSafetyBlockedError(Exception):
    """Raised when runtime safety policy blocks normal output generation."""

    def __init__(self, message: str, assessment: SafetyAssessment, artifact_path: Path) -> None:
        super().__init__(message)
        self.assessment = assessment
        self.artifact_path = artifact_path


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    """Lambda-compatible handler for the CaseOps pipeline."""
    _load_env_file()
    try:
        payload = _normalise_event(event)
        request = LambdaPipelineRequest.model_validate(payload)
        result = run_lambda_pipeline(request)
        return _response(200, result)
    except (LambdaInputError, ValidationError, ValueError) as exc:
        _emit_lambda_event(
            "bad_request",
            level="WARNING",
            error_type="bad_request",
            message=str(exc),
        )
        return _response(400, {"status": "error", "error_type": "bad_request", "message": str(exc)})
    except LambdaSafetyBlockedError as exc:
        _emit_lambda_event(
            "safety_blocked",
            level="ERROR",
            safety_status=exc.assessment.status.value,
            safety_issue_count=len(exc.assessment.issues),
            safety_artifact_path=str(exc.artifact_path),
        )
        return _response(
            422,
            {
                "status": "blocked",
                "message": str(exc),
                "safety_status": exc.assessment.status.value,
                "safety_issues": [issue.model_dump(mode="json") for issue in exc.assessment.issues],
                "safety_artifact_path": str(exc.artifact_path),
            },
        )
    except (
        IntakeError,
        RuntimeDependencyError,
        PipelineWorkflowError,
        RuntimeSafetyError,
        OutputWriteError,
        StorageError,
    ) as exc:
        _emit_lambda_event(
            "lambda_error",
            level="ERROR",
            error_type=exc.__class__.__name__,
            message=str(exc),
        )
        return _response(
            500,
            {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            },
        )
    except Exception as exc:
        _emit_lambda_event(
            "lambda_error",
            level="ERROR",
            error_type="UnexpectedError",
            message=str(exc),
        )
        return _response(
            500,
            {
                "status": "error",
                "error_type": "UnexpectedError",
                "message": str(exc),
            },
        )


def run_lambda_pipeline(request: LambdaPipelineRequest) -> dict[str, Any]:
    """
    Materialise the event document, run intake, safety gates, pipeline, and output archival.

    Returns a JSON-serialisable summary suitable for Lambda responses.
    """
    output_dir = _resolve_output_dir()
    document_path = _materialise_document(request)
    metadata = request.to_intake_metadata()

    document_upload_service = _build_optional_s3_service("S3_DOCUMENT_BUCKET")
    intake_result = run_intake(
        file_path=str(document_path),
        metadata=metadata,
        output_dir=output_dir / "intake",
        s3_service=document_upload_service,
    )

    guardrails_config = load_guardrails_config()
    guardrails_service = _build_guardrails_service(guardrails_config)

    input_safety = run_operator_input_safety_check(
        document_id=intake_result.document_id,
        operator_input_text=build_operator_input_text(
            file_path=str(document_path),
            source_type=request.source_type,
            document_date=request.document_date,
            submitter_note=request.submitter_note,
        ),
        output_dir=output_dir,
        guardrails_config=guardrails_config,
        guardrails_service=guardrails_service,
    )
    if input_safety and input_safety.assessment.status == SafetyStatus.BLOCK:
        raise LambdaSafetyBlockedError(
            "Operator input blocked by runtime safety policy.",
            input_safety.assessment,
            input_safety.artifact_path,
        )

    runtime_config = load_pipeline_config()
    retrieval_provider, analysis_agent, validation_agent, tool_executor = (
        build_pipeline_dependencies(runtime_config=runtime_config)
    )
    session_id = generate_session_id()
    logger = _build_logger(session_id)

    output = run_pipeline(
        intake_result,
        retrieval_provider=retrieval_provider,
        analysis_agent=analysis_agent,
        validation_agent=validation_agent,
        tool_executor=tool_executor,
        logger=logger,
        session_id=session_id,
        max_attempts=runtime_config.max_agent_retries,
    )

    safety_result = run_case_output_safety_check(
        output=output,
        output_dir=output_dir,
        policy=FailurePolicy(
            low_confidence_threshold=runtime_config.escalation_confidence_threshold
        ),
        guardrails_config=guardrails_config,
        guardrails_service=guardrails_service,
    )
    if safety_result.assessment.status == SafetyStatus.BLOCK:
        raise LambdaSafetyBlockedError(
            "Generated output blocked by runtime safety policy.",
            safety_result.assessment,
            safety_result.artifact_path,
        )

    output_path = write_case_output(output, output_dir=output_dir)
    s3_archive_location = _archive_output_to_s3(output_path, output.document_id)

    response: dict[str, Any] = {
        "status": "ok",
        "document_id": output.document_id,
        "session_id": output.session_id,
        "severity": output.severity,
        "category": output.category,
        "confidence_score": output.confidence_score,
        "escalation_required": output.escalation_required,
        "escalation_reason": output.escalation_reason,
        "citation_count": len(output.citations),
        "safety_status": safety_result.assessment.status.value,
        "safety_issue_count": len(safety_result.assessment.issues),
        "output_path": str(output_path),
        "safety_artifact_path": str(safety_result.artifact_path),
        "s3_archive": s3_archive_location,
    }
    if request.include_output:
        response["case_output"] = output.model_dump(mode="json")
    return response


def _load_env_file() -> None:
    """Load a local .env file when present; Lambda environment variables still win."""
    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path, override=False)


def _normalise_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON object payload from a direct or API Gateway-style event."""
    if not isinstance(event, dict):
        raise LambdaInputError("event must be a JSON object")
    if "body" not in event:
        return event

    body = event.get("body")
    if body is None:
        raise LambdaInputError("event body must not be null")
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as exc:
            raise LambdaInputError(f"event body is not valid base64 UTF-8: {exc}") from exc
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LambdaInputError(f"event body is not valid JSON: {exc}") from exc
    elif isinstance(body, dict):
        payload = body
    else:
        raise LambdaInputError("event body must be a JSON string or object")
    if not isinstance(payload, dict):
        raise LambdaInputError("event body must decode to a JSON object")
    return payload


def _materialise_document(request: LambdaPipelineRequest) -> Path:
    if request.document is not None:
        return _write_inline_document(request)
    if request.s3 is not None:
        return _download_s3_document(request)
    raise LambdaInputError("event must include a document source")


def _write_inline_document(request: LambdaPipelineRequest) -> Path:
    assert request.document is not None
    destination = _safe_input_path(request.document.filename)
    if request.document.text is not None:
        content = request.document.text.encode("utf-8")
    else:
        try:
            content = base64.b64decode(request.document.base64_content or "", validate=True)
        except Exception as exc:
            raise LambdaInputError(f"document.base64_content is invalid: {exc}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return destination


def _download_s3_document(request: LambdaPipelineRequest) -> Path:
    assert request.s3 is not None
    filename = request.s3.filename or PurePosixPath(unquote_plus(request.s3.key)).name
    if not filename:
        filename = "caseops-input.txt"
    destination = _safe_input_path(filename)
    service = S3Service(bucket_name=request.s3.bucket)
    service.download_object(request.s3.key, destination)
    return destination


def _safe_input_path(filename: str) -> Path:
    name = filename.strip()
    if not name or PurePosixPath(name).name != name or "\\" in name:
        raise LambdaInputError("document filename must be a simple file name")
    return _resolve_input_dir() / name


def _resolve_input_dir() -> Path:
    return Path(os.getenv("CASEOPS_LAMBDA_INPUT_DIR", _DEFAULT_INPUT_DIR))


def _resolve_output_dir() -> Path:
    return Path(os.getenv("OUTPUT_DIR", _DEFAULT_OUTPUT_DIR))


def _build_logger(session_id: str) -> PipelineLogger:
    from app.services.cloudwatch_service import build_cloudwatch_emitter

    config = LoggingConfig.from_env()
    return PipelineLogger(
        session_id=session_id,
        config=config,
        cloudwatch_emitter=build_cloudwatch_emitter(enabled=config.enable_cloudwatch),
    )


def _build_guardrails_service(guardrails_config):  # type: ignore[no-untyped-def]
    if not guardrails_config.enable_guardrails:
        return None
    from app.services.guardrails_service import GuardrailsService

    return GuardrailsService(region=os.getenv("AWS_REGION", "us-east-1"))


def _build_optional_s3_service(env_name: str) -> S3Service | None:
    bucket = os.getenv(env_name, "").strip()
    if not bucket:
        return None
    return S3Service(bucket_name=bucket)


def _archive_output_to_s3(output_path: Path, document_id: str) -> str | None:
    service = _build_optional_s3_service("S3_OUTPUT_BUCKET")
    if service is None:
        return None
    key = service.upload_case_output(output_path, document_id)
    return f"s3://{service.bucket_name}/{key}"


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, separators=(",", ":"), default=str),
    }


def _emit_lambda_event(event: str, *, level: str, **data: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
        **data,
    }
    print(json.dumps(payload, separators=(",", ":"), default=str))
