"""
E-1 unit tests — final output packaging utility.

Coverage:

  write_case_output — file system behaviour:
  - output file is created in the specified directory
  - file name is {document_id}.json
  - output directory is created when it does not exist
  - nested output directory is created (parents=True)
  - existing output file is overwritten
  - resolved absolute path is returned

  write_case_output — content fidelity:
  - written file contains valid JSON
  - JSON round-trips back to an equivalent CaseOutput
  - document_id is preserved in the output file
  - session_id is preserved in the output file
  - all required CaseOutput fields are present in the JSON

  write_case_output — error handling:
  - OutputWriteError is raised when the directory cannot be created
  - OutputWriteError chains the original OSError via __cause__

  OutputWriteError:
  - is a subclass of Exception

No AWS calls are made.  All tests use pytest tmp_path for isolation.
"""

import json
from pathlib import Path

import pytest

from app.schemas.output_models import CaseOutput, Citation
from app.utils.output_writer import OutputWriteError, write_case_output


# ── shared builders ────────────────────────────────────────────────────────────


_DOC_ID = "doc-20260405-e1test01"
_SESSION_ID = "sess-a1b2c3d4"


def _make_citation() -> Citation:
    return Citation(
        source_id="s3://caseops-kb/fda/test.txt::0",
        source_label="FDA Test Document",
        excerpt="...test excerpt...",
        relevance_score=0.88,
    )


def _make_output(
    document_id: str = _DOC_ID,
    session_id: str | None = _SESSION_ID,
    escalation_required: bool = False,
) -> CaseOutput:
    return CaseOutput(
        document_id=document_id,
        source_filename="advisory.txt",
        source_type="FDA",
        severity="High",
        category="Regulatory / Manufacturing Deficiency",
        summary="Facility failed to establish adequate written procedures.",
        recommendations=["Initiate CAPA immediately.", "Escalate to compliance."],
        citations=[_make_citation()],
        confidence_score=0.87,
        unsupported_claims=[],
        escalation_required=escalation_required,
        escalation_reason=None,
        validated_by="tool-executor-agent-v1",
        session_id=session_id,
        timestamp="2026-04-05T00:00:00+00:00",
    )


# ── file creation behaviour ────────────────────────────────────────────────────


def test_output_file_is_created(tmp_path: Path) -> None:
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    expected = tmp_path / f"{_DOC_ID}.json"
    assert expected.exists()


def test_output_filename_is_document_id_json(tmp_path: Path) -> None:
    output = _make_output()
    result_path = write_case_output(output, output_dir=tmp_path)
    assert result_path.name == f"{_DOC_ID}.json"


def test_output_dir_is_created_when_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "output" / "dir"
    assert not nested.exists()
    output = _make_output()
    write_case_output(output, output_dir=nested)
    assert nested.exists()
    assert (nested / f"{_DOC_ID}.json").exists()


def test_existing_output_file_is_overwritten(tmp_path: Path) -> None:
    output = _make_output()
    dest = tmp_path / f"{_DOC_ID}.json"
    dest.write_text("stale content", encoding="utf-8")

    write_case_output(output, output_dir=tmp_path)

    content = dest.read_text(encoding="utf-8")
    assert content != "stale content"
    parsed = json.loads(content)
    assert parsed["document_id"] == _DOC_ID


def test_returns_resolved_absolute_path(tmp_path: Path) -> None:
    output = _make_output()
    result = write_case_output(output, output_dir=tmp_path)
    assert result.is_absolute()


def test_returned_path_points_to_existing_file(tmp_path: Path) -> None:
    output = _make_output()
    result = write_case_output(output, output_dir=tmp_path)
    assert result.exists()
    assert result.is_file()


# ── content fidelity ───────────────────────────────────────────────────────────


def test_output_file_contains_valid_json(tmp_path: Path) -> None:
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    raw = (tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)  # raises if not valid JSON
    assert isinstance(parsed, dict)


