"""
Tests for app/workflows/runtime_safety.py.

No live AWS calls are made; GuardrailsService is replaced with a small fake.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.guardrail_models import GuardrailAssessmentResult, GuardrailSource
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.safety_models import FailurePolicy, SafetyStatus
from app.services.guardrails_service import GuardrailsServiceError
from app.utils.config import GuardrailsConfig
from app.workflows.runtime_safety import (
    RuntimeSafetyError,
    build_operator_input_text,
    run_case_output_safety_check,
    run_operator_input_safety_check,
)


_DOC_ID = "doc-20260606-safety"


class FakeGuardrailsService:
    def __init__(self, results: list[GuardrailAssessmentResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[dict] = []

    def assess_text(
        self,
        text: str,
        guardrail_id: str,
        guardrail_version: str,
        source: GuardrailSource,
        *,
        include_trace: bool = False,
    ) -> GuardrailAssessmentResult:
        self.calls.append(
            {
                "text": text,
                "guardrail_id": guardrail_id,
                "guardrail_version": guardrail_version,
                "source": source,
                "include_trace": include_trace,
            }
        )
        return self.results.pop(0)


class FailingGuardrailsService:
    def assess_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise GuardrailsServiceError("guardrail unavailable")


def _guardrails_config(*, enabled: bool = True, guardrail_id: str = "gr-test") -> GuardrailsConfig:
    return GuardrailsConfig(
        enable_guardrails=enabled,
        guardrail_id=guardrail_id,
        guardrail_version="1",
        guardrail_trace=True,
    )


def _guardrail_result(
    *,
    source: GuardrailSource,
    intervened: bool,
) -> GuardrailAssessmentResult:
    return GuardrailAssessmentResult(
        guardrail_id="gr-test",
        guardrail_version="1",
        source=source,
        intervened=intervened,
        action="GUARDRAIL_INTERVENED" if intervened else "NONE",
        blocked=intervened,
        finding_types=["TEST_POLICY"] if intervened else [],
    )


def _case_output(**overrides) -> CaseOutput:
    data = {
        "document_id": _DOC_ID,
        "source_filename": "advisory.txt",
        "source_type": "FDA",
        "severity": "High",
        "category": "Regulatory",
        "summary": "Facility failed to maintain written procedures.",
        "recommendations": ["Start CAPA."],
        "citations": [
            Citation(
                chunk_id="chunk-1",
                source_id="src-1",
                source_label="FDA document",
                excerpt="written procedures were inadequate",
                relevance_score=0.9,
            )
        ],
        "confidence_score": 0.9,
        "unsupported_claims": [],
        "escalation_required": False,
        "escalation_reason": None,
        "validated_by": "tool-executor-agent-v1",
        "session_id": "sess-test",
        "timestamp": "2026-06-06T00:00:00+00:00",
    }
    data.update(overrides)
    return CaseOutput(**data)


def test_build_operator_input_text_includes_submitter_note() -> None:
    text = build_operator_input_text(
        file_path="data/sample.txt",
        source_type="FDA",
        document_date="2026-06-06",
        submitter_note="High priority",
    )
    assert "file_path: data/sample.txt" in text
    assert "source_type: FDA" in text
    assert "submitter_note: High priority" in text


def test_operator_input_check_disabled_returns_none(tmp_path: Path) -> None:
    fake = FakeGuardrailsService()
    result = run_operator_input_safety_check(
        document_id=_DOC_ID,
        operator_input_text="input",
        output_dir=tmp_path,
        guardrails_config=_guardrails_config(enabled=False),
        guardrails_service=fake,
    )
    assert result is None
    assert fake.calls == []
    assert not (tmp_path / f"{_DOC_ID}.safety.json").exists()


def test_operator_input_check_nonintervention_writes_allow_artifact(tmp_path: Path) -> None:
    fake = FakeGuardrailsService(
        [_guardrail_result(source=GuardrailSource.INPUT, intervened=False)]
    )
    result = run_operator_input_safety_check(
        document_id=_DOC_ID,
        operator_input_text="input",
        output_dir=tmp_path,
        guardrails_config=_guardrails_config(),
        guardrails_service=fake,
    )
    assert result is not None
    assert result.assessment.status == SafetyStatus.ALLOW
    assert result.artifact_path.name == f"{_DOC_ID}.safety.json"
    assert fake.calls[0]["source"] == GuardrailSource.INPUT
    assert fake.calls[0]["include_trace"] is True


def test_operator_input_check_intervention_blocks(tmp_path: Path) -> None:
    fake = FakeGuardrailsService(
        [_guardrail_result(source=GuardrailSource.INPUT, intervened=True)]
    )
    result = run_operator_input_safety_check(
        document_id=_DOC_ID,
        operator_input_text="blocked input",
        output_dir=tmp_path,
        guardrails_config=_guardrails_config(),
        guardrails_service=fake,
    )
    assert result is not None
    assert result.assessment.status == SafetyStatus.BLOCK
    assert result.assessment.has_blocking_issue is True
    assert result.assessment.issues[0].metadata["source"] == "input"


def test_case_output_safety_check_writes_deterministic_artifact(tmp_path: Path) -> None:
    result = run_case_output_safety_check(
        output=_case_output(),
        output_dir=tmp_path,
        policy=FailurePolicy(),
        guardrails_config=_guardrails_config(enabled=False),
    )
    assert result.assessment.status == SafetyStatus.ALLOW
    data = json.loads((tmp_path / f"{_DOC_ID}.safety.json").read_text(encoding="utf-8"))
    assert data["document_id"] == _DOC_ID
    assert data["status"] == "allow"


def test_case_output_safety_check_output_guardrail_intervention_blocks(tmp_path: Path) -> None:
    fake = FakeGuardrailsService(
        [_guardrail_result(source=GuardrailSource.OUTPUT, intervened=True)]
    )
    result = run_case_output_safety_check(
        output=_case_output(),
        output_dir=tmp_path,
        policy=FailurePolicy(),
        guardrails_config=_guardrails_config(),
        guardrails_service=fake,
    )
    assert result.assessment.status == SafetyStatus.BLOCK
    assert result.assessment.issues[0].metadata["source"] == "output"
    assert fake.calls[0]["source"] == GuardrailSource.OUTPUT
    assert _DOC_ID in fake.calls[0]["text"]


def test_case_output_safety_check_uses_deterministic_policy(tmp_path: Path) -> None:
    result = run_case_output_safety_check(
        output=_case_output(unsupported_claims=["unsupported"]),
        output_dir=tmp_path,
        policy=FailurePolicy(),
        guardrails_config=_guardrails_config(enabled=False),
    )
    assert result.assessment.status == SafetyStatus.BLOCK


def test_missing_guardrail_id_raises_runtime_safety_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeSafetyError, match="CASEOPS_GUARDRAIL_ID"):
        run_case_output_safety_check(
            output=_case_output(),
            output_dir=tmp_path,
            policy=FailurePolicy(),
            guardrails_config=_guardrails_config(guardrail_id=""),
            guardrails_service=FakeGuardrailsService(),
        )


def test_guardrails_service_error_is_wrapped(tmp_path: Path) -> None:
    with pytest.raises(RuntimeSafetyError, match="Output Guardrails check failed"):
        run_case_output_safety_check(
            output=_case_output(),
            output_dir=tmp_path,
            policy=FailurePolicy(),
            guardrails_config=_guardrails_config(),
            guardrails_service=FailingGuardrailsService(),
        )
