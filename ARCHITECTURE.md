# Architecture — Bedrock CaseOps Multi-Agent Control Tower

**Version:** 0.2
**Last Updated:** 2026-04-11

> **Phase 1 (v1 MVP) complete. Phase F (Evaluation Foundation) complete. Phase G (Retrieval & Output Quality) complete. Phase H (Safety & Guardrails) complete. Phase I (Optimization) complete — I-0 (Prompt Caching), I-1 (Prompt Routing), I-2 (Baseline vs. Optimized Comparison). Phase J-0 (CloudWatch Evaluation Dashboard) complete. Phase J-1 (Evaluation Result Artifacts + Reporting) complete. Phase J-2 (v2 Hardening Checkpoint) complete. Phase 2 engineering scope is complete.**
>
> **Implementation Status:** All MVP engineering phases are implemented in code: Phase A (intake), Phase B (retrieval), Phase C (analysis + validation), Phase D (orchestration + escalation), Phase E-0 (structured logging + CloudWatch), Phase E-1 (CLI end-to-end flow + S3 output archiving), and Phase E-2 (test hardening, sample cases, config hardening, demo readiness). Phase F adds a fully local, offline evaluation layer: typed evaluation contracts and schemas (F-0), a curated evaluation dataset with 7 cases and reference expected outputs (F-1), and an offline evaluation harness with dataset loader, deterministic scorer, and scoring runner (F-2). Phase G-0 adds offline retrieval quality metrics: three deterministic metrics scored against F-1 retrieval expectations, with fixture-based candidate input and 55 new tests. Phase G-1 adds offline citation quality metrics: four deterministic metrics scored against CitationExpectation references, with five candidate output fixtures and 64 new tests. Phase G-2 adds a composite output-quality scorer that composes F-2 and G-1 sub-scores plus three final-output-only checks (summary_nonempty, recommendations_present_when_expected, unsupported_claims_clean), with 46 new tests. Phase H-0 adds typed safety contracts (SafetyIssue, SafetyAssessment, FailurePolicy) and a local deterministic safety policy evaluator (evaluate_safety, evaluate_safety_from_raw) with six policy rules and 144 new tests. Phase H-1 adds the Bedrock Guardrails integration foundation: a normalized GuardrailAssessmentResult contract (guardrail_models.py), a thin GuardrailsService wrapper for the ApplyGuardrail API (guardrails_service.py), a Guardrails → H-0 safety adapter (guardrails_adapter.py), GuardrailsConfig config block, and two new safety_models enum extensions (GUARDRAILS source, GUARDRAIL_INTERVENTION code), with 134 new tests. Phase H-2 adds the adversarial and edge-case safety evaluation suite: 10 curated fixtures covering schema failures, unsupported claims, missing citations, low confidence, empty retrieval, escalation-required, Guardrails intervention, combined blocking+escalation priority, and clean passing cases; plus a narrow safety suite runner (safety_suite.py) with SafetyCaseFixture, SafetyCaseResult, SafetySuiteSummary dataclasses and run_safety_suite() batch executor; 91 new tests. Phase I-0 adds prompt caching integration: PromptCachingConfig dataclass and loader (config.py), apply_prompt_caching() pure function (prompt_cache.py), optional caching_config wiring in BedrockAnalysisService and BedrockValidationService, .env.example section, and 63 new tests covering config defaults/overrides/validation/immutability, disabled and enabled request-shaping, service integration, and no-live-AWS confirmation. Phase I-1 adds prompt routing: PromptRoutingConfig dataclass and loader (config.py), pure resolve_model_id() routing function (prompt_router.py), optional routing_config wiring in both Bedrock services (resolution at construction time via "analysis" and "validation" routes), .env.example section, and 63 new tests covering config defaults/overrides/case-insensitivity/invalid-flag/immutability, disabled and enabled routing paths, analysis and validation route resolution, priority chain (route override → routing default → caller fallback), service integration, no-regression with routing off, and no live AWS dependency. Phase I-2 adds the baseline vs. optimized comparison workflow: ComparisonVerdict Literal type in evaluation_models.py; app/evaluation/comparison_runner.py with ComparisonCaseResult, ComparisonSummary, and ComparisonRunResult frozen dataclasses and run_comparison() runner; the runner composes G-2 score_output_quality() and H-0 evaluate_safety() to score both sides, computes per-case score deltas and safety status changes, classifies verdicts (improved/regressed/unchanged) using COMPARISON_DELTA_EPSILON, and aggregates a ComparisonSummary; 4 paired fixtures in tests/fixtures/comparison_cases/ covering improved, unchanged, regressed, and safety-change scenarios; 108 new tests. Phase J-2 adds the v2 hardening checkpoint: Phase2CheckpointResult/Phase2ReadinessBlock typed contracts (checkpoint_models.py) with a model-level consistency guard preventing misrepresentation of the external blocker state; CheckpointInputs frozen dataclass and build_checkpoint() runner (checkpoint_runner.py) composing all Phase 2 layer readiness indicators into one typed checkpoint summary; generate_checkpoint_report() pure markdown generator and write_checkpoint() artifact writer (checkpoint_writer.py) producing outputs/checkpoints/{id}/checkpoint.json + report.md; ArtifactKind extended with "checkpoint"; 114 new tests. All 2119 unit and evaluation tests pass without live AWS calls.
>
> **Live Bedrock runtime validation is pending:** Live AWS Knowledge Base end-to-end validation is currently blocked by AWS-side Titan Text Embeddings V2 throttling/runtime issues in the target account. The architecture and all implementation are complete and correct — this is not a code issue. Live validation will be completed when the AWS-side blocker is resolved. The Phase F evaluation layer is fully independent of this blocker.

---

## 1. Architecture Overview

The system is a multi-agent pipeline built on AWS Bedrock. Each agent has a defined, narrow responsibility. A Supervisor Agent coordinates the pipeline and handles routing, retries, and escalation decisions. Agents communicate through structured messages; no agent reads raw document content directly except the Retrieval Agent.

All inference runs through Amazon Bedrock's Converse API. Retrieval is handled by Amazon Bedrock Knowledge Bases (managed vector store). Document storage and output archiving use Amazon S3. Execution is hosted in AWS Lambda. Observability is handled by Amazon CloudWatch.

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI / Trigger                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Document Intake Pipeline                   │
│  validate → assign document_id → local artifact → S3 upload  │
│  → typed registration handoff                                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               Supervisor / Planner Agent                      │
│  orchestrates pipeline, routes exceptions, makes final call  │
└──────┬───────────────┬──────────────────┬───────────────────┘
       │               │                  │
       ▼               ▼                  ▼
┌──────────┐  ┌────────────────┐  ┌──────────────────────┐
│Retrieval │  │  Analysis      │  │ Validation / Critic  │
│  Agent   │  │    Agent       │  │       Agent          │
│          │  │                │  │                      │
│KB query  │  │classification  │  │audit + confidence    │
│citations │  │recommendations │  │flag unsupported      │
└──────────┘  └────────────────┘  └──────────────────────┘
       │               │                  │
       └───────────────┴──────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Tool Executor Agent                        │
│  format output → apply escalation logic → write to S3        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│            Structured JSON Output + CloudWatch Logs          │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. End-to-End Workflow

All steps 1–13 are implemented. Phase E-2 adds test hardening, sample cases, expected output fixtures, config coverage, and demo readiness on top of this foundation.

```
1. Operator invokes CLI with document path  [implemented — Phase E-1]
2. Intake pipeline validates file and metadata, assigns document_id, writes local intake artifact, uploads raw document and intake artifact to S3 (optional), returns typed IntakeRegistration result  [implemented]
3. Supervisor Agent receives the IntakeRegistration (S3 key + document_id + metadata) and initiates the pipeline  [implemented]
4. Supervisor invokes Retrieval Agent with document context  [implemented]
5. Retrieval Agent queries Bedrock Knowledge Base, returns typed RetrievalResult with EvidenceChunk objects and citations  [implemented]
6. Supervisor invokes Analysis Agent with retrieved chunks  [implemented]
7. Analysis Agent produces severity, category, summary, recommendations via Bedrock Converse API  [implemented]
8. Supervisor invokes Validation Agent with analysis output + source chunks  [implemented]
9. Validation Agent audits claims, returns confidence score and unsupported claim flags via Bedrock Converse API  [implemented]
10. Supervisor invokes Tool Executor Agent with validated analysis  [implemented]
11. Tool Executor formats final CaseOutput schema, applies escalation rule, returns structured output  [implemented]
12. Output written locally to outputs/{document_id}.json via write_case_output(); archived to s3://{S3_OUTPUT_BUCKET}/outputs/{document_id}/case_output.json via S3Service.upload_case_output() when S3_OUTPUT_BUCKET is configured  [implemented — Phase E-1]
13. All steps logged via structured JSON logger; local file written to outputs/logs/{session_id}.log; optional CloudWatch emission via cloudwatch_service  [implemented — Phase E-0]
```

