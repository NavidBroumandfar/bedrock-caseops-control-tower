"""
B-2 unit tests — retrieval workflow.

Coverage:
  Translation — IntakeResult → RetrievalRequest:
    - document_id passes through from intake.document_id
    - source_type passes through from intake.record.source_type
    - source_filename maps from intake.record.original_filename
    - source_document_s3_key populated from storage when S3 was used
    - source_document_s3_key is None when intake ran in local-only mode
    - submitter_note becomes query_text when present
    - query_text is None when submitter_note is absent (provider fallback applies)

  Workflow — run_retrieval:
    - returns a typed RetrievalResult on success
    - result.document_id matches intake.document_id
    - works correctly with FakeRetrievalProvider (non-empty)
    - works correctly with FakeRetrievalProvider (empty)
    - provider failure (RetrievalServiceError) propagates through the workflow boundary
    - the returned object is always a RetrievalResult regardless of path

No AWS credentials or live calls required.
"""

import pytest

from app.schemas.intake_models import IntakeRecord, IntakeResult, StorageRegistration
from app.schemas.retrieval_contract import RetrievalProvider
from app.schemas.retrieval_models import RetrievalRequest, RetrievalResult
from app.services.kb_service import RetrievalServiceError
from app.workflows.retrieval_workflow import (
    RetrievalWorkflowError,
    _build_retrieval_request,
    run_retrieval,
)
from tests.fakes.fake_retrieval import FakeRetrievalProvider


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_intake_record(
    document_id: str = "doc-20260404-a1b2c3d4",
    original_filename: str = "warning_letter.txt",
    source_type: str = "FDA",
    submitter_note: str | None = None,
) -> IntakeRecord:
    """Build a minimal IntakeRecord without touching the filesystem."""
    return IntakeRecord(
        document_id=document_id,
        original_filename=original_filename,
        extension=".txt",
        absolute_path=f"/tmp/{original_filename}",
        file_size_bytes=1024,
        intake_timestamp="2026-04-04T00:00:00+00:00",
        source_type=source_type,
        document_date="2026-04-04",
        submitter_note=submitter_note,
    )


@pytest.fixture()
def intake_local_only() -> IntakeResult:
    """IntakeResult with no S3 storage (local-only mode)."""
    record = _make_intake_record()
    return IntakeResult(
        document_id=record.document_id,
        artifact_path="/tmp/outputs/intake/doc-20260404-a1b2c3d4.json",
        record=record,
        storage=None,
    )


@pytest.fixture()
def intake_with_storage() -> IntakeResult:
    """IntakeResult with S3 storage populated."""
    record = _make_intake_record()
    return IntakeResult(
        document_id=record.document_id,
        artifact_path="/tmp/outputs/intake/doc-20260404-a1b2c3d4.json",
        record=record,
        storage=StorageRegistration(
            bucket_name="caseops-bucket",
            source_document_key="documents/doc-20260404-a1b2c3d4/raw/warning_letter.txt",
            intake_artifact_key="artifacts/intake/doc-20260404-a1b2c3d4.json",
        ),
    )


@pytest.fixture()
def intake_with_submitter_note() -> IntakeResult:
    """IntakeResult where the operator supplied a submitter_note."""
    record = _make_intake_record(
        submitter_note="Equipment cleaning violations cited across three production lines."
    )
    return IntakeResult(
        document_id=record.document_id,
        artifact_path="/tmp/outputs/intake/doc-20260404-a1b2c3d4.json",
        record=record,
        storage=None,
    )


# ── translation: document_id ──────────────────────────────────────────────────


def test_translation_document_id_passthrough(intake_local_only: IntakeResult) -> None:
    """document_id must pass through from the intake handoff without modification."""
    request = _build_retrieval_request(intake_local_only)
    assert request.document_id == intake_local_only.document_id


# ── translation: source_type ──────────────────────────────────────────────────


def test_translation_source_type_fda(intake_local_only: IntakeResult) -> None:
    request = _build_retrieval_request(intake_local_only)
    assert request.source_type == "FDA"


def test_translation_source_type_all_valid() -> None:
    """All four valid SourceType values must translate cleanly."""
    for source_type in ("FDA", "CISA", "Incident", "Other"):
        record = _make_intake_record(source_type=source_type)
        intake = IntakeResult(
            document_id=record.document_id,
            artifact_path="/tmp/x.json",
            record=record,
            storage=None,
        )
        request = _build_retrieval_request(intake)
        assert request.source_type == source_type


# ── translation: source_filename ─────────────────────────────────────────────


