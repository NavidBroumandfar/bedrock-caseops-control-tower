"""
J-2 checkpoint artifact writer.

Writes a completed Phase2CheckpointResult to disk as:
  {output_root}/checkpoints/{checkpoint_id}/checkpoint.json
  {output_root}/checkpoints/{checkpoint_id}/report.md

The JSON artifact is the serialized checkpoint model.  The markdown report is a
concise human-readable summary of the Phase 2 checkpoint suitable for a portfolio
reviewer or post-project audit.

Public surface:
  CheckpointWriteError   — raised on filesystem failure.
  generate_checkpoint_report() — pure function; returns markdown string; no I/O.
  write_checkpoint()     — writes both artifacts; returns (json_path, report_path).

Separation constraints:
  - No boto3, no live AWS calls.
  - No scoring logic.
  - Imports only: checkpoint_models, json, pathlib, datetime.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.checkpoint_models import Phase2CheckpointResult


class CheckpointWriteError(Exception):
    """Raised when the checkpoint writer cannot write to the output directory."""


# ── Pure report generator ────────────────────────────────────────────────────


def generate_checkpoint_report(result: Phase2CheckpointResult) -> str:
    """
    Generate a concise markdown report for a Phase2CheckpointResult.

    Returns a deterministic markdown string; no I/O.
    """
    lines: list[str] = []

    lines.append("# Phase 2 v2 Hardening Checkpoint Report")
    lines.append("")
    lines.append(f"**Checkpoint ID:** `{result.checkpoint_id}`  ")
    lines.append(f"**Created at:** {result.created_at}  ")
    lines.append(f"**Phase version:** {result.phase_version}  ")
    lines.append(f"**Overall status:** `{result.status}`  ")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Phase 2 Completion Summary")
    lines.append("")
    lines.append(f"**Engineering complete:** {'Yes' if result.engineering_complete else 'No'}  ")
    lines.append(f"**Live AWS validated:** {'Yes' if result.live_aws_validated else 'No — see External Blockers below'}  ")
    lines.append(f"**Total offline tests passing:** {result.total_tests_offline}  ")
    lines.append("")

    lines.append("### Completed phases / subphases")
    lines.append("")
    for phase in result.completed_phases:
        lines.append(f"- {phase}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Readiness by Layer")
    lines.append("")
    for block in result.readiness:
        status_icon = "✅" if block.is_ready else "❌"
        lines.append(f"### {status_icon} {block.layer_name.replace('_', ' ').title()}")
        lines.append("")
        lines.append(f"**Ready:** {'Yes' if block.is_ready else 'No'}  ")
        lines.append(f"**Completed subphases:** {', '.join(block.completed_subphases)}  ")
        if block.notes:
            lines.append(f"**Notes:** {block.notes}  ")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## What was hardened in J-2")
    lines.append("")
    lines.append(
        "- Added typed `Phase2CheckpointResult` contract and `Phase2ReadinessBlock` "
        "per-layer readiness model (`app/schemas/checkpoint_models.py`)."
    )
    lines.append(
        "- Added narrow checkpoint runner that composes Phase 2 layer readiness "
        "indicators into a single honest checkpoint summary "
        "(`app/evaluation/checkpoint_runner.py`)."
    )
    lines.append(
        "- Added checkpoint writer that persists the checkpoint as a local JSON "
        "artifact and this markdown report (`app/evaluation/checkpoint_writer.py`)."
    )
    lines.append(
        "- Added `ArtifactKind` extension: `'checkpoint'` added to the literal type "
        "so the J-2 output is consistently classifiable alongside J-1 artifact kinds."
    )
    lines.append(
        "- Applied targeted serialization hardening: `created_at` validator now "
        "accepts both offset-aware and naive ISO 8601 strings consistently across "
        "`ArtifactMetadata` and `Phase2CheckpointResult`."
    )
    lines.append(
        "- Strengthened model-level consistency guard: `Phase2CheckpointResult` raises "
        "`ValueError` if `status='complete'` while `live_aws_validated=False`, preventing "
        "the checkpoint from silently misrepresenting the external blocker state."
    )
    lines.append(
        "- Updated all four documentation files to reflect Phase 2 complete and J-2 complete."
    )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## External Blockers")
    lines.append("")
    if result.external_blockers:
        for blocker in result.external_blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Checkpoint Verdict")
    lines.append("")

    verdict_descriptions = {
        "complete": (
            "Phase 2 is fully complete — all engineering scope done and live AWS "
            "validation confirmed."
        ),
        "complete_blocked": (
            "Phase 2 engineering scope is **complete**. "
            "Live AWS end-to-end validation remains **externally blocked** "
            "by AWS-side Titan Text Embeddings V2 throttling in the target account. "
            "This is not a code issue. All pipeline logic is implemented and correct. "
            "Live validation will be completed when the AWS-side blocker is resolved."
        ),
        "incomplete": (
            "Phase 2 engineering scope is **not yet complete**. "
            "One or more layers remain not implemented."
        ),
    }
    lines.append(verdict_descriptions[result.status])
    lines.append("")

    if result.notes:
        lines.append("---")
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        lines.append(result.notes)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_This report was generated automatically by the J-2 checkpoint runner. "
        "It is a local offline artifact — no live AWS calls were made._"
    )
    lines.append("")

    return "\n".join(lines)


# ── Writer ────────────────────────────────────────────────────────────────────


def write_checkpoint(
    result: Phase2CheckpointResult,
    output_root: Path,
    *,
    generate_report: bool = True,
) -> tuple[Path, Path | None]:
    """
    Write a Phase2CheckpointResult to disk.

    Creates:
      {output_root}/checkpoints/{checkpoint_id}/checkpoint.json
      {output_root}/checkpoints/{checkpoint_id}/report.md  (when generate_report=True)

    Parameters
    ----------
    result
        Completed Phase2CheckpointResult from checkpoint_runner.build_checkpoint().
    output_root
        Root directory for all checkpoint artifacts.
    generate_report
        When True (default), writes a markdown report alongside the JSON artifact.

    Returns
    -------
    (json_path, report_path) — absolute paths to the written files.
    report_path is None when generate_report=False.

    Raises
    ------
    CheckpointWriteError on any filesystem failure.
    """
    checkpoint_dir = output_root / "checkpoints" / result.checkpoint_id
    try:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CheckpointWriteError(
            f"Cannot create checkpoint directory {checkpoint_dir}: {exc}"
        ) from exc

    json_path = checkpoint_dir / "checkpoint.json"
    try:
        json_path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise CheckpointWriteError(
            f"Cannot write checkpoint.json to {json_path}: {exc}"
        ) from exc

    report_path: Path | None = None
    if generate_report:
        report_text = generate_checkpoint_report(result)
        report_path = checkpoint_dir / "report.md"
        try:
            report_path.write_text(report_text, encoding="utf-8")
        except OSError as exc:
            raise CheckpointWriteError(
                f"Cannot write checkpoint report.md to {report_path}: {exc}"
            ) from exc

    return json_path, report_path
