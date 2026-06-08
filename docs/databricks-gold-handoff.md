# Databricks Gold Handoff

Last updated: 2026-06-08

## Purpose

This repository consumes schema-versioned Databricks Gold export payloads as a downstream Bedrock CaseOps input boundary.

Databricks owns raw document ingestion, parsing, extraction, classification, and AI-ready Gold record production. This Bedrock repository owns retrieval, reasoning, validation, escalation, and `CaseOutput` generation after a Gold record has been accepted.

## Current Adapter

The first Bedrock-side implementation is local and offline-safe:

- Schema contracts: `app/schemas/databricks_gold_models.py`
- Consumer adapter: `app/services/databricks_gold_adapter.py`
- Fixture: `tests/fixtures/databricks_gold/sample_gold_payload.json`
- Tests: `tests/test_databricks_gold_adapter.py`
- Contract regression tests: `tests/test_databricks_gold_contract.py`

The adapter reads a local JSON payload, validates the schema, selects one Gold record, writes a normalized local snapshot, and returns the existing `IntakeResult` handoff used by the supervisor pipeline.

It does not call Databricks, Delta Share, S3, Bedrock, or any network service.

## CLI Intake

Use `intake-gold` to validate and register one local Gold export record without running retrieval or agents:

```bash
python3 -m app.cli intake-gold tests/fixtures/databricks_gold/sample_gold_payload.json
```

For multi-record payloads, select a specific record:

```bash
python3 -m app.cli intake-gold path/to/gold_payload.json \
    --gold-record-id gold-fda-20260608-001
```

The command prints the same registration summary as normal file intake and writes local artifacts under `outputs/databricks_gold/{document_id}/` by default.

The next local downstream steps are the case work item workflow documented in
`docs/case-work-item-workflow.md` and the supervisor case brief workflow
documented in `docs/case-brief-workflow.md`. Both start from normalized local
handoff objects, not from raw Gold payloads.

Use `brief-gold` to build the full local packet chain without running live
retrieval or agents:

```bash
python3 -m app.cli brief-gold tests/fixtures/databricks_gold/sample_gold_payload.json
```

## Payload Contract

The current schema version is:

```text
databricks-gold-export.v1
```

The expected producer is:

```text
databricks-caseops-lakehouse
```

Each Gold record must provide:

- `gold_record_id`
- `source_document_id`
- `source_filename`
- `source_type`
- `document_date`
- `retrieval_query`
- `lineage.gold_record_id`
- `lineage.source_document_id`

The adapter maps `retrieval_query` into `IntakeRecord.submitter_note`, which the existing retrieval workflow already uses as the Knowledge Base query signal.

## Safety Boundary

Local fixtures and payloads must not contain Databricks workspace URLs, account IDs, tokens, PATs, activation links, credentials, or customer data.

The current model rejects secret-like `custom_metadata` keys such as `workspace_url`, `account_id`, `token`, `pat`, `activation_link`, `credential`, and `secret`.

## Fixture Refresh

Sanitized payload fixtures live under `tests/fixtures/databricks_gold/`.
Refresh rules are documented in `tests/fixtures/databricks_gold/README.md`.

Contract fixtures should remain synthetic and local-only. They should prove the Bedrock repo can keep accepting the upstream Gold export shape without adding private workspace evidence or any live service integration.

## Current Caveat

The upstream Phase 4 closeout was a personal Databricks dev workspace smoke validation. It was not a staging or production enterprise deployment and it was not a Bedrock runtime validation.

This adapter only establishes the Bedrock-side consumer contract. Direct Delta Share consumption should be added later as a separate provider boundary after the local payload contract is stable.
