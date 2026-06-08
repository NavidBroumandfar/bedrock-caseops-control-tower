# Databricks Gold Fixture Refresh

These fixtures are sanitized contract examples for the downstream Bedrock intake adapter.

Refresh rules:

- Keep payloads on the documented `databricks-gold-export.v1` shape.
- Use synthetic IDs, filenames, dates, summaries, classifications, and lineage values.
- Do not include private platform URLs, real organization identifiers, auth material, email addresses, customer names, or generated private runtime output.
- Preserve `retrieval_query`; Bedrock maps it into `IntakeResult.record.submitter_note`.
- Keep fixtures local JSON only. Do not add Delta Share profiles, Databricks API responses, AWS responses, S3 locations, or Bedrock outputs.

After refreshing a fixture, run:

```bash
.venv/bin/python -m pytest tests/test_databricks_gold_contract.py tests/test_databricks_gold_adapter.py --tb=short
```

Before committing, also run the repository safety scan documented in the handoff workflow.
