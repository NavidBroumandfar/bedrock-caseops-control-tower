# Cursor Context — Bedrock CaseOps Multi-Agent Control Tower

> **Orientation file only.**
> This is a quick-start reference for new Cursor sessions.
> It is NOT the source of truth. For authoritative decisions:
> - Scope and roadmap → `PROJECT_SPEC.md`
> - Technical design → `ARCHITECTURE.md`
> - Public project summary → `README.md`
> - This file → quick orientation, current phase, repo map

---

## How to Start a Session

At the beginning of any major implementation session, read these four files in order before making changes:

1. `README.md` — understand the public-facing purpose and constraints
2. `PROJECT_SPEC.md` — confirm scope, requirements, and what phase you are in
3. `ARCHITECTURE.md` — confirm the technical design before touching any code
4. `docs/CURSOR_CONTEXT.md` (this file) — orient quickly on current state

If there is any conflict between files, `PROJECT_SPEC.md` and `ARCHITECTURE.md` take precedence over this file.

---

## Project Purpose

Build an AWS-native multi-agent pipeline that processes technical and operational documents (FDA letters, CISA advisories, incident reports), retrieves grounded evidence from a Bedrock Knowledge Base, classifies severity/category, recommends actions, validates outputs, and flags escalations. Outputs are structured JSON with full citations.

This is a GitHub portfolio project. It should feel production-style but remain narrow and finishable as an MVP.

---

## Locked AWS Stack

| Service | Role |
|---|---|
| Amazon S3 | Document storage and output archiving |
| Amazon Bedrock | Foundation model inference (Converse API) |
| Amazon Bedrock Knowledge Bases | Managed vector store and retrieval |
| Amazon Bedrock Agents | Agent orchestration and tool use |
| AWS Lambda | Serverless execution |
| Amazon CloudWatch | Logging and observability |

Do not add other AWS services without explicit discussion.

---

## Baseline Agent Architecture (locked)

1. **Supervisor / Planner Agent** — orchestrates pipeline, routes exceptions
2. **Retrieval Agent** — queries Bedrock KB, returns evidence chunks + citations
3. **Analysis Agent** — classifies severity/category, summarizes, recommends (evidence-only)
4. **Validation / Critic Agent** — audits analysis for unsupported claims, assigns confidence score
5. **Tool Executor Agent** — formats CaseOutput, applies escalation logic, archives to S3

---

## Repo Structure

```
bedrock-caseops-control-tower/
├── app/
│   ├── agents/        ← agent classes (Supervisor, Retrieval, Analysis, Validation, ToolExecutor)
│   ├── services/      ← AWS service wrappers (s3, bedrock, kb, cloudwatch)
│   ├── workflows/     ← pipeline orchestration and supervisor routing
│   ├── schemas/       ← Pydantic models (IntakeMetadata, EvidenceChunk, AnalysisOutput, CaseOutput, evaluation_models, etc.)
│   ├── evaluation/    ← offline evaluation harness: loader, scorer, runner (Phase F)
│   └── utils/         ← id generation, logging, config, file helpers
├── notebooks/         ← exploration and prototyping only
├── tests/             ← unit tests; mock AWS, no live calls required
├── data/
│   ├── sample_documents/   ← public-domain test docs (FDA, CISA, synthetic)
│   ├── expected_outputs/   ← reference JSON for pipeline test assertions
│   └── evaluation/         ← curated eval dataset: cases/ + expected/ (Phase F)
├── outputs/           ← runtime output (gitignored)
└── docs/
    └── CURSOR_CONTEXT.md   ← this file
```

---

## MVP Boundaries

**Must include:**
- Document intake (local file → S3)
- Bedrock KB retrieval with citations
- Multi-agent orchestration
- Structured JSON output (CaseOutput schema)
- Severity classification and escalation logic
- CloudWatch logging
- CLI interface
- Unit tests for core logic (no live AWS)

