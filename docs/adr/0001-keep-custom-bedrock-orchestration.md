# ADR 0001: Keep Custom Bedrock-Powered Orchestration

Date: 2026-06-06

Status: Accepted

## Context

The repository implements an application-level Python multi-agent RAG pipeline:

- Bedrock Knowledge Bases retrieval through `bedrock-agent-runtime.retrieve`
- Bedrock Converse model calls through `bedrock-runtime.converse`
- Local supervisor orchestration for retrieval, analysis, validation, retry, and empty-retrieval routing
- Pydantic schemas at every agent boundary
- Runtime safety gates, claim-level grounding, evaluation workflows, and a Lambda/SAM deployment foundation

This is not a native Amazon Bedrock Agents deployment. Native Bedrock Agents would introduce managed agent definitions, aliases, action groups, and `InvokeAgent` runtime calls. AWS documents the native runtime as an `InvokeAgent` sequence with preprocessing, orchestration, action group invocation, Knowledge Base queries, session state, traces, and final output streaming options:

- [How Amazon Bedrock Agents works](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-how.html)
- [InvokeAgent API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_InvokeAgent.html)
- [Invoke an agent from your application](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-invoke-agent.html)
- [CreateAgentActionGroup API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_CreateAgentActionGroup.html)

The current system already has explicit orchestration, typed contracts, deterministic safety policy, and tests around the agent boundaries. Replacing the supervisor with native Bedrock Agents would add a second orchestration layer and require new deployment assets for agent versions, aliases, action groups, and action handlers.

## Decision

Keep the project on custom Python orchestration for the mainline product path.

Use the precise description "Bedrock-powered custom Python agents" or "custom Bedrock-powered orchestration" in README, docs, diagrams, and portfolio language. Do not describe the mainline implementation as native Amazon Bedrock Agents unless native agent resources and `InvokeAgent` calls are actually added.

## Rationale

Custom orchestration is the better fit for the current system because:

- The workflow is not open-ended chat; it is a structured case-review pipeline with a known sequence.
- Pydantic contracts make every boundary testable and keep output shape predictable.
- Safety, escalation, and claim-level citation checks need deterministic gates before persistence.
- The supervisor workflow and retry behavior are already covered by a large offline test suite.
- The Lambda/SAM foundation deploys the current runtime without translating it into action groups.
- Native Bedrock Agents would add operational complexity before proving a material product benefit.

Native Bedrock Agents are still useful for a separate proof of concept if the goal becomes demonstrating AWS-managed agent orchestration specifically.

## Consequences

The repository will:

- Continue investing in `app/workflows/`, `app/agents/`, `app/services/`, schemas, evaluation, safety, and deployment around the custom runtime.
- Keep AWS service access explicit through service wrappers rather than hiding orchestration behind `InvokeAgent`.
- Treat Bedrock Knowledge Bases, Converse, Guardrails, S3, CloudWatch Logs, and CloudWatch Metrics as direct service dependencies.
- Avoid native agent aliases and action groups in the mainline SAM stack.
- Revisit native Bedrock Agents only after live SAM deployment validation or when a product/portfolio requirement explicitly calls for native Agents.

## Revisit Triggers

Reconsider this decision if one or more of these become true:

- The project must demonstrate native Bedrock Agents as a named capability.
- The pipeline evolves from fixed case-review workflow into open-ended conversational task planning.
- Managed Bedrock Agent tracing, session state, or action-group routing provides clear operational value over the custom supervisor.
- The team is ready to maintain agent definitions, aliases, action groups, and Lambda action handlers as first-class deployment artifacts.

## Deferred Native Agent Proof of Concept

If a proof of concept is needed later, keep it separate from the mainline runtime:

1. Add one Bedrock Supervisor Agent.
2. Expose retrieval, analysis, validation, and output assembly as action groups or return-control actions.
3. Invoke it through `bedrock-agent-runtime.invoke_agent`.
4. Compare output determinism, traceability, debugging effort, deployment complexity, latency, and testability against the current Lambda handler.
5. Promote only if it beats the custom runtime on a concrete requirement.
