"""
Document ID generation for the intake pipeline.

Format: doc-{YYYYMMDD}-{uuid4[:8]}
Example: doc-20260330-a3f7c812
"""

import uuid
from datetime import datetime, timezone


def generate_document_id() -> str:
    """Return a unique, sortable document ID anchored to today's UTC date."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_uuid = str(uuid.uuid4())[:8]
    return f"doc-{today}-{short_uuid}"
