"""
J-2 typed contract for the final Phase 2 v2 hardening checkpoint.

This module defines the minimal typed model representing the outcome of the J-2
checkpoint: a single honest summary of Phase 2 completeness, readiness indicators,
known external blockers, and the overall checkpoint verdict.

It is a metadata contract, not a scoring contract.  All evaluation and scoring
logic lives in app/evaluation/.

Phase2CheckpointStatus  — Literal verdict type.
Phase2ReadinessBlock    — Per-layer readiness indicator (evaluation, safety,
                          optimization, observability).
Phase2CheckpointResult  — Root checkpoint model returned by the J-2 runner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Possible overall checkpoint verdicts.
Phase2CheckpointStatus = Literal[
    "complete",          # all layers engineering-complete, no blocking issues
    "complete_blocked",  # engineering-complete but externally blocked (live AWS)
    "incomplete",        # one or more layers not yet engineering-complete
]


class Phase2ReadinessBlock(BaseModel):
    """
    Readiness indicator for one Phase 2 layer.

    layer_name   — short human-readable name, e.g. "evaluation", "safety".
    is_ready     — True when the layer's engineering scope is complete and
                   its tests all pass without live AWS.
    completed_subphases — list of subphase identifiers that are done.
    notes        — optional free-text elaboration.
    """

    model_config = ConfigDict(frozen=True)

    layer_name: str
    is_ready: bool
    completed_subphases: list[str]
    notes: str = ""

    @field_validator("layer_name")
    @classmethod
    def _non_empty_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("layer_name must be non-empty")
        return v


class Phase2CheckpointResult(BaseModel):
    """
    Final Phase 2 v2 hardening checkpoint result.

    Produced by the J-2 checkpoint runner.  It honestly represents:
      - which Phase 2 subphases are complete
      - readiness status per evaluation layer
      - total offline test count
      - known external blockers (live AWS throttling)
      - overall checkpoint verdict

    checkpoint_id     — stable run identifier.
    created_at        — ISO 8601 UTC timestamp.
    phase_version     — phase label, e.g. "phase2-v2".
    completed_phases  — list of completed phase labels (e.g. ["F", "G", "H", "I", "J-0", "J-1", "J-2"]).
    total_tests_offline — total passing tests without live AWS at checkpoint time.
    readiness         — per-layer readiness blocks (evaluation, safety, optimization,
                        observability_reporting).
    external_blockers — list of known external (non-code) blockers.
    engineering_complete — True when all Phase 2 engineering scope is done.
    live_aws_validated   — True only when live AWS end-to-end validation has passed;
                           False while the Titan Embeddings throttling blocker remains.
    status            — overall checkpoint verdict.
    notes             — optional free-text observations or warnings.
    """

    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    created_at: str          # ISO 8601 UTC
    phase_version: str       # e.g. "phase2-v2"

    completed_phases: list[str]      # ordered list, e.g. ["F", "G", "H", "I", "J-0", "J-1", "J-2"]
    total_tests_offline: int         # total passing tests without live AWS

    readiness: list[Phase2ReadinessBlock]  # one block per evaluation layer

    external_blockers: list[str]     # non-empty when any external blocker exists
    engineering_complete: bool
    live_aws_validated: bool

    status: Phase2CheckpointStatus
    notes: str = ""

    # ── validators ─────────────────────────────────────────────────────────────

    @field_validator("checkpoint_id", "phase_version")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must be non-empty")
        return v

    @field_validator("created_at")
    @classmethod
    def _valid_iso8601(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"created_at must be a valid ISO 8601 datetime string, got: {v!r}"
            )
        return v

    @field_validator("total_tests_offline")
    @classmethod
    def _non_negative_tests(cls, v: int) -> int:
        if v < 0:
            raise ValueError("total_tests_offline must be >= 0")
        return v

    @model_validator(mode="after")
    def _status_consistency(self) -> "Phase2CheckpointResult":
        """
        Guard: if live_aws_validated is False and engineering_complete is True,
        status must be 'complete_blocked', not 'complete'.
        """
        if self.engineering_complete and not self.live_aws_validated:
            if self.status == "complete":
                raise ValueError(
                    "status cannot be 'complete' when live_aws_validated is False; "
                    "use 'complete_blocked' to honestly reflect the external blocker"
                )
        if not self.engineering_complete and self.status in ("complete", "complete_blocked"):
            raise ValueError(
                "status cannot be 'complete' or 'complete_blocked' when "
                "engineering_complete is False; use 'incomplete'"
            )
        return self
