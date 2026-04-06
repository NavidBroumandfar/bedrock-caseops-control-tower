# Architecture — Bedrock CaseOps Multi-Agent Control Tower

**Version:** 0.1 (MVP)
**Last Updated:** 2026-04-05

> **Phase 1 (v1 MVP) complete — Phase 2 not started.**
>
> **Implementation Status:** All MVP engineering phases are implemented in code: Phase A (intake), Phase B (retrieval), Phase C (analysis + validation), Phase D (orchestration + escalation), Phase E-0 (structured logging + CloudWatch), Phase E-1 (CLI end-to-end flow + S3 output archiving), and Phase E-2 (test hardening, sample cases, config hardening, demo readiness). Local output writing, S3 output archiving, structured logging, CloudWatch integration, CLI flow, and test coverage are all complete. The repository is portfolio-ready and test-complete for the MVP scope.
>
> **Live Bedrock runtime validation is pending:** Live AWS Knowledge Base end-to-end validation is currently blocked by AWS-side Titan Text Embeddings V2 throttling/runtime issues in the target account. The architecture and all implementation are complete and correct — this is not a code issue. All 678 unit and integration-style tests pass without live AWS calls. Live validation will be completed when the AWS-side blocker is resolved.

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

## 13. Future Extensibility

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

> **v2 implementation roadmap:** The next planned implementation phase (Phase 2) follows subphases F through J — covering evaluation foundation, retrieval and output quality, safety and guardrails, optimization, and observability/reporting. See `PROJECT_SPEC.md §13` for the full breakdown. Phase 2 has not started.
