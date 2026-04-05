"""
Structured JSON logging utility — E-0.

Provides a reusable, consistent logging interface across all pipeline components.

Every log entry is a JSON object with a standard set of fields:
  timestamp, level, session_id, document_id, agent, event, data

Output destinations (controlled by LoggingConfig):
  - stdout (always)
  - local session file under outputs/logs/{session_id}.log
  - CloudWatch (optional, via injected CloudWatchEmitter)

Design constraints:
  - No AWS dependency is required for this module to function
  - CloudWatch emission is optional and always fail-safe
  - Callers construct a PipelineLogger once per session and pass it around
  - No global logging state or module-level singletons
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


# ── log level constants ─────────────────────────────────────────────────────────

DEBUG = "DEBUG"
INFO = "INFO"
WARNING = "WARNING"
ERROR = "ERROR"

_LEVEL_ORDER = {DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40}


# ── CloudWatch emitter protocol ─────────────────────────────────────────────────


class CloudWatchEmitter(Protocol):
    """
    Protocol for an object that can emit a log entry to CloudWatch.

    The real implementation lives in app/services/cloudwatch_service.py.
    A no-op or mock can be injected in tests and local dev.
    """

    def emit(self, session_id: str, entry: dict[str, Any]) -> None:
        """Emit a single structured log entry.  Must not raise."""
        ...


# ── logging config ──────────────────────────────────────────────────────────────


class LoggingConfig:
    """
    Holds observability configuration for a pipeline session.

    Reads defaults from environment variables but can be overridden via
    constructor arguments so the config stays testable without env mutation.

    Environment variables:
      CASEOPS_LOG_LEVEL             — DEBUG | INFO | WARNING | ERROR (default INFO)
      CASEOPS_ENABLE_LOCAL_FILE_LOG — true | false (default true)
      CASEOPS_ENABLE_CLOUDWATCH     — true | false (default false)
      OUTPUT_DIR                    — base output directory (default outputs)
    """

    def __init__(
        self,
        *,
        log_level: str | None = None,
        enable_local_file: bool | None = None,
        enable_cloudwatch: bool | None = None,
        output_dir: str | None = None,
    ) -> None:
        self.log_level: str = (
            log_level
            or os.getenv("CASEOPS_LOG_LEVEL")
            or os.getenv("LOG_LEVEL", INFO)
        ).upper()

        self.enable_local_file: bool = enable_local_file if enable_local_file is not None else (
            os.getenv("CASEOPS_ENABLE_LOCAL_FILE_LOG", "true").lower() != "false"
        )

        self.enable_cloudwatch: bool = enable_cloudwatch if enable_cloudwatch is not None else (
            os.getenv("CASEOPS_ENABLE_CLOUDWATCH", "false").lower() == "true"
        )

        self.output_dir: str = output_dir or os.getenv("OUTPUT_DIR", "outputs")

    @classmethod
    def from_env(cls) -> "LoggingConfig":
        """Construct a LoggingConfig from environment variables."""
        return cls()


# ── pipeline logger ─────────────────────────────────────────────────────────────


class PipelineLogger:
    """
    Session-scoped structured JSON logger.

    One PipelineLogger is created per pipeline run and passed to components
    that need to emit log events.  It writes to stdout and, optionally, to a
    local session log file and/or CloudWatch.

    Usage:
        logger = PipelineLogger(session_id="sess-abc123", config=LoggingConfig())
        logger.info(agent="supervisor", event="session_start", document_id="doc-xxx")
        logger.error(agent="analysis-agent", event="analysis_failed",
                     document_id="doc-xxx", data={"error": str(exc)})
    """

    def __init__(
        self,
        session_id: str,
        *,
        config: LoggingConfig | None = None,
        cloudwatch_emitter: CloudWatchEmitter | None = None,
        _stdout: Any = None,
    ) -> None:
        self._session_id = session_id
        self._config = config or LoggingConfig.from_env()
        self._cloudwatch_emitter = cloudwatch_emitter
        self._stdout = _stdout or sys.stdout
        self._log_file: Path | None = None

        self._min_level_order: int = _LEVEL_ORDER.get(self._config.log_level, 20)

        if self._config.enable_local_file:
            self._log_file = _resolve_log_path(self._config.output_dir, session_id)
            _ensure_log_dir(self._log_file)

    # ── public log-level methods ────────────────────────────────────────────────

    def debug(
        self,
        *,
        agent: str,
        event: str,
        document_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self._emit(DEBUG, agent=agent, event=event, document_id=document_id, data=data)

    def info(
        self,
        *,
        agent: str,
        event: str,
        document_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self._emit(INFO, agent=agent, event=event, document_id=document_id, data=data)

    def warning(
        self,
        *,
        agent: str,
        event: str,
        document_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self._emit(WARNING, agent=agent, event=event, document_id=document_id, data=data)

    def error(
        self,
        *,
        agent: str,
        event: str,
        document_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self._emit(ERROR, agent=agent, event=event, document_id=document_id, data=data)

    # ── public accessors ────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def log_file_path(self) -> Path | None:
        """Return the resolved local log file path, or None if file logging is disabled."""
        return self._log_file

    # ── private emission ────────────────────────────────────────────────────────

    def _emit(
        self,
        level: str,
        *,
        agent: str,
        event: str,
        document_id: str,
        data: dict[str, Any] | None,
    ) -> None:
        if _LEVEL_ORDER.get(level, 0) < self._min_level_order:
            return

        entry = _build_entry(
            level=level,
            session_id=self._session_id,
            document_id=document_id,
            agent=agent,
            event=event,
            data=data or {},
        )

        line = _serialize(entry)

        # stdout — always
        try:
            print(line, file=self._stdout, flush=True)
        except Exception:
            pass

        # local file — optional, fail-safe
        if self._log_file is not None:
            try:
                with self._log_file.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass

        # CloudWatch — optional, fail-safe
        if self._config.enable_cloudwatch and self._cloudwatch_emitter is not None:
            try:
                self._cloudwatch_emitter.emit(self._session_id, entry)
            except Exception:
                pass


# ── no-op logger ────────────────────────────────────────────────────────────────


class NoOpLogger:
    """
    Drop-in replacement for PipelineLogger that discards all log entries.

    Useful as a default in components that accept an optional logger so callers
    are not forced to pass one in tests or simple scripts.
    """

    session_id: str = ""
    log_file_path: Path | None = None

    def debug(self, **_: Any) -> None:
        pass

    def info(self, **_: Any) -> None:
        pass

    def warning(self, **_: Any) -> None:
        pass

    def error(self, **_: Any) -> None:
        pass


# ── private helpers ─────────────────────────────────────────────────────────────


def _build_entry(
    *,
    level: str,
    session_id: str,
    document_id: str,
    agent: str,
    event: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Construct the standard structured log payload."""
    return {
        "timestamp": _utc_now(),
        "level": level,
        "session_id": session_id,
        "document_id": document_id,
        "agent": agent,
        "event": event,
        "data": data,
    }


