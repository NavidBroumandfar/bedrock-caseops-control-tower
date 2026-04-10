"""
H-2 adversarial and edge-case safety evaluation suite.

Loads a curated set of adversarial case fixtures, routes each through the
appropriate H-phase evaluation layer, and produces per-case results plus an
aggregate suite summary.

This module is an evaluation runner — not a runtime integration layer.  It does
not make live AWS calls, does not touch the Converse inference path, and does
not alter the H-0 or H-1 implementations in any way.  It composes them as-is.

Evaluation paths supported:
  "raw"       → evaluate_safety_from_raw()         (H-0 schema-failure path)
  "typed"     → evaluate_safety()                  (H-0 policy evaluator)
  "guardrail" → guardrail_result_to_assessment()   (H-1 Guardrails adapter)

Fixture format (JSON):
  _description        — human-readable case description
  _case_id            — stable unique identifier for this case
  _expected_status    — expected SafetyStatus value ("allow"/"warn"/"escalate"/"block")
  _expected_issue_codes — list of SafetyIssueCode values that must appear in the result
  _evaluation_path    — which evaluation layer to invoke ("raw"/"typed"/"guardrail")
  _retrieval_chunk_count — optional int; passed to H-0 retrieval check (typed/raw paths only)
  _guardrail_result   — optional dict; GuardrailAssessmentResult fields (guardrail path only)
  _document_id        — document_id for the assessment (required for guardrail path)
  input               — CaseOutput-like dict (raw/typed paths) or null (guardrail path)

Public surface:
  DEFAULT_SUITE_DIR       — default path to tests/fixtures/safety_cases/
  SafetyCaseFixture       — in-memory fixture representation (frozen dataclass)
  SafetyCaseResult        — per-case evaluation result (frozen dataclass)
  SafetySuiteSummary      — aggregate suite run summary (frozen dataclass)
  load_safety_fixture()   — load one fixture from a JSON path
  load_safety_suite()     — load all fixtures from the suite directory in stable order
  evaluate_case()         — evaluate one fixture and return a typed result
  run_safety_suite()      — load + evaluate all cases; return results + summary

Separation constraints:
  - This module does not import any AWS service client or boto3 code.
  - This module does not import the CLI, Converse inference, or any live runtime path.
  - It imports only: safety_policy (H-0), guardrails_adapter (H-1), and the schema modules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evaluation.guardrails_adapter import guardrail_result_to_assessment
from app.evaluation.safety_policy import DEFAULT_POLICY, evaluate_safety, evaluate_safety_from_raw
from app.schemas.guardrail_models import GuardrailAssessmentResult
from app.schemas.output_models import CaseOutput
from app.schemas.safety_models import FailurePolicy, SafetyAssessment, SafetyIssueCode, SafetyStatus

# Default suite directory (relative to the repo root, resolved at import time).
_REPO_ROOT: Path = Path(__file__).parent.parent.parent
DEFAULT_SUITE_DIR: Path = _REPO_ROOT / "tests" / "fixtures" / "safety_cases"


# ── Fixture dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SafetyCaseFixture:
    """
    In-memory representation of one adversarial safety case fixture.

    Loaded from a JSON file by load_safety_fixture().

    case_id              — stable unique identifier (matches the _case_id field).
    description          — human-readable description of the adversarial scenario.
    evaluation_path      — "raw", "typed", or "guardrail".
    expected_status      — the SafetyStatus the suite expects for this case.
    expected_issue_codes — SafetyIssueCodes expected to appear in the assessment;
                           the runner checks that all listed codes are present.
    input_data           — the raw input dict (CaseOutput-like or malformed).
                           None for guardrail-path cases.
    retrieval_chunk_count — optional int passed as retrieval context to H-0.
    guardrail_result     — raw dict matching GuardrailAssessmentResult fields;
                           used only by the "guardrail" evaluation path.
    document_id          — document_id for assessment construction;
                           required for the "guardrail" path; defaults to "unknown".
    """

    case_id: str
    description: str
    evaluation_path: str
    expected_status: SafetyStatus
    expected_issue_codes: tuple[SafetyIssueCode, ...]
    input_data: dict[str, Any] | None
    retrieval_chunk_count: int | None = None
    guardrail_result: dict[str, Any] | None = None
    document_id: str = "unknown"


# ── Result dataclasses ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SafetyCaseResult:
    """
    Result of evaluating one adversarial case fixture.

    case_id              — matches the fixture case_id.
    expected_status      — the status the fixture asserted.
    actual_status        — the status produced by the evaluator.
    passed               — True when actual_status == expected_status AND
                           all expected_issue_codes appear in the assessment.
    missing_issue_codes  — codes from expected_issue_codes absent in the result;
                           empty tuple when all expected codes are present.
    assessment           — the full SafetyAssessment produced by the evaluator.
    """

    case_id: str
    expected_status: SafetyStatus
    actual_status: SafetyStatus
    passed: bool
    missing_issue_codes: tuple[SafetyIssueCode, ...]
    assessment: SafetyAssessment


@dataclass(frozen=True)
class SafetySuiteSummary:
    """
    Aggregate results from a full safety suite run.

    total           — total number of cases evaluated.
    passed          — cases where actual_status == expected_status and all
                      expected issue codes were present.
    failed          — total - passed.
    failed_case_ids — tuple of case_ids for every case that did not pass,
                      in stable evaluation order.
    """

    total: int
    passed: int
    failed: int
    failed_case_ids: tuple[str, ...]


# ── Fixture loading ────────────────────────────────────────────────────────────


def load_safety_fixture(path: Path) -> SafetyCaseFixture:
    """
    Load one adversarial case fixture from a JSON file.

    Raises ValueError if required metadata keys are missing.
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    try:
        case_id: str = data["_case_id"]
        evaluation_path: str = data["_evaluation_path"]
        raw_expected_status: str = data["_expected_status"]
    except KeyError as exc:
        raise ValueError(
            f"Fixture file {path.name} is missing required key: {exc}"
        ) from exc

    description: str = data.get("_description", "")
    expected_status = SafetyStatus(raw_expected_status)

    raw_codes: list[str] = data.get("_expected_issue_codes", [])
    expected_issue_codes: tuple[SafetyIssueCode, ...] = tuple(
        SafetyIssueCode(c) for c in raw_codes
    )

    retrieval_chunk_count: int | None = data.get("_retrieval_chunk_count")
    guardrail_result: dict[str, Any] | None = data.get("_guardrail_result")
    document_id: str = str(data.get("_document_id") or "unknown")
    input_data: dict[str, Any] | None = data.get("input")

    return SafetyCaseFixture(
        case_id=case_id,
        description=description,
        evaluation_path=evaluation_path,
        expected_status=expected_status,
        expected_issue_codes=expected_issue_codes,
        input_data=input_data,
        retrieval_chunk_count=retrieval_chunk_count,
        guardrail_result=guardrail_result,
        document_id=document_id,
    )


