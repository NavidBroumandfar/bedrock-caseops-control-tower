# AWS Bootstrap Guide

Last updated: 2026-06-07

This guide is the practical user manual for bringing your own AWS account to
this repository.

The repository does not include a universal one-command AWS installer. That is
intentional. Bedrock model access, account permissions, quotas, regions, costs,
Knowledge Base storage choices, and Guardrail policies are account-specific.

Use this guide to prepare your account, then use the repository's SAM deployment
helper to deploy the CaseOps Lambda runtime.

## What This Guide Creates

You will prepare:

- AWS credentials and region.
- Bedrock model access.
- A Bedrock Knowledge Base with indexed documents.
- Optional source-type metadata filtering.
- Optional Bedrock Guardrail.
- Local `.env` values.
- A dev Lambda stack through this repo's SAM template.
- A smoke test and operational validation run.

The SAM template creates the application runtime resources:

- Lambda function.
- Lambda IAM role.
- Document bucket.
- Output bucket.
- Lambda service log group.
- Structured CaseOps pipeline log group.
- CloudWatch metric filters and alarms.

The SAM template does not create the Bedrock Knowledge Base or Guardrail.

## Prerequisites

Install local tooling:

```bash
python3 --version
aws --version
sam --version
```

Recommended Python version: `3.12`.

Confirm AWS identity:

```bash
aws sts get-caller-identity
```

Confirm the target region:

```bash
export AWS_REGION=us-east-2
aws configure get region
```

Use any Bedrock-supported region where your chosen model and Knowledge Bases
are available.

## Step 1: Enable Bedrock Model Access

In your AWS account, enable access to the model you want the pipeline to call.

Recommended starting point:

```bash
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
```

The live validation for this repo used a Converse-compatible model. Your model
must support the Bedrock Converse API.

Model access approval is account-specific and cannot be granted by this
repository.

## Step 2: Create a Knowledge Base Source Bucket

Create an S3 bucket for source documents used by your Bedrock Knowledge Base.
Keep it private.

Example:

```bash
export CASEOPS_KB_SOURCE_BUCKET=your-caseops-kb-source-bucket

aws s3 mb "s3://${CASEOPS_KB_SOURCE_BUCKET}" --region "$AWS_REGION"
aws s3api put-public-access-block \
  --bucket "$CASEOPS_KB_SOURCE_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

If your organization requires custom encryption, lifecycle rules, bucket
policies, or access logging, apply those controls before ingestion.

## Step 3: Upload Sample Documents

Upload the repository sample documents to a source prefix:

```bash
export CASEOPS_KB_SOURCE_PREFIX=kb-source

aws s3 sync data/sample_documents/ \
  "s3://${CASEOPS_KB_SOURCE_BUCKET}/${CASEOPS_KB_SOURCE_PREFIX}/sample_documents/"
```

For a production use case, replace these sample documents with your approved
source corpus.

## Step 4: Add Source-Type Metadata Sidecars

Source-type filtering works only when the indexed documents have Bedrock
Knowledge Base metadata sidecars.

For each document, create a matching `.metadata.json` file next to the source
object. Example for an FDA document:

```json
{
  "metadataAttributes": {
    "source_type": "FDA"
  }
}
```

Supported source types in this repository:

- `FDA`
- `CISA`
- `Incident`
- `Other`

Example sidecar object name:

```text
s3://your-caseops-kb-source-bucket/kb-source/sample_documents/fda_warning_letter_01.md.metadata.json
```

If you do not want source-type filtering, keep
`CASEOPS_ENABLE_SOURCE_TYPE_FILTER=false`.

## Step 5: Create the Bedrock Knowledge Base

Create a Bedrock Knowledge Base in your AWS account and region using the source
bucket and prefix from the previous steps.

You can do this with the AWS Console or your own infrastructure tooling. The
required outcome is:

- A Knowledge Base ID.
- A data source connected to your S3 source prefix.
- An ingestion job that completes successfully.
- At least one retrievable document chunk.

Record these values:

```bash
BEDROCK_KB_ID=your-knowledge-base-id
CASEOPS_KB_DATA_SOURCE_ID=your-data-source-id
```

Validate the Knowledge Base:

```bash
aws bedrock-agent get-knowledge-base \
  --knowledge-base-id "$BEDROCK_KB_ID" \
  --region "$AWS_REGION"

aws bedrock-agent list-data-sources \
  --knowledge-base-id "$BEDROCK_KB_ID" \
  --region "$AWS_REGION"
```

Start or re-run ingestion when you change source documents:

```bash
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$BEDROCK_KB_ID" \
  --data-source-id "$CASEOPS_KB_DATA_SOURCE_ID" \
  --region "$AWS_REGION"
