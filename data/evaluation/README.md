# F-1 Evaluation Dataset

This directory contains the **reference evaluation dataset** for the Bedrock CaseOps Multi-Agent Control Tower.

It is the F-1 artifact — the curated dataset foundation that the F-2 automated scoring runner and downstream G/H/J phases will consume.

---

## Purpose

The dataset provides a small, intentional set of reference cases against which pipeline outputs can be scored.
Each case defines:

- what document is being evaluated
- what a correct pipeline run should produce (severity, category, escalation, key facts)
- what the pipeline must **not** claim (forbidden assertions)
- optional retrieval expectations for G-phase scoring

The design goal is **keyword + concept coverage**, not exact string matching.
Expected outputs use fact lists and keyword sets so that the F-2 scorer can assess whether key concepts are present without requiring word-for-word match.

---

## Structure

```
data/evaluation/
├── README.md                        ← this file
├── cases/                           ← EvaluationCase fixtures (F-0 schema)
│   ├── eval-fda-001.json            — FDA warning letter: quality system deficiencies
│   ├── eval-fda-002.json            — FDA recall: undeclared pharmaceutical ingredients
│   ├── eval-cisa-001.json           — CISA ransomware advisory
│   ├── eval-cisa-002.json           — CISA ICS critical vulnerability advisory
│   ├── eval-incident-001.json       — Operational service outage incident
│   ├── eval-incident-002.json       — Data breach / exposure incident
│   └── eval-edge-001.json           — Minimal / ambiguous notice (edge case)
└── expected/                        ← ExpectedOutput fixtures (F-0 schema)
    ├── eval-fda-001.json
    ├── eval-fda-002.json
    ├── eval-cisa-001.json
    ├── eval-cisa-002.json
    ├── eval-incident-001.json
    ├── eval-incident-002.json
    └── eval-edge-001.json
```

Each `cases/` file maps 1:1 to an `expected/` file via shared `case_id`.

---

## Case Mix

| case_id          | Type       | Source document                            | Severity | Escalation |
|------------------|------------|---------------------------------------------|----------|------------|
| eval-fda-001     | Regulatory | fda_warning_letter_01.md                    | High     | Yes        |
| eval-fda-002     | Regulatory | fda_recall_01.md                            | High     | Yes        |
| eval-cisa-001    | Security   | cisa_advisory_01.md                         | High     | No         |
| eval-cisa-002    | Security   | cisa_ics_advisory_01.md                     | Critical | Yes        |
| eval-incident-001| Operational| incident_report_service_outage_01.md        | Medium   | No         |
| eval-incident-002| Operational| incident_report_data_breach_01.md           | High     | Yes        |
| eval-edge-001    | Edge       | sample_notice.txt                           | Low      | No         |

---

## Schema Alignment

All fixtures conform to the F-0 Pydantic schemas defined in `app/schemas/evaluation_models.py`:

- `EvaluationCase` — case descriptor (cases/ files)
- `ExpectedOutput` — reference judgment (expected/ files)
- `RetrievalExpectation` — embedded in case files where applicable

Dataset validity is verified by `tests/test_evaluation_dataset.py`.

---

## Source Document Policy

All referenced source documents are either:
- public-domain / publicly available documents (FDA, CISA)
- synthetic documents derived from public post-mortem patterns with no proprietary content

No confidential, proprietary, or personally identifiable data is included.

---

## Not Included Here

The following are intentionally **not** part of F-1:

- Scoring runner logic → F-2
- Retrieval quality metrics → G-0
- Adversarial test suite → H-2
- CloudWatch reporting → J-1
