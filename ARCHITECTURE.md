# Architecture — Bedrock CaseOps Multi-Agent Control Tower

**Version:** 0.2
**Last Updated:** 2026-04-10

> **Phase 1 (v1 MVP) complete. Phase F (Evaluation Foundation) complete. Phase G (Retrieval & Output Quality) complete.**
>
> **Implementation Status:** All MVP engineering phases are implemented in code: Phase A (intake), Phase B (retrieval), Phase C (analysis + validation), Phase D (orchestration + escalation), Phase E-0 (structured logging + CloudWatch), Phase E-1 (CLI end-to-end flow + S3 output archiving), and Phase E-2 (test hardening, sample cases, config hardening, demo readiness). Phase F adds a fully local, offline evaluation layer: typed evaluation contracts and schemas (F-0), a curated evaluation dataset with 7 cases and reference expected outputs (F-1), and an offline evaluation harness with dataset loader, deterministic scorer, and scoring runner (F-2). Phase G-0 adds offline retrieval quality metrics: three deterministic metrics scored against F-1 retrieval expectations, with fixture-based candidate input and 55 new tests. Phase G-1 adds offline citation quality metrics: four deterministic metrics scored against CitationExpectation references, with five candidate output fixtures and 64 new tests. Phase G-2 adds a composite output-quality scorer that composes F-2 and G-1 sub-scores plus three final-output-only checks (summary_nonempty, recommendations_present_when_expected, unsupported_claims_clean), with 46 new tests. All 1156 unit and evaluation tests pass without live AWS calls.
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

> **v2 implementation roadmap:** Phase 2 is in progress. Phase F (Evaluation Foundation) and Phase G (Retrieval & Output Quality — G-0, G-1, G-2) are complete. Remaining Phase 2 subphases (H through J) cover safety and guardrails, optimization, and observability/reporting. See `PROJECT_SPEC.md §13` for the full breakdown.

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

> **v2 implementation roadmap:** Phase G is complete. Phase H (Safety & Guardrails) is next. See `PROJECT_SPEC.md §13` for the full breakdown.
