"""
E-0 unit tests — structured logging utility (app/utils/logging_utils.py).

Coverage:

  _build_entry — payload structure:
  - all standard fields are present
  - field types match the specification
  - data field is populated from kwargs
  - empty data dict is used when data is None

  _serialize — JSON output:
  - output is valid JSON
  - output parses back to the original entry

  LoggingConfig — defaults:
  - sensible defaults when no env vars are set
  - log_level defaults to INFO
  - enable_local_file defaults to True
  - enable_cloudwatch defaults to False

  PipelineLogger — structured log payload format:
  - emits JSON log lines to stdout
  - emitted JSON contains all required standard fields
  - emitted JSON level matches the method called
  - emitted JSON session_id matches constructor arg
  - emitted JSON event and agent match call args
  - data field is preserved in emitted JSON

  PipelineLogger — log level filtering:
  - entries below configured min level are suppressed
  - entries at or above configured min level are emitted

  PipelineLogger — local file writing:
  - writes a log file when enable_local_file=True
  - log file contains valid JSON lines
  - log file path matches outputs/logs/{session_id}.log pattern
  - multiple log calls append to the same file
  - file writing does not raise if the directory already exists

  PipelineLogger — local file disabled:
  - no log file is created when enable_local_file=False
  - log_file_path is None when file logging is disabled

  PipelineLogger — CloudWatch integration:
  - CloudWatch emitter is called when enable_cloudwatch=True
  - CloudWatch emitter receives the correct session_id
  - CloudWatch emitter receives the structured entry dict
  - CloudWatch emitter error does not propagate to caller

  PipelineLogger — no CloudWatch emitter injected:
  - does not raise when enable_cloudwatch=True but no emitter is given

  NoOpLogger — interface:
  - all log methods can be called without raising
  - session_id is an empty string
  - log_file_path is None

  Pipeline instrumentation — key events emitted:
  - session_start is emitted by run_pipeline
  - intake_handoff_received is emitted by run_pipeline
  - retrieval_start is emitted by run_supervisor
  - retrieval_complete is emitted by run_supervisor
  - analysis_start is emitted by run_supervisor
  - analysis_complete is emitted by run_supervisor
  - validation_start is emitted by run_supervisor
  - validation_complete is emitted by run_supervisor
  - output_generation_complete is emitted by run_pipeline
  - escalation_triggered is emitted when escalation_required=True
  - retrieval_empty warning is emitted on empty retrieval
  - pipeline_failed error is emitted on supervisor failure

No live AWS calls are made.  No real file I/O for CloudWatch tests.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.utils.logging_utils import (
    DEBUG,
    ERROR,
    INFO,
    WARNING,
    LoggingConfig,
    NoOpLogger,
    PipelineLogger,
    _build_entry,
    _resolve_log_path,
    _serialize,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_logger(
    session_id: str = "sess-testtest",
    *,
    log_level: str = DEBUG,
    enable_local_file: bool = False,
    enable_cloudwatch: bool = False,
    output_dir: str = "outputs",
    cloudwatch_emitter=None,
    stdout=None,
) -> PipelineLogger:
    config = LoggingConfig(
        log_level=log_level,
        enable_local_file=enable_local_file,
        enable_cloudwatch=enable_cloudwatch,
        output_dir=output_dir,
    )
    return PipelineLogger(
        session_id,
        config=config,
        cloudwatch_emitter=cloudwatch_emitter,
        _stdout=stdout or io.StringIO(),
    )


def _parse_last_line(buf: io.StringIO) -> dict:
    """Return the last non-empty line from the buffer as a parsed JSON dict."""
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert lines, "No log output was written to the buffer"
    return json.loads(lines[-1])


def _collect_events(buf: io.StringIO) -> list[str]:
    """Return all event values from lines in the buffer."""
    events = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if line:
            parsed = json.loads(line)
            events.append(parsed["event"])
    return events


# ── _build_entry ─────────────────────────────────────────────────────────────


def test_build_entry_has_all_standard_fields() -> None:
    entry = _build_entry(
        level=INFO,
        session_id="sess-abc12345",
        document_id="doc-20260405-abcd1234",
        agent="supervisor",
        event="session_start",
        data={},
    )
    required = {"timestamp", "level", "session_id", "document_id", "agent", "event", "data"}
    assert required <= set(entry.keys())


def test_build_entry_level_matches_arg() -> None:
    entry = _build_entry(
        level=WARNING,
        session_id="sess-abc12345",
        document_id="doc-x",
        agent="pipeline",
        event="some_warning",
        data={},
    )
    assert entry["level"] == WARNING


def test_build_entry_data_is_preserved() -> None:
    payload = {"chunk_count": 3, "status": "ok"}
    entry = _build_entry(
        level=INFO,
        session_id="sess-x",
        document_id="doc-x",
        agent="agent",
        event="evt",
        data=payload,
    )
    assert entry["data"] == payload


def test_build_entry_timestamp_is_iso_string() -> None:
    entry = _build_entry(
        level=INFO,
        session_id="s",
        document_id="d",
        agent="a",
        event="e",
        data={},
    )
    ts = entry["timestamp"]
    assert isinstance(ts, str)
    # Rough ISO 8601 check: starts with a date portion
    assert re.match(r"\d{4}-\d{2}-\d{2}T", ts)


# ── _serialize ────────────────────────────────────────────────────────────────


def test_serialize_produces_valid_json() -> None:
    entry = _build_entry(
        level=INFO,
        session_id="sess-x",
        document_id="doc-x",
        agent="a",
        event="e",
        data={"key": "value"},
    )
    line = _serialize(entry)
    parsed = json.loads(line)
    assert isinstance(parsed, dict)


def test_serialize_round_trips_entry() -> None:
    entry = _build_entry(
        level=ERROR,
        session_id="sess-abc12345",
        document_id="doc-20260405-abcd1234",
        agent="supervisor",
        event="pipeline_failed",
        data={"error": "something went wrong"},
    )
    parsed = json.loads(_serialize(entry))
    assert parsed["level"] == ERROR
    assert parsed["session_id"] == "sess-abc12345"
    assert parsed["event"] == "pipeline_failed"
    assert parsed["data"]["error"] == "something went wrong"


# ── LoggingConfig — defaults ──────────────────────────────────────────────────


def test_logging_config_default_level_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    config = LoggingConfig()
    assert config.log_level == INFO


def test_logging_config_local_file_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", raising=False)
    config = LoggingConfig()
    assert config.enable_local_file is True


def test_logging_config_cloudwatch_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_ENABLE_CLOUDWATCH", raising=False)
    config = LoggingConfig()
    assert config.enable_cloudwatch is False


def test_logging_config_constructor_overrides() -> None:
    config = LoggingConfig(
        log_level="DEBUG",
        enable_local_file=False,
        enable_cloudwatch=True,
        output_dir="/tmp/testlogs",
    )
    assert config.log_level == DEBUG
    assert config.enable_local_file is False
    assert config.enable_cloudwatch is True
    assert config.output_dir == "/tmp/testlogs"


# ── PipelineLogger — emits to stdout ─────────────────────────────────────────


def test_logger_emits_json_to_stdout() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.info(agent="test-agent", event="test_event", document_id="doc-x")
    output = buf.getvalue().strip()
    assert len(output) > 0
    parsed = json.loads(output)
    assert isinstance(parsed, dict)


def test_logger_emitted_json_has_all_required_fields() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.info(agent="supervisor", event="analysis_start", document_id="doc-abc")
    entry = _parse_last_line(buf)
    for field in ("timestamp", "level", "session_id", "document_id", "agent", "event", "data"):
        assert field in entry, f"Missing field: {field}"


def test_logger_level_info_in_emitted_entry() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.info(agent="a", event="e", document_id="d")
    entry = _parse_last_line(buf)
    assert entry["level"] == INFO


def test_logger_level_warning_in_emitted_entry() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.warning(agent="a", event="e", document_id="d")
    entry = _parse_last_line(buf)
    assert entry["level"] == WARNING


def test_logger_level_error_in_emitted_entry() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.error(agent="a", event="e", document_id="d")
    entry = _parse_last_line(buf)
    assert entry["level"] == ERROR


def test_logger_session_id_in_emitted_entry() -> None:
    buf = io.StringIO()
    logger = _make_logger(session_id="sess-deadbeef", stdout=buf)
    logger.info(agent="a", event="e", document_id="d")
    entry = _parse_last_line(buf)
    assert entry["session_id"] == "sess-deadbeef"


def test_logger_agent_and_event_in_emitted_entry() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.info(agent="retrieval-agent", event="retrieval_complete", document_id="doc-z")
    entry = _parse_last_line(buf)
    assert entry["agent"] == "retrieval-agent"
    assert entry["event"] == "retrieval_complete"


def test_logger_data_preserved_in_emitted_entry() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.info(agent="a", event="e", document_id="d", data={"chunk_count": 5})
    entry = _parse_last_line(buf)
    assert entry["data"]["chunk_count"] == 5


def test_logger_empty_data_field_is_dict() -> None:
    buf = io.StringIO()
    logger = _make_logger(stdout=buf)
    logger.info(agent="a", event="e", document_id="d")
    entry = _parse_last_line(buf)
    assert isinstance(entry["data"], dict)


# ── PipelineLogger — log level filtering ─────────────────────────────────────


def test_logger_filters_below_min_level() -> None:
    buf = io.StringIO()
    logger = _make_logger(log_level=WARNING, stdout=buf)
    logger.debug(agent="a", event="should_be_filtered", document_id="d")
    logger.info(agent="a", event="also_filtered", document_id="d")
    # No lines should have been emitted
    assert buf.getvalue().strip() == ""


def test_logger_emits_at_min_level() -> None:
    buf = io.StringIO()
    logger = _make_logger(log_level=WARNING, stdout=buf)
    logger.warning(agent="a", event="emitted_warning", document_id="d")
    entry = _parse_last_line(buf)
    assert entry["event"] == "emitted_warning"


def test_logger_emits_above_min_level() -> None:
    buf = io.StringIO()
    logger = _make_logger(log_level=WARNING, stdout=buf)
    logger.error(agent="a", event="emitted_error", document_id="d")
    entry = _parse_last_line(buf)
    assert entry["event"] == "emitted_error"


# ── PipelineLogger — local file writing ──────────────────────────────────────


def test_logger_writes_local_file(tmp_path: Path) -> None:
    session_id = "sess-filetest"
    config = LoggingConfig(
        log_level=DEBUG,
        enable_local_file=True,
        enable_cloudwatch=False,
        output_dir=str(tmp_path),
    )
    logger = PipelineLogger(session_id, config=config, _stdout=io.StringIO())
    logger.info(agent="a", event="e", document_id="d")

    log_file = tmp_path / "logs" / f"{session_id}.log"
    assert log_file.exists(), "Expected log file to be created"


def test_logger_log_file_contains_valid_json(tmp_path: Path) -> None:
    session_id = "sess-jsoncheck"
    config = LoggingConfig(
        log_level=DEBUG,
        enable_local_file=True,
        enable_cloudwatch=False,
        output_dir=str(tmp_path),
    )
    logger = PipelineLogger(session_id, config=config, _stdout=io.StringIO())
    logger.info(agent="a", event="json_check", document_id="doc-x")

    log_file = tmp_path / "logs" / f"{session_id}.log"
    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry["event"] == "json_check"


def test_logger_log_file_path_property(tmp_path: Path) -> None:
    session_id = "sess-pathprop"
    config = LoggingConfig(
        log_level=DEBUG,
        enable_local_file=True,
        enable_cloudwatch=False,
        output_dir=str(tmp_path),
    )
    logger = PipelineLogger(session_id, config=config, _stdout=io.StringIO())
    expected = tmp_path / "logs" / f"{session_id}.log"
    assert logger.log_file_path == expected


def test_logger_multiple_calls_append_to_file(tmp_path: Path) -> None:
    session_id = "sess-append"
    config = LoggingConfig(
        log_level=DEBUG,
        enable_local_file=True,
        enable_cloudwatch=False,
        output_dir=str(tmp_path),
    )
    logger = PipelineLogger(session_id, config=config, _stdout=io.StringIO())
    logger.info(agent="a", event="first", document_id="d")
    logger.info(agent="a", event="second", document_id="d")
    logger.info(agent="a", event="third", document_id="d")

    log_file = tmp_path / "logs" / f"{session_id}.log"
    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    events = [json.loads(l)["event"] for l in lines]
    assert "first" in events
    assert "second" in events
    assert "third" in events


def test_logger_file_disabled_no_file_created(tmp_path: Path) -> None:
    session_id = "sess-nofile"
    config = LoggingConfig(
        log_level=DEBUG,
        enable_local_file=False,
        enable_cloudwatch=False,
        output_dir=str(tmp_path),
    )
    logger = PipelineLogger(session_id, config=config, _stdout=io.StringIO())
    logger.info(agent="a", event="e", document_id="d")

    log_dir = tmp_path / "logs"
    assert not log_dir.exists() or not any(log_dir.iterdir())


def test_logger_file_disabled_log_file_path_is_none(tmp_path: Path) -> None:
    session_id = "sess-nonepath"
    config = LoggingConfig(
        log_level=DEBUG,
        enable_local_file=False,
        enable_cloudwatch=False,
        output_dir=str(tmp_path),
    )
    logger = PipelineLogger(session_id, config=config, _stdout=io.StringIO())
    assert logger.log_file_path is None


# ── PipelineLogger — CloudWatch emitter ──────────────────────────────────────


def test_logger_calls_cloudwatch_emitter_when_enabled() -> None:
    mock_emitter = MagicMock()
    logger = _make_logger(
        session_id="sess-cwtest",
        enable_cloudwatch=True,
        cloudwatch_emitter=mock_emitter,
    )
    logger.info(agent="a", event="cw_test", document_id="doc-x")
    assert mock_emitter.emit.called


def test_logger_cloudwatch_emitter_receives_correct_session_id() -> None:
    mock_emitter = MagicMock()
    logger = _make_logger(
        session_id="sess-cwsid",
        enable_cloudwatch=True,
        cloudwatch_emitter=mock_emitter,
    )
    logger.info(agent="a", event="e", document_id="d")
    call_args = mock_emitter.emit.call_args
    assert call_args[0][0] == "sess-cwsid"


def test_logger_cloudwatch_emitter_receives_entry_dict() -> None:
    mock_emitter = MagicMock()
    logger = _make_logger(
        session_id="sess-entrydict",
        enable_cloudwatch=True,
        cloudwatch_emitter=mock_emitter,
    )
    logger.info(agent="a", event="entry_check", document_id="d")
    call_args = mock_emitter.emit.call_args
    entry = call_args[0][1]
    assert isinstance(entry, dict)
    assert entry["event"] == "entry_check"


def test_logger_cloudwatch_emitter_error_does_not_propagate() -> None:
    broken_emitter = MagicMock()
    broken_emitter.emit.side_effect = RuntimeError("CloudWatch unavailable")
    logger = _make_logger(
        enable_cloudwatch=True,
        cloudwatch_emitter=broken_emitter,
    )
    # This must not raise, even though the emitter fails
    logger.info(agent="a", event="e", document_id="d")


def test_logger_no_cloudwatch_emitter_does_not_raise() -> None:
    logger = _make_logger(
        enable_cloudwatch=True,
        cloudwatch_emitter=None,
    )
    logger.info(agent="a", event="e", document_id="d")


def test_logger_cloudwatch_disabled_emitter_not_called() -> None:
    mock_emitter = MagicMock()
    logger = _make_logger(
        enable_cloudwatch=False,
        cloudwatch_emitter=mock_emitter,
    )
    logger.info(agent="a", event="e", document_id="d")
    mock_emitter.emit.assert_not_called()


# ── NoOpLogger — interface ─────────────────────────────────────────────────────


def test_noop_logger_debug_does_not_raise() -> None:
    logger = NoOpLogger()
    logger.debug(agent="a", event="e", document_id="d")


def test_noop_logger_info_does_not_raise() -> None:
    logger = NoOpLogger()
    logger.info(agent="a", event="e", document_id="d")


def test_noop_logger_warning_does_not_raise() -> None:
    logger = NoOpLogger()
    logger.warning(agent="a", event="e", document_id="d")


def test_noop_logger_error_does_not_raise() -> None:
    logger = NoOpLogger()
    logger.error(agent="a", event="e", document_id="d")


def test_noop_logger_session_id_is_empty_string() -> None:
    logger = NoOpLogger()
    assert logger.session_id == ""


def test_noop_logger_log_file_path_is_none() -> None:
    logger = NoOpLogger()
    assert logger.log_file_path is None


# ── pipeline instrumentation — key events ────────────────────────────────────
#
# These tests verify that the workflow layers (supervisor, pipeline) emit
# the expected structured log events at the right stages.
# All AWS interaction is replaced by fakes/mocks.


def _make_pipeline_instruments():
    """
    Return (intake, retrieval_provider, analysis_agent, validation_agent, tool_executor)
    wired with fakes, ready for run_pipeline instrumentation tests.
    """
    from unittest.mock import MagicMock

    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.tool_executor_agent import ToolExecutorAgent
    from app.agents.validation_agent import ValidationAgent
    from app.schemas.analysis_models import AnalysisOutput
    from app.schemas.intake_models import IntakeRecord, IntakeResult
    from app.schemas.validation_models import ValidationOutput
    from tests.fakes.fake_retrieval import FakeRetrievalProvider

    doc_id = "doc-20260405-instrtest"
    record = IntakeRecord(
        document_id=doc_id,
        original_filename="test.txt",
        extension=".txt",
        absolute_path=f"/tmp/{doc_id}/test.txt",
        file_size_bytes=512,
        intake_timestamp="2026-04-05T00:00:00+00:00",
        source_type="CISA",
        document_date="2026-04-05",
    )
    intake = IntakeResult(
        document_id=doc_id,
        artifact_path=f"/tmp/outputs/{doc_id}.json",
        record=record,
        storage=None,
    )

    analysis_provider = MagicMock()
    analysis_provider.analyze.return_value = AnalysisOutput(
        document_id=doc_id,
        severity="High",
        category="Security",
        summary="Test summary.",
        recommendations=["Do something."],
    )
    analysis_agent = AnalysisAgent(provider=analysis_provider)

    validation_provider = MagicMock()
    validation_provider.validate.return_value = ValidationOutput(
        document_id=doc_id,
        confidence_score=0.9,
        unsupported_claims=[],
        validation_status="pass",
    )
    validation_agent = ValidationAgent(provider=validation_provider)

    return (
        intake,
        FakeRetrievalProvider(),
        analysis_agent,
        validation_agent,
        ToolExecutorAgent(),
    )


def test_pipeline_emits_session_start() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "session_start" in _collect_events(buf)


def test_pipeline_emits_intake_handoff_received() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "intake_handoff_received" in _collect_events(buf)


def test_pipeline_emits_output_generation_complete() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "output_generation_complete" in _collect_events(buf)


def test_supervisor_emits_retrieval_start() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "retrieval_start" in _collect_events(buf)


def test_supervisor_emits_retrieval_complete() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "retrieval_complete" in _collect_events(buf)


def test_supervisor_emits_analysis_start() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "analysis_start" in _collect_events(buf)


def test_supervisor_emits_analysis_complete() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "analysis_complete" in _collect_events(buf)


def test_supervisor_emits_validation_start() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "validation_start" in _collect_events(buf)


def test_supervisor_emits_validation_complete() -> None:
    from app.workflows.pipeline_workflow import run_pipeline

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    intake, rp, aa, va, te = _make_pipeline_instruments()
    run_pipeline(intake, retrieval_provider=rp, analysis_agent=aa, validation_agent=va, tool_executor=te, logger=logger)
    assert "validation_complete" in _collect_events(buf)


def test_pipeline_emits_escalation_triggered_when_required() -> None:
    """Escalation event must be emitted when confidence is below threshold."""
    from unittest.mock import MagicMock

    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.tool_executor_agent import ToolExecutorAgent
    from app.agents.validation_agent import ValidationAgent
    from app.schemas.analysis_models import AnalysisOutput
    from app.schemas.intake_models import IntakeRecord, IntakeResult
    from app.schemas.validation_models import ValidationOutput
    from app.workflows.pipeline_workflow import run_pipeline
    from tests.fakes.fake_retrieval import FakeRetrievalProvider

    doc_id = "doc-escalation"
    record = IntakeRecord(
        document_id=doc_id,
        original_filename="esc.txt",
        extension=".txt",
        absolute_path=f"/tmp/{doc_id}/esc.txt",
        file_size_bytes=100,
        intake_timestamp="2026-04-05T00:00:00+00:00",
        source_type="FDA",
        document_date="2026-04-05",
    )
    intake = IntakeResult(document_id=doc_id, artifact_path="/tmp/esc.json", record=record, storage=None)

    ap = MagicMock()
    ap.analyze.return_value = AnalysisOutput(
        document_id=doc_id,
        severity="Critical",  # triggers escalation
        category="Regulatory",
        summary="Critical finding.",
        recommendations=["Escalate immediately."],
    )
    vp = MagicMock()
    vp.validate.return_value = ValidationOutput(
        document_id=doc_id,
        confidence_score=0.4,  # below threshold → escalation
        unsupported_claims=["Claim X unsupported"],
        validation_status="fail",
    )

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    run_pipeline(
        intake,
        retrieval_provider=FakeRetrievalProvider(),
        analysis_agent=AnalysisAgent(provider=ap),
        validation_agent=ValidationAgent(provider=vp),
        tool_executor=ToolExecutorAgent(),
        logger=logger,
    )
    assert "escalation_triggered" in _collect_events(buf)


def test_supervisor_emits_retrieval_empty_warning() -> None:
    """Warning event must be emitted when KB returns no evidence chunks."""
    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.tool_executor_agent import ToolExecutorAgent
    from app.agents.validation_agent import ValidationAgent
    from app.schemas.intake_models import IntakeRecord, IntakeResult
    from app.workflows.pipeline_workflow import run_pipeline
    from tests.fakes.fake_retrieval import FakeRetrievalProvider

    doc_id = "doc-empty-retrieval"
    record = IntakeRecord(
        document_id=doc_id,
        original_filename="empty.txt",
        extension=".txt",
        absolute_path=f"/tmp/{doc_id}/empty.txt",
        file_size_bytes=50,
        intake_timestamp="2026-04-05T00:00:00+00:00",
        source_type="FDA",
        document_date="2026-04-05",
    )
    intake = IntakeResult(document_id=doc_id, artifact_path="/tmp/empty.json", record=record, storage=None)

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    run_pipeline(
        intake,
        retrieval_provider=FakeRetrievalProvider(return_empty=True),
        analysis_agent=AnalysisAgent(provider=MagicMock()),
        validation_agent=ValidationAgent(provider=MagicMock()),
        tool_executor=ToolExecutorAgent(),
        logger=logger,
    )
    assert "retrieval_empty" in _collect_events(buf)


def test_pipeline_emits_failure_event_on_supervisor_error() -> None:
    """pipeline_failed error event must be emitted when the supervisor raises."""
    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.tool_executor_agent import ToolExecutorAgent
    from app.agents.validation_agent import ValidationAgent
    from app.schemas.intake_models import IntakeRecord, IntakeResult
    from app.workflows.pipeline_workflow import PipelineWorkflowError, run_pipeline

    doc_id = "doc-fail-test"
    record = IntakeRecord(
        document_id=doc_id,
        original_filename="fail.txt",
        extension=".txt",
        absolute_path=f"/tmp/{doc_id}/fail.txt",
        file_size_bytes=50,
        intake_timestamp="2026-04-05T00:00:00+00:00",
        source_type="FDA",
        document_date="2026-04-05",
    )
    intake = IntakeResult(document_id=doc_id, artifact_path="/tmp/fail.json", record=record, storage=None)

    class _AlwaysFailProvider:
        def retrieve(self, request):  # type: ignore[override]
            raise RuntimeError("Simulated KB failure")

    buf = io.StringIO()
    logger = _make_logger(log_level=DEBUG, stdout=buf)
    with pytest.raises(PipelineWorkflowError):
        run_pipeline(
            intake,
            retrieval_provider=_AlwaysFailProvider(),  # type: ignore[arg-type]
            analysis_agent=AnalysisAgent(provider=MagicMock()),
            validation_agent=ValidationAgent(provider=MagicMock()),
            tool_executor=ToolExecutorAgent(),
            logger=logger,
        )
    assert "pipeline_failed" in _collect_events(buf)


# ── _resolve_log_path helper ──────────────────────────────────────────────────


def test_resolve_log_path_structure() -> None:
    path = _resolve_log_path("outputs", "sess-abc12345")
    assert path == Path("outputs") / "logs" / "sess-abc12345.log"
