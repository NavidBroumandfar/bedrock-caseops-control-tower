# Expected Outputs — Reference Fixtures

This directory contains **controlled reference fixtures** for the CaseOps pipeline.

These files represent what a valid, schema-compliant `CaseOutput` looks like for each
sample document in `data/sample_documents/`.  They are hand-authored fixtures, not
live AWS outputs.

---

## Purpose

- Demonstrate the CaseOutput schema shape to reviewers and interviewers
- Serve as reference data for test assertions and regression checks
- Show the expected structure for each supported document type (FDA, CISA)
- Illustrate escalation vs. non-escalation cases

---

## Important: These are NOT live AWS outputs

**Live Bedrock / Knowledge Base validation is currently pending** due to AWS-side
Titan Text Embeddings V2 throttling/runtime issues in the target account.

These fixtures were authored to:
1. Match the current `CaseOutput` Pydantic schema exactly
2. Use realistic but controlled values derived from the public sample documents
3. Represent what the pipeline would produce if live AWS calls succeeded

The `citations` in these fixtures reference the fake KB chunks defined in
`tests/fakes/fake_retrieval.py`, which is the same fake retrieval layer used in
all non-live unit and integration tests.

---

## Files

| File | Source Document | Source Type | Notes |
|---|---|---|---|
| `fda_warning_letter_01_expected.json` | `fda_warning_letter_01.md` | FDA | High severity, escalation triggered |
| `cisa_advisory_01_expected.json` | `cisa_advisory_01.md` | CISA | High severity, no escalation |

---

## Schema Reference

```json
{
  "document_id": "doc-YYYYMMDD-xxxxxxxx",
  "source_filename": "filename.md",
  "source_type": "FDA | CISA | Incident | Other",
  "severity": "Critical | High | Medium | Low",
  "category": "string",
  "summary": "string",
  "recommendations": ["string", ...],
  "citations": [
    {
      "source_id": "string",
      "source_label": "string",
      "excerpt": "string",
      "relevance_score": 0.0
    }
  ],
  "confidence_score": 0.0,
  "unsupported_claims": [],
  "escalation_required": false,
  "escalation_reason": null,
  "validated_by": "tool-executor-agent-v1",
  "session_id": "sess-xxxxxxxx",
  "timestamp": "ISO 8601"
}
```