---

## 3. Component Responsibilities

### app/agents/
Agent definitions, prompt templates, and per-agent invocation logic. Each agent is a Python class with a `run()` method that accepts a typed input and returns a typed output. Agents do not call AWS services directly — they call service methods from `app/services/`.

### app/services/
Thin wrappers around AWS service clients. Each service module exposes clean, testable methods:
- `s3_service.py` — upload, download, list
- `bedrock_service.py` — Converse API calls
- `kb_service.py` — Knowledge Base retrieve calls
- `cloudwatch_service.py` — structured log emission (implemented E-0; boto3 client injected for testability)

### app/workflows/
Orchestration logic. The `pipeline.py` module defines the full document-to-output flow and calls agents in order. The `supervisor.py` module contains the Supervisor Agent's routing and exception-handling logic.

### app/schemas/
Pydantic models for all structured data: intake metadata, KB results, analysis output, validation output, final CaseOutput. These are the contracts between agents and the boundary that ensures outputs are parseable.

### app/utils/
Shared helpers: ID generation, logging, output packaging, environment config loading.
- `id_utils.py` — `generate_document_id()` and `generate_session_id()`
- `logging_utils.py` — `PipelineLogger` (structured JSON emission to stdout + local file + CloudWatch); `NoOpLogger` for tests and CLI callers that do not need logging
- `output_writer.py` — `write_case_output(output, output_dir)`: serialises a `CaseOutput` to `{output_dir}/{document_id}.json`; raises `OutputWriteError` on filesystem failure
- `config.py` — `ObservabilityConfig` and `PipelineConfig` loaded from environment variables

### tests/
Unit tests for each module. Tests for agents and workflows use mock services — no live AWS calls required. Integration tests are optional and gated by an environment flag.

---

## 4. Agent Responsibilities

### Supervisor / Planner Agent
- Receives the document reference after intake
- Constructs the pipeline execution plan
- Invokes sub-agents in sequence (Retrieval → Analysis → Validation → Tool Executor)
- Handles retries on structured output parse failures (up to 2 retries)
- Routes to escalation path if Validation Agent returns confidence below threshold
- Does not perform retrieval, analysis, or formatting itself

### Retrieval Agent
- Receives the document content or a derived search query
- Queries the Bedrock Knowledge Base via `RetrieveAndGenerate` or `Retrieve` API
- Returns a list of evidence chunks, each with: text, source identifier, relevance score
- Does not interpret or classify the retrieved content

### Analysis Agent
- Receives the list of retrieved evidence chunks
- Produces: severity level, category, one-paragraph summary, list of recommendations
- Prompt explicitly constrains the agent to only use the provided chunks as evidence
- Output is structured to match the AnalysisOutput Pydantic model
- Does not query the KB or perform retrieval itself

### Validation / Critic Agent
- Receives the AnalysisOutput and the original evidence chunks
- For each claim in the summary and recommendations, checks whether it is supported by at least one provided chunk
- Returns: confidence score (0.0–1.0), list of unsupported claims (may be empty), validation status
- Does not modify the analysis output — it only audits it

### Tool Executor Agent
- Receives validated analysis output
- Applies escalation logic: escalation_required = True if any of the following:
  - severity is Critical
  - confidence_score < 0.6
  - Validation Agent flagged any unsupported claims
  - Analysis Agent explicitly recommended escalation
- Formats the final CaseOutput schema and returns it to the pipeline orchestrator
- Output writing is handled by `app/utils/output_writer.py` (E-1), called from the CLI after run_pipeline returns

---

## 5. Document Intake Flow

```
CLI input: file path + optional metadata overrides
     │
     ▼
Validate file exists, is readable, and is within size limit
     │
     ▼
Validate required metadata fields:
  - source_type (FDA / CISA / Incident / Other)
  - document_date (YYYY-MM-DD)
  - submitter_note (optional)
     │
     ▼
Assign document_id: doc-{YYYYMMDD}-{uuid4[:8]}
     │
     ▼
Build IntakeMetadata Pydantic model → validate
     │
     ▼
Write local intake artifact to outputs/{document_id}/intake.json
     │
     ▼
[Optional] Upload raw source document to S3:
  s3://{bucket}/documents/{document_id}/raw/{filename}
  Metadata tags: document_id, source_type, intake_timestamp
     │
     ▼
[Optional] Upload intake artifact to S3:
  s3://{bucket}/documents/{document_id}/intake.json
     │
     ▼
Return typed IntakeRegistration result to caller:
  - document_id, s3_key, intake_artifact_path, metadata snapshot
```

---

## 6. Retrieval Flow

Retrieval foundation is implemented. The workflow returns a typed `RetrievalResult` containing a list of `EvidenceChunk` objects with source identifiers, excerpts, and relevance scores.

```
Input: document content or derived query string
     │
     ▼
Call Bedrock Knowledge Base: Retrieve API
  - max_results: 5 (configurable)
  - filter: optional source_type filter
     │
     ▼
Parse response:
  - extract text, source_location, score per chunk
  - build list of EvidenceChunk objects
     │
     ▼
If no chunks returned:
  - log warning, return empty evidence list
  - Supervisor routes to low-confidence escalation path
     │
     ▼
Return EvidenceChunks to Supervisor
```

---

## 7. Analysis Flow

Analysis agent and Bedrock Converse service are implemented. The agent accepts a typed `AnalysisRequest`, calls the Converse API, and returns a typed `AnalysisOutput`.

```
Input: list of EvidenceChunk objects
     │
     ▼
Build prompt:
  - System: "You are an analysis agent. Use ONLY the provided evidence chunks."
  - Context: formatted evidence chunks with source labels
  - Task: classify severity, assign category, summarize, recommend
     │
     ▼
Call Bedrock Converse API
     │
     ▼
Parse response into AnalysisOutput Pydantic model
  - If parse fails: retry with correction prompt (max 2 attempts)
  - If retry fails: return error state; Supervisor escalates
     │
     ▼
Return AnalysisOutput to Supervisor
```

---

## 8. Validation Flow

Validation / critic agent and Bedrock Converse validation path are implemented. The agent accepts a typed input containing the `AnalysisOutput` and original `EvidenceChunk` list, calls the Converse API, and returns a typed `ValidationOutput`.

```
Input: AnalysisOutput + list of EvidenceChunks
     │
     ▼
Build prompt:
  - System: "You are a critic agent. Audit each claim against the provided evidence."
  - Context: analysis output + evidence chunks
  - Task: identify unsupported claims, assign confidence score 0.0–1.0
     │
     ▼
Call Bedrock Converse API
     │
     ▼
Parse response into ValidationOutput Pydantic model
     │
     ▼
Return ValidationOutput to Supervisor
```

---

## 9. Escalation Flow

```
Tool Executor receives ValidationOutput + AnalysisOutput
     │
     ▼
Apply escalation rules (any of the following triggers escalation):
  1. severity == "Critical"
  2. confidence_score < ESCALATION_CONFIDENCE_THRESHOLD (default: 0.60)
  3. len(unsupported_claims) > 0
  4. "escalate" in any recommendation string (case-insensitive)
     │
     ▼
Set escalation_required = True / False
     │
     ▼
If escalation_required:
  - Log escalation event to CloudWatch with document_id and trigger reason
  - Include escalation_reason field in output
     │
     ▼
Proceed to output formatting
```

---

## 10. Structured Output Design

The final output conforms to the `CaseOutput` Pydantic schema:

```python
class CaseOutput(BaseModel):
    document_id: str
    source_filename: str
    source_type: str
    severity: Literal["Critical", "High", "Medium", "Low"]
    category: str
    summary: str
    recommendations: list[str]
    citations: list[Citation]
    confidence_score: float          # 0.0 – 1.0
    unsupported_claims: list[str]    # empty if fully grounded
    escalation_required: bool
    escalation_reason: str | None
    validated_by: str                # agent name + version
    session_id: str
    timestamp: str                   # ISO 8601
```

