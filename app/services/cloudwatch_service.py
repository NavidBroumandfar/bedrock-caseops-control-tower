"""
CloudWatch Logs service wrapper — E-0.

Thin wrapper around the boto3 CloudWatch Logs client.  Satisfies the
CloudWatchEmitter protocol defined in app/utils/logging_utils.py.

Design constraints:
  - No business logic: only log group/stream management and event submission
  - All failures are caught and swallowed — CloudWatch is an optional sink
  - Never raises; callers can rely on fire-and-forget semantics
  - Disabled gracefully when CASEOPS_ENABLE_CLOUDWATCH is false or AWS is unavailable
  - Mockable: the boto3 client can be injected at construction time

Environment variables (read from app/utils/config.py via caller, or directly):
  CASEOPS_CLOUDWATCH_LOG_GROUP         — log group name (default /caseops/pipeline)
  CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX — stream name prefix (default caseops-session)
  AWS_REGION                           — AWS region (default us-east-1)
"""

from __future__ import annotations

import os
import time
from typing import Any


class CloudWatchLogsService:
    """
    CloudWatch Logs emitter.

    Creates the log group and per-session log stream on first emit if they do
    not already exist, then puts each log entry as a CloudWatch log event.

    All methods are fail-safe: any exception from the boto3 client is caught
    and silently discarded so a CloudWatch outage never breaks the pipeline.

    Usage:
        service = CloudWatchLogsService()  # reads config from env
        service.emit(session_id="sess-abc123", entry={...})

    Test usage (inject a mock client):
        service = CloudWatchLogsService(client=mock_client)
    """

    def __init__(
        self,
        *,
        log_group: str | None = None,
        log_stream_prefix: str | None = None,
        region: str | None = None,
        client: Any = None,
    ) -> None:
        self._log_group: str = (
            log_group
            or os.getenv("CASEOPS_CLOUDWATCH_LOG_GROUP")
            or os.getenv("CLOUDWATCH_LOG_GROUP", "/caseops/pipeline")
        )
        self._log_stream_prefix: str = (
            log_stream_prefix
            or os.getenv("CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX", "caseops-session")
        )
        self._region: str = region or os.getenv("AWS_REGION", "us-east-1")
        self._client = client or self._build_client()

        # Track which (group, stream) pairs have been initialised this process lifetime.
        # This avoids redundant CreateLogGroup / CreateLogStream calls per-emit.
        self._initialised_streams: set[str] = set()

    # ── public interface (CloudWatchEmitter protocol) ───────────────────────────

    def emit(self, session_id: str, entry: dict[str, Any]) -> None:
        """
        Emit a single structured log entry to CloudWatch Logs.

        Derives the log stream name from session_id.  Creates the log group
        and stream if they do not exist.  Never raises — all failures are
        suppressed so CloudWatch issues cannot break the pipeline.
        """
        if self._client is None:
            return

        stream_name = self._stream_name(session_id)

        try:
            self._ensure_log_group()
            self._ensure_log_stream(stream_name)
            self._put_event(stream_name, entry)
        except Exception:
            pass

    # ── private helpers ─────────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        """
        Construct a boto3 CloudWatch Logs client.

        Returns None if boto3 is not available or cannot construct the client,
        so the service degrades gracefully in environments without AWS credentials.
        """
        try:
            import boto3

            return boto3.client("logs", region_name=self._region)
        except Exception:
            return None

    def _stream_name(self, session_id: str) -> str:
        """Derive the CloudWatch log stream name from session_id."""
        return f"{self._log_stream_prefix}/{session_id}"

    def _ensure_log_group(self) -> None:
        """Create the log group if it does not already exist."""
        if self._log_group in self._initialised_streams:
            return
        try:
            self._client.create_log_group(logGroupName=self._log_group)
        except self._client.exceptions.ResourceAlreadyExistsException:
            pass
        self._initialised_streams.add(self._log_group)

    def _ensure_log_stream(self, stream_name: str) -> None:
        """Create the log stream if it does not already exist."""
        cache_key = f"{self._log_group}/{stream_name}"
        if cache_key in self._initialised_streams:
            return
        try:
            self._client.create_log_stream(
                logGroupName=self._log_group,
                logStreamName=stream_name,
            )
        except self._client.exceptions.ResourceAlreadyExistsException:
            pass
        self._initialised_streams.add(cache_key)

    def _put_event(self, stream_name: str, entry: dict[str, Any]) -> None:
        """
        Submit a single log event to CloudWatch Logs.

        Uses the current epoch millisecond timestamp as the CloudWatch event
        timestamp.  The structured entry is serialised as JSON in the message.
        """
        import json

        message = json.dumps(entry, separators=(",", ":"), default=str)
        timestamp_ms = int(time.time() * 1000)

        self._client.put_log_events(
            logGroupName=self._log_group,
            logStreamName=stream_name,
            logEvents=[{"timestamp": timestamp_ms, "message": message}],
        )


# ── no-op emitter ───────────────────────────────────────────────────────────────


class NoOpCloudWatchEmitter:
    """
    CloudWatch emitter that discards all log entries.

    Used when CloudWatch is disabled (CASEOPS_ENABLE_CLOUDWATCH=false) or in
    tests where real AWS calls must not be made.
    """

    def emit(self, session_id: str, entry: dict[str, Any]) -> None:  # noqa: ARG002
        pass


def build_cloudwatch_emitter(
    *,
    enabled: bool | None = None,
    log_group: str | None = None,
    log_stream_prefix: str | None = None,
    region: str | None = None,
    client: Any = None,
) -> "CloudWatchLogsService | NoOpCloudWatchEmitter":
    """
    Factory: return the appropriate CloudWatch emitter based on config.

    When `enabled` is None, reads CASEOPS_ENABLE_CLOUDWATCH from the environment.
    Returns a NoOpCloudWatchEmitter if disabled to avoid any AWS calls.
    """
    if enabled is None:
        enabled = os.getenv("CASEOPS_ENABLE_CLOUDWATCH", "false").lower() == "true"

    if not enabled:
        return NoOpCloudWatchEmitter()

    return CloudWatchLogsService(
        log_group=log_group,
        log_stream_prefix=log_stream_prefix,
        region=region,
        client=client,
    )