def test_translation_source_filename_maps_from_original_filename(
    intake_local_only: IntakeResult,
) -> None:
    """source_filename in the request must equal intake.record.original_filename."""
    request = _build_retrieval_request(intake_local_only)
    assert request.source_filename == intake_local_only.record.original_filename


def test_translation_source_filename_preserves_name() -> None:
    record = _make_intake_record(original_filename="cisa_advisory_2026.txt")
    intake = IntakeResult(
        document_id=record.document_id,
        artifact_path="/tmp/x.json",
        record=record,
        storage=None,
    )
    request = _build_retrieval_request(intake)
    assert request.source_filename == "cisa_advisory_2026.txt"


# ── translation: source_document_s3_key ──────────────────────────────────────


def test_translation_s3_key_none_when_no_storage(intake_local_only: IntakeResult) -> None:
    """source_document_s3_key must be None when intake ran in local-only mode."""
    request = _build_retrieval_request(intake_local_only)
    assert request.source_document_s3_key is None


def test_translation_s3_key_populated_when_storage_present(
    intake_with_storage: IntakeResult,
) -> None:
    """source_document_s3_key must be taken from storage.source_document_key."""
    request = _build_retrieval_request(intake_with_storage)
    assert request.source_document_s3_key == (
        intake_with_storage.storage.source_document_key  # type: ignore[union-attr]
    )


def test_translation_s3_key_exact_value(intake_with_storage: IntakeResult) -> None:
    request = _build_retrieval_request(intake_with_storage)
    assert request.source_document_s3_key == (
        "documents/doc-20260404-a1b2c3d4/raw/warning_letter.txt"
    )


# ── translation: query_text ───────────────────────────────────────────────────


def test_translation_query_text_none_when_no_submitter_note(
    intake_local_only: IntakeResult,
) -> None:
    """
    When submitter_note is absent, query_text must be None.

    The provider's built-in fallback (source_type + source_filename) then applies.
    This is intentional for MVP: no query construction happens in the workflow.
    """
    assert intake_local_only.record.submitter_note is None
    request = _build_retrieval_request(intake_local_only)
    assert request.query_text is None


def test_translation_query_text_uses_submitter_note(
    intake_with_submitter_note: IntakeResult,
) -> None:
    """submitter_note is the most informative intake signal and must become query_text."""
    request = _build_retrieval_request(intake_with_submitter_note)
    assert request.query_text == intake_with_submitter_note.record.submitter_note


def test_translation_query_text_exact_content() -> None:
    note = "Batch control failures and incomplete equipment cleaning documentation."
    record = _make_intake_record(submitter_note=note)
    intake = IntakeResult(
        document_id=record.document_id,
        artifact_path="/tmp/x.json",
        record=record,
        storage=None,
    )
    request = _build_retrieval_request(intake)
    assert request.query_text == note


# ── translation: result is a valid RetrievalRequest ──────────────────────────


def test_translation_returns_retrieval_request(intake_local_only: IntakeResult) -> None:
    request = _build_retrieval_request(intake_local_only)
    assert isinstance(request, RetrievalRequest)


# ── workflow: run_retrieval — success path ────────────────────────────────────


def test_run_retrieval_returns_retrieval_result(intake_local_only: IntakeResult) -> None:
    """run_retrieval must always return a typed RetrievalResult."""
    provider = FakeRetrievalProvider()
    result = run_retrieval(intake_local_only, provider)
    assert isinstance(result, RetrievalResult)


def test_run_retrieval_document_id_matches_intake(intake_local_only: IntakeResult) -> None:
    """result.document_id must equal intake.document_id for traceability."""
    provider = FakeRetrievalProvider()
    result = run_retrieval(intake_local_only, provider)
    assert result.document_id == intake_local_only.document_id


def test_run_retrieval_success_status(intake_local_only: IntakeResult) -> None:
    provider = FakeRetrievalProvider()
    result = run_retrieval(intake_local_only, provider)
    assert result.retrieval_status == "success"


def test_run_retrieval_returns_evidence_chunks(intake_local_only: IntakeResult) -> None:
    provider = FakeRetrievalProvider()
    result = run_retrieval(intake_local_only, provider)
    assert result.retrieved_count > 0
    assert len(result.evidence_chunks) == result.retrieved_count


def test_run_retrieval_with_storage(intake_with_storage: IntakeResult) -> None:
    """Workflow works correctly when the intake handoff includes S3 storage."""
    provider = FakeRetrievalProvider()
    result = run_retrieval(intake_with_storage, provider)
    assert isinstance(result, RetrievalResult)
    assert result.document_id == intake_with_storage.document_id