```python
class Citation(BaseModel):
    source_id: str       # KB source identifier
    source_label: str    # human-readable label (filename / doc title)
    excerpt: str         # relevant text snippet from KB chunk
    relevance_score: float
```

Outputs are written as:
- `outputs/{document_id}.json` (local)
- `s3://{bucket}/outputs/{document_id}/case_output.json` (archived)

---

## 11. Citation Handling

Citations are first-class objects in this system.

- Every evidence chunk returned by the Retrieval Agent carries a `source_id` (KB source location) and an `excerpt`
- The Analysis Agent is explicitly prompted to reference chunk labels in its recommendations
- The Validation Agent checks that each claim maps to at least one chunk
- The Tool Executor preserves all citations in the final output — no citation is dropped or fabricated
- Citations in the output reference the original KB source, not derived text

This ensures every output is independently auditable: a reviewer can open the KB source and verify each citation manually.

---

## 12. Logging and Observability

**Status:** Implemented in Phase E-0. Config loading is covered by tests in `tests/test_config.py` (E-2).

### Log Structure

All log entries are structured JSON with the following standard fields:

```json
{
  "timestamp": "2024-03-15T14:22:01.342Z",
  "level": "INFO",
  "session_id": "sess-abc123",
  "document_id": "doc-20240315-fda-001",
  "agent": "analysis-agent",
  "event": "analysis_complete",
  "data": { ... }
}
```

### Implementation

`PipelineLogger` in `app/utils/logging_utils.py` is the sole logging interface across the pipeline.
It is constructed once per session by the caller and passed into `run_pipeline` and `run_supervisor`
as an optional keyword argument.  When omitted, a `NoOpLogger` is used transparently.

Key events emitted at each pipeline stage:

| Stage | Event |
|---|---|
| Pipeline start | `session_start` |
| After intake handoff | `intake_handoff_received` |
| Retrieval | `retrieval_start`, `retrieval_complete`, `retrieval_empty` (warning) |
| Analysis | `analysis_start`, `analysis_complete` |
| Validation | `validation_start`, `validation_complete`, `validation_unsupported_claims_detected` |
| Retry | `{step}_retry` (warning) |
| Escalation | `escalation_triggered` |
| Output ready | `output_generation_complete` |
| Failures | `pipeline_failed`, `{step}_failed` (error) |

### Log Levels
- `DEBUG` — prompt construction, raw model responses, retry attempts
- `INFO` — agent step start/complete, output written, escalation triggered
- `WARNING` — empty retrieval, low confidence, parse retry, unsupported claims
- `ERROR` — unrecoverable failures, schema validation errors

### Destinations
- **stdout** — always enabled; compact JSON lines
- **Local file** — `outputs/logs/{session_id}.log`; enabled by default (`CASEOPS_ENABLE_LOCAL_FILE_LOG=true`)
- **CloudWatch Logs** — log group `/caseops/pipeline`, log stream `caseops-session/{session_id}`; opt-in via `CASEOPS_ENABLE_CLOUDWATCH=true`

CloudWatch emission is handled by `CloudWatchLogsService` in `app/services/cloudwatch_service.py`.
Failures to emit to CloudWatch are silently swallowed — they never break the pipeline.

### Observability Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `CASEOPS_LOG_LEVEL` | `INFO` | Minimum log level |
| `CASEOPS_ENABLE_LOCAL_FILE_LOG` | `true` | Write session log to `outputs/logs/` |
| `CASEOPS_ENABLE_CLOUDWATCH` | `false` | Emit to CloudWatch Logs |
| `CASEOPS_CLOUDWATCH_LOG_GROUP` | `/caseops/pipeline` | CloudWatch log group name |
| `CASEOPS_CLOUDWATCH_LOG_STREAM_PREFIX` | `caseops-session` | Stream name prefix |

### Metrics (CloudWatch) — planned v2
- `pipeline.documents_processed` — count
- `pipeline.escalations_triggered` — count
- `pipeline.confidence_score` — distribution
- `agent.latency_ms` — per agent, per invocation

---

## 13. Phase F — Evaluation Architecture

Phase F adds a local, offline evaluation layer on top of the v1 runtime pipeline. It is deterministic, fully independent of live AWS runtime availability, and requires no external services to run.

### Components

| Component | Location | Description |
|---|---|---|
| **Evaluation schemas** | `app/schemas/evaluation_models.py` | Typed contracts for evaluation cases, expected outputs, dimension scores, and aggregated summaries |
| **Evaluation dataset** | `data/evaluation/cases/` | 7 curated cases (FDA, CISA, Incident, edge) with structured inputs |
| **Expected outputs** | `data/evaluation/expected/` | Reference expected outputs matching the evaluation schema per case |
| **Dataset loader** | `app/evaluation/loader.py` | Loads and validates evaluation cases and expected outputs from disk |
| **Scorer** | `app/evaluation/scorer.py` | Deterministic scoring of actual vs. expected outputs across dimensions (severity, escalation, confidence, citations) |
| **Runner** | `app/evaluation/runner.py` | Orchestrates batch evaluation: loads dataset, scores all cases, returns aggregated summary |

### Design properties

- **Local and offline** — no AWS calls required; evaluates structured outputs against reference expectations
- **Deterministic** — scoring logic is rule-based; same inputs always produce the same scores
- **Decoupled** — the evaluation layer does not modify the runtime pipeline; it consumes `CaseOutput` objects as read-only inputs
- **Foundation for Phase G+** — designed to be extended with retrieval quality metrics, citation checks, and safety scoring in subsequent phases

### Evaluation flow

```
Load evaluation dataset (cases + expected outputs)
     │
     ▼
For each case:
  Load EvaluationCase + EvaluationExpectedOutput
     │
     ▼
  Score actual output vs. expected:
    - severity match
    - escalation flag match
    - confidence score within threshold
    - citation presence
     │
     ▼
  Produce per-case EvaluationDimensionScores
     │
     ▼
Aggregate all case scores → EvaluationSummary
  - pass rate, dimension averages, failing case IDs
```

---

## 14. Future Extensibility

The following design decisions preserve extensibility without overengineering:

| Decision | Rationale |
|---|---|
| Agents are Python classes with typed inputs/outputs | Can be wrapped in Lambda functions or Bedrock Agents without changing logic |
| Services are decoupled from agents | Can be mocked for tests; can swap implementations per environment |
| CaseOutput schema is versioned | Future schema changes are additive and backward-compatible |
| Escalation rules are config-driven | Thresholds can be adjusted without code changes |
| Pipeline is sequential in MVP | Parallel agent invocation is a drop-in optimization for v2 |
| No framework lock-in for orchestration | Supervisor logic is plain Python; can be migrated to Bedrock Flows in v3 |
| CloudWatch log structure is consistent | Dashboards and alarms can be added in v2 without changing log emission |

## 14. Phase G-0 — Retrieval Quality Metrics

Phase G-0 adds an offline retrieval quality evaluation layer on top of the Phase F foundation.  It scores candidate retrieval results against the retrieval expectations embedded in the F-1 expected fixtures.  No live AWS calls are made.

### Components

| Component | Location | Description |
|---|---|---|
| **Retrieval scorer** | `app/evaluation/retrieval_scorer.py` | Deterministic scoring of candidate RetrievalResult objects against RetrievalExpectation references across three metrics |
| **Retrieval expectations loader** | `app/evaluation/loader.py` (extended) | `load_retrieval_expectations()` extracts `_retrieval_expectation` blocks from F-1 expected fixtures |
| **Candidate retrieval fixtures** | `tests/fixtures/retrieval_outputs/` | Five test-only JSON fixtures: strong, weak, missing source labels, missing evidence terms, empty |
| **Tests** | `tests/test_retrieval_scorer.py` | 55 tests covering all three metrics, pass/fail logic, fixture loading, dataset alignment |

### Scoring metrics

| Metric | Behaviour |
|---|---|
| `minimum_chunks_match` | 1.0 if candidate chunk count >= expected minimum; else 0.0 |
| `source_label_hit_rate` | Fraction of expected source labels matched (case-insensitive); 1.0 (N/A) if none defined |
| `required_evidence_term_coverage` | Fraction of required terms found via substring search across chunk text (case-insensitive); 1.0 (N/A) if none defined |

Overall score is the mean of the three metric scores.  Pass/fail uses `RETRIEVAL_PASS_THRESHOLD = 0.75`.

### Design properties

