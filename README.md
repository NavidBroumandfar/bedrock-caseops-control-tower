# Bedrock CaseOps Multi-Agent Control Tower

An AWS-native, production-style multi-agent system that reviews technical and operational documents, retrieves grounded evidence, classifies severity and category, recommends next actions, validates outputs, and flags high-risk cases for escalation.

Built on Amazon Bedrock, Amazon Bedrock Knowledge Bases, and AWS Lambda — with structured JSON outputs, full citation tracking, and CloudWatch observability.

---

## Problem

Operational and technical teams regularly process large volumes of documents — incident reports, regulatory advisories, recall notices — where the stakes of missing something are high. Manual review is slow, inconsistent, and does not scale. Existing automation often lacks grounding: it summarizes without verifying, classifies without explaining, and escalates without a traceable rationale.

This project addresses that gap by combining retrieval-augmented generation (RAG) with a structured multi-agent pipeline that separates concerns across specialized agents, validates its own outputs, and produces auditable, citation-backed results.

---

## Why Agentic AI + Grounded Retrieval

Large language models used alone hallucinate and drift. A single-agent RAG setup lacks the separation needed to catch its own errors. This project applies a supervisor-orchestrated multi-agent pattern where:

- A **Retrieval Agent** fetches only what is actually in the knowledge base
- An **Analysis Agent** works strictly from retrieved evidence
- A **Validation Agent** audits the analysis for unsupported claims, missing citations, and confidence drift
- A **Tool Executor** handles structured actions (severity tagging, escalation triggers, output formatting)
- A **Supervisor** coordinates the full pipeline and routes exceptions

This design gives every output a traceable chain of custody from raw document to final recommendation.

---

## Architecture Summary

```
Document Intake
      │
      ▼
   S3 Storage
      │
      ▼
Bedrock Knowledge Base (indexed from S3)
      │
      ▼
Supervisor / Planner Agent
      │
      ├──► Retrieval Agent     → evidence chunks + citations
      ├──► Analysis Agent      → classification + recommendations
      ├──► Validation Agent    → output audit + confidence scoring
      └──► Tool Executor       → structured JSON output + escalation flag
      │
      ▼
   CloudWatch Logs + Outputs
```

---

## MVP Scope

| In Scope | Out of Scope |
|---|---|
| Document intake with metadata validation | Full CI/CD pipeline |
| S3 document storage | Frontend or web UI |
| Bedrock Knowledge Base retrieval | Auth and multi-user management |
| Multi-agent orchestration | Bedrock Guardrails (planned v2) |
| Structured JSON output with citations | Bedrock Evaluations (planned v2) |
| Severity classification and escalation logic | Model fine-tuning |
| CloudWatch logging | Enterprise deployment |
| CLI interface | Prompt caching / routing (planned v2) |

---

## AWS Stack

| Service | Role |
|---|---|
| **Amazon S3** | Raw document storage and output archiving |
| **Amazon Bedrock** | Foundation model inference (Claude via Converse API) |
| **Amazon Bedrock Knowledge Bases** | Managed vector store and retrieval |
| **Amazon Bedrock Agents** | Agent orchestration and tool use |
| **AWS Lambda** | Serverless execution of agent workflows |
| **Amazon CloudWatch** | Logging, metrics, and observability |

---

## Repo Structure

```
bedrock-caseops-control-tower/
├── app/
│   ├── agents/          # Agent definitions and prompt logic
│   ├── services/        # AWS service clients (S3, Bedrock, KB)
│   ├── workflows/       # Orchestration and routing logic
│   ├── schemas/         # Pydantic models for structured I/O
│   ├── evaluation/      # Offline evaluation harness (Phase F)
│   └── utils/           # Logging, ID generation, file helpers
├── notebooks/           # Exploratory notebooks and prototypes
├── tests/               # Unit and integration tests
├── data/
│   ├── sample_documents/    # Public test documents (FDA, CISA, etc.)
│   ├── expected_outputs/    # Reference outputs for pipeline validation
│   └── evaluation/          # Curated eval dataset + expected outputs (Phase F)
├── outputs/             # Runtime-generated outputs (gitignored)
├── docs/                # Architecture notes and project memory
├── .env.example
├── requirements.txt
├── PROJECT_SPEC.md
├── ARCHITECTURE.md
└── README.md
```