**Must exclude (for now):**
- Frontend or API server
- Authentication / multi-user
- CI/CD pipeline
- Bedrock Guardrails (planned v2)
- Bedrock Evaluations (planned v2)
- Prompt caching / routing (planned v2)
- Bedrock Flows (planned v3)
- Model customization (planned v3)
- Multi-region support

---

## Architecture Constraints

These are design contracts followed across all implementation work. They apply to all phases and must not be violated when extending the system.

- Agents are Python classes with a `run()` method that accepts and returns typed Pydantic models
- Agents do not call AWS clients directly — they call service methods from `app/services/`
- Service modules are thin wrappers; they do not contain business logic
- All structured outputs conform to the `CaseOutput` schema defined in `app/schemas/`
- Citations are first-class: never dropped, never fabricated
- Escalation threshold is config-driven (set in `.env`, read from config)
- All logs are structured JSON with `session_id`, `document_id`, `agent`, `event` fields
- No inline code comments that only describe what the code does; comments explain intent

---

## Current Implementation Phase

**Phase 1 — v1 MVP COMPLETE | Phase F — Evaluation Foundation COMPLETE | Phase G — Retrieval & Output Quality COMPLETE | Phase H-0 — Safety Contracts + Failure Policies COMPLETE | Phase H-1 — Bedrock Guardrails Integration COMPLETE**

> **Live Bedrock validation is pending:** Live AWS Knowledge Base sync is currently blocked by AWS-side Titan Text Embeddings V2 throttling/runtime issues in the target account. All code is implemented correctly; all 1434 unit and evaluation tests pass without live AWS calls. This is an external AWS-side blocker, not a code issue. The Phase F, G, H-0, and H-1 layers are fully independent of this blocker.

The repository is portfolio-ready, test-complete, and demo-friendly for the full MVP and Phase F evaluation scope.

### Completed
- **A-0** — repo foundation, source-of-truth docs, project scaffold
- **A-1** — local document intake pipeline (file validation, metadata validation, `document_id` generation, local intake artifact)
- **A-2** — S3 storage adapter; raw document and intake artifact uploads to S3
- **A-3** — typed intake registration handoff contract (`IntakeRegistration` result returned after intake)
- Real AWS S3 verification completed successfully
- **B-0** — retrieval contracts + evidence schemas (`EvidenceChunk`, `RetrievalRequest`, `RetrievalResult`)
- **B-1** — Bedrock Knowledge Base service wrapper (`kb_service.py`)
- **B-2** — retrieval workflow returning typed `RetrievalResult` with `EvidenceChunk` objects and citations
- **C-0** — analysis output schemas (`AnalysisOutput`, `AnalysisRequest`)
- **C-1** — analysis agent + Bedrock Converse service (`bedrock_service.py`, `analysis_agent.py`)
- **C-2** — validation / critic agent + Bedrock Converse validation path (`validation_agent.py`)
- **D-0** — supervisor / planner workflow
- **D-1** — tool executor + escalation logic
- **D-2** — end-to-end multi-agent orchestration (intake handoff → retrieval → analysis → validation → `CaseOutput`)
- **E-0** — structured logging + CloudWatch integration:
  - `app/utils/logging_utils.py` — `PipelineLogger` (JSON to stdout + local file + CloudWatch); `NoOpLogger`
  - `app/services/cloudwatch_service.py` — thin CloudWatch Logs wrapper; `NoOpCloudWatchEmitter`; `build_cloudwatch_emitter` factory
  - `app/utils/config.py` — `ObservabilityConfig`, `PipelineConfig` from env vars
  - `.env.example` — updated with all `CASEOPS_*` observability variables
  - `pipeline_workflow.py` — instrumented with session_start, intake_handoff_received, escalation_triggered, output_generation_complete, pipeline_failed
  - `supervisor_workflow.py` — instrumented with retrieval_start/complete/empty, analysis_start/complete, validation_start/complete, retry warnings, step failure errors
  - 84 new tests in `test_logging_utils.py` and `test_cloudwatch_service.py`