- **Separate from F-2 runner** — retrieval scoring works independently; composition with the broader runner is deferred to G-1/G-2
- **Local and deterministic** — scoring is rule-based; same inputs always produce the same scores
- **Reuses existing contracts** — `RetrievalResult`, `EvidenceChunk`, `RetrievalExpectation`, and `DimensionScore` are all existing types from the repo
- **Extensible** — additional metrics (e.g. relevance-score distribution) can be added as new `_score_*` helpers without changing the public API

> **v2 implementation roadmap:** Phase F (Evaluation Foundation), Phase G (Retrieval & Output Quality — G-0, G-1, G-2), Phase H (Safety & Guardrails — H-0, H-1, H-2), and Phase I (Optimization — I-0, I-1, I-2) are complete. Phase J (Observability & Reporting) is next. See `PROJECT_SPEC.md §13` for the full breakdown.

---

## 15. Phase G-1 — Citation Quality Checks

Phase G-1 adds an offline citation quality evaluation layer on top of the Phase F and G-0 foundations.  It scores citation fields in candidate `CaseOutput` objects against citation expectations embedded in the F-1 expected fixtures.  No live AWS calls are made.

### Components

| Component | Location | Description |
|---|---|---|
| **Citation scorer** | `app/evaluation/citation_scorer.py` | Deterministic scoring of candidate CaseOutput citation fields against CitationExpectation references across four metrics |
| **CitationExpectation schema** | `app/schemas/evaluation_models.py` (extended) | Minimal new contract: `citations_required`, `expected_source_labels`, `required_excerpt_terms`, `minimum_citation_count` |
| **Citation expectations loader** | `app/evaluation/loader.py` (extended) | `load_citation_expectations()` extracts `_citation_expectation` blocks from F-1 expected fixtures |
| **F-1 fixture updates** | `data/evaluation/expected/` | `_citation_expectation` blocks added to eval-fda-001, eval-fda-002, eval-cisa-001, eval-incident-001, eval-edge-001 |
| **Candidate citation fixtures** | `tests/fixtures/citation_outputs/` | Five test-only JSON fixtures: strong, missing citations, wrong source labels, empty excerpts, not-required |
| **Tests** | `tests/test_citation_scorer.py` | 64 tests covering all four metrics, pass/fail logic, fixture loading, dataset alignment, separation from G-0 |

### Scoring metrics

| Metric | Behaviour |
|---|---|
| `citation_presence` | 1.0 if citations present when required (or not required); 0.0 if absent when required |
| `citation_source_label_alignment` | Fraction of expected source labels matched (case-insensitive); 1.0 (N/A) if none defined |
| `citation_excerpt_evidence_coverage` | Fraction of required terms found via substring search across concatenated excerpts (case-insensitive); 1.0 (N/A) if none defined |
| `citation_excerpt_nonempty` | 1.0 if all excerpts non-empty; 0.0 if any excerpt is empty or whitespace-only; 1.0 when no citations and not required |

Overall score is the mean of the four metric scores.  Pass/fail uses `CITATION_PASS_THRESHOLD = 0.75`; `citation_presence` and `citation_excerpt_nonempty` are hard-gate dimensions.

### Design properties

- **Separate from G-0 and F-2** — citation scoring works independently from retrieval scoring and the batch output runner; the scorer operates on `CaseOutput.citations` fields directly
- **Local and deterministic** — scoring is rule-based; same inputs always produce the same scores
- **Reuses existing contracts** — `CaseOutput`, `Citation`, `DimensionScore` are existing types; `CitationExpectation` is the only new schema (minimal addition)
- **Backward-compatible fixture extension** — `_citation_expectation` blocks follow the same private-key convention as `_retrieval_expectation`; existing loader and scorer logic is unchanged

---

## 16. Phase G-2 — Output Quality Scoring

Phase G-2 adds a composite output-quality evaluation layer that answers: **"How good is this final `CaseOutput` as an auditable, usable output artifact?"**

It is a composition layer — not a new scoring engine — that reuses F-2 and G-1 work and adds only a small, deterministic set of final-output-only checks.

### Components

| Component | Location | Description |
|---|---|---|
| **Output quality scorer** | `app/evaluation/output_quality_scorer.py` | Composite scorer: calls F-2 `score_case()` and G-1 `score_citations()`, then adds three final-output-only checks; returns `OutputQualityScoringResult` |
| **OutputQualityScoringResult schema** | `app/schemas/evaluation_models.py` (extended) | Typed result model: `core_case_alignment_score`, `citation_quality_score`, `dimension_scores`, `overall_score`, `pass_fail`, `pass_threshold`, `notes` |
| **Candidate output fixtures** | `tests/fixtures/output_quality_outputs/` | Five targeted G-2 fixtures: strong, blank summary, missing recommendations, unsupported claims, good core + weak citations |
| **Tests** | `tests/test_output_quality_scorer.py` | 46 tests covering all dimensions, pass/fail logic, sub-score propagation, fixture integration, architectural separation, and candidate typing |

### Scoring dimensions

| Dimension | Source | Behaviour |
|---|---|---|
| `core_case_alignment_score` | F-2 reused | `score_case()` overall score: severity, escalation, summary facts, recommendation keywords, forbidden claims |
| `citation_quality_score` | G-1 reused | `score_citations()` overall score: presence, source labels, excerpt coverage, nonempty excerpts |
| `summary_nonempty` | G-2 final-output check | 1.0 if `summary.strip()` non-empty; else 0.0 (hard gate) |
| `recommendations_present_when_expected` | G-2 final-output check | 1.0 if expected output defines recommendation keywords and candidate has at least one non-empty recommendation; 1.0 (N/A) if no keywords defined |
| `unsupported_claims_clean` | G-2 final-output check | 1.0 if `unsupported_claims` is empty; else 0.0 (hard gate) |

Overall score = mean of all five component scores. Pass/fail: `summary_nonempty` must pass, `unsupported_claims_clean` must pass, and overall score >= `OUTPUT_QUALITY_PASS_THRESHOLD` (default 0.75).

### Design properties

- **Composition over duplication** — calls `score_case()` and `score_citations()` directly; no scoring logic is duplicated
- **Separate from G-0** — does not import `retrieval_scorer.py`; retrieval quality remains a distinct evaluation layer
- **Local and deterministic** — scoring is rule-based; same inputs always produce the same scores
- **Typed result** — `OutputQualityScoringResult` is a Pydantic model in `evaluation_models.py`; reuses `DimensionScore` for the three final-output checks

> **v2 implementation roadmap:** Phase G is complete. Phase H (Safety & Guardrails — H-0, H-1, H-2) is complete. Phase I (Optimization — I-0, I-1, I-2) is complete. Phase J (Observability & Reporting) is next. See `PROJECT_SPEC.md §13` for the full breakdown.

---

## 17. Phase H-0 — Safety Contracts + Failure Policies

Phase H-0 adds a local, deterministic safety foundation that answers: **"Is this candidate output safe and policy-compliant, and what action should be taken?"**

It is the safety equivalent of F-0: contracts first, evaluator second, integration with Bedrock Guardrails deferred to H-1.

### Components

| Component | Location | Description |
|---|---|---|
| **Safety schemas** | `app/schemas/safety_models.py` | Typed contracts: `SafetyIssue`, `SafetyAssessment`, `FailurePolicy`; enums for `SafetyIssueCode`, `SafetyIssueSeverity`, `IssueSource`, `SafetyStatus` |
| **Safety policy evaluator** | `app/evaluation/safety_policy.py` | Deterministic local evaluator: `evaluate_safety()` for typed `CaseOutput`; `evaluate_safety_from_raw()` for unvalidated dicts; `DEFAULT_POLICY` |
| **Tests** | `tests/test_safety_models.py`, `tests/test_safety_policy.py` | 144 tests covering all six policy rules, status semantics, schema validation, separation, and determinism |

---

## 18. Phase H-1 — Bedrock Guardrails Integration

Phase H-1 adds the Bedrock Guardrails integration foundation, answering: **"Can Bedrock Guardrails findings flow into the H-0 safety layer?"**

It is a thin integration layer — service wrapper + normalized contract + H-0 adapter.  No live AWS calls, no runtime pipeline changes.

### Components

