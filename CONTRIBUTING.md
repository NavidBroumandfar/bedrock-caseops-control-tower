# Contributing

This repository is a public portfolio/reference implementation for a custom
Bedrock-powered CaseOps pipeline. Contributions should preserve the current
architecture: application-level Python orchestration, explicit AWS service
wrappers, Pydantic contracts, deterministic safety gates, and no native Bedrock
Agents in the mainline runtime.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` only for local AWS-backed runs. Never commit
`.env`, generated outputs, account IDs, ARNs, request IDs, private logs, or
Lambda response payloads.

## Checks

Before opening a pull request:

```bash
make lint
make test
python3 -m compileall -q app scripts
sam validate --template-file template.yaml
```

Use `sam validate --template-file template.yaml --lint` when SAM/cfn-lint is
installed locally.

## Development Rules

- Keep agents thin: validate preconditions and delegate provider calls.
- Keep AWS client logic inside `app/services/`.
- Add or update Pydantic schemas before changing agent boundary contracts.
- Do not run analysis without retrieved evidence.
- Preserve claim-level traceability when adding findings or recommendations.
- Keep public docs sanitized and link to `docs/live-validation.md` instead of
  committing private AWS artifacts.

## Pull Requests

PRs should include:

- A concise summary of the behavior or documentation change.
- Tests for code changes, or a clear reason tests are not needed.
- Notes on any AWS-backed validation that was run.
- Confirmation that `.env` and `outputs/` remain ignored.
