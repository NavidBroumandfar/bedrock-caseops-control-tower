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

These are design contracts followed across all implementation work. Intake and retrieval foundations are now implemented. Analysis, validation, orchestration, and CloudWatch logging contracts remain upcoming (Phases C–E).

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

**Phase 1 — v1 MVP (active) | Phases A and B complete**

### Completed
- **A-0** — repo foundation, source-of-truth docs, project scaffold
- **A-1** — local document intake pipeline (file validation, metadata validation, `document_id` generation, local intake artifact)
- **A-2** — S3 storage adapter; raw document and intake artifact uploads to S3
- **A-3** — typed intake registration handoff contract (`IntakeRegistration` result returned after intake)
- Real AWS S3 verification completed successfully
- **B-0** — retrieval contracts + evidence schemas (`EvidenceChunk`, `RetrievalRequest`, `RetrievalResult`)
- **B-1** — Bedrock Knowledge Base service wrapper (`kb_service.py`)
- **B-2** — retrieval workflow returning typed `RetrievalResult` with `EvidenceChunk` objects and citations

### Next step
- **C-0** — analysis output schemas

### Not yet implemented
- Analysis generation (Phase C)
- Validation / critic logic (Phase C)
- Supervisor orchestration (Phase D)
- Tool executor / escalation workflow (Phase D)
- CloudWatch logging (Phase E)

Reference: `ARCHITECTURE.md §5–6` for intake and retrieval flows. `PROJECT_SPEC.md §13` for the full subphase roadmap.

---

## Key Files

| File | Purpose |
|---|---|
| `README.md` | Public-facing project summary |
| `PROJECT_SPEC.md` | Scope, requirements, roadmap — **source of truth** |
| `ARCHITECTURE.md` | Technical design — **source of truth** |
| `docs/CURSOR_CONTEXT.md` | This file — quick orientation only |
| `app/schemas/` | Pydantic models — contracts between all components |
| `.env.example` | Environment variable reference |