def load_safety_suite(suite_dir: Path | None = None) -> list[SafetyCaseFixture]:
    """
    Load all adversarial case fixtures from suite_dir in stable alphabetical order.

    Uses DEFAULT_SUITE_DIR when suite_dir is not provided.
    Only files matching the *.json glob are loaded — non-JSON files are ignored.
    """
    if suite_dir is None:
        suite_dir = DEFAULT_SUITE_DIR

    paths = sorted(suite_dir.glob("*.json"))
    return [load_safety_fixture(p) for p in paths]


# ── Case evaluation ────────────────────────────────────────────────────────────


def evaluate_case(
    fixture: SafetyCaseFixture,
    policy: FailurePolicy | None = None,
) -> SafetyCaseResult:
    """
    Evaluate one adversarial case fixture through the appropriate H-phase layer.

    Routing:
      "raw"       → evaluate_safety_from_raw()         (H-0 schema-failure path)
      "typed"     → evaluate_safety()                  (H-0 policy evaluator)
      "guardrail" → guardrail_result_to_assessment()   (H-1 Guardrails adapter)

    The policy parameter is forwarded to H-0 evaluation paths.  It is ignored
    for the "guardrail" path (the adapter has no configurable policy).
    Defaults to DEFAULT_POLICY when not provided.

    Raises ValueError for an unknown evaluation_path.
    """
    resolved_policy = policy if policy is not None else DEFAULT_POLICY

    assessment: SafetyAssessment
    if fixture.evaluation_path == "raw":
        assessment = evaluate_safety_from_raw(
            fixture.input_data,
            policy=resolved_policy,
            retrieval_chunk_count=fixture.retrieval_chunk_count,
        )
    elif fixture.evaluation_path == "typed":
        candidate = CaseOutput.model_validate(fixture.input_data)
        assessment = evaluate_safety(
            candidate,
            policy=resolved_policy,
            retrieval_chunk_count=fixture.retrieval_chunk_count,
        )
    elif fixture.evaluation_path == "guardrail":
        if fixture.guardrail_result is None:
            raise ValueError(
                f"Case '{fixture.case_id}': evaluation_path='guardrail' requires "
                "_guardrail_result to be set in the fixture."
            )
        gr = GuardrailAssessmentResult.model_validate(fixture.guardrail_result)
        assessment = guardrail_result_to_assessment(
            gr, document_id=fixture.document_id
        )
    else:
        raise ValueError(
            f"Case '{fixture.case_id}': unknown evaluation_path "
            f"{fixture.evaluation_path!r}. Expected 'raw', 'typed', or 'guardrail'."
        )

    actual_status = assessment.status
    actual_codes = {issue.issue_code for issue in assessment.issues}

    missing: tuple[SafetyIssueCode, ...] = tuple(
        code for code in fixture.expected_issue_codes if code not in actual_codes
    )

    passed = (actual_status == fixture.expected_status) and not missing

    return SafetyCaseResult(
        case_id=fixture.case_id,
        expected_status=fixture.expected_status,
        actual_status=actual_status,
        passed=passed,
        missing_issue_codes=missing,
        assessment=assessment,
    )


# ── Suite runner ───────────────────────────────────────────────────────────────


def run_safety_suite(
    suite_dir: Path | None = None,
    policy: FailurePolicy | None = None,
) -> tuple[list[SafetyCaseResult], SafetySuiteSummary]:
    """
    Load and evaluate all adversarial case fixtures in the suite.

    Fixtures are loaded in stable alphabetical order from suite_dir (or
    DEFAULT_SUITE_DIR).  Each case is evaluated independently through the
    appropriate H-phase layer.

    Parameters
    ----------
    suite_dir : override the fixture directory; defaults to DEFAULT_SUITE_DIR.
    policy    : FailurePolicy forwarded to H-0 evaluation paths; defaults to
                DEFAULT_POLICY.

    Returns
    -------
    Tuple of:
      - list[SafetyCaseResult]  — one result per case, in load order.
      - SafetySuiteSummary      — aggregate counts and failed case IDs.
    """
    fixtures = load_safety_suite(suite_dir)
    results = [evaluate_case(f, policy=policy) for f in fixtures]

    passed_count = sum(1 for r in results if r.passed)
    failed_ids = tuple(r.case_id for r in results if not r.passed)

    summary = SafetySuiteSummary(
        total=len(results),
        passed=passed_count,
        failed=len(failed_ids),
        failed_case_ids=failed_ids,
    )

    return results, summary
