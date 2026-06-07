# Live Validation

Last updated: 2026-06-07

This file records public-safe live validation evidence. Real account IDs,
resource ARNs, generated bucket names, Knowledge Base IDs, Guardrail IDs, log
stream names, request IDs, document IDs, session IDs, and local response files
are intentionally redacted from this public repository.

Private validation artifacts should stay in ignored local paths such as
`outputs/` or in a private release record.

## Result Summary

Status: passed with caveats.

The CaseOps runtime was validated against live AWS services:

- One live CLI run completed against Amazon Bedrock Knowledge Bases and the
  Bedrock Converse API.
- One dev Lambda invocation completed and archived a final `CaseOutput` to S3.
- Staging validation used an isolated staging Knowledge Base, staging Lambda
  stack, and live Guardrails allow/block checks.
- Production infrastructure was created and verified.
- Exactly one synthetic production canary was run.
- Real production traffic was not launched.

Final public release state:

| Field | Value |
|---|---|
| Live Bedrock KB retrieval validated | Yes |
| Dev Lambda validation | Passed |
| Staging Lambda validation | Passed |
| Live Guardrails allow/block validation | Passed |
| Production synthetic canary | Passed |
| Production synthetic canaries run | `1` |
| `production_traffic_launched` | `false` |

## Environment Shape

| Field | Public-safe value |
|---|---|
| AWS region | `us-east-2` |
| Runtime model family | Bedrock Converse-compatible hosted model |
| Retrieval service | Amazon Bedrock Knowledge Bases |
| Lambda runtime | `python3.12` |
| Deployment tool | AWS SAM |
| Guardrails | Enabled in staging and production validation |
| Output archive | S3 `outputs/{document_id}/case_output.json` |
| Structured logs | CloudWatch Logs `/caseops/pipeline/{environment}` |

## Live KB Smoke

The explicit live KB smoke script is `scripts/live_kb_smoke.py`.

Default behavior:

```bash
python3 scripts/live_kb_smoke.py
```

Result: skipped unless `CASEOPS_ENABLE_LIVE_AWS_SMOKE=true` or `--force` is
used.

Live smoke command:

```bash
python3 scripts/live_kb_smoke.py --force
```

Public-safe result:

| Field | Value |
|---|---|
| Retrieved count | `5` |
| Elapsed time | `1918 ms` |
| Top matched source | `sample_documents/sample_notice.txt` |
| Other matched source | `sample_documents/fda_warning_letter_01.md` |

## Full CLI Pipeline Run

Command shape:

```bash
python3 -m app.cli run data/sample_documents/fda_warning_letter_01.md \
  --source-type FDA \
  --document-date 2025-07-30 \
  --submitter-note "FDA warning letter quality system deficiencies corrective preventive action cleaning disinfection validation"
```

Public-safe result:

| Field | Value |
|---|---|
| Status | Success |
| Retrieval status | `success` |
| Retrieved chunks | `5` |
| Citation count | `5` |
| Severity | `Critical` |
| Category | `Regulatory / Cybersecurity Vulnerability` |
| Confidence score | `0.80` |
| Escalation required | `true` |
| Total CLI wall time | `4.857 seconds` |

## Retrieval Quality Follow-Up

The sample S3 source documents were given Bedrock Knowledge Base metadata
sidecars with a `source_type` string attribute. The retrieval service supports
`CASEOPS_ENABLE_SOURCE_TYPE_FILTER=true`, which sends a Bedrock metadata filter
matching `RetrievalRequest.source_type`.

Public-safe filtered smoke result:

| Field | Value |
|---|---|
| Source-type filter | `FDA` |
| Retrieved count | `4` |
| Elapsed time | `1854 ms` |
| Top matched sources | `sample_notice.txt`, `caseops-kb-test.md`, `fda_warning_letter_01.md` |
| Cross-domain CISA citations | `0` |

Public-safe filtered full pipeline result:

| Field | Value |
|---|---|
| Status | Success |
| Retrieved chunks | `4` |
| Citation count | `4` |
| Severity | `High` |
| Category | `Regulatory / Manufacturing Deficiency` |
| Confidence score | `0.70` |
| Cross-domain CISA citations | `0` |

## Dev Deployment Validation

Completed on 2026-06-07.

Deployment preflight:

```bash
python3 scripts/deployment_preflight.py
```

Public-safe result:

| Field | Value |
|---|---|
| Status | Passed |
| SAM CLI | Available |
| AWS identity | Resolved |
| Region | `us-east-2` |
| Knowledge Base status | `ACTIVE` |
| Knowledge Base data sources | `1` |
| CloudFormation template validation | Passed |

SAM build:

```bash
sam build
```

Result: succeeded.

SAM deploy command shape:

```bash
sam deploy \
  --stack-name bedrock-caseops-control-tower-dev \
  --region us-east-2 \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    EnvironmentName=dev \
    BedrockKbId=YOUR_DEV_KB_ID \
    BedrockModelId=YOUR_MODEL_ID \
    EnableGuardrails=false \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset
```

Public-safe Lambda invoke result:

| Field | Value |
|---|---|
| AWS invoke status code | `200` |
| Application status | `ok` |
| Severity | `High` |
| Category | `Regulatory / Manufacturing Deficiency` |
| Confidence score | `0.80` |
| Escalation required | `true` |
| Safety status | `escalate` |
| Citation count | `4` |
| S3 archive | Verified |
| Lambda service logs | Verified |
| Structured pipeline logs | Verified |

## Staging Operational Validation

Completed on 2026-06-07.

Public-safe staging result:

| Field | Value |
|---|---|
| Stack status | `UPDATE_COMPLETE` |
| Staging Knowledge Base | Isolated from dev |
| Staging vector index | Isolated from dev |
| Staging Lambda invocation | Passed |
| Staging S3 archive | Verified |
| Staging Lambda logs | Verified |
| Staging structured logs | Verified |

Staging Guardrails block-path invocation:

| Field | Value |
|---|---|
| AWS invoke status code | `200` |
| Lambda response `statusCode` | `422` |
| Application status | `blocked` |
| Safety status | `block` |
| Safety issue code | `guardrail_intervention` |
| Structured Lambda log event | `safety_blocked` |

Operational validation command shape:

```bash
python3 scripts/operational_check.py \
  --stack-name bedrock-caseops-control-tower-staging \
  --region us-east-2 \
  --response-file outputs/staging-lambda-response.json
```

Result:

| Check | Status |
|---|---|
| CloudFormation stack status and outputs | Passed |
| Lambda response AWS/application status | Passed |
| S3 archive object existence | Passed |
| Lambda service log stream existence | Passed |
| Structured pipeline log stream existence | Passed |

Monitoring resources:

| Resource | Status |
|---|---|
| Lambda errors alarm | Created |
| Lambda throttles alarm | Created |
| Lambda duration alarm | Created |
| Application errors metric filter and alarm | Created |
| Archive failures metric filter and alarm | Created |
| Safety blocks metric filter and alarm | Created |

## Production Synthetic Canary

Completed on 2026-06-07.

Production traffic flag:

| Field | Value |
|---|---|
| `production_traffic_launched` | `false` |
| Synthetic Lambda invokes run | `1` |
| Production traffic launched | No |

Pre-canary production state:

| Check | Result |
|---|---|
| Production output bucket | Empty before invocation |
| Production Lambda service logs | No log streams before invocation |
| Production structured pipeline logs | No log streams before invocation |

Production deployment identity:

| Field | Public-safe value |
|---|---|
| AWS account | Redacted |
| AWS identity | Redacted |
| Region | `us-east-2` |
| Stack status | `CREATE_COMPLETE` |
| Lambda runtime | `python3.12` |
| Memory / timeout | `1024 MB` / `900 seconds` |
| Production Knowledge Base status | `ACTIVE` |
| Production data source status | `AVAILABLE` |
| Production ingestion status | `COMPLETE` |
| Production Guardrail status | `READY` |
| Lambda guardrails setting | `CASEOPS_ENABLE_GUARDRAILS=true` |

Canary command shape:

```bash
aws lambda invoke \
  --function-name caseops-pipeline-production \
  --region us-east-2 \
  --payload fileb://events/lambda-inline-example.json \
  outputs/production-canary-response.json
```

AWS invoke result:

| Field | Value |
|---|---|
| AWS invoke status code | `200` |
| Executed version | `$LATEST` |
| Response file | `outputs/production-canary-response.json` |

Lambda response body:

