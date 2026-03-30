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
│   └── utils/           # Logging, ID generation, file helpers
├── notebooks/           # Exploratory notebooks and prototypes
├── tests/               # Unit and integration tests
├── data/
│   ├── sample_documents/    # Public test documents (FDA, CISA, etc.)
│   └── expected_outputs/    # Reference outputs for validation
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

## Status

MVP in active development. See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full roadmap and [ARCHITECTURE.md](ARCHITECTURE.md) for the technical design.
