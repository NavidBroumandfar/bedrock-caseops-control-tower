# Case Work Item Workflow

Last updated: 2026-06-08

## Purpose

The case work item workflow is the first local downstream step after intake.
It starts from an existing `IntakeResult` and creates a deterministic
`CaseWorkItem` that a control-tower queue or operator can inspect before any
live retrieval or agent runtime begins.

This workflow does not accept raw Databricks payloads. Databricks Gold payloads
must first pass through the local Gold adapter and become the same `IntakeResult`
used by normal document intake.

## Scope

Implemented:

- Schema: `app/schemas/case_context_models.py`
- Workflow: `app/workflows/case_context_workflow.py`
- Tests: `tests/test_case_context_workflow.py`

The workflow writes:

```text
outputs/case_work_items/{document_id}/work_item.json
```

The work item includes:

- `work_item_id`
- `document_id`
- source filename, type, and date
- local intake artifact path
- local source artifact path
- storage mode and registered storage keys when present
- retrieval query and query source
- deterministic routing lane
- deterministic priority hint
- readiness status
- next local runtime step

The next local packet-building step is the supervisor case brief workflow
documented in `docs/case-brief-workflow.md`.

## Non-Goals

This workflow does not call Databricks, Delta Share, S3, AWS SDKs, Bedrock,
Knowledge Bases, vector search, retrieval providers, analysis agents, validation
agents, or the final pipeline.

It is a local control-tower context builder only.

## Relationship to Databricks Gold

The boundary is:

```text
Databricks Gold payload
  -> local Gold adapter
  -> IntakeResult
  -> CaseWorkItem
  -> SupervisorCaseBrief
```

This keeps the public story honest: Databricks produces sanitized, structured
Gold records; this Bedrock repo consumes the normalized intake handoff and
prepares local case context for downstream Bedrock workflows.