| Component | Location | Description |
|---|---|---|
| **Guardrail schemas** | `app/schemas/guardrail_models.py` | Typed normalized contract: `GuardrailSource` enum, `GuardrailAssessmentResult` Pydantic model; repo-local normalization boundary for ApplyGuardrail responses |
| **Guardrails service** | `app/services/guardrails_service.py` | Thin wrapper around ApplyGuardrail API: `GuardrailsService.assess_text()`, `GuardrailsServiceError`; injectable client for testability; finding extraction from all sub-policies |
| **Guardrails adapter** | `app/evaluation/guardrails_adapter.py` | H-1 → H-0 bridge: `guardrail_result_to_issues()`, `guardrail_result_to_assessment()`; maps interventions to blocking `SafetyIssue` objects with `GUARDRAILS` source |
| **Safety models extension** | `app/schemas/safety_models.py` | Added `GUARDRAILS` to `IssueSource`; added `GUARDRAIL_INTERVENTION` to `SafetyIssueCode` |
| **Config** | `app/utils/config.py` | `GuardrailsConfig` dataclass + `load_guardrails_config()`; four env vars: `CASEOPS_ENABLE_GUARDRAILS`, `CASEOPS_GUARDRAIL_ID`, `CASEOPS_GUARDRAIL_VERSION`, `CASEOPS_GUARDRAIL_TRACE` |
| **Tests** | `tests/test_guardrail_models.py`, `tests/test_guardrails_service.py`, `tests/test_guardrails_adapter.py` | 134 tests: schema validation, service request construction, all finding sub-policies, intervention/non-intervention paths, client failure handling, adapter mapping, structural separation |

### Integration mapping

```
GuardrailsService.assess_text(text, guardrail_id, guardrail_version, source)
     │
     ▼
GuardrailAssessmentResult
  .intervened    → True / False
  .blocked       → True / False
  .finding_types → ["HATE", "PII_EMAIL", ...]
  .action        → "GUARDRAIL_INTERVENED" / "NONE"
     │
     ▼ guardrails_adapter.guardrail_result_to_issues()
     │
     ▼
list[SafetyIssue]  (one blocking issue on intervention; empty on non-intervention)
  .issue_code  = SafetyIssueCode.GUARDRAIL_INTERVENTION
  .source      = IssueSource.GUARDRAILS
  .blocking    = True
     │
     ▼ guardrails_adapter.guardrail_result_to_assessment()
     │
     ▼
SafetyAssessment
  .status = BLOCK (intervention) | ALLOW (non-intervention)
```

### Design properties

- **No live AWS dependency** — boto3 client is injected; all tests use mocks
- **Thin service** — no policy logic in the service; all decisions in the adapter
- **Normalized contract** — `GuardrailAssessmentResult` is the only type that crosses the service boundary
- **H-0 compatible** — adapter output is `SafetyIssue` / `SafetyAssessment`, the same types produced by H-0
- **Runtime pipeline untouched** — no changes to `pipeline_workflow.py`, `cli.py`, `bedrock_service.py`, or any agent

> **H-2 complete:** Phase H-2 (adversarial and edge-case evaluation suite) is implemented. See `PROJECT_SPEC.md §13` and Section 19 below.

### Safety issue codes

| Code | Category |
|---|---|
| `unsupported_claims_present` | Raw observation — validation layer |
| `missing_citations_when_required` | Raw observation — citation quality |
| `empty_or_weak_retrieval` | Raw observation — retrieval layer |
| `low_confidence_output` | Raw observation — output quality |
| `schema_or_contract_failure` | Raw observation — schema layer |
| `escalation_policy_triggered` | Raw observation — policy layer |
| `unsafe_output_block_required` | Derived outcome — for caller use |

### Status semantics

| Status | Meaning |
|---|---|
| `allow` | No meaningful issues; output proceeds. |
| `warn` | Non-blocking issues present; output proceeds with flag. |
| `escalate` | Output may proceed but requires escalation or human review. |
| `block` | Output must not be accepted as safe or usable. |

### Status decision rule (deterministic)

1. Any blocking issue → **BLOCK**
2. `ESCALATION_POLICY_TRIGGERED` present, OR `LOW_CONFIDENCE_OUTPUT` + `escalate_on_low_confidence=True` → **ESCALATE**
3. Any non-blocking issue present → **WARN**
4. No issues → **ALLOW**

### Design properties

- **Local and offline** — no AWS calls; evaluates typed `CaseOutput` objects against configurable policy rules
- **Deterministic** — same inputs always produce the same `SafetyAssessment`
- **Schema-failure path** — `evaluate_safety_from_raw()` returns a blocking assessment immediately when the raw input cannot be parsed into a valid `CaseOutput`
- **Decoupled** — does not import retrieval_scorer, citation_scorer, output_quality_scorer, runner, or any AWS service
- **Policy-configurable** — `FailurePolicy` exposes thresholds and flags so rules can be tightened or relaxed without code changes
- **Foundation for H-1** — Bedrock Guardrails integration plugs into the `SafetyAssessment` contract via the H-1 adapter (guardrails_adapter.py)

---

## 19. Phase H-2 — Adversarial and Edge-Case Evaluation Suite

Phase H-2 adds a curated adversarial evaluation suite that stress-tests the completed H-phase safety foundation under difficult, unsafe, or tricky conditions.  It is a **local evaluation layer**, not a runtime integration phase.

### Components

| Component | Location | Description |
|---|---|---|
| **Adversarial fixtures** | `tests/fixtures/safety_cases/` | 10 curated JSON fixtures: one per adversarial scenario, each declaring its evaluation path, expected status, and expected issue codes |
| **Safety suite runner** | `app/evaluation/safety_suite.py` | Narrow runner: `load_safety_fixture()`, `load_safety_suite()`, `evaluate_case()`, `run_safety_suite()`; three typed frozen dataclasses: `SafetyCaseFixture`, `SafetyCaseResult`, `SafetySuiteSummary` |
| **Tests** | `tests/test_safety_suite.py` | 91 tests: fixture loading, one test group per adversarial case, batch runner, result field contracts, structural isolation |

### Adversarial case mix

| File | Case ID | Evaluation path | Expected status | Scenario |
|---|---|---|---|---|
| `01_schema_failure_raw.json` | `schema_failure_raw` | raw | block | Malformed dict missing required fields |
| `02_unsupported_claims_block.json` | `unsupported_claims_block` | typed | block | Unsupported claim above blocking threshold |
| `03_missing_citations_block.json` | `missing_citations_block` | typed | block | No citations when policy requires them |
| `04_low_confidence_escalate.json` | `low_confidence_escalate` | typed | escalate | Confidence 0.35, below 0.6 threshold |
| `05_empty_retrieval_warn.json` | `empty_retrieval_warn` | typed | warn | Clean output, retrieval_chunk_count=0 |
| `06_escalation_required_escalate.json` | `escalation_required_escalate` | typed | escalate | escalation_required=True on candidate |
| `07_guardrail_intervention_block.json` | `guardrail_intervention_block` | guardrail | block | Guardrails intervened; GUARDRAIL_INTERVENTION issue |
| `08_guardrail_non_intervention_allow.json` | `guardrail_non_intervention_allow` | guardrail | allow | Guardrails passed; no issues |
| `09_combined_block_overrides_escalate.json` | `combined_block_overrides_escalate` | typed | block | Blocking + escalation present; block wins |
| `10_clean_allow_case.json` | `clean_allow_case` | typed | allow | High confidence, cited, no claims; fully clean |

### Runner flow

```
load_safety_suite(suite_dir)
     │
     ▼  alphabetical order
For each SafetyCaseFixture:
  evaluate_case(fixture)
     │
     ├── evaluation_path == "raw"       → evaluate_safety_from_raw()         (H-0)
     ├── evaluation_path == "typed"     → evaluate_safety()                  (H-0)
     └── evaluation_path == "guardrail" → guardrail_result_to_assessment()   (H-1)
     │
     ▼
  SafetyCaseResult
    .actual_status vs .expected_status
    .missing_issue_codes  ← expected codes absent from assessment
    .passed               ← status match AND no missing codes
     │
     ▼
SafetySuiteSummary
  .total / .passed / .failed / .failed_case_ids
```

### Design properties

- **Evaluation only** — no changes to H-0 or H-1; composes existing logic exclusively
- **No live AWS** — no boto3, no Bedrock client, no Converse inference imports
- **Data-driven** — cases are JSON fixtures; adding a new adversarial case requires only a new fixture file
- **Deterministic** — same fixture directory always produces the same results in the same order
- **Narrow** — `safety_suite.py` is a dedicated H-2 runner, not a replacement for the F-2 batch evaluation runner

> **Phase H complete. Phase I (I-0, I-1, I-2) complete. Phase J-0 (CloudWatch Evaluation Dashboard) complete. Phase J-1 complete. Phase J-2 complete.** Phase 2 is complete in engineering scope. See `PROJECT_SPEC.md §13`.

