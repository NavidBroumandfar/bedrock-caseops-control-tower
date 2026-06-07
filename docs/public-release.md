# Public Release Guide

Last updated: 2026-06-07

This guide describes what a public user can reuse and what they must provide in
their own AWS account.

## Release Posture

This repository is public-safe as a reference implementation and portfolio
project. It is not a hosted service and it does not grant access to the
maintainer's AWS resources.

Public-safe state:

- No committed `.env`.
- No committed AWS credentials.
- No committed private keys.
- Runtime outputs are ignored under `outputs/`.
- Live validation docs use sanitized placeholders instead of account-specific
  AWS identifiers.
- Real production traffic was not launched.

## What Another User Can Reuse

A user can clone this repository and reuse:

- The Python multi-agent pipeline.
- Pydantic contract models.
- Bedrock Knowledge Base retrieval wrapper.
- Bedrock Converse analysis and validation services.
- Runtime safety and optional Bedrock Guardrails gates.
- Evaluation CLI workflows.
- AWS Lambda handler.
- AWS SAM deployment template.
- Operational validation helper.
- Sample Lambda invocation events.
- Test suite and sample documents.

## Bring Your Own AWS

To run the full live pipeline, a user must provide their own:

- AWS account and credentials.
- AWS region with Bedrock access.
- Bedrock model access.
- Bedrock Knowledge Base.
- Knowledge Base data source and indexed documents.
- Optional Bedrock Guardrail.
- Optional dev, staging, and production environment separation.

The SAM template deploys the application Lambda, document bucket, output
bucket, log groups, IAM role, metric filters, and alarms. It does not create a
Bedrock Knowledge Base or Guardrail from scratch. For a practical account setup
walkthrough, see `docs/aws-bootstrap.md`.

## Quickstart

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Run tests:

```bash
python3 -m pytest -q
```

Create local config:

```bash
cp .env.example .env
```

Fill in at minimum:

```bash
AWS_REGION=your-region
BEDROCK_KB_ID=your-knowledge-base-id
BEDROCK_MODEL_ID=your-bedrock-model-id
```

Run local diagnostics:

```bash
python3 -m app.cli doctor
python3 -m app.cli check-config
```

Run a local intake-only command:

```bash
python3 -m app.cli intake data/sample_documents/fda_warning_letter_01.md \
  --source-type FDA \
  --document-date 2026-03-30
```

Run the live pipeline only after AWS prerequisites are configured:

```bash
python3 -m app.cli run data/sample_documents/fda_warning_letter_01.md \
  --source-type FDA \
  --document-date 2026-03-30 \
  --submitter-note "FDA warning letter quality system deficiencies"
```

Deploy the Lambda stack:

```bash
python3 scripts/deployment_preflight.py
python3 scripts/deploy_stack.py --environment dev
```

For the full AWS preparation sequence, follow `docs/aws-bootstrap.md`.

## Knowledge Base Setup Expectations

The runtime expects a Bedrock Knowledge Base with source documents indexed and
retrievable. For source-type filtering, each S3 source document should have a
Bedrock metadata sidecar that includes:

```json
{
  "metadataAttributes": {
    "source_type": "FDA"
  }
}
```

Supported source types in this repo are:

- `FDA`
- `CISA`
- `Incident`
- `Other`

## Guardrails

Guardrails are disabled by default. To enable them, provide:

```bash
CASEOPS_ENABLE_GUARDRAILS=true
CASEOPS_GUARDRAIL_ID=your-guardrail-id
CASEOPS_GUARDRAIL_VERSION=1
```

For SAM deployment:

```bash
python3 scripts/deploy_stack.py \
  --environment dev \
  --enable-guardrails \
  --guardrail-id YOUR_GUARDRAIL_ID \
  --guardrail-version 1
```

## What This Repository Does Not Provide

- Hosted production service access.
- Maintainer AWS credentials or resource access.
- Native Amazon Bedrock Agents.
- Bedrock Knowledge Base creation automation.
- Guardrail creation automation.
- A web UI.
- Multi-user authentication.
- Full CI/CD release orchestration.

## Public Validation Evidence

The public `docs/live-validation.md` file records sanitized validation
evidence. Exact account IDs, resource ARNs, generated bucket names, CloudWatch
log streams, request IDs, document IDs, session IDs, and local response files
are intentionally withheld.