| Field | Value |
|---|---|
| Application status | `ok` |
| Severity | `High` |
| Category | `Regulatory / Manufacturing Deficiency` |
| Confidence score | `1.0` |
| Escalation required | `false` |
| Citation count | `3` |
| Safety status | `allow` |
| Safety issue count | `0` |
| Safety artifact path | Lambda-local `/tmp/.../*.safety.json` |
| S3 archive | Verified |

Case output evidence:

| Field | Value |
|---|---|
| Grounded claims | `4` |
| Claim validations | `4` |
| Unsupported claims | `0` |
| Archived JSON matches response `case_output` | `true` |
| Validated by | `tool-executor-agent-v1` |

Citations:

| Source | Relevance score |
|---|---|
| `fda_warning_letter_01.md` | `0.5107359099192422` |
| `sample_notice.txt` | `0.4538062212604815` |
| `fda_recall_01.md` | `0.4060596476201803` |

S3 archive verification:

| Field | Value |
|---|---|
| Key shape | `outputs/{document_id}/case_output.json` |
| Size | `4,008 bytes` |
| Server-side encryption | `AES256` |
| Object metadata | `document_id={document_id}` |

Lambda service log evidence:

| Field | Value |
|---|---|
| Runtime init | `python:3.12` |
| Duration | `4689.07 ms` |
| Billed duration | `5909 ms` |
| Max memory used | `116 MB` |
| Lambda service errors observed | None in retrieved events |

Structured pipeline log evidence:

| Field | Value |
|---|---|
| Structured events observed | `session_start`, `intake_handoff_received`, `retrieval_start`, `retrieval_complete`, `analysis_start`, `analysis_complete`, `validation_start`, `validation_complete`, `output_generation_complete` |
| Retrieval result | `retrieval_status=success`, `chunk_count=3` |
| Analysis result | `severity=High`, `category=Regulatory / Manufacturing Deficiency`, `recommendation_count=3` |
| Validation result | `validation_status=pass`, `confidence_score=1.0`, `unsupported_claim_count=0` |
| Output result | `escalation_required=false`, `citation_count=3` |

Operational validation helper:

```bash
.venv/bin/python scripts/operational_check.py \
  --stack-name bedrock-caseops-control-tower-production \
  --region us-east-2 \
  --response-file outputs/production-canary-response.json \
  --json
```

Result:

| Check | Status |
|---|---|
| CloudFormation stack status and outputs | Passed |
| Lambda response AWS/application status | Passed |
| S3 archive object existence | Passed |
| Lambda service log stream existence | Passed |
| Structured pipeline log stream existence | Passed |

## Caveats

- This repository is public. Account-specific values are intentionally redacted
  from this file.
- The public repo does not publish live Lambda response bodies, local safety
  artifacts, CloudWatch log stream names, generated S3 bucket names, or account
  ARNs.
- The runtime expects the operator to bring an AWS account, model access, and an
  existing Bedrock Knowledge Base. See `docs/public-release.md` and
  `docs/deployment.md`.
- Real production traffic was intentionally not launched.

## Exit Criteria

Phase 3 live validation exit criteria are satisfied for runtime connectivity:
one sample document produced a valid `CaseOutput` with more than one live
Bedrock Knowledge Base citation.

Phase 9 live deployment validation exit criteria are satisfied:

- `python3 scripts/deployment_preflight.py` passed without `--skip-sam`.
- `sam build` succeeded.
- `sam deploy` created the dev stack.
- One Lambda invocation succeeded against the deployed stack.
- The resulting output JSON was archived to S3.

Phase 10 production-readiness exit criteria are satisfied:

- Dev and staging stack deployment commands are documented and repeatable.
- Staging uses a separate Knowledge Base, vector index, buckets, Lambda
  function, and log groups.
- Guardrails allow/block behavior was validated live from Lambda.
- Operators can inspect health, logs, output archives, safety blocks, and
  archive/application failure signals.
- A production readiness checklist defines the minimum release evidence.

Phase 16 production synthetic canary exit criteria are satisfied:

- Exactly one synthetic production Lambda invocation was run with the committed
  sample event.
- The production Lambda response returned AWS status `200` and application
  status `ok`.
- The production S3 archive object exists and matches the response
  `case_output`.
- Lambda service logs and structured pipeline logs exist for the invocation.
- Runtime safety returned `safety_status=allow`, `safety_issue_count=0`, and a
  safety assessment artifact path.
- Production traffic remains disabled: `production_traffic_launched=false`.
