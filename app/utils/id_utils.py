"""
ID generation utilities for the CaseOps pipeline.

generate_document_id — intake-time document ID, anchored to today's UTC date
generate_session_id  — per-pipeline-run session ID for logging and output tracing

Formats:
  document_id : doc-{YYYYMMDD}-{uuid4[:8]}   e.g. doc-20260330-a3f7c812
  session_id  : sess-{uuid4.hex[:8]}          e.g. sess-a3f7c812
"""

import uuid
from datetime import datetime, timezone


def generate_document_id() -> str:
    """Return a unique, sortable document ID anchored to today's UTC date."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_uuid = str(uuid.uuid4())[:8]
    return f"doc-{today}-{short_uuid}"


def generate_session_id() -> str:
    """Return a short, human-readable session identifier: 'sess-{8 hex chars}'."""
    return f"sess-{uuid.uuid4().hex[:8]}"
