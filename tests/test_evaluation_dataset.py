"""
F-1 dataset validation tests — reference evaluation dataset integrity.

Validates that every fixture in data/evaluation/ conforms to the F-0 schemas,
that cross-fixture references are internally consistent, and that all referenced
source documents exist on disk.

Coverage:
  Dataset completeness:
    - cases/ directory contains at least one fixture
    - expected/ directory contains at least one fixture
    - case_id set in cases/ matches case_id set in expected/ exactly (1:1)

  EvaluationCase schema (per fixture in cases/):
    - file loads as valid JSON
    - validates against EvaluationCase without error
    - case_id matches the fixture filename stem
    - source_type is a valid pipeline value (FDA / CISA / Incident / Other)
    - document_date is YYYY-MM-DD
    - tags is a list

  ExpectedOutput schema (per fixture in expected/):
    - file loads as valid JSON
    - validates against ExpectedOutput without error
    - case_id matches the fixture filename stem
    - expected_severity is one of Critical / High / Medium / Low
    - expected_category is non-empty
    - expected_summary_facts / expected_recommendation_keywords / forbidden_claims
      are all lists

  RetrievalExpectation (where present in expected/ fixtures):
    - validates against RetrievalExpectation without error
    - case_id matches the parent fixture case_id
    - minimum_expected_chunks >= 1

  Source document references:
    - every source_filename in cases/ maps to a file that exists under
      data/sample_documents/

  Determinism / isolation:
    - fixtures load identically on repeated reads (no runtime state)
    - no fixture contains a field named "aws" or "credentials"

No AWS credentials or live calls required.
No mocks needed.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.evaluation_models import (
    EvaluationCase,
    ExpectedOutput,
    RetrievalExpectation,
)

# ── path constants ─────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent
_EVAL_DIR = _REPO_ROOT / "data" / "evaluation"
_CASES_DIR = _EVAL_DIR / "cases"
_EXPECTED_DIR = _EVAL_DIR / "expected"
_SAMPLE_DOCS_DIR = _REPO_ROOT / "data" / "sample_documents"

_VALID_SOURCE_TYPES = {"FDA", "CISA", "Incident", "Other"}
_VALID_SEVERITIES = {"Critical", "High", "Medium", "Low"}


# ── helpers ────────────────────────────────────────────────────────────────────


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _case_fixtures() -> list[Path]:
    return sorted(_CASES_DIR.glob("*.json"))


def _expected_fixtures() -> list[Path]:
    return sorted(_EXPECTED_DIR.glob("*.json"))


def _case_ids_from_cases() -> set[str]:
    return {p.stem for p in _case_fixtures()}


def _case_ids_from_expected() -> set[str]:
    return {p.stem for p in _expected_fixtures()}


# ── dataset completeness ───────────────────────────────────────────────────────


class TestDatasetCompleteness:
    def test_cases_directory_exists(self):
        assert _CASES_DIR.is_dir(), f"cases/ directory not found: {_CASES_DIR}"

    def test_expected_directory_exists(self):
        assert _EXPECTED_DIR.is_dir(), f"expected/ directory not found: {_EXPECTED_DIR}"

    def test_cases_directory_non_empty(self):
        fixtures = _case_fixtures()
        assert len(fixtures) >= 1, "cases/ must contain at least one JSON fixture"

    def test_expected_directory_non_empty(self):
        fixtures = _expected_fixtures()
        assert len(fixtures) >= 1, "expected/ must contain at least one JSON fixture"

    def test_case_ids_match_between_directories(self):
        case_ids = _case_ids_from_cases()
        expected_ids = _case_ids_from_expected()
        assert case_ids == expected_ids, (
            f"case_id mismatch between cases/ and expected/.\n"
            f"  only in cases/: {case_ids - expected_ids}\n"
            f"  only in expected/: {expected_ids - case_ids}"
        )

    def test_dataset_has_minimum_case_count(self):
        # F-1 spec: 6–8 cases; enforce at least 6
        fixtures = _case_fixtures()
        assert len(fixtures) >= 6, (
            f"F-1 dataset must contain at least 6 cases, found {len(fixtures)}"
        )

    def test_readme_exists(self):
        readme = _EVAL_DIR / "README.md"
        assert readme.is_file(), f"data/evaluation/README.md not found: {readme}"


# ── EvaluationCase fixtures ────────────────────────────────────────────────────


class TestEvaluationCaseFixtures:
    @pytest.fixture(params=_case_fixtures(), ids=lambda p: p.stem)
    def case_fixture(self, request):
        return request.param

    def test_fixture_is_valid_json(self, case_fixture):
        data = _load_json(case_fixture)
        assert isinstance(data, dict)

    def test_fixture_validates_against_evaluation_case(self, case_fixture):
        data = _load_json(case_fixture)
        # strip private annotation keys before validation
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        try:
            EvaluationCase(**clean)
        except ValidationError as exc:
            pytest.fail(
                f"{case_fixture.name} failed EvaluationCase validation:\n{exc}"
            )

    def test_case_id_matches_filename(self, case_fixture):
        data = _load_json(case_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        case = EvaluationCase(**clean)
        assert case.case_id == case_fixture.stem, (
            f"case_id {case.case_id!r} does not match filename stem {case_fixture.stem!r}"
        )

    def test_source_type_is_valid(self, case_fixture):
        data = _load_json(case_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        case = EvaluationCase(**clean)
        assert case.source_type in _VALID_SOURCE_TYPES, (
            f"source_type {case.source_type!r} is not a valid pipeline value; "
            f"expected one of {_VALID_SOURCE_TYPES}"
        )

    def test_document_date_is_iso_format(self, case_fixture):
        from datetime import datetime

        data = _load_json(case_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        case = EvaluationCase(**clean)
        try:
            datetime.strptime(case.document_date, "%Y-%m-%d")
        except ValueError:
            pytest.fail(
                f"document_date {case.document_date!r} is not YYYY-MM-DD"
            )

    def test_tags_is_a_list(self, case_fixture):
        data = _load_json(case_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        case = EvaluationCase(**clean)
        assert isinstance(case.tags, list)

    def test_source_document_exists(self, case_fixture):
        data = _load_json(case_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        case = EvaluationCase(**clean)
        doc_path = _SAMPLE_DOCS_DIR / case.source_filename
        assert doc_path.is_file(), (
            f"source_filename {case.source_filename!r} referenced in {case_fixture.name} "
            f"does not exist at {doc_path}"
        )

    def test_fixture_contains_no_credential_fields(self, case_fixture):
        data = _load_json(case_fixture)
        for key in data:
            assert "aws" not in key.lower(), (
                f"Fixture {case_fixture.name} contains an unexpected 'aws' key: {key!r}"
            )
            assert "credential" not in key.lower(), (
                f"Fixture {case_fixture.name} contains an unexpected 'credential' key: {key!r}"
            )

    def test_fixture_loads_identically_on_repeated_read(self, case_fixture):
        first = _load_json(case_fixture)
        second = _load_json(case_fixture)
        assert first == second, (
            f"Fixture {case_fixture.name} produced different results on repeated reads"
        )


# ── ExpectedOutput fixtures ────────────────────────────────────────────────────


class TestExpectedOutputFixtures:
    @pytest.fixture(params=_expected_fixtures(), ids=lambda p: p.stem)
    def expected_fixture(self, request):
        return request.param

    def test_fixture_is_valid_json(self, expected_fixture):
        data = _load_json(expected_fixture)
        assert isinstance(data, dict)

    def test_fixture_validates_against_expected_output(self, expected_fixture):
        data = _load_json(expected_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        try:
            ExpectedOutput(**clean)
        except ValidationError as exc:
            pytest.fail(
                f"{expected_fixture.name} failed ExpectedOutput validation:\n{exc}"
            )

    def test_case_id_matches_filename(self, expected_fixture):
        data = _load_json(expected_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        output = ExpectedOutput(**clean)
        assert output.case_id == expected_fixture.stem, (
            f"case_id {output.case_id!r} does not match filename stem "
            f"{expected_fixture.stem!r}"
        )

    def test_expected_severity_is_valid(self, expected_fixture):
        data = _load_json(expected_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        output = ExpectedOutput(**clean)
        assert output.expected_severity in _VALID_SEVERITIES, (
            f"expected_severity {output.expected_severity!r} is not valid"
        )

    def test_expected_category_is_non_empty(self, expected_fixture):
        data = _load_json(expected_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        output = ExpectedOutput(**clean)
        assert output.expected_category.strip(), (
            f"expected_category must be non-empty in {expected_fixture.name}"
        )

    def test_list_fields_are_lists(self, expected_fixture):
        data = _load_json(expected_fixture)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        output = ExpectedOutput(**clean)
        assert isinstance(output.expected_summary_facts, list)
        assert isinstance(output.expected_recommendation_keywords, list)
        assert isinstance(output.forbidden_claims, list)

    def test_fixture_contains_no_credential_fields(self, expected_fixture):
        data = _load_json(expected_fixture)
        for key in data:
            assert "aws" not in key.lower(), (
                f"Fixture {expected_fixture.name} contains an unexpected 'aws' key: {key!r}"
            )
            assert "credential" not in key.lower(), (
                f"Fixture {expected_fixture.name} contains an unexpected 'credential' key: "
                f"{key!r}"
            )


# ── RetrievalExpectation (embedded in expected/ fixtures) ──────────────────────


class TestRetrievalExpectationEmbeds:
    """
    Validates inline _retrieval_expectation blocks where present.
    These are stored under the private key '_retrieval_expectation' to avoid
    polluting the ExpectedOutput schema while keeping retrieval contracts
    co-located with the expected output fixture for readability.
    """

    @pytest.fixture(params=_expected_fixtures(), ids=lambda p: p.stem)
    def expected_fixture(self, request):
        return request.param

    def test_retrieval_expectation_validates_if_present(self, expected_fixture):
        data = _load_json(expected_fixture)
        retrieval_block = data.get("_retrieval_expectation")
        if retrieval_block is None:
            pytest.skip(f"No _retrieval_expectation in {expected_fixture.name}")
        clean = {k: v for k, v in retrieval_block.items() if not k.startswith("_")}
        try:
            RetrievalExpectation(**clean)
        except ValidationError as exc:
            pytest.fail(
                f"_retrieval_expectation in {expected_fixture.name} failed validation:\n{exc}"
            )

    def test_retrieval_expectation_case_id_matches_parent(self, expected_fixture):
        data = _load_json(expected_fixture)
        retrieval_block = data.get("_retrieval_expectation")
        if retrieval_block is None:
            pytest.skip(f"No _retrieval_expectation in {expected_fixture.name}")
        parent_case_id = data.get("case_id")
        retrieval_case_id = retrieval_block.get("case_id")
        assert retrieval_case_id == parent_case_id, (
            f"_retrieval_expectation.case_id {retrieval_case_id!r} does not match "
            f"parent case_id {parent_case_id!r} in {expected_fixture.name}"
        )

    def test_retrieval_expectation_minimum_chunks_is_at_least_one(
        self, expected_fixture
    ):
        data = _load_json(expected_fixture)
        retrieval_block = data.get("_retrieval_expectation")
        if retrieval_block is None:
            pytest.skip(f"No _retrieval_expectation in {expected_fixture.name}")
        clean = {k: v for k, v in retrieval_block.items() if not k.startswith("_")}
        expectation = RetrievalExpectation(**clean)
        assert expectation.minimum_expected_chunks >= 1, (
            f"minimum_expected_chunks must be >= 1 in {expected_fixture.name}"
        )


# ── cross-fixture consistency ──────────────────────────────────────────────────


class TestCrossFixtureConsistency:
    def test_every_case_has_a_matching_expected_output(self):
        case_ids = _case_ids_from_cases()
        expected_ids = _case_ids_from_expected()
        missing = case_ids - expected_ids
        assert not missing, (
            f"These case_ids have no matching expected/ fixture: {missing}"
        )

    def test_every_expected_output_has_a_matching_case(self):
        case_ids = _case_ids_from_cases()
        expected_ids = _case_ids_from_expected()
        orphans = expected_ids - case_ids
        assert not orphans, (
            f"These expected/ fixtures have no matching cases/ fixture: {orphans}"
        )

    def test_case_ids_are_unique_within_cases_directory(self):
        seen: set[str] = set()
        for path in _case_fixtures():
            data = _load_json(path)
            case_id = data.get("case_id")
            assert case_id not in seen, (
                f"Duplicate case_id {case_id!r} found in cases/ directory"
            )
            seen.add(case_id)

    def test_escalation_expectation_is_consistent_with_severity(self):
        """
        Cases with Critical severity must require escalation.
        This reflects the pipeline escalation rule: severity == 'Critical' always triggers.
        """
        for case_path in _case_fixtures():
            case_data = _load_json(case_path)
            expected_path = _EXPECTED_DIR / case_path.name
            if not expected_path.exists():
                continue
            expected_data = _load_json(expected_path)

            severity = expected_data.get("expected_severity")
            escalation = expected_data.get("expected_escalation_required")

            if severity == "Critical":
                assert escalation is True, (
                    f"Critical severity must have expected_escalation_required=true "
                    f"in {expected_path.name}"
                )

    def test_no_case_references_a_nonexistent_source_file(self):
        missing_docs: list[str] = []
        for case_path in _case_fixtures():
            data = _load_json(case_path)
            source_filename = data.get("source_filename", "")
            doc_path = _SAMPLE_DOCS_DIR / source_filename
            if not doc_path.is_file():
                missing_docs.append(
                    f"{case_path.name} → {source_filename} (not found at {doc_path})"
                )
        assert not missing_docs, (
            f"The following cases reference missing source documents:\n"
            + "\n".join(f"  {m}" for m in missing_docs)
        )
