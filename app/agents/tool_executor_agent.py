"""
Tool Executor Agent — D-1.

Consumes a typed SupervisorResult, applies escalation rules, maps retrieval
evidence to output citations, and returns a typed CaseOutput.

Public surface:
  ToolExecutorAgent           — the agent class; callers use run()
  ESCALATION_CONFIDENCE_THRESHOLD — the confidence threshold for escalation
                                    (module-level constant; config-driven in E-phase)

Architecture contract:
  Input  — SupervisorResult (D-0 typed handoff)
  Output — CaseOutput (D-1 final output contract)

  No boto3 clients, S3 writes, CloudWatch calls, or output file writes belong
  here.  Those are E-phase concerns.  This module owns only the assembly of
  the typed output and the escalation decision.

Escalation rules (any of the following triggers escalation_required = True):
  1. severity == "Critical"
  2. confidence_score < ESCALATION_CONFIDENCE_THRESHOLD (0.60)
  3. len(unsupported_claims) > 0
  4. any recommendation contains "escalate" (case-insensitive)

Empty-retrieval path:
  When SupervisorResult.analysis is None, the KB returned no evidence.
  Analysis and validation were not attempted.  The Tool Executor produces a
  conservative CaseOutput with:
    - severity = "Low"       — cannot classify without evidence; "Low" avoids
                               a false alarm but escalation_required = True
                               ensures the case is reviewed
    - confidence_score = 0.0
    - unsupported_claims     — a single entry naming the root cause
    - escalation_required    = True
    - escalation_reason      — explicit message naming the empty-retrieval cause
    - citations              = []
  Safe placeholder values (severity, category, summary) are used only because
  the schema requires concrete values — they are not assertions about risk.
"""

from datetime import datetime, timezone

from app.schemas.analysis_models import SeverityLevel
from app.schemas.output_models import CaseOutput, Citation
from app.schemas.retrieval_models import EvidenceChunk
from app.schemas.supervisor_models import SupervisorResult

# Minimum confidence score before escalation is required.
# Matches the architecture default (ARCHITECTURE.md §9).
ESCALATION_CONFIDENCE_THRESHOLD: float = 0.60

# Human-readable agent identifier stamped into every CaseOutput.
_VALIDATED_BY = "tool-executor-agent-v1"

# Placeholder values used only on the empty-retrieval path where no analysis
# was produced.  They signal "unknown" rather than asserting a risk level.
_EMPTY_PATH_SEVERITY: SeverityLevel = "Low"
_EMPTY_PATH_CATEGORY = "Unclassified"
_EMPTY_PATH_SUMMARY = (
    "No evidence was retrieved from the knowledge base. "
    "The document could not be grounded or assessed."
)


class ToolExecutorAgent:
    """
    Tool Executor Agent: assembles the final CaseOutput from a SupervisorResult.

    The agent is stateless and has no injected dependencies — all logic is
    deterministic given the SupervisorResult input.  No AWS calls are made.

    Usage:
        agent = ToolExecutorAgent()
        output = agent.run(supervisor_result)
    """

    def run(self, supervisor_result: SupervisorResult) -> CaseOutput:
        """
        Assemble and return a CaseOutput from the given SupervisorResult.

        Handles both the success path (analysis and validation populated) and
        the empty-retrieval path (analysis is None, validation is None).
        """
        if supervisor_result.analysis is None:
            return self._handle_empty_retrieval(supervisor_result)

        return self._handle_success(supervisor_result)

    # ── private: success path ─────────────────────────────────────────────────

    def _handle_success(self, result: SupervisorResult) -> CaseOutput:
        analysis = result.analysis
        validation = result.validation

        # validation should not be None on the success path, but guard defensively
        # so the agent degrades cleanly rather than raising an unhandled AttributeError.
        if validation is None:
            confidence_score = 0.0
            unsupported_claims = [
                "Validation output was not produced despite evidence being available."
            ]
        else:
            confidence_score = validation.confidence_score
            unsupported_claims = validation.unsupported_claims

        citations = _map_chunks_to_citations(result.retrieval.evidence_chunks)

        escalation_required, escalation_reason = _determine_escalation(
            severity=analysis.severity,
            confidence_score=confidence_score,
            unsupported_claims=unsupported_claims,
            recommendations=analysis.recommendations,
        )

        return CaseOutput(
            document_id=result.document_id,
            source_filename=result.intake.record.original_filename,
            source_type=result.intake.record.source_type,
            severity=analysis.severity,
            category=analysis.category,
            summary=analysis.summary,
            recommendations=analysis.recommendations,
            citations=citations,
            confidence_score=confidence_score,
            unsupported_claims=unsupported_claims,
            escalation_required=escalation_required,
            escalation_reason=escalation_reason,
            validated_by=_VALIDATED_BY,
            timestamp=_utc_now(),
        )

    # ── private: empty-retrieval path ─────────────────────────────────────────

    def _handle_empty_retrieval(self, result: SupervisorResult) -> CaseOutput:
        unsupported_claims = [
            "No evidence chunks were retrieved; all document claims are unverifiable."
        ]
        escalation_reason = (
            "No evidence was retrieved from the knowledge base. "
            "The document cannot be grounded or assessed without retrieval results."
        )

        return CaseOutput(
            document_id=result.document_id,
            source_filename=result.intake.record.original_filename,
            source_type=result.intake.record.source_type,
            severity=_EMPTY_PATH_SEVERITY,
            category=_EMPTY_PATH_CATEGORY,
            summary=_EMPTY_PATH_SUMMARY,
            recommendations=[],
            citations=[],
            confidence_score=0.0,
            unsupported_claims=unsupported_claims,
            escalation_required=True,
            escalation_reason=escalation_reason,
            validated_by=_VALIDATED_BY,
            timestamp=_utc_now(),
        )


# ── module-level helpers ───────────────────────────────────────────────────────


def _map_chunks_to_citations(chunks: list[EvidenceChunk]) -> list[Citation]:
    """
    Convert EvidenceChunks to Citations.

    One citation per chunk; fields are preserved directly without rewriting.
    No citation is dropped or fabricated.
    """
    return [
        Citation(
            source_id=chunk.source_id,
            source_label=chunk.source_label,
            excerpt=chunk.excerpt,
            relevance_score=chunk.relevance_score,
        )
        for chunk in chunks
    ]


def _determine_escalation(
    severity: SeverityLevel,
    confidence_score: float,
    unsupported_claims: list[str],
    recommendations: list[str],
) -> tuple[bool, str | None]:
    """
    Apply escalation rules and return (escalation_required, escalation_reason).

    Collects all triggered reasons so the escalation_reason field is specific
    about which rule(s) fired.  Returns (False, None) when no rule triggers.
    """
    reasons: list[str] = []

    if severity == "Critical":
        reasons.append("severity is Critical")

    if confidence_score < ESCALATION_CONFIDENCE_THRESHOLD:
        reasons.append(
            f"confidence_score {confidence_score:.2f} is below threshold "
            f"{ESCALATION_CONFIDENCE_THRESHOLD:.2f}"
        )

    if len(unsupported_claims) > 0:
        count = len(unsupported_claims)
        reasons.append(
            f"{count} unsupported claim{'s' if count != 1 else ''} detected"
        )

    if any("escalate" in r.lower() for r in recommendations):
        reasons.append("recommendation explicitly indicates escalation")

    if reasons:
        return True, "; ".join(reasons)
    return False, None


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