def test_document_id_preserved_in_file(tmp_path: Path) -> None:
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    data = json.loads((tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8"))
    assert data["document_id"] == _DOC_ID


def test_session_id_preserved_in_file(tmp_path: Path) -> None:
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    data = json.loads((tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8"))
    assert data["session_id"] == _SESSION_ID


def test_session_id_none_preserved_in_file(tmp_path: Path) -> None:
    output = _make_output(session_id=None)
    write_case_output(output, output_dir=tmp_path)
    data = json.loads((tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8"))
    assert data["session_id"] is None


def test_all_required_fields_present_in_json(tmp_path: Path) -> None:
    required_fields = {
        "document_id",
        "source_filename",
        "source_type",
        "severity",
        "category",
        "summary",
        "recommendations",
        "citations",
        "confidence_score",
        "unsupported_claims",
        "escalation_required",
        "escalation_reason",
        "validated_by",
        "session_id",
        "timestamp",
    }
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    data = json.loads((tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8"))
    assert required_fields.issubset(data.keys())


def test_json_round_trips_to_valid_case_output(tmp_path: Path) -> None:
    """Written JSON must parse back to a valid CaseOutput without errors."""
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    raw = (tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8")
    parsed = CaseOutput.model_validate_json(raw)
    assert parsed.document_id == _DOC_ID
    assert parsed.session_id == _SESSION_ID


def test_citations_preserved_in_json(tmp_path: Path) -> None:
    output = _make_output()
    write_case_output(output, output_dir=tmp_path)
    data = json.loads((tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8"))
    assert len(data["citations"]) == 1
    assert data["citations"][0]["source_label"] == "FDA Test Document"


def test_escalation_flag_preserved_in_json(tmp_path: Path) -> None:
    output = _make_output(escalation_required=True)
    write_case_output(output, output_dir=tmp_path)
    data = json.loads((tmp_path / f"{_DOC_ID}.json").read_text(encoding="utf-8"))
    assert data["escalation_required"] is True


def test_output_dir_accepts_string_path(tmp_path: Path) -> None:
    """output_dir can be supplied as a plain string."""
    output = _make_output()
    write_case_output(output, output_dir=str(tmp_path))
    assert (tmp_path / f"{_DOC_ID}.json").exists()


def test_different_document_ids_produce_different_files(tmp_path: Path) -> None:
    out1 = _make_output(document_id="doc-20260405-aaa")
    out2 = _make_output(document_id="doc-20260405-bbb")
    write_case_output(out1, output_dir=tmp_path)
    write_case_output(out2, output_dir=tmp_path)
    assert (tmp_path / "doc-20260405-aaa.json").exists()
    assert (tmp_path / "doc-20260405-bbb.json").exists()


# ── error handling ─────────────────────────────────────────────────────────────


def test_output_write_error_is_exception() -> None:
    assert issubclass(OutputWriteError, Exception)


def test_raises_output_write_error_on_unwritable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OutputWriteError is raised when the output file cannot be written."""
    output = _make_output()

    # Make Path.write_text raise an OSError to simulate permission failure.
    original_write_text = Path.write_text

    def _broken_write_text(self: Path, *args, **kwargs) -> None:  # type: ignore[override]
        if self.name.endswith(".json"):
            raise OSError("Permission denied")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _broken_write_text)

    with pytest.raises(OutputWriteError, match="Cannot write output file"):
        write_case_output(output, output_dir=tmp_path)


def test_output_write_error_chains_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original OSError must be chained via __cause__."""
    output = _make_output()
    original_write_text = Path.write_text
    original_oserror = OSError("simulated write failure")

    def _broken_write_text(self: Path, *args, **kwargs) -> None:  # type: ignore[override]
        if self.name.endswith(".json"):
            raise original_oserror
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _broken_write_text)

    with pytest.raises(OutputWriteError) as exc_info:
        write_case_output(output, output_dir=tmp_path)

    assert exc_info.value.__cause__ is original_oserror
