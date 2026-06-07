# Security Policy

## Supported Scope

This repository is a public reference implementation for a custom
Bedrock-powered CaseOps pipeline. It includes local Python code, tests, AWS SAM
deployment assets, and documentation.

The repository does not contain:

- AWS credentials.
- A committed `.env` file.
- Private Lambda response payloads.
- Private CloudWatch logs.
- Generated S3 output artifacts.

Runtime outputs and local secrets are intentionally ignored by `.gitignore`.

## Reporting a Vulnerability

Please do not publish exploit details, credentials, account identifiers, or
private validation artifacts in a public issue.

Preferred reporting path:

1. Use GitHub's private vulnerability reporting feature if it is enabled for
   this repository.
2. If private reporting is not available, open a minimal GitHub issue that says
   a security concern exists, without including sensitive details.

## Public-Repo Hygiene

Before publishing changes, run:

```bash
git status --short --untracked-files=all
git check-ignore -v .env outputs/example.json
python3 -m ruff check .
rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!outputs/**' \
  --glob '!SECURITY.md' --glob '!tests/**' \
  'AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|aws_secret_access_key|BEGIN .*PRIVATE KEY'
python3 -m pytest -q
sam validate --template-file template.yaml
```

Expected posture:

- `.env` remains untracked and ignored.
- `outputs/` remains untracked and ignored.
- Account-specific AWS IDs, generated bucket names, ARNs, request IDs, and log
  stream names stay out of public docs.
- AWS credentials are managed through IAM roles, AWS profiles, or local
  `~/.aws/credentials`, not committed project files.

## Deployment Security Notes

The SAM template creates private S3 buckets with public access blocked and
server-side encryption enabled. IAM permissions are scoped to the generated
document bucket, generated output bucket, configured Bedrock model, configured
Knowledge Base, configured Guardrail when enabled, CloudWatch Logs, and the
configured CloudWatch Metrics namespace.

This project is not a substitute for your organization's security review,
threat model, or compliance process.
