![Bedrock CaseOps Control Tower](docs/assets/bedrock-caseops-banner.svg)

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![AWS Bedrock](https://img.shields.io/badge/Platform-AWS%20Bedrock-FF9900?style=flat-square&logo=amazonaws&logoColor=white)](https://aws.amazon.com/bedrock/)
[![Architecture](https://img.shields.io/badge/Architecture-Multi--Agent%20RAG-6B7FD7?style=flat-square)]()
[![Tests](https://img.shields.io/badge/Tests-2%2C133%20passing-2EA043?style=flat-square)](./tests/)
[![Status](https://img.shields.io/badge/Status-Portfolio%20%2F%20Non--Production-E67E22?style=flat-square)]()

> A Bedrock-powered custom Python multi-agent RAG pipeline for high-stakes document review — grounded retrieval, evidence-backed analysis, validation, and structured escalation, traceable from source document to final recommendation.

---

## What This Project Does

Operational and technical teams regularly process large volumes of high-stakes documents — FDA warning letters, CISA advisories, incident reports, recall notices — where the cost of a missed classification, an unsupported recommendation, or an untraced escalation is real. Manual review does not scale. Existing AI automation often makes things worse: it summarizes without verifying, classifies without explaining, and escalates without a rationale that anyone can audit.

This project addresses that gap with a deliberately structured multi-agent pipeline: specialized Python components for retrieval, analysis, validation, and escalation, each with a defined scope and responsibility. Outputs carry retrieved-evidence citations, validation confidence, and escalation rationale so review results are auditable rather than just automated.

---

## Positioning

This is the **downstream Bedrock-powered reasoning layer** of the Bedrock CaseOps system. This repo owns grounded retrieval, custom Python orchestration, output validation, escalation logic, and structured case-support outputs. Upstream document preparation belongs to the Databricks repo.

| Concern | This Repo | Databricks Lakehouse |
|---|---|---|
| Raw document ingestion and parsing | No | Yes |
| Structured field extraction | No | Yes |
| Classification and routing | No | Yes |
| Governed AI-ready asset preparation | No | Yes |
| Gold export payload delivery | Consumes | Yes |
| Grounded retrieval via Knowledge Base | Yes | No |
| Custom Python multi-agent orchestration and reasoning | Yes | No |
| Output validation and confidence scoring | Yes | No |
| Escalation logic and structured outputs | Yes | No |
| Evaluation, safety, and observability | Yes | No |

This repo does not own document transformation or upstream data structuring. Its contract is: governed document asset in, structured auditable review output out.

---

## Why Multi-Agent + Grounded Retrieval

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
CLI
      │
      ▼
Document Intake
      │
      ├── optional S3 document archive
      │
      ▼
Retrieval Workflow → Bedrock Knowledge Base
      │
      ▼
Supervisor Workflow
      │
      ├──► Analysis Agent      → Bedrock Converse
      ├──► Validation Agent    → Bedrock Converse
      └──► Tool Executor Agent → structured JSON output + escalation flag
      │
      ▼
Local outputs + optional S3 archive + optional CloudWatch logs
```

The agents in this repository are application-level Python classes and workflows. The current implementation does **not** define native Amazon Bedrock Agents, agent aliases, action groups, Lambda handlers, or `invoke_agent` calls. See [agents.md](agents.md) for the current agent inventory and [ROADMAP.md](ROADMAP.md) for the planned evolution.

---

## AWS Service Usage

| Service | Role |
|---|---|
| **Amazon S3** | Optional raw document upload and optional final output archiving |
| **Amazon Bedrock** | Foundation model inference (Claude via Converse API) |
| **Amazon Bedrock Knowledge Bases** | Managed vector store and retrieval |
| **Amazon CloudWatch** | Optional structured logging and evaluation metrics when enabled |

Not implemented today: native Amazon Bedrock Agents, AWS Lambda deployment, infrastructure-as-code, and CI workflow assets. The current runtime is a CLI-driven custom Python orchestration layer that calls Bedrock services directly through boto3-backed service classes.

---

## MVP Scope

| In Scope | Out of Scope |
|---|---|
| Document intake with metadata validation | Full CI/CD pipeline |
| Optional S3 document storage and output archiving | Frontend or web UI |
| Bedrock Knowledge Base retrieval | Auth and multi-user management |
| Custom Python multi-agent orchestration | Model fine-tuning |
| Structured JSON output with citations | Enterprise deployment infrastructure |
| Severity classification and escalation logic | Multi-region support |
| Optional CloudWatch logging | Document format conversion (assumes clean text input) |
| CLI interface | |

---

## Repo Structure

```
bedrock-caseops-control-tower/
├── app/
│   ├── agents/          # Agent definitions and prompt logic
│   ├── services/        # AWS service clients (S3, Bedrock, KB)
│   ├── workflows/       # Orchestration and routing logic
│   ├── schemas/         # Pydantic models for structured I/O
│   ├── evaluation/      # Offline evaluation harness
│   └── utils/           # Logging, ID generation, file helpers
├── notebooks/           # Exploratory notebooks and prototypes
├── tests/               # Unit and integration tests
├── data/
│   ├── sample_documents/    # Public test documents (FDA, CISA, etc.)
│   ├── expected_outputs/    # Reference outputs for pipeline validation
│   └── evaluation/          # Curated evaluation dataset and reference outputs
├── docs/
│   └── assets/              # README assets
├── outputs/             # Runtime-generated outputs (gitignored)
├── .env.example
├── Makefile
├── requirements.txt
├── agents.md
├── ROADMAP.md
├── PROJECT_SPEC.md
├── ARCHITECTURE.md
└── README.md
```

---

## Example End-to-End Workflow

1. An operator runs the CLI with a document path (e.g., an FDA warning letter in text or markdown format)
2. The intake pipeline validates metadata, assigns a document ID, and optionally stores the file in S3 when `S3_DOCUMENT_BUCKET` is configured
3. The supervisor workflow receives the document reference and initiates the pipeline
4. The retrieval workflow queries the Bedrock Knowledge Base and returns grounded evidence chunks with source citations
5. The Analysis Agent classifies severity (Critical / High / Medium / Low), assigns a category, and generates recommendations using retrieved evidence
6. The Validation Agent audits the analysis output for unsupported claims and assigns a confidence score
7. The Tool Executor Agent formats the final structured JSON output, applies escalation logic if warranted, and writes local output with optional S3 archiving
8. Pipeline steps are logged locally and optionally emitted to CloudWatch when `CASEOPS_ENABLE_CLOUDWATCH=true`

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

## What This Demonstrates

This project tackles a set of applied AI engineering problems that are hard to show with toy examples:

- **Agentic system design** — a supervisor-coordinated custom Python multi-agent hierarchy, not a single prompt chain; each agent has a defined scope and typed contract
- **Grounded retrieval** — generated outputs carry chunk-level Knowledge Base citations; claim-level citation is a planned improvement
- **Self-validating outputs** — a dedicated Validation Agent audits every analysis for unsupported claims, missing citations, and confidence drift before the output is accepted
- **Structured escalation** — escalation is rule-driven and explainable: severity, confidence threshold, unsupported claims, and explicit recommendations all feed into a deterministic escalation decision
- **Production-grade data modeling** — Pydantic schemas enforce contract boundaries between every agent; structured JSON with full citation tracking throughout
- **Bedrock service integration** — Bedrock Converse, Bedrock Knowledge Bases, optional S3, and optional CloudWatch integrations through explicit service boundaries
- **Evaluation and observability** — offline evaluation harness across retrieval quality, citation quality, and output quality; deterministic safety contracts; Guardrails configuration and tests; CloudWatch evaluation dashboard support; adversarial and edge-case test coverage
- **Clean, readable architecture** — modular, testable, and decoupled without being over-engineered

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

Create and activate a local virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

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

The CLI loads `.env` automatically from the current working directory tree. Existing shell environment variables take precedence over `.env` values.

S3 variables are optional per step:
- `S3_DOCUMENT_BUCKET` — enables S3 upload of the raw document and intake artifact; if absent, intake runs in local-only mode.
- `S3_OUTPUT_BUCKET` — enables S3 archiving of the final JSON output to `s3://{bucket}/outputs/{document_id}/case_output.json`; if absent, output is written locally only.

Check local configuration without making AWS calls:

```bash
python3 -m app.cli doctor
python3 -m app.cli check-config
```

### Run the full end-to-end pipeline

```bash
python3 -m app.cli run path/to/advisory.txt \
    --source-type FDA \
    --document-date 2026-03-30
```

With an optional submitter note (used as the KB retrieval query):

```bash
python3 -m app.cli run path/to/advisory.txt \
    --source-type CISA \
    --document-date 2026-03-30 \
    --submitter-note "Critical ICS vulnerability — immediate review required"
```

Supported `--source-type` values: `FDA`, `CISA`, `Incident`, `Other`

On success, the CLI prints a structured summary and writes the final JSON output to `outputs/{document_id}.json`.

### Register a document without running the pipeline

```bash
python3 -m app.cli intake path/to/advisory.txt \
    --source-type FDA \
    --document-date 2026-03-30
```

### Show available commands

```bash
python3 -m app.cli --help
python3 -m app.cli doctor
python3 -m app.cli run --help
python3 -m app.cli intake --help
```

Common shortcuts are available through `make`:

```bash
make test
make cli-help
make doctor
make intake-sample
make live-smoke
```

### Live AWS status

Live Bedrock / Knowledge Base validation has not been completed in this workspace. The `run` command will surface a clear failure message when required config, AWS credentials, model access, or Knowledge Base retrieval are unavailable. The offline test suite validates the custom Python runtime without live AWS calls.

---

## Demo Flow (No AWS Required)

The full pipeline flow can be exercised locally without live AWS credentials using the test suite and the provided sample documents.

### Step 1: Run the test suite

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest tests/ -v
```

The offline suite passes without live AWS and covers intake, retrieval contracts, analysis, validation, escalation, output writing, CLI commands, structured logging, optional CloudWatch paths, evaluation scoring, deterministic safety contracts, runtime prompt caching/routing wiring, comparison workflows, and local reporting.

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

These fixtures are controlled reference outputs — **not** live AWS outputs. See `data/expected_outputs/README.md` for details.

### Step 4: Run the intake command locally (no AWS needed)

The `intake` command validates and registers a document without requiring any AWS services:

```bash
python3 -m app.cli intake data/sample_documents/fda_warning_letter_01.md \
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
python3 -m app.cli run data/sample_documents/fda_warning_letter_01.md \
    --source-type FDA \
    --document-date 2026-03-30 \
    --submitter-note "FDA warning letter — quality system deficiencies"
```

On success, the CLI prints a structured summary and writes a JSON output to `outputs/{document_id}.json`.

> **Live AWS status:** Live Bedrock / Knowledge Base validation is pending. Use `python3 -m app.cli doctor` first to confirm required local config before attempting a live run.

---

## Project Status

**Phase 1 — Core Multi-Agent MVP:** Implemented and offline-validated. The custom Python pipeline includes document intake, grounded retrieval via Bedrock Knowledge Bases, analysis and validation agents, escalation logic, structured JSON output with chunk-level citations, CLI interface, and optional CloudWatch observability.

**Phase 2 — Evaluation, Safety, Optimization, and Observability:** Implemented as testable modules with runtime config wiring for prompt caching, prompt routing, retry count, and escalation threshold. This phase added a structured offline evaluation harness, deterministic safety contracts, Guardrails configuration and adapters, adversarial and edge-case evaluation, prompt caching and routing modules, baseline vs. optimized comparison workflows, a CloudWatch evaluation dashboard definition, and local evaluation artifact reporting.

**Current state:** The repository is an offline-validated custom Bedrock-powered orchestration system. Live end-to-end validation against a provisioned Bedrock Knowledge Base remains pending, and there is not yet a Lambda handler, infrastructure-as-code, CI workflow, or native Bedrock Agent deployment.

For the current agent inventory and implementation gaps, see [agents.md](agents.md). For the phased roadmap, see [ROADMAP.md](ROADMAP.md). Older detailed design notes live in [PROJECT_SPEC.md](PROJECT_SPEC.md) and [ARCHITECTURE.md](ARCHITECTURE.md); where they conflict with the current status, prefer `agents.md` and `ROADMAP.md`.

---

## Connected Repositories

This repo is the **downstream** component of a two-repository architecture. The boundary between them is intentional and non-negotiable.

| Repository | Role |
|---|---|
| [**databricks-caseops-lakehouse**](https://github.com/NavidBroumandfar/databricks-caseops-lakehouse) | Upstream — governed document ingestion, parsing, extraction, classification, and AI-ready asset preparation on the Databricks Lakehouse platform |
| **bedrock-caseops-control-tower** *(this repo)* | Downstream — grounded retrieval, multi-agent reasoning, output validation, escalation, and structured review generation on AWS Bedrock |

The handoff point between these systems is the formal **Gold export payload** — a schema-versioned, contract-enforced structured record produced by the Databricks repo and consumed by this repo. This repo does not own document transformation, extraction, or upstream data structuring. Those concerns belong entirely to the Databricks Lakehouse.

---

## Let's Connect

If you're exploring this project, working on agentic AI systems or AWS Bedrock architecture, or open to discussing applied AI and cloud engineering roles — I'd be glad to connect.

&nbsp;

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Navid%20Broumandfar-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/navid-broomandfar/)
[![GitHub](https://img.shields.io/badge/GitHub-NavidBroumandfar-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NavidBroumandfar)
[![Email](https://img.shields.io/badge/Email-Contact-D14836?style=for-the-badge&logo=gmail&logoColor=white)](mailto:broomandnavid@gmail.com)

&nbsp;

---

This project was developed with AI-assisted workflows. The system architecture, agent design, schema contracts, evaluation framework, and safety boundaries were intentionally designed and directed by the author, with AI tooling used to support and accelerate implementation as part of a modern engineering workflow.