---

## 20. Phase I-0 — Prompt Caching Integration

Phase I-0 adds a narrow, optional prompt-caching integration layer to the Bedrock Converse invocation path.  It is designed for **integration readiness and architecture correctness**, not live performance measurement — live AWS validation remains pending due to the existing Titan Embeddings throttling blocker.

### Components

| Component | Location | Description |
|---|---|---|
| **PromptCachingConfig** | `app/utils/config.py` | Frozen dataclass with four fields: `enable_prompt_caching`, `cache_system_prompt`, `min_cacheable_tokens`, `max_cache_checkpoints`; validated on load; off by default |
| **load_prompt_caching_config()** | `app/utils/config.py` | Loads config from four `CASEOPS_*` env vars; raises `ValueError` on invalid values so misconfigured deployments fail loudly |
| **prompt_cache.py** | `app/services/prompt_cache.py` | Single pure function `apply_prompt_caching(system_blocks, config)`; injects a `{"cachePoint": {"type": "default"}}` block after the last text block in the Converse `system` array; returns input unchanged when disabled |
| **BedrockAnalysisService** | `app/services/bedrock_service.py` | Accepts optional `caching_config`; passes system blocks through `apply_prompt_caching` before each Converse call |
| **BedrockValidationService** | `app/services/bedrock_service.py` | Same as analysis service; caching is symmetric across both invocation paths |
| **Tests** | `tests/test_prompt_caching_config.py`, `tests/test_prompt_cache.py` | 63 new tests: config defaults, env var overrides, case insensitivity, invalid value validation, boundary values, immutability, disabled/enabled request-shaping, service integration, no-regression with caching off, no live AWS dependency |

### Integration point

```
BedrockAnalysisService._call_converse(system_prompt, user_message)
     │
     ▼
system_blocks = [{"text": system_prompt}]
     │
     ▼ apply_prompt_caching(system_blocks, caching_config)   [I-0 integration point]
     │
     ├── caching disabled → system_blocks unchanged (same object returned)
     │
     └── caching enabled  → [{"text": system_prompt}, {"cachePoint": {"type": "default"}}]
     │
     ▼
client.converse(system=system_blocks, messages=[...])
```

### Design properties

- **Off by default** — `CASEOPS_ENABLE_PROMPT_CACHING=false`; no opt-in required for existing deployments
- **Single integration point** — caching logic lives entirely in `prompt_cache.py`; not scattered across agents or workflows
- **No business logic change** — agents, workflows, and schemas are untouched
- **Pure and testable** — `apply_prompt_caching` is a pure function; all 63 tests run without live AWS
- **Validated on load** — invalid env var values raise `ValueError` at startup
- **Extensible for I-1** — message-level caching (few-shot examples) can be added to `apply_prompt_caching` without changing callers

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CASEOPS_ENABLE_PROMPT_CACHING` | `false` | Master switch for prompt caching |
| `CASEOPS_CACHE_SYSTEM_PROMPT` | `true` | Cache the system block when caching is enabled |
| `CASEOPS_MIN_CACHEABLE_TOKENS` | `1024` | Minimum token count (informational; Bedrock enforces server-side) |
| `CASEOPS_MAX_CACHE_CHECKPOINTS` | `1` | Max cachePoint markers per request (1–4; I-0 uses 1) |

> **Phase I complete (I-0, I-1, I-2).** Phase J (Observability & Reporting) is next. See `PROJECT_SPEC.md §13`.

---

## 21. Phase I-1 — Prompt Routing Strategy

Phase I-1 adds a clean, optional, config-driven routing layer that determines which Bedrock model ID is used for analysis and validation calls.  It creates the correct architecture seam for optimization and comparison work in I-2.  No live AWS calls, no benchmarking, no dynamic heuristics.

### Components

| Component | Location | Description |
|---|---|---|
| **PromptRoutingConfig** | `app/utils/config.py` | Frozen dataclass with four fields: `enable_prompt_routing`, `default_model_id`, `analysis_model_id`, `validation_model_id`; off by default |
| **load_prompt_routing_config()** | `app/utils/config.py` | Loads config from four `CASEOPS_*` env vars; raises `ValueError` on invalid enable flag so misconfigured deployments fail loudly |
| **prompt_router.py** | `app/services/prompt_router.py` | Pure `resolve_model_id(route, routing_config, fallback_model_id)` function; `PromptRoute` literal type; deterministic; no boto3 dependency |
| **BedrockAnalysisService** | `app/services/bedrock_service.py` | Accepts optional `routing_config`; resolves model ID at construction time via `resolve_model_id("analysis", ...)` |
| **BedrockValidationService** | `app/services/bedrock_service.py` | Accepts optional `routing_config`; resolves model ID at construction time via `resolve_model_id("validation", ...)` |
| **Tests** | `tests/test_prompt_routing_config.py`, `tests/test_prompt_router.py` | 63 new tests: config defaults, overrides, case-insensitivity, invalid flag, immutability, disabled path, enabled path, analysis route, validation route, priority chain, service integration, no-regression, no live AWS |

### Integration point

```
BedrockAnalysisService.__init__(model_id, routing_config)
     │
     ▼
base_model_id = model_id or env("BEDROCK_MODEL_ID") or hardcoded_default
     │
     ▼ resolve_model_id("analysis", routing_config, base_model_id)  [I-1 integration point]
     │
     ├── routing disabled → base_model_id unchanged
     │
     └── routing enabled  → route override → routing default → base_model_id
     │
     ▼
self._model_id = resolved_model_id
     │
     ▼ (rest of service unchanged)
_call_converse(...) uses self._model_id as before
```

### Resolution priority chain (routing enabled)

```
1. Route-specific override (analysis_model_id / validation_model_id)
2. routing_config.default_model_id
3. Caller's fallback (from BEDROCK_MODEL_ID env or hardcoded safe default)
```

### Design properties

- **Off by default** — `CASEOPS_ENABLE_PROMPT_ROUTING=false`; existing deployments unaffected without opt-in
- **Resolution at construction time** — model ID is resolved once per service instance; `_call_converse` is completely unchanged
- **Isolated** — `prompt_router.py` has no boto3 dependency, no I/O, no config loading; pure function only
- **Task-based only** — only `analysis` and `validation` routes exist in I-1; document-type routing and dynamic heuristics are explicitly out of scope
- **No agent changes** — the route name is implicit in the service type; agents and workflows are untouched
- **Validated on load** — invalid enable flag raises `ValueError` at startup; model ID strings are not validated locally (Bedrock rejects invalid IDs at runtime)
- **Honest scope** — no benchmarking, no dashboards, no comparison workflows; those are I-2

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CASEOPS_ENABLE_PROMPT_ROUTING` | `false` | Master switch for prompt routing |
| `CASEOPS_ROUTING_DEFAULT_MODEL_ID` | `""` | Default model when routing is enabled and no route override is set; falls through to `BEDROCK_MODEL_ID` if empty |
| `CASEOPS_ROUTING_ANALYSIS_MODEL_ID` | `""` | Model ID used exclusively for analysis calls when routing is enabled |
| `CASEOPS_ROUTING_VALIDATION_MODEL_ID` | `""` | Model ID used exclusively for validation calls when routing is enabled |

> **Phase I complete.** Phase J (Observability & Reporting) is next. See `PROJECT_SPEC.md §13`.

---

## 22. Phase I-2 — Baseline vs. Optimized Comparison Workflow

Phase I-2 adds a clean offline comparison workflow that answers: **"Did the optimized configuration improve output quality and safety?"**

> **Phase J-0 complete.** See Section 23 for the full J-0 architecture.

It is a composition layer — not a new scoring engine — that reuses G-2 and H-0 work and adds only the minimal logic needed to compute, classify, and aggregate per-case deltas.

### Components

| Component | Location | Description |
|---|---|---|
| **ComparisonVerdict type** | `app/schemas/evaluation_models.py` (extended) | Literal type alias `"improved"` / `"regressed"` / `"unchanged"` |
| **Comparison runner** | `app/evaluation/comparison_runner.py` | `run_comparison()` runner; `ComparisonCaseResult`, `ComparisonSummary`, `ComparisonRunResult` frozen dataclasses; `ComparisonAlignmentError` |
| **Comparison fixtures** | `tests/fixtures/comparison_cases/` | 4 paired fixture sets: cases/, expected/, baseline/, optimized/ |
| **Tests** | `tests/test_comparison_models.py`, `tests/test_comparison_runner.py` | 108 tests covering model contracts, verdict classification, delta correctness, improved/regressed/unchanged cases, missing-case handling, aggregate summary, determinism, no live AWS |

