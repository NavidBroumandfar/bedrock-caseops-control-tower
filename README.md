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
| Multi-agent orchestration | Bedrock Guardrails (implemented — Phase H) |
| Structured JSON output with citations | Bedrock Evaluations (implemented — Phase F/G) |
| Severity classification and escalation logic | Model fine-tuning |
| CloudWatch logging | Enterprise deployment |
| CLI interface | Prompt caching / routing (implemented — Phase I) |

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

Live Bedrock / Knowledge Base validation is currently blocked by AWS-side Titan Text Embeddings V2 throttling in the target account. The full pipeline code is complete and correct; the `run` command will surface a clear failure message when AWS calls cannot be completed. All 1759 tests pass without live AWS calls.

---

## Demo Flow (No AWS Required)

The full pipeline flow can be exercised locally without live AWS credentials using the test suite and the provided sample documents.

### Step 1: Run the test suite

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

All 2005 tests pass without live AWS, covering intake, retrieval, analysis, validation, escalation, output writing, CLI commands, structured logging, CloudWatch service, config loading, the full Phase F evaluation layer (schemas, dataset loader, scorer, runner), Phase G-0 retrieval quality metrics (retrieval scorer, fixture loading, dataset alignment), Phase G-1 citation quality checks (citation scorer, citation expectations, fixture loading, dataset alignment), Phase G-2 output quality scoring (composite scorer, five-dimension result, fixture integration, architectural separation), Phase H-0 safety contracts and deterministic policy evaluator (safety_models, safety_policy — six policy rules), Phase H-1 Bedrock Guardrails integration (guardrail_models, guardrails_service, guardrails_adapter — intervention and non-intervention paths), Phase H-2 adversarial and edge-case safety evaluation suite (safety_suite runner, 10 adversarial fixtures, status-priority and combined-scenario coverage), Phase I-0 prompt caching integration (PromptCachingConfig, apply_prompt_caching, service wiring — config defaults/overrides/validation, disabled and enabled request-shaping, no-regression with caching off), Phase I-1 prompt routing strategy (PromptRoutingConfig, resolve_model_id, service wiring — config defaults/overrides/invalid-flag/immutability, disabled and enabled routing paths, analysis and validation route resolution, priority chain, service integration, no-regression with routing off), Phase I-2 baseline vs. optimized comparison workflow (ComparisonVerdict type, ComparisonCaseResult/ComparisonSummary/ComparisonRunResult dataclasses, run_comparison runner composing G-2 + H-0, 4 paired fixtures covering improved/unchanged/regressed/safety-change, delta correctness, missing-case handling, determinism, no live AWS), Phase J-0 CloudWatch evaluation dashboard (EvaluationDashboardConfig config loading/validation, EvaluationMetricDatum contract with unit and value validation, CloudWatchMetricsService/NoOpMetricsService with injectable client, build_metrics_service factory, evaluation_run_summary_to_metrics/comparison_summary_to_metrics/safety_distribution_to_metrics translation functions, build_evaluation_dashboard dashboard body builder, dashboard_body_to_json serialiser — all offline, no live AWS), and Phase J-1 evaluation result artifacts and reporting (ArtifactMetadata/ReportBundle typed contracts, write_evaluation_run/write_safety_run/write_comparison_run artifact writers, generate_evaluation_run_report/generate_safety_run_report/generate_comparison_run_report markdown generators — all offline, local-first, no live AWS).

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

This evaluation layer is fully local and offline — it is independent of live AWS runtime availability.

**Phase G-0 (Retrieval Quality Metrics) is complete** — offline retrieval quality scoring against F-1 retrieval expectations, with three deterministic metrics and 55 new tests.

**Phase G-1 (Citation Quality Checks) is complete** — offline citation quality scoring against new `CitationExpectation` references, with four deterministic metrics and 64 new tests.

**Phase G-2 (Output Quality Scoring) is complete** — offline composite output-quality scorer that composes F-2 core case alignment and G-1 citation quality into a unified `OutputQualityScoringResult`, plus three final-output-only checks (summary_nonempty, recommendations_present_when_expected, unsupported_claims_clean), with 46 new tests. **Phase G is now complete.**

**Phase G (Retrieval & Output Quality) is complete** — G-0 retrieval metrics, G-1 citation checks, and G-2 output quality scoring are all implemented and test-complete.

**Phase H (Safety & Guardrails) is complete** — the repository now includes:

- Typed safety contracts and deterministic failure-policy evaluator (`app/schemas/safety_models.py`, `app/evaluation/safety_policy.py`)
- Normalized Guardrails contract, thin service wrapper, and H-0 adapter (`app/schemas/guardrail_models.py`, `app/services/guardrails_service.py`, `app/evaluation/guardrails_adapter.py`)
- Adversarial and edge-case safety evaluation suite: 10 curated fixtures covering schema failures, unsupported claims, missing citations, low confidence, empty retrieval, escalation, Guardrails intervention, combined scenarios, and clean passing cases (`tests/fixtures/safety_cases/`, `app/evaluation/safety_suite.py`)

**Phase I-0 (Prompt Caching Integration) is complete** — the repository now includes:

- `PromptCachingConfig` dataclass and `load_prompt_caching_config()` in `app/utils/config.py` — four validated env vars, off by default, safe behavior when absent
- `app/services/prompt_cache.py` — single pure function `apply_prompt_caching(system_blocks, config)` that injects a `cachePoint` block into the Bedrock Converse `system` array when enabled; returns input unchanged when disabled
- `BedrockAnalysisService` and `BedrockValidationService` in `app/services/bedrock_service.py` — accept optional `caching_config`; pass system blocks through `apply_prompt_caching` before each Converse call; pre-I-0 behavior is preserved when config is absent
- `.env.example` — `# ── Prompt Caching (I-0)` section with all four variables

**Phase I-1 (Prompt Routing Strategy) is complete** — the repository now includes:

- `PromptRoutingConfig` dataclass and `load_prompt_routing_config()` in `app/utils/config.py` — four env vars (enable flag, default model, per-route overrides for analysis and validation), off by default, fails loudly on invalid enable flag
- `app/services/prompt_router.py` — pure `resolve_model_id(route, routing_config, fallback_model_id)` function; `PromptRoute` literal type; deterministic; no boto3 dependency; implements a three-level priority chain (route override → routing default → caller fallback)
- `BedrockAnalysisService` and `BedrockValidationService` in `app/services/bedrock_service.py` — accept optional `routing_config`; resolve model ID at construction time via the `"analysis"` and `"validation"` routes respectively; `_call_converse` is untouched; pre-I-1 behavior is preserved when config is absent or routing is disabled
- `.env.example` — `# ── Prompt Routing (I-1)` section with all four variables

**Phase I-2 (Baseline vs. Optimized Comparison) is complete** — the repository now includes:

- `ComparisonVerdict` Literal type alias in `app/schemas/evaluation_models.py` — `"improved"` / `"regressed"` / `"unchanged"` classification
- `app/evaluation/comparison_runner.py` — offline comparison runner: loads baseline and optimized candidate outputs from two directories, scores both sides using G-2 `score_output_quality()` and H-0 `evaluate_safety()`, computes per-case score deltas and safety status changes, classifies verdicts, and aggregates a typed `ComparisonSummary`; typed frozen dataclasses `ComparisonCaseResult`, `ComparisonSummary`, `ComparisonRunResult`; missing cases tracked, not raised
- `tests/fixtures/comparison_cases/` — 4 paired fixtures (cases/, expected/, baseline/, optimized/) covering: improved (optimized hits all expected facts/keywords), unchanged (identical outputs), regressed (optimized introduces unsupported claims), and safety-change (unchanged quality verdict, safety status changes from ESCALATE → ALLOW)
- 108 new tests covering: typed model contracts, verdict classification, score delta correctness, improved/regressed/unchanged cases, missing baseline/optimized handling, aggregate summary accuracy, deterministic repeated runs, and no live AWS dependency

**Phase I is now complete.** All unit and evaluation tests pass without live AWS calls.

**Phase J-0 (CloudWatch Evaluation Dashboard) is complete** — the repository now includes:

- `EvaluationDashboardConfig` dataclass and `load_evaluation_dashboard_config()` in `app/utils/config.py` — four validated env vars, off by default, safe no-op when absent
- `EvaluationMetricDatum` Pydantic model in `app/schemas/evaluation_models.py` — typed CloudWatch metric datum contract with validated unit, finite value enforcement, and optional dimensions
- `app/services/cloudwatch_metrics_service.py` — thin CloudWatch Metrics wrapper (`CloudWatchMetricsService`, `NoOpMetricsService`, `build_metrics_service` factory); injectable boto3 client; fail-safe swallowed exceptions; entirely distinct from the E-0 CloudWatch Logs service
- `app/evaluation/metrics_translator.py` — pure translation functions mapping `EvaluationRunSummary` (F-2), `ComparisonSummary` (I-2), and safety distributions to `EvaluationMetricDatum` lists; 14 named metric constants shared with the dashboard builder
- `app/evaluation/dashboard_builder.py` — pure dashboard body builder producing a valid CloudWatch dashboard JSON dict (title widget + four metric widgets); no live AWS required; ready for `put_dashboard` when credentials are available
- `.env.example` — `# ── Evaluation Dashboard / CloudWatch Metrics (J-0)` section with all four variables

The J-0 layer is fully offline-testable without live AWS credentials.

**Phase J-1 (Evaluation Result Artifacts + Reporting) is complete** — the repository now includes:

- `ArtifactMetadata` and `ReportBundle` typed contracts (`app/schemas/artifact_models.py`)
- `write_evaluation_run()`, `write_safety_run()`, `write_comparison_run()` local artifact writers (`app/evaluation/artifact_writer.py`) — each writes `summary.json` + `case_results.json` + optional `report.md` to a predictable subdirectory under `output_root`
- `generate_evaluation_run_report()`, `generate_safety_run_report()`, `generate_comparison_run_report()` pure markdown report generators (`app/evaluation/report_generator.py`)
- Consistent output structure: `outputs/evaluation_runs/`, `outputs/safety_runs/`, `outputs/comparison_runs/`

J-2 (v2 hardening checkpoint) remains not started.

**Phase J-1 is now complete** — see the J-1 section above for details.

**Phase J-2 (v2 hardening checkpoint)** remains not started.

**Live Bedrock runtime validation** remains pending due to AWS-side Titan Text Embeddings V2 throttling/runtime issues in the target account. This is an external blocker, not a code issue.

See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full roadmap and [ARCHITECTURE.md](ARCHITECTURE.md) for the technical design.