---

## Example End-to-End Workflow

1. An operator runs the CLI with a document path (e.g., an FDA warning letter in PDF or text format)
2. The intake pipeline validates metadata, assigns a document ID, and stores the file in S3
3. The Supervisor Agent receives the document reference and initiates the pipeline
4. The Retrieval Agent queries the Bedrock Knowledge Base and returns grounded evidence chunks with source citations
5. The Analysis Agent classifies severity (Critical / High / Medium / Low), assigns a category, and generates recommendations — using only the retrieved evidence
6. The Validation Agent audits the analysis output for unsupported claims and assigns a confidence score
7. The Tool Executor formats the final structured JSON output, applies escalation logic if warranted, and writes results to S3 and local outputs
8. All agent steps are logged to CloudWatch with session and document IDs for full traceability

---

## Sample Output Schema (simplified)

```json
{
  "document_id": "doc-20240315-fda-001",
  "source": "FDA Warning Letter – XYZ Facility",
  "severity": "High",
  "category": "Regulatory / Manufacturing Deficiency",
  "summary": "Facility failed to establish written procedures for equipment cleaning...",
  "recommendations": [
    "Initiate CAPA for cleaning validation gaps",
    "Escalate to compliance team within 48 hours"
  ],
  "citations": [
    {"source": "FDA Warning Letter 2024-WL-0032", "excerpt": "...no written procedures..."}
  ],
  "confidence_score": 0.87,
  "escalation_required": true,
  "validated_by": "validation-agent-v1",
  "timestamp": "2024-03-15T14:22:01Z"
}
```

---

## Why This Is a Strong Applied AI Portfolio Project

This project demonstrates a set of skills that are difficult to show with toy examples:

- **Agentic system design** — not just calling an LLM, but coordinating specialized agents with defined responsibilities
- **Grounded retrieval** — outputs tied to verifiable sources, not open-ended generation
- **Output validation** — a critic agent that audits the pipeline's own outputs
- **Production data modeling** — Pydantic schemas, structured JSON, citation tracking
- **AWS-native implementation** — real use of Bedrock, Knowledge Bases, S3, Lambda, and CloudWatch
- **Operational focus** — built around a realistic use case (regulatory / incident review) with escalation logic
- **Clean architecture** — modular, testable, and readable without being overengineered

---

## Data Sources

All sample documents used in this project are sourced from publicly available, legally safe data:

