"""
J-2 Phase 2 hardening checkpoint runner.

Composes the completed Phase 2 evaluation layers (F/G/H/I/J-0/J-1) into one
final typed Phase2CheckpointResult.  This is a composition module — it reads
indicators that callers supply and assembles them into the checkpoint model.
It does not re-score anything or call any AWS service.

The runner is intentionally narrow.  Callers provide the readiness state of each
Phase 2 layer as simple parameters; the runner assembles them into the contract
and derives the overall status from the supplied flags.

Public surface:
  build_checkpoint()   — main entry point; returns Phase2CheckpointResult.
  CheckpointInputs     — typed NamedTuple grouping all optional indicators.

Separation constraints:
  - No boto3, no live AWS calls.
  - No scoring logic — callers pass already-computed indicators.
  - Imports only: checkpoint_models, id_utils, datetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.schemas.checkpoint_models import (
    Phase2CheckpointResult,
    Phase2CheckpointStatus,
    Phase2ReadinessBlock,
)
from app.utils.id_utils import generate_session_id


# ── Checkpoint inputs contract ──────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointInputs:
    """
    Caller-supplied indicators for the J-2 checkpoint.

    Each field corresponds to a readiness indicator or count for one Phase 2
    layer.  Defaults reflect the completed v2 engineering scope.

    evaluation_ready      — F-2 + G-0/G-1/G-2 offline evaluation complete.
    safety_ready          — H-0/H-1/H-2 safety layer complete.
    optimization_ready    — I-0/I-1/I-2 optimization layer complete.
    observability_ready   — J-0 dashboard + J-1 artifact/reporting complete.
    checkpoint_ready      — J-2 this checkpoint layer complete.

    completed_phases      — ordered list of completed phase/subphase labels.
    total_tests_offline   — total passing tests without live AWS.
    external_blockers     — list of known external (non-code) blockers.
    live_aws_validated    — True only when live end-to-end validation has passed.
    notes                 — optional free-text observations.
    """

    evaluation_ready: bool = True
    safety_ready: bool = True
    optimization_ready: bool = True
    observability_ready: bool = True
    checkpoint_ready: bool = True

    completed_phases: tuple[str, ...] = (
        "F", "G", "H", "I", "J-0", "J-1", "J-2",
    )
    total_tests_offline: int = 0
    external_blockers: tuple[str, ...] = (
        "Live AWS Bedrock runtime validation blocked by AWS-side "
        "Titan Text Embeddings V2 throttling in the target account",
    )
    live_aws_validated: bool = False
    notes: str = ""


# ── Layer metadata ───────────────────────────────────────────────────────────

_LAYER_SUBPHASES: dict[str, list[str]] = {
    "evaluation": ["F-0", "F-1", "F-2", "G-0", "G-1", "G-2"],
    "safety": ["H-0", "H-1", "H-2"],
    "optimization": ["I-0", "I-1", "I-2"],
    "observability_reporting": ["J-0", "J-1"],
}

_LAYER_NOTES: dict[str, str] = {
    "evaluation": (
        "Offline evaluation harness: typed contracts, curated 7-case dataset, "
        "deterministic scorer, retrieval quality metrics, citation quality checks, "
        "composite output quality scorer."
    ),
    "safety": (
        "Safety contracts, deterministic failure-policy evaluator, Bedrock Guardrails "
        "integration wrapper and adapter, adversarial evaluation suite with 10 curated "
        "fixtures."
    ),
    "optimization": (
        "Prompt caching integration, prompt routing strategy, baseline vs. optimized "
        "comparison workflow with 4 paired fixtures."
    ),
    "observability_reporting": (
        "CloudWatch Metrics service wrapper, evaluation metric translator, dashboard "
        "body builder; local artifact writer and markdown report generator for "
        "evaluation, safety, and comparison runs."
    ),
}


# ── Builder ──────────────────────────────────────────────────────────────────


def _build_readiness_blocks(inputs: CheckpointInputs) -> list[Phase2ReadinessBlock]:
    """Assemble per-layer readiness blocks from the supplied inputs."""
    layer_ready = {
        "evaluation": inputs.evaluation_ready,
        "safety": inputs.safety_ready,
        "optimization": inputs.optimization_ready,
        "observability_reporting": inputs.observability_ready,
    }
    blocks: list[Phase2ReadinessBlock] = []
    for layer_name, subphases in _LAYER_SUBPHASES.items():
        blocks.append(
            Phase2ReadinessBlock(
                layer_name=layer_name,
                is_ready=layer_ready[layer_name],
                completed_subphases=subphases,
                notes=_LAYER_NOTES[layer_name],
            )
        )
    return blocks


def _derive_status(inputs: CheckpointInputs, engineering_complete: bool) -> Phase2CheckpointStatus:
    """Derive the checkpoint status from the supplied flags."""
    if not engineering_complete:
        return "incomplete"
    if not inputs.live_aws_validated:
        return "complete_blocked"
    return "complete"


def build_checkpoint(
    inputs: CheckpointInputs | None = None,
    *,
    checkpoint_id: str | None = None,
) -> Phase2CheckpointResult:
    """
    Build the final Phase 2 checkpoint result.

    Parameters
    ----------
    inputs
        Optional CheckpointInputs; defaults to CheckpointInputs() which reflects the
        completed v2 engineering scope (all layers ready, live AWS still blocked).
    checkpoint_id
        Stable identifier for this checkpoint run; generated if not provided.

    Returns
    -------
    Phase2CheckpointResult — fully typed, immutable checkpoint model.
    """
    if inputs is None:
        inputs = CheckpointInputs()

    effective_id = checkpoint_id if checkpoint_id else generate_session_id()
    created_at = datetime.now(timezone.utc).isoformat()

    readiness_blocks = _build_readiness_blocks(inputs)

    all_layers_ready = all(b.is_ready for b in readiness_blocks) and inputs.checkpoint_ready

    status = _derive_status(inputs, engineering_complete=all_layers_ready)

    return Phase2CheckpointResult(
        checkpoint_id=effective_id,
        created_at=created_at,
        phase_version="phase2-v2",
        completed_phases=list(inputs.completed_phases),
        total_tests_offline=inputs.total_tests_offline,
        readiness=readiness_blocks,
        external_blockers=list(inputs.external_blockers),
        engineering_complete=all_layers_ready,
        live_aws_validated=inputs.live_aws_validated,
        status=status,
        notes=inputs.notes,
    )
