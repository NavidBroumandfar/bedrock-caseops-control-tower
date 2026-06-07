# Phase 9: Live Deployment Validation

Phase 9 validates that the Phase 7 deployment foundation can be deployed in a
real AWS account. It does not change the product architecture chosen in
[ADR 0001](adr/0001-keep-custom-bedrock-orchestration.md).

## Scope

Phase 9 checks:

- AWS CLI identity is available for the target account.
- SAM CLI is installed for build/deploy.
- `AWS_REGION`, `BEDROCK_KB_ID`, and `BEDROCK_MODEL_ID` are configured.
- Guardrails settings are internally consistent when enabled.
- AWS CloudFormation accepts `template.yaml`.
- The configured Bedrock Knowledge Base is reachable and reports status/data sources.
- A dev stack can be deployed and invoked with the sample Lambda event.

## Current Workspace Result

On 2026-06-07:

- AWS CLI is installed.
- SAM CLI `1.161.1` is installed and available on `PATH`.
- AWS identity resolved for the target account.
- `AWS_REGION`, `BEDROCK_KB_ID`, and `BEDROCK_MODEL_ID` are set via `.env`.
- `python3 scripts/deployment_preflight.py` passed without `--skip-sam`.
- CloudFormation accepted `template.yaml`.
- The configured Knowledge Base is reachable, `ACTIVE`, and has 1 data source.
- `sam build` succeeded.
- `sam deploy` created the dev stack `bedrock-caseops-control-tower-dev`.
- The deployed Lambda `caseops-pipeline-dev` returned AWS invoke status code `200` and application status `ok`.
- The final `CaseOutput` was archived to S3.

See `docs/live-validation.md` for public-safe validation evidence. Exact
account IDs, ARNs, generated bucket names, output object IDs, and CloudWatch log
stream names are intentionally not published.

## Run Preflight

```bash
python3 scripts/deployment_preflight.py
```

If SAM is not installed but you still want the AWS-side checks:

```bash
python3 scripts/deployment_preflight.py --skip-sam
```

Machine-readable output:

```bash
python3 scripts/deployment_preflight.py --skip-sam --json
```

## Install SAM CLI

Install the AWS SAM CLI with the method appropriate for the workstation, then
rerun:

```bash
sam --version
python3 scripts/deployment_preflight.py
```

## Deploy Dev Stack

After preflight passes:

```bash
sam build
sam deploy --guided \
  --stack-name bedrock-caseops-control-tower-dev \
  --parameter-overrides \
    EnvironmentName=dev \
    BedrockKbId="$BEDROCK_KB_ID" \
    BedrockModelId="$BEDROCK_MODEL_ID"
```

For repeatable non-interactive deployment, use the Phase 10 helper:

```bash
python3 scripts/deploy_stack.py --environment dev
```

For staging, pass a separate Knowledge Base with either environment variables
or explicit arguments:

```bash
STAGING_BEDROCK_KB_ID=YOUR_STAGING_KB_ID \
STAGING_BEDROCK_MODEL_ID="$BEDROCK_MODEL_ID" \
python3 scripts/deploy_stack.py --environment staging
```

## Invoke Dev Stack

```bash
aws lambda invoke \
  --function-name caseops-pipeline-dev \
  --payload fileb://events/lambda-inline-example.json \
  outputs/lambda-response.json
```

Expected result:

- Lambda response `statusCode` is `200`.
- Response body has `status: "ok"`.
- Output bucket contains `outputs/{document_id}/case_output.json`.
- CloudWatch contains Lambda service logs and CaseOps structured logs.

## Operational Check

After invoking Lambda, run the Phase 10 operational validation helper:

```bash
python3 scripts/operational_check.py \
  --stack-name bedrock-caseops-control-tower-dev \
  --region "$AWS_REGION" \
  --response-file outputs/lambda-response.json
```

This checks:

- CloudFormation stack status and required outputs.
- Lambda response AWS status and application status.
- S3 archive object existence.
- Lambda service log stream existence.
- Structured pipeline log stream existence for the invocation session.

For stack-only checks before a Lambda response exists:

```bash
python3 scripts/operational_check.py \
  --stack-name bedrock-caseops-control-tower-dev \
  --region "$AWS_REGION" \
  --skip-response
```

## Phase 10 Result

Phase 10 production-readiness validation completed on 2026-06-07. Staging was
deployed with a separate Knowledge Base and vector index, live Guardrails were
validated from Lambda, CloudWatch metric filters and alarms were created, and a
release gate was documented in `docs/production-readiness.md`.

See `docs/live-validation.md` for the exact staging stack, Guardrail, S3
archive, log stream, and monitoring evidence.

## Deployment Validation Exit Criteria

Phase 9 was completed on 2026-06-07:

- `python3 scripts/deployment_preflight.py` passed without `--skip-sam`.
- `sam build` succeeded.
- `sam deploy` created a dev stack.
- One Lambda invocation succeeded against the deployed stack.
- The resulting output JSON was archived to S3.
- The validation result was recorded in `docs/live-validation.md`.
