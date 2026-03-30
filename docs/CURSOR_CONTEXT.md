# Cursor Context — Bedrock CaseOps Multi-Agent Control Tower

Use this file to orient a new Cursor chat session quickly.

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
app/
  agents/        ← agent classes (Retrieval, Analysis, Validation, ToolExecutor, Supervisor)
  services/      ← AWS service wrappers (s3, bedrock, kb, cloudwatch)
  workflows/     ← pipeline orchestration and supervisor routing
  schemas/       ← Pydantic models (IntakeMetadata, EvidenceChunk, AnalysisOutput, CaseOutput, etc.)
  utils/         ← id generation, logging, config, file helpers
notebooks/       ← exploration and prototyping only
tests/           ← unit tests; mock AWS, no live calls required
data/
  sample_documents/   ← public-domain test docs (FDA, CISA, synthetic)
  expected_outputs/   ← reference JSON for test assertions
outputs/         ← runtime output (gitignored)
docs/            ← this file + future architectural notes
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
- Bedrock Guardrails
- Bedrock Evaluations
- Prompt caching / routing
- Bedrock Flows
- Model customization
- Multi-region support

---

## Implementation Rules

- Agents are Python classes with a `run()` method that accepts and returns typed Pydantic models
- Agents do not call AWS clients directly — they call service methods from `app/services/`
- Service modules are thin wrappers; they do not contain business logic
- All outputs conform to the `CaseOutput` Pydantic schema defined in `app/schemas/`
- Citations are first-class: never dropped, never fabricated
- Escalation is config-driven (threshold in `.env`)
- Logs are structured JSON with session_id, document_id, agent, event fields
- No inline code comments that just describe what the code does

---

## Current Implementation Phase

**Phase 1 — v1 MVP:** Core agentic RAG pipeline

Next immediate step: build the document intake pipeline stub
- Accept local file path
- Assign document_id (doc-{YYYYMMDD}-{uuid4[:8]})
- Validate required metadata (source_type, document_date, filename)
- Build IntakeMetadata Pydantic model
- Stub the S3 upload call (implement service method, leave boto3 call for when credentials are confirmed)
- Return document_id to caller

See ARCHITECTURE.md §5 for the intake flow spec.
See PROJECT_SPEC.md §8 F1–F2 for the functional requirements.

---

## Key Files

- `README.md` — public-facing project summary
- `PROJECT_SPEC.md` — scope, requirements, roadmap (source of truth)
- `ARCHITECTURE.md` — technical design (source of truth)
- `docs/CURSOR_CONTEXT.md` — this file
- `app/schemas/` — Pydantic models are the contracts between all components
- `.env.example` — environment variable reference
