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
│   ├── schemas/       ← Pydantic models (IntakeMetadata, EvidenceChunk, AnalysisOutput, CaseOutput, etc.)
│   └── utils/         ← id generation, logging, config, file helpers
├── notebooks/         ← exploration and prototyping only
├── tests/             ← unit tests; mock AWS, no live calls required
├── data/
│   ├── sample_documents/   ← public-domain test docs (FDA, CISA, synthetic)
│   └── expected_outputs/   ← reference JSON for test assertions
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

**Phase 1 — v1 MVP COMPLETE | Phases A, B, C, D, E-0, E-1, and E-2 complete**

> **Live Bedrock validation is pending:** Live AWS Knowledge Base sync is currently blocked by AWS-side Titan Text Embeddings V2 throttling/runtime issues in the target account. All code is implemented correctly; all 678 unit and integration-style tests pass without live AWS calls. This is an external AWS-side blocker, not a code issue.

The repository is portfolio-ready, test-complete, and demo-friendly for the full MVP engineering scope.

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

### Next step
- Phase 2 (v2: Evaluation and Optimization) — NOT started; see `PROJECT_SPEC.md §13`

### Phase 2 roadmap (not started)

Phase 2 follows the same lettered-subphase naming convention as Phase 1 (A–E):

| Phase | Theme | Subphases |
|---|---|---|
| **F** | Evaluation Foundation | F-0 evaluation contracts + schemas, F-1 reference dataset, F-2 scoring runner |
| **G** | Retrieval & Output Quality | G-0 retrieval metrics, G-1 citation quality, G-2 output scoring |
| **H** | Safety & Guardrails | H-0 safety contracts, H-1 Bedrock Guardrails integration, H-2 adversarial suite |
| **I** | Optimization | I-0 prompt caching, I-1 prompt routing, I-2 baseline vs optimized comparison |
| **J** | Observability & Reporting | J-0 CloudWatch dashboard, J-1 result artifacts, J-2 v2 hardening checkpoint |

See `PROJECT_SPEC.md §13` for the full Phase 2 subphase breakdown.

### Not yet implemented
- Live Bedrock validation (blocked by AWS-side throttling — not a code issue)
- Phase 2: Bedrock Evaluations, Guardrails, prompt caching, evaluation harness (out of scope for MVP)

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