def _serialize(entry: dict[str, Any]) -> str:
    """Serialize a log entry to a compact JSON string."""
    return json.dumps(entry, separators=(",", ":"), default=str)


def _utc_now() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _resolve_log_path(output_dir: str, session_id: str) -> Path:
    """Return the absolute path to the session log file."""
    return Path(output_dir) / "logs" / f"{session_id}.log"


def _ensure_log_dir(log_file: Path) -> None:
    """Create the parent directory for the log file if it does not exist."""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# ── stdlib integration shim ─────────────────────────────────────────────────────
#
# Configures the root stdlib logger to emit structured JSON to stdout so that
# any code using the standard `logging` module gets consistent formatting.
# This is optional and only activated when explicitly called.


def configure_stdlib_logging(level: str = INFO) -> None:
    """
    Configure the Python stdlib logging module to emit structured JSON lines.

    Call once at application startup (e.g. in the CLI entry point).
    This does not affect PipelineLogger — it only harmonises any code using
    the standard `logging.getLogger()` interface.
    """

    class _StructuredFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            entry = {
                "timestamp": _utc_now(),
                "level": record.levelname,
                "session_id": "",
                "document_id": "",
                "agent": record.name,
                "event": "stdlib_log",
                "data": {"message": record.getMessage()},
            }
            if record.exc_info:
                entry["data"]["exc_info"] = self.formatException(record.exc_info)
            return _serialize(entry)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
