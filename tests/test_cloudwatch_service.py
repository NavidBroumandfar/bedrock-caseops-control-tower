"""
E-0 unit tests — CloudWatch logging service wrapper (app/services/cloudwatch_service.py).

Coverage:

  CloudWatchLogsService — constructor:
  - accepts an injected client (no real boto3 calls required)
  - reads log group from CASEOPS_CLOUDWATCH_LOG_GROUP env var
  - reads log stream prefix from CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX env var
  - uses fallback defaults when env vars are absent

  CloudWatchLogsService — emit:
  - calls create_log_group on first emit
  - calls create_log_stream on first emit
  - calls put_log_events with the correct log group and stream
  - put_log_events message is valid JSON containing the entry
  - put_log_events timestamp is an integer (epoch ms)
  - second emit to the same session does not re-create the stream
  - ResourceAlreadyExistsException on create_log_group is silently ignored
  - ResourceAlreadyExistsException on create_log_stream is silently ignored
  - any exception from put_log_events is silently swallowed (never raises)
  - a completely broken client does not cause emit to raise

  CloudWatchLogsService — stream naming:
  - stream name includes the session_id
  - stream name includes the configured prefix

  NoOpCloudWatchEmitter:
  - emit does not raise
  - emit does not call any boto3 methods

  build_cloudwatch_emitter — factory:
  - returns NoOpCloudWatchEmitter when enabled=False
  - returns CloudWatchLogsService when enabled=True
  - reads CASEOPS_ENABLE_CLOUDWATCH from env when enabled arg is None
  - returns NoOpCloudWatchEmitter when env var is absent (default disabled)

No live AWS calls are made.  The boto3 client is always injected as a mock.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.cloudwatch_service import (
    CloudWatchLogsService,
    NoOpCloudWatchEmitter,
    build_cloudwatch_emitter,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_client() -> MagicMock:
    """
    Return a mock boto3 CloudWatch Logs client.

    The mock's exception types are set up so that create_log_group and
    create_log_stream can simulate ResourceAlreadyExistsException.
    """
    client = MagicMock()
    # boto3 exceptions are accessed as client.exceptions.SomeException —
    # configure the mock to expose an exception class as an attribute.
    already_exists = type("ResourceAlreadyExistsException", (Exception,), {})
    client.exceptions = MagicMock()
    client.exceptions.ResourceAlreadyExistsException = already_exists
    return client


def _make_service(
    log_group: str = "/test/caseops",
    log_stream_prefix: str = "test-session",
    client: MagicMock | None = None,
) -> tuple[CloudWatchLogsService, MagicMock]:
    mock_client = client or _make_mock_client()
    service = CloudWatchLogsService(
        log_group=log_group,
        log_stream_prefix=log_stream_prefix,
        client=mock_client,
    )
    return service, mock_client


# ── constructor ───────────────────────────────────────────────────────────────


def test_constructor_accepts_injected_client() -> None:
    mock_client = _make_mock_client()
    service = CloudWatchLogsService(client=mock_client)
    assert service._client is mock_client


def test_constructor_reads_log_group_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_CLOUDWATCH_LOG_GROUP", "/my/custom/group")
    service = CloudWatchLogsService(client=_make_mock_client())
    assert service._log_group == "/my/custom/group"


def test_constructor_reads_stream_prefix_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX", "my-prefix")
    service = CloudWatchLogsService(client=_make_mock_client())
    assert service._log_stream_prefix == "my-prefix"


def test_constructor_uses_default_log_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_CLOUDWATCH_LOG_GROUP", raising=False)
    monkeypatch.delenv("CLOUDWATCH_LOG_GROUP", raising=False)
    service = CloudWatchLogsService(client=_make_mock_client())
    assert service._log_group == "/caseops/pipeline"


def test_constructor_uses_default_stream_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX", raising=False)
    service = CloudWatchLogsService(client=_make_mock_client())
    assert service._log_stream_prefix == "caseops-session"


def test_constructor_explicit_args_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_CLOUDWATCH_LOG_GROUP", "/env/group")
    service = CloudWatchLogsService(
        log_group="/explicit/group",
        client=_make_mock_client(),
    )
    assert service._log_group == "/explicit/group"


# ── emit: AWS calls ───────────────────────────────────────────────────────────


def test_emit_calls_create_log_group() -> None:
    service, mock_client = _make_service()
    service.emit("sess-abc12345", {"event": "test"})
    mock_client.create_log_group.assert_called_once()


def test_emit_calls_create_log_stream() -> None:
    service, mock_client = _make_service()
    service.emit("sess-abc12345", {"event": "test"})
    mock_client.create_log_stream.assert_called_once()


def test_emit_calls_put_log_events() -> None:
    service, mock_client = _make_service()
    service.emit("sess-abc12345", {"event": "test"})
    mock_client.put_log_events.assert_called_once()


def test_emit_put_log_events_uses_correct_log_group() -> None:
    service, mock_client = _make_service(log_group="/test/group")
    service.emit("sess-abc12345", {"event": "test"})
    call_kwargs = mock_client.put_log_events.call_args[1]
    assert call_kwargs["logGroupName"] == "/test/group"


def test_emit_put_log_events_uses_correct_stream_name() -> None:
    service, mock_client = _make_service(
        log_group="/test/group",
        log_stream_prefix="my-prefix",
    )
    service.emit("sess-teststr", {"event": "test"})
    call_kwargs = mock_client.put_log_events.call_args[1]
    assert "sess-teststr" in call_kwargs["logStreamName"]
    assert "my-prefix" in call_kwargs["logStreamName"]


def test_emit_message_is_valid_json() -> None:
    service, mock_client = _make_service()
    entry = {"event": "analysis_start", "level": "INFO", "agent": "supervisor"}
    service.emit("sess-jsontest", entry)
    call_kwargs = mock_client.put_log_events.call_args[1]
    events = call_kwargs["logEvents"]
    assert len(events) == 1
    parsed = json.loads(events[0]["message"])
    assert isinstance(parsed, dict)


def test_emit_message_contains_entry_fields() -> None:
    service, mock_client = _make_service()
    entry = {"event": "retrieval_complete", "level": "INFO", "chunk_count": 3}
    service.emit("sess-fields", entry)
    call_kwargs = mock_client.put_log_events.call_args[1]
    parsed = json.loads(call_kwargs["logEvents"][0]["message"])
    assert parsed["event"] == "retrieval_complete"
    assert parsed["chunk_count"] == 3


def test_emit_log_event_timestamp_is_integer() -> None:
    service, mock_client = _make_service()
    service.emit("sess-ts", {"event": "e"})
    call_kwargs = mock_client.put_log_events.call_args[1]
    ts = call_kwargs["logEvents"][0]["timestamp"]
    assert isinstance(ts, int)
    assert ts > 0


# ── emit: idempotency / caching ───────────────────────────────────────────────


def test_emit_second_call_same_session_does_not_recreate_stream() -> None:
    service, mock_client = _make_service()
    service.emit("sess-idem", {"event": "first"})
    service.emit("sess-idem", {"event": "second"})
    # create_log_stream should have been called exactly once (first emit only)
    assert mock_client.create_log_stream.call_count == 1


def test_emit_second_call_same_session_does_not_recreate_log_group() -> None:
    service, mock_client = _make_service()
    service.emit("sess-grp", {"event": "first"})
    service.emit("sess-grp", {"event": "second"})
    assert mock_client.create_log_group.call_count == 1


def test_emit_different_sessions_create_separate_streams() -> None:
    service, mock_client = _make_service()
    service.emit("sess-alpha", {"event": "e"})
    service.emit("sess-beta", {"event": "e"})
    assert mock_client.create_log_stream.call_count == 2


# ── emit: exception handling ──────────────────────────────────────────────────


def test_emit_ignores_resource_already_exists_on_create_log_group() -> None:
    service, mock_client = _make_service()
    already_exists = mock_client.exceptions.ResourceAlreadyExistsException
    mock_client.create_log_group.side_effect = already_exists("already exists")
    # Must not raise
    service.emit("sess-exists", {"event": "e"})


def test_emit_ignores_resource_already_exists_on_create_log_stream() -> None:
    service, mock_client = _make_service()
    already_exists = mock_client.exceptions.ResourceAlreadyExistsException
    mock_client.create_log_stream.side_effect = already_exists("already exists")
    service.emit("sess-streamexists", {"event": "e"})


def test_emit_swallows_put_log_events_exception() -> None:
    service, mock_client = _make_service()
    mock_client.put_log_events.side_effect = RuntimeError("CloudWatch API failure")
    # Must not raise — CloudWatch failures are fire-and-forget
    service.emit("sess-putfail", {"event": "e"})


def test_emit_swallows_any_exception_from_client() -> None:
    service, mock_client = _make_service()
    mock_client.create_log_group.side_effect = Exception("Unexpected AWS error")
    service.emit("sess-anyerr", {"event": "e"})


def test_emit_with_none_client_does_not_raise() -> None:
    service = CloudWatchLogsService(client=None)
    # When client is None (_build_client returned None), emit must degrade gracefully.
    service.emit("sess-noclient", {"event": "e"})


# ── stream naming ─────────────────────────────────────────────────────────────


def test_stream_name_includes_session_id() -> None:
    service, _ = _make_service(log_stream_prefix="caseops-session")
    stream = service._stream_name("sess-abc12345")
    assert "sess-abc12345" in stream


def test_stream_name_includes_prefix() -> None:
    service, _ = _make_service(log_stream_prefix="my-prefix")
    stream = service._stream_name("sess-xyz")
    assert "my-prefix" in stream


# ── NoOpCloudWatchEmitter ─────────────────────────────────────────────────────


def test_noop_emitter_does_not_raise() -> None:
    emitter = NoOpCloudWatchEmitter()
    emitter.emit("sess-noop", {"event": "e"})


def test_noop_emitter_does_not_call_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-op emitter must never touch boto3."""
    import boto3

    original_client = boto3.client
    called = []

    def spy(*args, **kwargs):
        called.append(args)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("boto3.client", spy)
    emitter = NoOpCloudWatchEmitter()
    emitter.emit("sess-noop", {"event": "e"})
    assert len(called) == 0


# ── build_cloudwatch_emitter factory ─────────────────────────────────────────


def test_factory_returns_noop_when_disabled() -> None:
    emitter = build_cloudwatch_emitter(enabled=False)
    assert isinstance(emitter, NoOpCloudWatchEmitter)


def test_factory_returns_service_when_enabled() -> None:
    mock_client = _make_mock_client()
    emitter = build_cloudwatch_emitter(enabled=True, client=mock_client)
    assert isinstance(emitter, CloudWatchLogsService)


def test_factory_reads_env_when_enabled_arg_is_none_and_env_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_CLOUDWATCH", "true")
    mock_client = _make_mock_client()
    emitter = build_cloudwatch_emitter(client=mock_client)
    assert isinstance(emitter, CloudWatchLogsService)


def test_factory_returns_noop_when_env_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASEOPS_ENABLE_CLOUDWATCH", raising=False)
    emitter = build_cloudwatch_emitter()
    assert isinstance(emitter, NoOpCloudWatchEmitter)


def test_factory_returns_noop_when_env_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEOPS_ENABLE_CLOUDWATCH", "false")
    emitter = build_cloudwatch_emitter()
    assert isinstance(emitter, NoOpCloudWatchEmitter)
