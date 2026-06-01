# Agents

Last updated: 2026-06-01

## Purpose

This file documents the agentic architecture that exists in this repository today, and where it differs from native Amazon Bedrock Agents.

The current system is a custom Python multi-agent RAG pipeline that uses:

- Amazon Bedrock Knowledge Bases for retrieval
- Amazon Bedrock Converse API for model inference
- Local Python orchestration for agent sequencing
- Pydantic schemas as contracts between each agent boundary

It does not currently use native Amazon Bedrock Agents, agent aliases, action groups, or `invoke_agent`.

## Current Runtime Shape

```text
CLI
  |
  v
Document Intake
  |
  v
Retrieval Workflow -> Bedrock Knowledge Base
  |
  v
Supervisor Workflow
  |
  +--> Analysis Agent -> Bedrock Converse
  |
  +--> Validation Agent -> Bedrock Converse
  |
  v
Tool Executor Agent
  |
  v
CaseOutput JSON + optional S3 archive + logs
```

## Agent Inventory

| Component | File | Role | Runtime AWS dependency | Status |
|---|---|---|---|---|
| Supervisor / Planner | `app/workflows/supervisor_workflow.py` | Coordinates retrieval, analysis, validation, retry behavior, and empty-retrieval routing. | None directly; dependencies are injected. | Implemented |
| Retrieval Agent Layer | `app/workflows/retrieval_workflow.py`, `app/services/kb_service.py` | Builds retrieval request and queries Bedrock Knowledge Base for evidence chunks. | `bedrock-agent-runtime.retrieve` | Implemented |
| Analysis Agent | `app/agents/analysis_agent.py`, `app/services/bedrock_service.py` | Produces severity, category, summary, and recommendations from evidence. | `bedrock-runtime.converse` | Implemented |
| Validation / Critic Agent | `app/agents/validation_agent.py`, `app/services/bedrock_service.py` | Audits analysis output against retrieved chunks and produces confidence plus unsupported claims. | `bedrock-runtime.converse` | Implemented |
| Tool Executor Agent | `app/agents/tool_executor_agent.py` | Assembles final `CaseOutput`, maps chunks to citations, and applies escalation rules. | None | Implemented |

## Important Distinction

This repository has an agentic architecture, but the agents are application-level Python components. They are not native Bedrock Agents.

Native Bedrock Agents would usually introduce:

- Agent definitions managed by Bedrock
- Agent aliases
- Action groups
- Lambda-backed tool handlers
- `invoke_agent` calls
- AWS-side orchestration state

The current implementation keeps orchestration in the application. That is a valid and often simpler architecture for an MVP, but the README and architecture documents should describe it precisely as custom Bedrock-powered orchestration unless native Bedrock Agents are added.

## Current Strengths

- Clear separation of responsibilities across retrieval, analysis, validation, and output assembly.
- Agents use typed Pydantic contracts instead of passing unstructured dictionaries.
- Workflow layers do not instantiate AWS clients directly; service dependencies are injected.
- Empty retrieval is handled conservatively and escalated.
- Offline test coverage is strong: 2,133 passing tests were verified after Phase 2 runtime config wiring.
- Evaluation, safety, prompt routing, prompt caching, and reporting layers exist as testable modules.
- Prompt routing, prompt caching, retry count, and escalation threshold are wired into the CLI runtime path.

## Current Gaps

- Live AWS end-to-end execution has not been validated in this environment.
- `.env` is documented but not loaded automatically by the CLI.
- Guardrails configuration exists but is not wired into `_build_pipeline_deps()`.
- Citations are currently chunk-level citations attached to the final output, not claim-level citations attached to each summary claim or recommendation.
- Guardrails and deterministic safety policy are implemented as evaluation/foundation layers, not as enforced runtime gates in the CLI path.
- No Lambda handler, infrastructure-as-code, CI workflow, or native Bedrock Agent deployment exists yet.

## Future Agent Design Rules

Use these rules when extending the system:

1. Keep agent classes thin.
   Agents should validate preconditions and delegate provider calls. AWS client logic belongs in `app/services/`.

2. Keep schemas as the contract boundary.
   New agent outputs should get explicit Pydantic models before implementation.

3. Do not allow ungrounded analysis.
   Analysis should only run when retrieval produced evidence.

4. Prefer claim-level traceability.
   Any generated finding, recommendation, or escalation rationale should carry the evidence chunk IDs that support it.

5. Make runtime safety explicit.
   Guardrails and deterministic safety policy should be wired into the runtime path before presenting the system as hardened.

6. Choose one orchestration direction.
   Either keep custom Python orchestration and document it honestly, or add native Bedrock Agents with action groups and deployment assets.

## Candidate Native Bedrock Agent Evolution

If the project should become a true native Bedrock Agents system, the likely target shape is:

```text
CLI / Lambda trigger
  |
  v
Bedrock Supervisor Agent
  |
  +--> Retrieval action group
  +--> Analysis action group
  +--> Validation action group
  +--> Output / escalation action group
  |
  v
Structured CaseOutput
```

That migration should happen only after the current custom runtime is live-validated, because native Bedrock Agents will add deployment complexity and make debugging harder.
