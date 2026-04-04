"""
B-2 retrieval workflow — wires the A-3 intake handoff to the B-0/B-1 retrieval layer.

Public surface:
  run_retrieval           — translate intake handoff → call provider → return result
  RetrievalWorkflowError  — raised for wiring-level failures (not provider failures)

Contract:
  Input  — IntakeResult (the typed A-3 handoff returned by run_intake)
  Output — RetrievalResult (the typed B-0 schema returned by any RetrievalProvider)

This module owns translation from the intake domain to the retrieval domain.
It does not contain retrieval mechanics, analysis, or orchestration logic.

Error boundary:
  RetrievalServiceError from the provider propagates unchanged — it is already
  AWS-detail-free and carries a descriptive message.  RetrievalWorkflowError is
  reserved for failures in the translation step itself (e.g. a provider rejects
  a field value derived from the intake handoff).

Query strategy (MVP):
  If intake.record.submitter_note is present, it is used as query_text directly.
  The submitter note is the most informative human-supplied signal at intake time.
  When absent, query_text is set to None and the provider's built-in fallback
  derives a query from source_type + source_filename — see BedrockKBService._build_query.
  No LLM-generated or summarization-based query construction is performed here.
"""

from app.schemas.intake_models import IntakeResult
from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.retrieval_models import RetrievalRequest, RetrievalResult


class RetrievalWorkflowError(Exception):
    """
    Raised when the retrieval workflow cannot proceed due to a wiring-level failure.

    Distinct from RetrievalServiceError (raised inside the provider/service layer).
    This exception covers failures that occur during translation of the intake
    handoff into a valid RetrievalRequest — before the provider is ever called.
    """


def run_retrieval(
    intake: IntakeResult,
    provider: RetrievalProvider,
) -> RetrievalResult:
    """
    Translate the A-3 intake handoff into a RetrievalRequest and invoke the provider.

    Returns a typed RetrievalResult on success.

    RetrievalWorkflowError is raised if the intake handoff cannot be translated
    into a valid RetrievalRequest.  RetrievalServiceError from the provider
    propagates unchanged — the service layer is responsible for wrapping raw
    AWS exceptions before they reach this boundary.
    """
    try:
        request = _build_retrieval_request(intake)
    except Exception as exc:
        raise RetrievalWorkflowError(
            f"Failed to build RetrievalRequest from intake handoff "
            f"(document_id={intake.document_id!r}): {exc}"
        ) from exc

    return provider.retrieve(request)


# ── private helpers ────────────────────────────────────────────────────────────


def _build_retrieval_request(intake: IntakeResult) -> RetrievalRequest:
    """
    Translate an IntakeResult into a RetrievalRequest.

    Field mapping from A-3 intake contract to B-0 retrieval contract:
      intake.document_id                     → document_id
      intake.record.source_type              → source_type
      intake.record.original_filename        → source_filename
      intake.storage.source_document_key     → source_document_s3_key  (None when no S3)
      intake.record.submitter_note           → query_text               (None when absent)
    """
    source_document_s3_key: str | None = (
        intake.storage.source_document_key if intake.storage is not None else None
    )

    # submitter_note is used as-is when present; it contains the operator's
    # description of the document and is the most reliable query signal available
    # at intake time.  When absent, None is passed and the provider falls back to
    # its own deterministic query derived from source_type + source_filename.
    query_text: str | None = intake.record.submitter_note or None

    return RetrievalRequest(
        document_id=intake.document_id,
        source_type=intake.record.source_type,  # type: ignore[arg-type]
        source_filename=intake.record.original_filename,
        source_document_s3_key=source_document_s3_key,
        query_text=query_text,
    )
