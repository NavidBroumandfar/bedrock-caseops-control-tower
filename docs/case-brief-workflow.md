# Supervisor Case Brief Workflow

Last updated: 2026-06-08

## Purpose

The supervisor case brief workflow is the local packet-building step after a
`CaseWorkItem`. It creates a deterministic `SupervisorCaseBrief` that an
operator or future supervisor runtime can inspect before live retrieval,
analysis, validation, or agent execution begins.

This workflow does not accept raw Databricks payloads. Databricks Gold payloads
must first become an `IntakeResult`, then a `CaseWorkItem`.

## Scope

Implemented:

- Schema: `app/schemas/case_context_models.py`
- Workflow: `app/workflows/case_brief_workflow.py`
- CLI: `python3 -m app.cli brief-gold ...`
- Tests: `tests/test_case_brief_workflow.py`

The workflow writes:

```text
outputs/case_briefs/{document_id}/case_brief.json
```

The case brief includes:

- case brief, work item, and document identifiers
- source filename, source type, and document date
- deterministic routing lane and priority hint
- expected retrieval request preview
- local and registered source artifact references
- live runtime requirements such as `BEDROCK_KB_ID`, `BEDROCK_MODEL_ID`, and
  `AWS_REGION`
- operator notes that make clear no live runtime has executed

## CLI

Build the full local Databricks-derived packet chain:

```bash
python3 -m app.cli brief-gold tests/fixtures/databricks_gold/sample_gold_payload.json
```

This command performs:

```text
Databricks Gold payload
  -> IntakeResult
  -> CaseWorkItem
  -> SupervisorCaseBrief
```

Use `--output-root` to choose the local artifact root:

```bash
python3 -m app.cli brief-gold tests/fixtures/databricks_gold/sample_gold_payload.json \
    --output-root outputs
```

For multi-record payloads, select a specific record:

```bash
python3 -m app.cli brief-gold path/to/gold_payload.json \
    --gold-record-id gold-fda-20260608-001
```

## Non-Goals

This workflow does not call Databricks, Delta Share, S3, AWS SDKs, Bedrock,
Knowledge Bases, vector search, retrieval providers, analysis agents, validation
agents, or the final pipeline.

It is a local supervisor-preparation packet only. A brief being ready means the
handoff is structured and reviewable; it does not mean live retrieval or
reasoning has been executed.