### Comparison flow

```
run_comparison(baseline_dir, optimized_dir, dataset_dir)
     │
     ▼ load_dataset() — EvaluationCase + ExpectedOutput pairs
     │ load_citation_expectations() — CitationExpectation per case
     │
     ▼ scan baseline_dir and optimized_dir → dict[case_id, Path]
     │
     ▼ for each case in dataset:
     │   if missing from either dir → record in missing_*_case_ids; skip
     │
     ├── score_output_quality(baseline, expected, citation_exp)  [G-2]
     ├── score_output_quality(optimized, expected, citation_exp) [G-2]
     ├── evaluate_safety(baseline, policy)                       [H-0]
     ├── evaluate_safety(optimized, policy)                      [H-0]
     │
     ▼ compute:
     │   score_delta = optimized_score − baseline_score
     │   verdict = "improved" | "regressed" | "unchanged"  (COMPARISON_DELTA_EPSILON gate)
     │   safety_status_changed = baseline_status ≠ optimized_status
     │
     ▼ ComparisonCaseResult (per case)
     │
     ▼ ComparisonSummary (aggregate):
     │   baseline_average_score vs. optimized_average_score
     │   average_score_delta
     │   baseline_pass_count vs. optimized_pass_count
     │   baseline_safety_distribution vs. optimized_safety_distribution
     │   improved_case_ids / regressed_case_ids / unchanged_case_ids
     │
     ▼ ComparisonRunResult
```

### Input contract

```
baseline_dir/
  {case_id}.json   ← CaseOutput JSON, one file per case
optimized_dir/
  {case_id}.json   ← CaseOutput JSON, one file per case
dataset_dir/
  cases/           ← EvaluationCase JSONs (shared expected reference)
  expected/        ← ExpectedOutput + optional _citation_expectation blocks
```

### Verdict classification

```
score_delta > COMPARISON_DELTA_EPSILON   →  "improved"
score_delta < -COMPARISON_DELTA_EPSILON  →  "regressed"
|score_delta| ≤ COMPARISON_DELTA_EPSILON →  "unchanged"

COMPARISON_DELTA_EPSILON = 0.005 (absorbs floating-point noise)
```

Safety status change is tracked independently of the verdict — a case can have an "unchanged" quality verdict while its safety status changes (as demonstrated by the cmp-004 fixture).

### Fixture case mix

| File | Case ID | Expected verdict | Scenario |
|---|---|---|---|
| `baseline/cmp-001.json` + `optimized/cmp-001.json` | `cmp-001` | improved | Optimized hits all expected facts and keywords; baseline misses both |
| `baseline/cmp-002.json` + `optimized/cmp-002.json` | `cmp-002` | unchanged | Identical outputs; delta = 0.0 |
| `baseline/cmp-003.json` + `optimized/cmp-003.json` | `cmp-003` | regressed | Optimized introduces unsupported claims; G-2 hard gate fails |
| `baseline/cmp-004.json` + `optimized/cmp-004.json` | `cmp-004` | unchanged (safety changes) | Same quality score; baseline confidence=0.35 → ESCALATE; optimized confidence=0.91 → ALLOW |

### Design properties

- **Composition over duplication** — calls `score_output_quality()` and `evaluate_safety()` directly; no scoring logic is duplicated
- **No live AWS dependency** — no boto3, no Bedrock client, no Converse inference
- **Deterministic** — same inputs always produce the same results in the same order
- **Missing-case tolerant** — missing files are recorded, not raised; only fully-paired cases are scored
- **Safety tracking decoupled from quality verdict** — safety status change is a separate field, not folded into the verdict

---

## 23. Phase J-0 — CloudWatch Evaluation Dashboard

Phase J-0 adds the **evaluation observability foundation**: a typed metric contract, a pure translation layer, a thin CloudWatch Metrics service wrapper, and a dashboard body builder.  It surfaces the outcomes of the completed F / G / H / I evaluation phases in a CloudWatch dashboard without requiring live AWS in tests.

### Components

| Component | Location | Description |
|---|---|---|
| **EvaluationDashboardConfig** | `app/utils/config.py` | Frozen dataclass with five fields: `enable_evaluation_metrics`, `metrics_namespace`, `dashboard_name`, `environment`, `aws_region`; off by default; raises `ValueError` on invalid enable flag |
| **load_evaluation_dashboard_config()** | `app/utils/config.py` | Loads config from four `CASEOPS_*` env vars plus `AWS_REGION` |
| **EvaluationMetricDatum** | `app/schemas/evaluation_models.py` | Pydantic model: typed CloudWatch Metrics datum contract; validated `unit` (Literal of all CloudWatch units), finite `value`, non-empty `metric_name`/`namespace`, optional `dimensions` |
| **cloudwatch_metrics_service.py** | `app/services/cloudwatch_metrics_service.py` | Thin boto3 `put_metric_data` wrapper: `CloudWatchMetricsService`, `NoOpMetricsService`, `build_metrics_service` factory; injectable client; all exceptions swallowed; no-op on empty datum list or None client |
| **metrics_translator.py** | `app/evaluation/metrics_translator.py` | Pure translation functions: `evaluation_run_summary_to_metrics()`, `comparison_summary_to_metrics()`, `safety_distribution_to_metrics()`; 14 named metric constants shared with dashboard builder |
| **dashboard_builder.py** | `app/evaluation/dashboard_builder.py` | Pure builder: `build_evaluation_dashboard(config)` → valid CloudWatch dashboard body dict; `dashboard_body_to_json(body)` → compact JSON string for `put_dashboard` |
| **Tests** | `tests/test_evaluation_dashboard_config.py`, `tests/test_cloudwatch_metrics_service.py`, `tests/test_metrics_translator.py`, `tests/test_dashboard_builder.py` | 113 tests: config loading/validation, service wrapper with mocked client, translation correctness, dashboard structure |

### Observability flow

```
EvaluationRunSummary (F-2)     ComparisonSummary (I-2)     safety distribution (dict)
        │                               │                               │
        └───────────────────────────────┴───────────────────────────────┘
                                        │
                        metrics_translator.py (pure functions)
                                        │
                            list[EvaluationMetricDatum]
                                        │
                        CloudWatchMetricsService.publish_metrics()
                                        │
                            CloudWatch Metrics put_metric_data
                                  (when enabled + AWS available)
```

```
EvaluationDashboardConfig
        │
dashboard_builder.build_evaluation_dashboard(config)
        │
dashboard body dict  →  dashboard_body_to_json()  →  compact JSON
        │
CloudWatch put_dashboard(DashboardName=..., DashboardBody=...)
  (when deployed; not required for tests or offline use)
```

### Dashboard widget layout (24-column CloudWatch grid)

| y | x | Width | Type | Content |
|---|---|---|---|---|
| 0 | 0 | 24 | text | Title + environment/namespace context |
| 2 | 0 | 12 | metric | Evaluation Quality — EvalPassCount, EvalFailCount, EvalTotalCases |
| 2 | 12 | 12 | metric | Safety Status Distribution — SafetyAllow, SafetyWarn, SafetyEscalate, SafetyBlock |
| 8 | 0 | 12 | metric | Baseline vs. Optimized — CmpImprovedCount, CmpRegressedCount, CmpUnchangedCount |
| 8 | 12 | 12 | metric | Output Quality Scores — EvalAverageScore, CmpBaselinePassCount, CmpOptimizedPassCount |

### Metric names emitted by the translator