- **E-1** — CLI end-to-end flow, final JSON output packaging, and S3 output archiving:
  - `app/utils/output_writer.py` — `write_case_output(output, output_dir)` writes `CaseOutput` to `{output_dir}/{document_id}.json`; `OutputWriteError` for filesystem failures
  - `app/services/s3_service.py` — added `upload_case_output(local_path, document_id)` method; uploads to `outputs/{document_id}/case_output.json`; follows existing upload pattern
  - `app/utils/id_utils.py` — added public `generate_session_id()` alongside existing `generate_document_id()`
  - `app/workflows/pipeline_workflow.py` — `run_pipeline()` now accepts an optional `session_id` parameter for CLI-driven session consistency
  - `app/cli.py` — added `run` command: intake → pipeline → local write → S3 archive (if `S3_OUTPUT_BUCKET` set) → operator summary; graceful failure at every step; E-0 logger wired in; non-zero exit on any failure
  - `tests/test_output_writer.py` — 20 tests for local output writing behaviour
  - `tests/test_cli.py` — 35 tests for CLI run command (argument validation, success path, all failure paths, S3 archive paths, logger integration, no live AWS)
  - `tests/test_s3_service.py` — 5 new tests for `upload_case_output` (key format, content, metadata, error handling)
  - 604 total tests passing at E-1 completion
- **E-2** — tests, hardening, sample cases, demo readiness:
  - `tests/test_config.py` — 57 new tests covering all `ObservabilityConfig` and `PipelineConfig` defaults, env var overrides, type correctness, and immutability
  - `tests/test_end_to_end_flow.py` — 34 new tests running the full intake → pipeline → output flow using real sample documents (`data/sample_documents/`) with all AWS mocked
  - `tests/test_cli.py` — 2 new tests verifying `[hint]` messages for pipeline init failure and pipeline runtime failure
  - `app/cli.py` — hardened error messaging: `[hint]` lines added for pipeline initialisation failure (pointing to `BEDROCK_KB_ID`) and for `PipelineWorkflowError` (pointing to AWS credentials and KB setup)
  - `data/expected_outputs/fda_warning_letter_01_expected.json` — reference CaseOutput fixture for FDA warning letter sample
  - `data/expected_outputs/cisa_advisory_01_expected.json` — reference CaseOutput fixture for CISA advisory sample
  - `data/expected_outputs/README.md` — explains fixture format, purpose, and live-AWS status
  - `README.md` — added Demo Flow section with step-by-step instructions for local demo without live AWS; updated status and test count
  - `ARCHITECTURE.md` — updated implementation status to reflect E-2 complete; updated test count
  - 678 total tests pass
- **F-0** — evaluation contracts + scoring schemas (`app/schemas/evaluation_models.py`): typed models for `EvaluationCase`, `EvaluationExpectedOutput`, `EvaluationDimensionScores`, `EvaluationSummary`
- **F-1** — curated reference dataset + expected outputs (`data/evaluation/`): 7 evaluation cases (FDA x2, CISA x2, Incident x2, edge x1) with corresponding reference expected outputs
- **F-2** — offline evaluation harness: `app/evaluation/loader.py` (dataset loader), `app/evaluation/scorer.py` (deterministic dimension scorer), `app/evaluation/runner.py` (batch scoring runner with aggregated summary)
  - 5 new test files: `test_evaluation_schemas.py`, `test_evaluation_dataset.py`, `test_evaluation_loader.py`, `test_evaluation_scorer.py`, `test_evaluation_runner.py`
  - 994 total tests pass
