# Deployment Foundation

This repository deploys as custom Python orchestration on AWS Lambda. It does
not deploy native Amazon Bedrock Agents.

## Stack Choice

Phase 7 uses AWS SAM because the current runtime is one Python Lambda function
plus environment configuration. The stack creates:

- Lambda function: `app.lambda_handler.lambda_handler`
- S3 document bucket for Lambda input reads plus intake/source uploads
- S3 output bucket for final `CaseOutput` archives
- Lambda service log group and CaseOps structured pipeline log group
- IAM role with explicit S3, Bedrock, CloudWatch Logs, CloudWatch Metrics, and optional Guardrails permissions
- CloudWatch metric filters and alarms for Lambda errors, throttles, duration,
  application errors, archive failures, and safety blocks

## Event Contract

Invoke with either inline text:

```json
{
  "source_type": "FDA",
  "document_date": "2026-03-30",
  "submitter_note": "FDA warning letter - quality system deficiencies",
  "document": {
    "filename": "advisory.txt",
    "text": "Document text to process."
  }
}
```

Or with an S3 object in the stack document bucket:

```json
{
  "source_type": "CISA",
  "document_date": "2026-03-30",
  "submitter_note": "Critical ICS vulnerability review",
  "s3": {
    "bucket": "replace-with-document-bucket",
    "key": "incoming/advisory.txt"
  }
}
```

The handler also accepts API Gateway-style events whose `body` is the same JSON
object. Inline documents may use `base64_content` instead of `text`.

## Deploy Dev

Prerequisites:

- AWS SAM CLI
- AWS credentials for the target account
- Bedrock model access in the target region
- An existing Bedrock Knowledge Base ID

For a public-repo overview of what must be supplied in your own AWS account,
see [public-release.md](public-release.md). For a step-by-step AWS preparation
manual, see [aws-bootstrap.md](aws-bootstrap.md).

Run local tests first:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

Run deployment preflight before building:

```bash
python3 scripts/deployment_preflight.py
```

If SAM CLI is not installed yet, this command will report that as a blocking
issue. For AWS-side checks only, use `--skip-sam`. See
[deployment-validation.md](deployment-validation.md).

Build and deploy:

```bash
sam build
sam deploy --guided \
  --stack-name bedrock-caseops-control-tower-dev \
  --parameter-overrides \
    EnvironmentName=dev \
    BedrockKbId=YOUR_DEV_KB_ID \
    BedrockModelId=anthropic.claude-3-haiku-20240307-v1:0
```

Or use the repeatable Phase 10 helper, which reads `.env` and passes the same
template parameters non-interactively:

```bash
python3 scripts/deploy_stack.py --environment dev
```

Use `--dry-run` to inspect the SAM commands without creating or updating AWS
resources:

```bash
python3 scripts/deploy_stack.py --environment dev --dry-run
```

If your configured model is an inference profile, provisioned model, imported
model, or another non-foundation-model ARN, pass `BedrockModelArn` explicitly so
the IAM policy grants `bedrock:InvokeModel` to the right resource.

## Deploy Staging

Use a separate stack and a separate Knowledge Base:

```bash
sam deploy \
  --stack-name bedrock-caseops-control-tower-staging \
  --parameter-overrides \
    EnvironmentName=staging \
    BedrockKbId=YOUR_STAGING_KB_ID \
    BedrockModelId=anthropic.claude-3-haiku-20240307-v1:0
```

The deployment helper supports environment-specific overrides. For example:

```bash
STAGING_BEDROCK_KB_ID=YOUR_STAGING_KB_ID \
STAGING_BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0 \
python3 scripts/deploy_stack.py --environment staging
```

Equivalent explicit arguments:

```bash
python3 scripts/deploy_stack.py \
  --environment staging \
  --kb-id YOUR_STAGING_KB_ID \
  --model-id anthropic.claude-3-haiku-20240307-v1:0
```

Keep dev and staging isolated by stack name, Knowledge Base ID, generated S3
buckets, log groups, and the `CASEOPS_ENVIRONMENT` metric dimension.

## Guardrails

Guardrails are disabled by default. To enable them:

```bash
sam deploy \
  --stack-name bedrock-caseops-control-tower-dev \
  --parameter-overrides \
    EnvironmentName=dev \
    BedrockKbId=YOUR_DEV_KB_ID \
    EnableGuardrails=true \
    GuardrailId=YOUR_GUARDRAIL_ID \
    GuardrailVersion=1
```

When enabled, the Lambda role receives only `bedrock:ApplyGuardrail` on the
configured Guardrail ARN.

With the helper:

```bash
python3 scripts/deploy_stack.py \
  --environment dev \
  --enable-guardrails \
  --guardrail-id YOUR_GUARDRAIL_ID \
  --guardrail-version 1
```

## Invoke

Get the function name:

```bash
aws cloudformation describe-stacks \
  --stack-name bedrock-caseops-control-tower-dev \
  --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue" \
  --output text
```

Invoke with an inline event:

```bash
aws lambda invoke \
  --function-name caseops-pipeline-dev \
  --payload fileb://events/lambda-inline-example.json \
  outputs/lambda-response.json
```

Validate the deployed stack and invocation evidence:

```bash
python3 scripts/operational_check.py \
  --stack-name bedrock-caseops-control-tower-dev \
  --region "$AWS_REGION" \
  --response-file outputs/lambda-response.json
```

For S3 input, upload a document to the stack document bucket, update
`events/lambda-s3-example.json`, then invoke the same way.

## Release Gate

For the production-readiness checklist, rollback commands, and cleanup
procedures, see [production-readiness.md](production-readiness.md).

## IAM Scope

The SAM template grants:

- `s3:GetObject` on the generated document bucket
- `s3:PutObject` on `documents/*` and `artifacts/intake/*` in the document bucket
- `s3:PutObject` on `outputs/*` in the output bucket
- `bedrock:InvokeModel` on the configured model ARN
- `bedrock:Retrieve` on the configured Knowledge Base ARN
- `bedrock:ApplyGuardrail` only when Guardrails are enabled
- `logs:CreateLogStream` and `logs:PutLogEvents` for Lambda logs
- `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents` for the CaseOps structured log group
- `cloudwatch:PutMetricData` constrained to the configured metrics namespace