```

Wait until the ingestion job reports `COMPLETE` before testing the full
pipeline.

## Step 6: Optional Guardrail

Guardrails are optional. If you want runtime Guardrails enforcement, create a
Bedrock Guardrail in your AWS account and record:

```bash
CASEOPS_ENABLE_GUARDRAILS=true
CASEOPS_GUARDRAIL_ID=your-guardrail-id
CASEOPS_GUARDRAIL_VERSION=1
```

If you do not want Guardrails, use:

```bash
CASEOPS_ENABLE_GUARDRAILS=false
```

## Step 7: Configure Local `.env`

Create a local `.env` file:

```bash
cp .env.example .env
```

Minimum live-run values:

```bash
AWS_REGION=us-east-2
BEDROCK_KB_ID=your-knowledge-base-id
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
CASEOPS_ENABLE_SOURCE_TYPE_FILTER=true
CASEOPS_ENABLE_GUARDRAILS=false
```

For deployment helper defaults:

```bash
CASEOPS_DEPLOY_ENVIRONMENT=dev
CASEOPS_STACK_NAME=bedrock-caseops-control-tower-dev
DEV_BEDROCK_KB_ID=your-dev-knowledge-base-id
DEV_BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
DEV_CASEOPS_ENABLE_GUARDRAILS=false
```

If Guardrails are enabled:

```bash
DEV_CASEOPS_ENABLE_GUARDRAILS=true
DEV_CASEOPS_GUARDRAIL_ID=your-guardrail-id
DEV_CASEOPS_GUARDRAIL_VERSION=1
```

Never commit `.env`.

## Step 8: Validate Local Configuration

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

Run config checks:

```bash
python3 -m app.cli doctor
python3 -m app.cli check-config
python3 scripts/deployment_preflight.py --skip-sam
```

Run a live KB smoke test:

```bash
CASEOPS_ENABLE_LIVE_AWS_SMOKE=true python3 scripts/live_kb_smoke.py
```

## Step 9: Run the CLI Pipeline

```bash
python3 -m app.cli run data/sample_documents/fda_warning_letter_01.md \
  --source-type FDA \
  --document-date 2026-03-30 \
  --submitter-note "FDA warning letter quality system deficiencies"
```

Expected output shape:

```text
[ok] Pipeline completed.
     document_id      : doc-...
     session_id       : sess-...
     severity         : ...
     safety status    : ...
     output           : outputs/{document_id}.json
```

## Step 10: Deploy the Dev Lambda Stack

Run preflight:

```bash
python3 scripts/deployment_preflight.py
```

Inspect deployment commands without changing AWS:

```bash
python3 scripts/deploy_stack.py --environment dev --dry-run
```

Deploy:

```bash
python3 scripts/deploy_stack.py --environment dev
```

The stack creates its own document and output buckets. It does not use the
Knowledge Base source bucket from Step 2 as the runtime output bucket.

## Step 11: Invoke Lambda

```bash
aws lambda invoke \
  --function-name caseops-pipeline-dev \
  --region "$AWS_REGION" \
  --payload fileb://events/lambda-inline-example.json \
  outputs/dev-lambda-response.json
```

Validate the invocation evidence:

```bash
python3 scripts/operational_check.py \
  --stack-name bedrock-caseops-control-tower-dev \
  --region "$AWS_REGION" \
  --response-file outputs/dev-lambda-response.json
```

## Step 12: Optional Staging and Production Separation

For staging or production, repeat the Knowledge Base setup with isolated source
buckets, data sources, vector indexes, SAM stacks, and output buckets.

Recommended environment variables:

```bash
STAGING_BEDROCK_KB_ID=your-staging-kb-id
STAGING_BEDROCK_MODEL_ID=your-model-id
STAGING_CASEOPS_ENABLE_GUARDRAILS=true
STAGING_CASEOPS_GUARDRAIL_ID=your-guardrail-id
STAGING_CASEOPS_GUARDRAIL_VERSION=1

PRODUCTION_BEDROCK_KB_ID=your-production-kb-id
PRODUCTION_BEDROCK_MODEL_ID=your-model-id
PRODUCTION_CASEOPS_ENABLE_GUARDRAILS=true
PRODUCTION_CASEOPS_GUARDRAIL_ID=your-guardrail-id
PRODUCTION_CASEOPS_GUARDRAIL_VERSION=1
```

Deploy staging:

```bash
python3 scripts/deploy_stack.py --environment staging
```

Deploy production only after staging validation passes:

```bash
python3 scripts/deploy_stack.py --environment production
```

Run exactly one synthetic production canary before any real production traffic.

## Troubleshooting

`AccessDeniedException`

- Confirm the AWS identity has permissions for Bedrock, S3, IAM, Lambda,
  CloudFormation, CloudWatch Logs, and CloudWatch Metrics.
- Confirm model access is enabled in the selected region.

`ResourceNotFoundException` for Knowledge Base

- Confirm `BEDROCK_KB_ID`.
- Confirm `AWS_REGION`.
- Confirm the Knowledge Base was created in the same account and region.

Zero retrieval results

- Confirm ingestion completed.
- Confirm source documents were uploaded to the data source prefix.
- If source-type filtering is enabled, confirm metadata sidecars contain the
  correct `source_type`.

Guardrail failures

- Confirm `CASEOPS_GUARDRAIL_ID`.
- Confirm `CASEOPS_GUARDRAIL_VERSION`.
- Confirm the Guardrail is available in the selected region.

SAM deploy failures

- Run `python3 scripts/deployment_preflight.py`.
- Confirm the deploying identity can create IAM roles and CloudFormation stacks.
- Run `python3 scripts/deploy_stack.py --environment dev --dry-run` to inspect
  parameters.

## Cleanup

Delete a SAM stack:

```bash
aws cloudformation delete-stack \
  --stack-name bedrock-caseops-control-tower-dev \
  --region "$AWS_REGION"
```

Delete Knowledge Base resources only when you are sure they are no longer
needed. The exact cleanup order depends on how your Knowledge Base storage and
data source were created.

At minimum, review:

- Bedrock Knowledge Base.
- Bedrock data source.
- Source S3 bucket and documents.
- Vector storage or index resources.
- Optional Guardrail.
- CloudWatch log groups.
- Generated SAM document/output buckets.

## Public-Repo Safety Reminder

Do not commit:

- `.env`
- `outputs/`
- AWS credentials
- generated bucket names
- account IDs
- ARNs
- CloudWatch log stream names
- Lambda response payloads from private environments
