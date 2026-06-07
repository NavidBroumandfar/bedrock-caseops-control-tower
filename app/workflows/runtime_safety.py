"""
Runtime safety gates for the CLI path.

This module wires the existing deterministic safety policy and optional Bedrock
Guardrails integration into the live operator workflow.  It owns runtime
decisioning only; final output assembly still belongs to the pipeline/tool
executor and local persistence still belongs to app.utils.output_writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.evaluation.guardrails_adapter import (
    guardrail_result_to_assessment,
    guardrail_result_to_issues,
)
from app.evaluation.safety_policy import evaluate_safety
from app.schemas.guardrail_models import GuardrailAssessmentResult, GuardrailSource
from app.schemas.output_models import CaseOutput
from app.schemas.safety_models import FailurePolicy, SafetyAssessment
from app.services.guardrails_service import GuardrailsService, GuardrailsServiceError
from app.utils.config import GuardrailsConfig
from app.utils.output_writer import OutputWriteError, write_safety_assessment


class RuntimeSafetyError(Exception):
    """Raised when runtime safety checks cannot be executed or persisted."""


class GuardrailsTextAssessor(Protocol):
    """Minimal protocol implemented by GuardrailsService and test fakes."""

    def assess_text(
        self,
        text: str,
        guardrail_id: str,
        guardrail_version: str,
        source: GuardrailSource,
        *,
        include_trace: bool = False,
    ) -> GuardrailAssessmentResult:
        ...


@dataclass(frozen=True)
class RuntimeSafetyResult:
    """Runtime safety decision plus the persisted assessment artifact path."""

    assessment: SafetyAssessment
    artifact_path: Path


def build_operator_input_text(
    *,
    file_path: str,
    source_type: str,
    document_date: str,
    submitter_note: str | None,
) -> str:
    """
    Build the operator-supplied CLI input string assessed by Guardrails.

    The document body is not included here; this check covers direct operator
    metadata and notes supplied to the CLI.
    """
    lines = [
        f"file_path: {file_path}",
        f"source_type: {source_type}",
        f"document_date: {document_date}",
    ]
    if submitter_note:
        lines.append(f"submitter_note: {submitter_note}")
    return "\n".join(lines)


def run_operator_input_safety_check(
    *,
    document_id: str,
    operator_input_text: str,
    output_dir: str | Path,
    guardrails_config: GuardrailsConfig,
    guardrails_service: GuardrailsTextAssessor | None = None,
) -> RuntimeSafetyResult | None:
    """
    Apply Bedrock Guardrails to operator input when Guardrails are enabled.

    Returns None when CASEOPS_ENABLE_GUARDRAILS is false.  When enabled, the
    result is always persisted as {document_id}.safety.json so an early block
    has an audit artifact.
    """
    if not guardrails_config.enable_guardrails:
        return None

    _validate_guardrails_config(guardrails_config)
    service = guardrails_service or GuardrailsService()
    try:
        result = service.assess_text(
            text=operator_input_text,
            guardrail_id=guardrails_config.guardrail_id,
            guardrail_version=guardrails_config.guardrail_version,
            source=GuardrailSource.INPUT,
            include_trace=guardrails_config.guardrail_trace,
        )
    except GuardrailsServiceError as exc:
        raise RuntimeSafetyError(f"Operator input Guardrails check failed: {exc}") from exc

    assessment = guardrail_result_to_assessment(
        result,
        document_id=document_id,
        notes="runtime operator input guardrail assessment",
    )
    artifact_path = _persist_assessment(assessment, output_dir)
    return RuntimeSafetyResult(assessment=assessment, artifact_path=artifact_path)


def run_case_output_safety_check(
    *,
    output: CaseOutput,
    output_dir: str | Path,
    policy: FailurePolicy | None = None,
    guardrails_config: GuardrailsConfig | None = None,
    guardrails_service: GuardrailsTextAssessor | None = None,
) -> RuntimeSafetyResult:
    """
    Evaluate deterministic safety and optional Guardrails for a CaseOutput.

    The returned assessment is persisted before the final CaseOutput JSON is
    written, allowing BLOCK decisions to stop normal output persistence while
    still leaving an audit trail.
    """
    additional_issues = []
    guardrails_applied = False

    if guardrails_config and guardrails_config.enable_guardrails:
        _validate_guardrails_config(guardrails_config)
        service = guardrails_service or GuardrailsService()
        try:
            result = service.assess_text(
                text=output.model_dump_json(),
                guardrail_id=guardrails_config.guardrail_id,
                guardrail_version=guardrails_config.guardrail_version,
                source=GuardrailSource.OUTPUT,
                include_trace=guardrails_config.guardrail_trace,
            )
        except GuardrailsServiceError as exc:
            raise RuntimeSafetyError(f"Output Guardrails check failed: {exc}") from exc
        additional_issues.extend(guardrail_result_to_issues(result))
        guardrails_applied = True

    notes = (
        "runtime deterministic safety assessment with Bedrock Guardrails"
        if guardrails_applied
        else "runtime deterministic safety assessment"
    )
    assessment = evaluate_safety(
        output,
        policy=policy,
        additional_issues=additional_issues,
        notes=notes,
    )
    artifact_path = _persist_assessment(assessment, output_dir)
    return RuntimeSafetyResult(assessment=assessment, artifact_path=artifact_path)


def _validate_guardrails_config(config: GuardrailsConfig) -> None:
    if not config.guardrail_id.strip():
        raise RuntimeSafetyError(
            "CASEOPS_GUARDRAIL_ID is required when CASEOPS_ENABLE_GUARDRAILS=true."
        )
    if not config.guardrail_version.strip():
        raise RuntimeSafetyError(
            "CASEOPS_GUARDRAIL_VERSION is required when CASEOPS_ENABLE_GUARDRAILS=true."
        )


def _persist_assessment(
    assessment: SafetyAssessment,
    output_dir: str | Path,
) -> Path:
    try:
        return write_safety_assessment(assessment, output_dir=output_dir)
    except OutputWriteError as exc:
        raise RuntimeSafetyError(f"Could not write safety assessment: {exc}") from exc