- [FDA Recalls and Warning Letters](https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts)
- [CISA Advisories](https://www.cisa.gov/news-events/cybersecurity-advisories)
- Public technical incident reports and post-mortems
- Synthetic cases derived from public sources

No confidential or proprietary data is used anywhere in this project.

---

## Running the CLI

### Prerequisites

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Minimum required for the full pipeline:

```
BEDROCK_KB_ID=your-knowledge-base-id
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
AWS_REGION=us-east-1
```

S3 variables are optional per step:
- `S3_DOCUMENT_BUCKET` — enables S3 upload of the raw document and intake artifact; if absent, intake runs in local-only mode.
- `S3_OUTPUT_BUCKET` — enables S3 archiving of the final JSON output to `s3://{bucket}/outputs/{document_id}/case_output.json`; if absent, output is written locally only.

### Run the full end-to-end pipeline

```bash
python -m app.cli run path/to/advisory.txt \
    --source-type FDA \
    --document-date 2026-03-30
```

With an optional submitter note (used as the KB retrieval query):

```bash
python -m app.cli run path/to/advisory.txt \
    --source-type CISA \
    --document-date 2026-03-30 \
    --submitter-note "Critical ICS vulnerability — immediate review required"
```

Supported `--source-type` values: `FDA`, `CISA`, `Incident`, `Other`

On success, the CLI prints a structured summary and writes the final JSON output to `outputs/{document_id}.json`.

### Register a document without running the pipeline

```bash
python -m app.cli intake path/to/advisory.txt \
    --source-type FDA \
    --document-date 2026-03-30
```

### Show available commands

```bash
python -m app.cli --help
python -m app.cli run --help
python -m app.cli intake --help
```

### Current status note

Live Bedrock / Knowledge Base validation is currently blocked by AWS-side Titan Text Embeddings V2 throttling in the target account. The full pipeline code is complete and correct; the `run` command will surface a clear failure message when AWS calls cannot be completed. All 994 tests pass without live AWS calls.

---

## Demo Flow (No AWS Required)

The full pipeline flow can be exercised locally without live AWS credentials using the test suite and the provided sample documents.

### Step 1: Run the test suite

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

All 994 tests pass without live AWS, covering intake, retrieval, analysis, validation, escalation, output writing, CLI commands, structured logging, CloudWatch service, config loading, and the full Phase F evaluation layer (schemas, dataset loader, scorer, runner).

### Step 2: Explore sample inputs

Sample documents are in `data/sample_documents/`:

```
data/sample_documents/
├── fda_warning_letter_01.md   — FDA warning letter (quality system deficiencies)
├── fda_recall_01.md           — FDA voluntary recall (undeclared ingredients)
├── cisa_advisory_01.md        — CISA #StopRansomware advisory
└── sample_notice.txt          — Minimal synthetic test notice
```

### Step 3: Explore expected outputs

Reference output fixtures matching the `CaseOutput` schema are in `data/expected_outputs/`:

```
data/expected_outputs/
├── README.md                          — explains the fixture format
├── fda_warning_letter_01_expected.json
└── cisa_advisory_01_expected.json
```

These fixtures are controlled reference outputs — **not** live AWS outputs.  See `data/expected_outputs/README.md` for details.

### Step 4: Run the intake command locally (no AWS needed)

The `intake` command validates and registers a document without requiring any AWS services:

```bash
python -m app.cli intake data/sample_documents/fda_warning_letter_01.md \
    --source-type FDA \
    --document-date 2026-03-30
```

Expected output:
```
[ok] Registration complete.
     document_id  : doc-20260330-xxxxxxxx
     artifact     : outputs/intake/doc-20260330-xxxxxxxx.json
     storage      : local only
```

### Step 5: Run the full pipeline (requires live AWS)

When AWS credentials, a provisioned Knowledge Base, and a Bedrock model are available:

```bash
python -m app.cli run data/sample_documents/fda_warning_letter_01.md \
    --source-type FDA \
    --document-date 2026-03-30 \
    --submitter-note "FDA warning letter — quality system deficiencies"
```

On success, the CLI prints a structured summary and writes a JSON output to `outputs/{document_id}.json`.

> **Live AWS status:** Live Bedrock / Knowledge Base validation is currently blocked by AWS-side Titan Text Embeddings V2 throttling. The `run` command will fail with a clear `[error]` and `[hint]` message when live AWS calls cannot complete.

---

## Status

**Phase 1 (v1 MVP) complete** — all subphases A through E-2 are implemented and test-complete. The MVP engineering layer is portfolio-ready.

**Phase F (Evaluation Foundation) complete** — the repository now includes:

- Typed evaluation contracts and scoring schemas (`app/schemas/evaluation_models.py`)
- A curated local evaluation dataset with 7 cases and reference expected outputs (`data/evaluation/`)
- An offline evaluation harness: dataset loader, deterministic scorer, and aggregated scoring runner (`app/evaluation/`)

This evaluation layer is fully local and offline — it is independent of live AWS runtime availability. All 994 unit and evaluation tests pass without live AWS calls.

**Phase G (Retrieval & Output Quality) is the next phase** — not yet started.

**Live Bedrock runtime validation** remains pending due to AWS-side Titan Text Embeddings V2 throttling/runtime issues in the target account. This is an external blocker, not a code issue.

See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full roadmap and [ARCHITECTURE.md](ARCHITECTURE.md) for the technical design.