def test_run_retrieval_with_submitter_note(
    intake_with_submitter_note: IntakeResult,
) -> None:
    """Workflow works correctly when submitter_note drives the query."""
    provider = FakeRetrievalProvider()
    result = run_retrieval(intake_with_submitter_note, provider)
    assert isinstance(result, RetrievalResult)


# ── workflow: run_retrieval — empty path ──────────────────────────────────────


def test_run_retrieval_empty_result_is_retrieval_result(
    intake_local_only: IntakeResult,
) -> None:
    """Empty retrieval must return a typed RetrievalResult, never raise."""
    provider = FakeRetrievalProvider(return_empty=True)
    result = run_retrieval(intake_local_only, provider)
    assert isinstance(result, RetrievalResult)


def test_run_retrieval_empty_status(intake_local_only: IntakeResult) -> None:
    provider = FakeRetrievalProvider(return_empty=True)
    result = run_retrieval(intake_local_only, provider)
    assert result.retrieval_status == "empty"


def test_run_retrieval_empty_zero_chunks(intake_local_only: IntakeResult) -> None:
    provider = FakeRetrievalProvider(return_empty=True)
    result = run_retrieval(intake_local_only, provider)
    assert result.evidence_chunks == []
    assert result.retrieved_count == 0


def test_run_retrieval_empty_has_warning(intake_local_only: IntakeResult) -> None:
    """Empty retrieval must carry a non-empty warning for Supervisor routing."""
    provider = FakeRetrievalProvider(return_empty=True)
    result = run_retrieval(intake_local_only, provider)
    assert result.warning is not None
    assert len(result.warning) > 0


def test_run_retrieval_empty_document_id_preserved(intake_local_only: IntakeResult) -> None:
    provider = FakeRetrievalProvider(return_empty=True)
    result = run_retrieval(intake_local_only, provider)
    assert result.document_id == intake_local_only.document_id


# ── workflow: run_retrieval — provider failure ────────────────────────────────


class _FailingProvider:
    """Test double that always raises RetrievalServiceError."""

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        raise RetrievalServiceError("Simulated KB service failure.")


def test_run_retrieval_service_error_propagates(intake_local_only: IntakeResult) -> None:
    """
    RetrievalServiceError from the provider must propagate through the workflow boundary.

    The service layer already abstracts AWS details; re-wrapping would obscure the
    cause chain.  Callers (future Supervisor) catch RetrievalServiceError directly.
    """
    provider = _FailingProvider()
    with pytest.raises(RetrievalServiceError):
        run_retrieval(intake_local_only, provider)  # type: ignore[arg-type]


def test_run_retrieval_service_error_carries_message(intake_local_only: IntakeResult) -> None:
    """The original service error message must be accessible to the caller."""
    provider = _FailingProvider()
    with pytest.raises(RetrievalServiceError, match="Simulated KB service failure"):
        run_retrieval(intake_local_only, provider)  # type: ignore[arg-type]


# ── workflow error: wiring-level failure ─────────────────────────────────────


class _TranslationFailureProvider:
    """Provider whose retrieve() should never be reached in wiring-error tests."""

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:  # pragma: no cover
        raise AssertionError("Provider should not be called when translation fails.")


def test_retrieval_workflow_error_on_bad_translation(monkeypatch) -> None:
    """
    If _build_retrieval_request raises, run_retrieval must wrap the failure in
    RetrievalWorkflowError so callers can distinguish translation errors from
    provider errors.
    """
    import app.workflows.retrieval_workflow as wf_module

    def _always_fails(intake: IntakeResult) -> RetrievalRequest:
        raise ValueError("Simulated translation failure.")

    monkeypatch.setattr(wf_module, "_build_retrieval_request", _always_fails)

    record = _make_intake_record()
    intake = IntakeResult(
        document_id=record.document_id,
        artifact_path="/tmp/x.json",
        record=record,
        storage=None,
    )
    with pytest.raises(RetrievalWorkflowError, match="Failed to build RetrievalRequest"):
        run_retrieval(intake, _TranslationFailureProvider())  # type: ignore[arg-type]


# ── protocol ──────────────────────────────────────────────────────────────────


def test_fake_provider_satisfies_retrieval_provider_protocol() -> None:
    """FakeRetrievalProvider must satisfy the RetrievalProvider Protocol (sanity check)."""
    assert isinstance(FakeRetrievalProvider(), RetrievalProvider)
