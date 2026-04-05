"""
E-2 tests — end-to-end flow using real sample documents.

These tests run the intake → pipeline → output flow with the actual sample
documents from data/sample_documents/, with all AWS interactions replaced by
mocks and fakes.  They demonstrate that the full path works correctly for
each supported document type without requiring live AWS credentials.

Purpose:
  - Verify that the sample docs in data/sample_documents/ are intake-compatible
  - Verify the pipeline produces a valid CaseOutput from each sample
  - Verify error paths produce clear failures rather than silent corruption
  - Serve as the closest thing to a "demo run" that works without AWS

Coverage:

  Sample document intake:
  - fda_warning_letter_01.md is accepted by intake without errors
  - cisa_advisory_01.md is accepted by intake without errors
  - fda_recall_01.md is accepted by intake without errors
  - sample_notice.txt is accepted by intake without errors
  - all intake results carry the correct source_type and extension

  Full pipeline flow (all AWS mocked):
  - fda_warning_letter_01.md → pipeline → valid CaseOutput
  - cisa_advisory_01.md → pipeline → valid CaseOutput
  - pipeline output conforms to CaseOutput schema (all required fields present)
  - document_id from intake is preserved in the pipeline output
  - source_filename from intake is preserved in the pipeline output
  - source_type from intake is preserved in the pipeline output
  - session_id is populated in the final output

  Output schema compliance:
  - severity is one of Critical / High / Medium / Low
  - confidence_score is in [0.0, 1.0]
  - citations is a list (may be non-empty on success path)
  - escalation_required is a bool
  - timestamp is a non-empty ISO 8601 string
  - validated_by is a non-empty string
  - recommendations is a list
  - unsupported_claims is a list

  Error path coverage:
  - missing file raises IntakeError with descriptive message
  - unsupported extension raises IntakeError
  - pipeline analysis failure surfaces as PipelineWorkflowError
  - pipeline validation failure surfaces as PipelineWorkflowError
  - empty retrieval path produces a valid CaseOutput with escalation_required=True

  Output writer integration:
  - write_case_output writes a JSON file for each sample document
  - written JSON is valid and parseable back into CaseOutput
  - document_id appears as the output filename

No live AWS calls are made.  All AWS interactions are replaced by fakes or mocks.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.analysis_agent import AnalysisAgent
from app.agents.tool_executor_agent import ToolExecutorAgent
from app.agents.validation_agent import ValidationAgent
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.intake_models import IntakeMetadata, IntakeResult
from app.schemas.output_models import CaseOutput
from app.schemas.validation_models import ValidationOutput
from app.services.intake_service import IntakeError, run_intake
from app.utils.output_writer import write_case_output
from app.workflows.pipeline_workflow import PipelineWorkflowError, run_pipeline
from tests.fakes.fake_retrieval import FakeRetrievalProvider


# ── paths to real sample documents ────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SAMPLE_DIR = _REPO_ROOT / "data" / "sample_documents"

_FDA_WARNING = _SAMPLE_DIR / "fda_warning_letter_01.md"
_CISA_ADVISORY = _SAMPLE_DIR / "cisa_advisory_01.md"
_FDA_RECALL = _SAMPLE_DIR / "fda_recall_01.md"
_SAMPLE_NOTICE = _SAMPLE_DIR / "sample_notice.txt"


# ── shared builders ────────────────────────────────────────────────────────────


def _make_metadata(source_type: str = "FDA", date: str = "2026-03-30") -> IntakeMetadata:
    return IntakeMetadata(source_type=source_type, document_date=date)


def _make_analysis_output(document_id: str, severity: str = "High") -> AnalysisOutput:
    return AnalysisOutput(
        document_id=document_id,
        severity=severity,
        category="Regulatory / Quality Deficiency",
        summary="Inspection findings indicate quality system gaps requiring corrective action.",
        recommendations=[
            "Initiate CAPA for each cited deficiency within 30 days.",
            "Submit written response to FDA within 15 business days.",
        ],
    )


def _make_validation_output(
    document_id: str,
    confidence_score: float = 0.82,
) -> ValidationOutput:
    return ValidationOutput(
        document_id=document_id,
        confidence_score=confidence_score,
        unsupported_claims=[],
        validation_status="pass",
    )


def _make_analysis_agent(document_id: str, severity: str = "High") -> AnalysisAgent:
    provider = MagicMock()
    provider.analyze.return_value = _make_analysis_output(document_id, severity)
    return AnalysisAgent(provider=provider)


def _make_validation_agent(document_id: str, confidence_score: float = 0.82) -> ValidationAgent:
    provider = MagicMock()
    provider.validate.return_value = _make_validation_output(document_id, confidence_score)
    return ValidationAgent(provider=provider)


def _run_full_pipeline(
    intake_result: IntakeResult,
    *,
    severity: str = "High",
    confidence_score: float = 0.82,
    use_empty_retrieval: bool = False,
) -> CaseOutput:
    """Run the full pipeline using fakes for all AWS dependencies."""
    document_id = intake_result.document_id
    return run_pipeline(
        intake_result,
        retrieval_provider=FakeRetrievalProvider(return_empty=use_empty_retrieval),
        analysis_agent=_make_analysis_agent(document_id, severity),
        validation_agent=_make_validation_agent(document_id, confidence_score),
        tool_executor=ToolExecutorAgent(),
    )


# ── sample document intake: compatibility tests ───────────────────────────────


def test_fda_warning_letter_intake_succeeds(tmp_path: Path) -> None:
    """fda_warning_letter_01.md must be accepted by the intake pipeline."""
    result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    assert result.document_id.startswith("doc-")
    assert result.record.extension == ".md"
    assert result.record.source_type == "FDA"


def test_cisa_advisory_intake_succeeds(tmp_path: Path) -> None:
    """cisa_advisory_01.md must be accepted by the intake pipeline."""
    result = run_intake(
        file_path=str(_CISA_ADVISORY),
        metadata=_make_metadata("CISA"),
        output_dir=tmp_path / "intake",
    )
    assert result.document_id.startswith("doc-")
    assert result.record.extension == ".md"
    assert result.record.source_type == "CISA"


def test_fda_recall_intake_succeeds(tmp_path: Path) -> None:
    """fda_recall_01.md must be accepted by the intake pipeline."""
    result = run_intake(
        file_path=str(_FDA_RECALL),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    assert result.document_id.startswith("doc-")
    assert result.record.source_type == "FDA"


def test_sample_notice_txt_intake_succeeds(tmp_path: Path) -> None:
    """sample_notice.txt must be accepted by the intake pipeline."""
    result = run_intake(
        file_path=str(_SAMPLE_NOTICE),
        metadata=_make_metadata("Other"),
        output_dir=tmp_path / "intake",
    )
    assert result.document_id.startswith("doc-")
    assert result.record.extension == ".txt"
    assert result.record.source_type == "Other"


def test_intake_artifact_is_written_for_sample_doc(tmp_path: Path) -> None:
    """run_intake must write a local JSON artifact for the sample document."""
    output_dir = tmp_path / "intake"
    result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=output_dir,
    )
    artifact = Path(result.artifact_path)
    assert artifact.exists()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["document_id"] == result.document_id
    assert data["source_type"] == "FDA"


def test_intake_records_correct_filename_for_each_sample(tmp_path: Path) -> None:
    """original_filename on the IntakeRecord must match the actual sample file name."""
    result = run_intake(
        file_path=str(_CISA_ADVISORY),
        metadata=_make_metadata("CISA"),
        output_dir=tmp_path / "intake",
    )
    assert result.record.original_filename == "cisa_advisory_01.md"


# ── full pipeline flow: happy path ────────────────────────────────────────────


def test_fda_warning_letter_pipeline_produces_case_output(tmp_path: Path) -> None:
    """fda_warning_letter_01.md → intake → pipeline must produce a CaseOutput."""
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output, CaseOutput)


def test_cisa_advisory_pipeline_produces_case_output(tmp_path: Path) -> None:
    """cisa_advisory_01.md → intake → pipeline must produce a CaseOutput."""
    intake_result = run_intake(
        file_path=str(_CISA_ADVISORY),
        metadata=_make_metadata("CISA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output, CaseOutput)


def test_pipeline_preserves_document_id_from_intake(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert output.document_id == intake_result.document_id


def test_pipeline_preserves_source_filename_from_intake(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert output.source_filename == intake_result.record.original_filename


def test_pipeline_preserves_source_type_from_intake(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_CISA_ADVISORY),
        metadata=_make_metadata("CISA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert output.source_type == "CISA"


def test_pipeline_output_has_session_id(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert output.session_id is not None
    assert len(output.session_id) > 0


# ── output schema compliance ───────────────────────────────────────────────────


def test_pipeline_output_severity_is_valid(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result, severity="High")
    assert output.severity in {"Critical", "High", "Medium", "Low"}


def test_pipeline_output_confidence_score_in_unit_interval(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result, confidence_score=0.82)
    assert 0.0 <= output.confidence_score <= 1.0


def test_pipeline_output_citations_is_list(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output.citations, list)


def test_pipeline_output_escalation_required_is_bool(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output.escalation_required, bool)


def test_pipeline_output_timestamp_is_non_empty_string(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output.timestamp, str)
    assert len(output.timestamp) > 0


def test_pipeline_output_validated_by_is_non_empty_string(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output.validated_by, str)
    assert len(output.validated_by) > 0


def test_pipeline_output_recommendations_is_list(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output.recommendations, list)


def test_pipeline_output_unsupported_claims_is_list(tmp_path: Path) -> None:
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    assert isinstance(output.unsupported_claims, list)


# ── error path coverage ───────────────────────────────────────────────────────


def test_missing_file_raises_intake_error(tmp_path: Path) -> None:
    """A non-existent file path must raise IntakeError with a descriptive message."""
    with pytest.raises(IntakeError, match="File not found"):
        run_intake(
            file_path=str(tmp_path / "does_not_exist.md"),
            metadata=_make_metadata("FDA"),
            output_dir=tmp_path / "intake",
        )


def test_unsupported_extension_raises_intake_error(tmp_path: Path) -> None:
    """A .csv file must be rejected at intake with a clear unsupported-type error."""
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("col1,col2\n1,2", encoding="utf-8")
    with pytest.raises(IntakeError, match="Unsupported file type"):
        run_intake(
            file_path=str(csv_file),
            metadata=_make_metadata("FDA"),
            output_dir=tmp_path / "intake",
        )


def test_pipeline_analysis_failure_surfaces_as_pipeline_workflow_error(tmp_path: Path) -> None:
    """A downstream analysis failure must surface as PipelineWorkflowError."""
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    failing_provider = MagicMock()
    failing_provider.analyze.side_effect = RuntimeError("Simulated analysis failure")
    failing_analysis = AnalysisAgent(provider=failing_provider)

    with pytest.raises(PipelineWorkflowError):
        run_pipeline(
            intake_result,
            retrieval_provider=FakeRetrievalProvider(),
            analysis_agent=failing_analysis,
            validation_agent=_make_validation_agent(intake_result.document_id),
            tool_executor=ToolExecutorAgent(),
        )


def test_pipeline_validation_failure_surfaces_as_pipeline_workflow_error(tmp_path: Path) -> None:
    """A downstream validation failure must surface as PipelineWorkflowError."""
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    failing_provider = MagicMock()
    failing_provider.validate.side_effect = RuntimeError("Simulated validation failure")
    failing_validation = ValidationAgent(provider=failing_provider)

    with pytest.raises(PipelineWorkflowError):
        run_pipeline(
            intake_result,
            retrieval_provider=FakeRetrievalProvider(),
            analysis_agent=_make_analysis_agent(intake_result.document_id),
            validation_agent=failing_validation,
            tool_executor=ToolExecutorAgent(),
        )


def test_empty_retrieval_path_produces_escalated_output(tmp_path: Path) -> None:
    """Empty retrieval must produce a valid CaseOutput with escalation_required=True."""
    intake_result = run_intake(
        file_path=str(_CISA_ADVISORY),
        metadata=_make_metadata("CISA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result, use_empty_retrieval=True)
    assert isinstance(output, CaseOutput)
    assert output.escalation_required is True
    assert output.citations == []


# ── output writer integration ─────────────────────────────────────────────────


def test_write_case_output_produces_json_file(tmp_path: Path) -> None:
    """write_case_output must produce a JSON file named {document_id}.json."""
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    output_path = write_case_output(output, output_dir=str(tmp_path / "outputs"))

    assert output_path.exists()
    assert output_path.name == f"{output.document_id}.json"


def test_written_json_is_parseable_back_to_case_output(tmp_path: Path) -> None:
    """The written JSON must round-trip back into a valid CaseOutput."""
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    output_path = write_case_output(output, output_dir=str(tmp_path / "outputs"))

    raw = json.loads(output_path.read_text(encoding="utf-8"))
    restored = CaseOutput(**raw)
    assert restored.document_id == output.document_id
    assert restored.severity == output.severity
    assert restored.confidence_score == pytest.approx(output.confidence_score)


def test_written_json_contains_required_top_level_fields(tmp_path: Path) -> None:
    """Written JSON must include all required CaseOutput fields."""
    required_fields = {
        "document_id", "source_filename", "source_type",
        "severity", "category", "summary", "recommendations",
        "citations", "confidence_score", "unsupported_claims",
        "escalation_required", "escalation_reason", "validated_by",
        "session_id", "timestamp",
    }
    intake_result = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake",
    )
    output = _run_full_pipeline(intake_result)
    output_path = write_case_output(output, output_dir=str(tmp_path / "outputs"))

    raw = json.loads(output_path.read_text(encoding="utf-8"))
    assert required_fields.issubset(set(raw.keys()))


def test_two_sample_docs_produce_different_document_ids(tmp_path: Path) -> None:
    """Each intake run must produce a distinct document_id."""
    result_fda = run_intake(
        file_path=str(_FDA_WARNING),
        metadata=_make_metadata("FDA"),
        output_dir=tmp_path / "intake_fda",
    )
    result_cisa = run_intake(
        file_path=str(_CISA_ADVISORY),
        metadata=_make_metadata("CISA"),
        output_dir=tmp_path / "intake_cisa",
    )
    assert result_fda.document_id != result_cisa.document_id