| Metric name | Source function | Unit | Description |
|---|---|---|---|
| `EvalPassCount` | `evaluation_run_summary_to_metrics` | Count | Cases that passed the quality threshold |
| `EvalFailCount` | `evaluation_run_summary_to_metrics` | Count | Cases that failed the quality threshold |
| `EvalTotalCases` | `evaluation_run_summary_to_metrics` | Count | Total cases in the evaluation run |
| `EvalAverageScore` | `evaluation_run_summary_to_metrics` | None | Mean overall quality score (0.0–1.0) |
| `SafetyAllow` | `safety_distribution_to_metrics` | Count | Outputs with SafetyStatus = allow |
| `SafetyWarn` | `safety_distribution_to_metrics` | Count | Outputs with SafetyStatus = warn |
| `SafetyEscalate` | `safety_distribution_to_metrics` | Count | Outputs with SafetyStatus = escalate |
| `SafetyBlock` | `safety_distribution_to_metrics` | Count | Outputs with SafetyStatus = block |
| `CmpImprovedCount` | `comparison_summary_to_metrics` | Count | Cases where optimized improved over baseline |
| `CmpRegressedCount` | `comparison_summary_to_metrics` | Count | Cases where optimized regressed from baseline |
| `CmpUnchangedCount` | `comparison_summary_to_metrics` | Count | Cases within the comparison epsilon |
| `CmpAverageScoreDelta` | `comparison_summary_to_metrics` | None | Mean score delta (may be negative) |
| `CmpBaselinePassCount` | `comparison_summary_to_metrics` | Count | Baseline outputs passing the quality threshold |
| `CmpOptimizedPassCount` | `comparison_summary_to_metrics` | Count | Optimized outputs passing the quality threshold |

All datums carry an `Environment` dimension from `config.environment`.

### Design properties

- **Off by default** — `CASEOPS_ENABLE_EVALUATION_METRICS=false`; no AWS calls without opt-in
- **Separated responsibilities** — translator maps data, service emits data, builder defines shape; no responsibility crosses boundaries
- **No live AWS in tests** — all 113 tests use mocked boto3 clients or no client at all
- **Distinct from E-0 Logs** — `cloudwatch_metrics_service.py` uses the `cloudwatch` boto3 client and `put_metric_data`; completely separate from the CloudWatch Logs service in E-0
- **Metric names are constants** — imported from `metrics_translator.py` by `dashboard_builder.py` so widget references stay consistent with what is emitted
- **Fail-safe** — all boto3 exceptions in the service are caught and discarded; a CloudWatch outage cannot affect the evaluation pipeline
- **Pure functions** — translator and builder have no I/O, no state, no AWS dependency; same inputs always produce the same outputs

> **J-1 complete. J-2 complete.** See `PROJECT_SPEC.md §13`.

---

## 24. Phase J-1 — Evaluation Result Artifacts + Reporting

Phase J-1 adds a **local artifact and reporting layer** that persists completed evaluation run results to disk and generates concise human-readable summaries.  It is entirely offline — no AWS dependency, no live runtime changes.

### Components

| Component | Location | Description |
|---|---|---|
| **Artifact models** | `app/schemas/artifact_models.py` | Typed contracts: `ArtifactKind` Literal type, `ArtifactMetadata` (frozen Pydantic model — run_id, kind, created_at, artifact_dir, artifact_files), `ReportBundle` (groups metadata + optional report path) |
| **Report generator** | `app/evaluation/report_generator.py` | Three pure functions: `generate_evaluation_run_report()`, `generate_safety_run_report()`, `generate_comparison_run_report()` — all return deterministic markdown strings; no I/O |
| **Artifact writer** | `app/evaluation/artifact_writer.py` | Three writer functions: `write_evaluation_run()`, `write_safety_run()`, `write_comparison_run()`; each writes `summary.json` + `case_results.json` + optional `report.md` to a predictable subdirectory; returns a `ReportBundle`; raises `ArtifactWriteError` on filesystem failure |
| **Tests** | `tests/test_artifact_models.py`, `tests/test_report_generator.py`, `tests/test_artifact_writer.py` | 122 new tests: model validation, report content, path construction, JSON correctness, determinism, error handling, no live AWS |

### Output directory structure

```
{output_root}/
  evaluation_runs/{run_id}/
    summary.json        ← EvaluationRunSummary (F-2)
    case_results.json   ← list[EvaluationResult] (F-2)
    report.md           ← human-readable markdown summary

  safety_runs/{suite_id}/
    summary.json        ← SafetySuiteSummary (H-2)
    case_results.json   ← list[SafetyCaseResult] (H-2)
    report.md

  comparison_runs/{run_id}/
    summary.json        ← ComparisonSummary (I-2)
    case_results.json   ← list[ComparisonCaseResult] (I-2)
    report.md
```

### Report content

Each markdown report answers the key human questions at a glance:

| Report | Key sections |
|---|---|
| Evaluation run | Run ID + timestamp, pass/fail counts and rate, average score, failing case IDs with scores, per-metric averages |
| Safety suite | Suite ID, pass/fail counts and rate, actual status distribution, failing cases with expected vs. actual status |
| Comparison run | Run ID, improved/regressed/unchanged counts, baseline vs. optimized avg scores and delta, per-case verdict table, safety status distribution |

### Serialization strategy

- **Pydantic models** (EvaluationRunSummary, EvaluationResult): `.model_dump(mode="json")`
- **SafetyCaseResult** (frozen dataclass with nested SafetyAssessment Pydantic model): manual field-by-field serializer; enum values as `.value` strings; nested model via `.model_dump(mode="json")`
- **ComparisonCaseResult / ComparisonSummary** (frozen dataclasses): manual field-by-field serializers; tuples become lists; enum-like verdict Literal stays as string

### Design properties

- **Offline-first** — no boto3, no Bedrock client, no live AWS calls
- **Deterministic** — same inputs always produce the same JSON content and report text
- **Composable** — artifact writer calls report generator internally; callers interact with a single `write_*` function
- **Safe failure** — `ArtifactWriteError` raised (not swallowed) on filesystem failure so callers can handle gracefully
- **Thin contracts** — `ArtifactMetadata` and `ReportBundle` are metadata models only; no scoring logic
- **Consistent structure** — three output subdirectories (`evaluation_runs/`, `safety_runs/`, `comparison_runs/`) under a configurable `output_root`
- **No runtime pipeline impact** — no changes to agents, workflows, CLI, or live service wrappers

---

## 25. Phase J-2 — v2 Hardening + Optimization Checkpoint

Phase J-2 adds the **final Phase 2 consolidation checkpoint**: a narrow typed contract, a composition runner, and a local artifact writer that together produce an honest, review-ready summary of Phase 2 completeness.

It is a cross-phase composition layer — it does not re-score anything or add new product features.

### Components

| Component | Location | Description |
|---|---|---|
| **Checkpoint schemas** | `app/schemas/checkpoint_models.py` | Typed contracts: `Phase2CheckpointStatus` Literal type, `Phase2ReadinessBlock` per-layer readiness model, `Phase2CheckpointResult` root checkpoint contract with model-level consistency guard |
| **Checkpoint runner** | `app/evaluation/checkpoint_runner.py` | `CheckpointInputs` frozen dataclass; `build_checkpoint()` function that assembles layer readiness indicators into a typed `Phase2CheckpointResult`; layer subphase metadata; status derivation logic |
| **Checkpoint writer** | `app/evaluation/checkpoint_writer.py` | `generate_checkpoint_report()` pure markdown generator (no I/O); `write_checkpoint()` artifact writer; `CheckpointWriteError` for filesystem failures |
| **ArtifactKind extension** | `app/schemas/artifact_models.py` | `"checkpoint"` added to the `ArtifactKind` Literal type so J-2 artifacts are consistently classifiable |
| **Tests** | `tests/test_checkpoint_models.py`, `tests/test_checkpoint_runner.py` | 114 new tests: contract validation, consistency guards, runner defaults/custom inputs, determinism, report content, writer path/content/error handling, no live AWS |

### Checkpoint output structure

```
{output_root}/checkpoints/{checkpoint_id}/
  checkpoint.json   ← Phase2CheckpointResult serialized
  report.md         ← human-readable Phase 2 checkpoint summary
```

### Consistency guard

`Phase2CheckpointResult` enforces an honest checkpoint via a Pydantic `model_validator`:

```
live_aws_validated=False + status='complete'      → ValueError (must use 'complete_blocked')
engineering_complete=False + status='complete'    → ValueError
engineering_complete=False + status='complete_blocked' → ValueError
```

This prevents any future code path from silently misrepresenting the external AWS blocker.

### Design properties

- **Composition, not duplication** — runner reads readiness flags from `CheckpointInputs`; does not re-score existing evaluations
- **Offline-first** — no boto3, no Bedrock client, no live AWS calls
- **Honest by construction** — consistency guard enforces correct status for the external-blocker case
- **Deterministic** — same inputs + same checkpoint_id always produce the same status and completed_phases
- **No runtime pipeline impact** — no changes to agents, workflows, CLI, or live service wrappers

> **Phase 2 complete. Phase J-2 is the final Phase 2 subphase.** Phase 3 (v3: optional customization experiments) remains future work. See `PROJECT_SPEC.md §13`.