- **G-0** — Retrieval quality metrics:
  - `app/evaluation/retrieval_scorer.py` — three deterministic offline metrics: `minimum_chunks_match`, `source_label_hit_rate`, `required_evidence_term_coverage`; `RetrievalScoringResult` (frozen dataclass); `score_retrieval()` public API
  - `app/evaluation/loader.py` — extended with `load_retrieval_expectations()` to extract `_retrieval_expectation` blocks from F-1 expected fixtures
  - `tests/fixtures/retrieval_outputs/` — 5 candidate retrieval fixtures: `strong_retrieval.json`, `weak_retrieval.json`, `missing_source_labels.json`, `missing_evidence_terms.json`, `empty_retrieval.json`
  - `tests/test_retrieval_scorer.py` — 55 tests covering all three metrics, pass/fail logic, fixture loading, dataset alignment, not-applicable policy, and deterministic repeated-run behavior
  - 1046 total tests pass
- **G-1** — Citation quality checks:
  - `app/schemas/evaluation_models.py` — extended with `CitationExpectation`: minimal new contract (`citations_required`, `expected_source_labels`, `required_excerpt_terms`, `minimum_citation_count`); backward-compatible with all existing fixtures
  - `app/evaluation/citation_scorer.py` — four deterministic offline metrics: `citation_presence`, `citation_source_label_alignment`, `citation_excerpt_evidence_coverage`, `citation_excerpt_nonempty`; `CitationScoringResult` (frozen dataclass); `score_citations()` public API; hard-gate pass/fail rule; fully separate from retrieval scorer
  - `app/evaluation/loader.py` — extended with `load_citation_expectations()` to extract `_citation_expectation` blocks from F-1 expected fixtures
  - `data/evaluation/expected/` — `_citation_expectation` blocks added to eval-fda-001, eval-fda-002, eval-cisa-001, eval-incident-001, eval-edge-001 (5 of 7 fixtures; edge-001 uses `citations_required: false`)
  - `tests/fixtures/citation_outputs/` — 5 candidate CaseOutput fixtures: `strong_citations.json`, `missing_citations.json`, `wrong_source_labels.json`, `empty_excerpts.json`, `no_citations_not_required.json`
  - `tests/test_citation_scorer.py` — 64 tests covering all four metrics, pass/fail logic, fixture loading, dataset alignment, separation from G-0, determinism, and candidate typing
  - 1110 total tests pass
- **G-2** — Output quality scoring:
  - `app/schemas/evaluation_models.py` — extended with `OutputQualityScoringResult`: typed Pydantic result combining `core_case_alignment_score`, `citation_quality_score`, `dimension_scores` (three final-output-only checks), `overall_score`, `pass_fail`, `pass_threshold`, `notes`
  - `app/evaluation/output_quality_scorer.py` — composite scorer: calls F-2 `score_case()` and G-1 `score_citations()`, adds `summary_nonempty`, `recommendations_present_when_expected`, `unsupported_claims_clean` checks; overall score = mean of five components; hard gates on summary_nonempty and unsupported_claims_clean; does NOT import retrieval_scorer (G-0)
  - `tests/fixtures/output_quality_outputs/` — 5 targeted fixtures: `strong_output.json`, `blank_summary.json`, `missing_recommendations.json`, `unsupported_claims_present.json`, `good_core_weak_citations.json`
  - `tests/test_output_quality_scorer.py` — 46 tests covering all dimensions, hard gates, sub-score propagation, overall score formula, pass/fail threshold, fixture integration, architectural separation, and candidate typing
  - 1156 total tests pass
- **H-0** — Safety contracts + failure policies:
  - `app/schemas/safety_models.py` — typed safety contracts: `SafetyIssueSeverity`, `SafetyIssueCode` (7 codes), `IssueSource`, `SafetyStatus` (allow/warn/escalate/block), `SafetyIssue`, `SafetyAssessment`, `FailurePolicy`
  - `app/evaluation/safety_policy.py` — local deterministic safety policy evaluator: `evaluate_safety()` (typed CaseOutput), `evaluate_safety_from_raw()` (unvalidated dict + schema-failure path), `DEFAULT_POLICY`; six policy rules; no AWS imports
  - `tests/test_safety_models.py` — 63 tests covering all enum values, validators, field defaults, model_validator consistency checks, round-trip serialisation
  - `tests/test_safety_policy.py` — 81 tests covering all six rules, status selection, SafetyAssessment output fields, DEFAULT_POLICY, determinism, and architectural separation
  - 1300 total tests pass
