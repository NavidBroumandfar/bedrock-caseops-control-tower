"""
J-1 typed contracts for local evaluation artifact metadata and report bundles.

These models describe the shape of persisted evaluation artifacts written by the
J-1 artifact writer.  They are metadata contracts, not scoring contracts — all
evaluation and scoring logic lives in app/evaluation/.

ArtifactKind     — Literal type discriminating the evaluation run type.
ArtifactMetadata — Metadata about one persisted artifact bundle on disk.
ReportBundle     — Groups persisted artifact metadata with an optional report path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# The three supported evaluation run types in J-1.
ArtifactKind = Literal["evaluation_run", "safety_run", "comparison_run"]


class ArtifactMetadata(BaseModel):
    """
    Metadata for one persisted evaluation artifact bundle.

    Produced by the J-1 artifact writer after successfully writing artifacts to
    disk.  Callers can persist this model itself as an index entry or use it
    to construct J-2 manifest records.

    run_id         — stable run/suite identifier matching the originating runner.
    kind           — discriminator for the evaluation type.
    created_at     — ISO 8601 UTC timestamp of artifact creation.
    artifact_dir   — directory path (relative to output root) where artifacts are written;
                     uses forward slashes for cross-platform consistency.
    artifact_files — list of filenames written inside artifact_dir; always non-empty.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    kind: ArtifactKind
    created_at: str         # ISO 8601 UTC
    artifact_dir: str       # relative path, e.g. "evaluation_runs/eval-001"
    artifact_files: list[str]  # e.g. ["summary.json", "case_results.json", "report.md"]

    @field_validator("run_id", "artifact_dir")
    @classmethod
    def must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be a non-empty string")
        return value

    @field_validator("artifact_files")
    @classmethod
    def must_not_be_empty(cls, items: list[str]) -> list[str]:
        if not items:
            raise ValueError("artifact_files must contain at least one filename")
        return items

    @field_validator("created_at")
    @classmethod
    def must_be_iso8601(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"created_at must be a valid ISO 8601 datetime string, got: {value!r}"
            )
        return value


class ReportBundle(BaseModel):
    """
    A completed J-1 artifact bundle: artifact metadata plus an optional report path.

    Returned by every write_* function in artifact_writer.py.  Callers can use
    the metadata for indexing and the report_path to locate the human-readable
    markdown report for display or archiving.

    metadata    — the ArtifactMetadata for the persisted run artifacts.
    report_path — relative path (from output root) to the markdown report file,
                  if one was generated; None when generate_report=False was passed.
    """

    metadata: ArtifactMetadata
    report_path: str | None = None