- **H-1** — Bedrock Guardrails integration:
  - `app/schemas/guardrail_models.py` — normalized contract: `GuardrailSource` enum, `GuardrailAssessmentResult` Pydantic model
  - `app/services/guardrails_service.py` — thin ApplyGuardrail wrapper: `GuardrailsService.assess_text()`, `GuardrailsServiceError`; injectable client; finding extraction from all AWS sub-policies
  - `app/evaluation/guardrails_adapter.py` — H-1 → H-0 bridge: `guardrail_result_to_issues()`, `guardrail_result_to_assessment()`; maps interventions to blocking `SafetyIssue` with `IssueSource.GUARDRAILS`
  - `app/schemas/safety_models.py` — extended: `GUARDRAILS` added to `IssueSource`; `GUARDRAIL_INTERVENTION` added to `SafetyIssueCode`
  - `app/utils/config.py` — `GuardrailsConfig` + `load_guardrails_config()` added
  - `.env.example` — Guardrails section added
  - `tests/test_guardrail_models.py`, `tests/test_guardrails_service.py`, `tests/test_guardrails_adapter.py` — 134 new tests; no live AWS calls
  - 1434 total tests pass

### Next step
- **Phase H-2** — Adversarial and edge-case evaluation suite; see `PROJECT_SPEC.md §13`

### Phase 2 roadmap

Phase 2 follows the same lettered-subphase naming convention as Phase 1 (A–E):

| Phase | Theme | Status | Subphases |
|---|---|---|---|
| **F** | Evaluation Foundation | ✅ Complete | F-0 evaluation contracts + schemas, F-1 reference dataset, F-2 scoring runner |
| **G** | Retrieval & Output Quality | ✅ Complete | G-0 retrieval metrics ✅, G-1 citation quality ✅, G-2 output scoring ✅ |
| **H** | Safety & Guardrails | In progress | H-0 safety contracts ✅, H-1 Bedrock Guardrails integration ✅, H-2 adversarial suite |
| **I** | Optimization | Not started | I-0 prompt caching, I-1 prompt routing, I-2 baseline vs optimized comparison |
| **J** | Observability & Reporting | Not started | J-0 CloudWatch dashboard, J-1 result artifacts, J-2 v2 hardening checkpoint |

See `PROJECT_SPEC.md §13` for the full Phase 2 subphase breakdown.

### Not yet implemented
- Live Bedrock validation (blocked by AWS-side throttling — not a code issue)
- Phase H-2: Adversarial evaluation suite
- Phase I–J: prompt optimization, CloudWatch reporting

Reference: `ARCHITECTURE.md §5–9` for component flows. `PROJECT_SPEC.md §13` for the full subphase roadmap.

---

## Key Files

| File | Purpose |
|---|---|
| `README.md` | Public-facing project summary + CLI usage instructions |
| `PROJECT_SPEC.md` | Scope, requirements, roadmap — **source of truth** |
| `ARCHITECTURE.md` | Technical design — **source of truth** |
| `docs/CURSOR_CONTEXT.md` | This file — quick orientation only |
| `app/cli.py` | CLI entry point — `intake` and `run` commands |
| `app/schemas/` | Pydantic models — contracts between all components |
| `app/utils/output_writer.py` | Final JSON output packaging utility |
| `app/utils/id_utils.py` | `generate_document_id()`, `generate_session_id()` |
| `.env.example` | Environment variable reference |
