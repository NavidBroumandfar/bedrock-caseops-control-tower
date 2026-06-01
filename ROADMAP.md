# Roadmap

Last updated: 2026-06-01

## Audit Baseline

The repository is a strong custom multi-agent RAG pipeline using AWS Bedrock services, but it is not yet a fully deployed AWS-native Bedrock Agents system.

Verified during audit:

- `python3 -m app.cli --help` works.
- `python3 -m pytest -q` passes with `2119 passed, 3 skipped`.
- No `.env` file is present in this workspace.
- `BEDROCK_KB_ID`, `BEDROCK_MODEL_ID`, `AWS_REGION`, and `AWS_PROFILE` are not currently exported.
- No Lambda handler, IaC, CI workflow, or native Bedrock Agent assets were found.

## Roadmap Principles

1. Make the current custom runtime honest and reliable before adding more AWS complexity.
2. Prove one live Bedrock Knowledge Base end-to-end run before claiming runtime completion.
3. Improve grounding from chunk-level citation to claim-level citation.
4. Wire existing safety, routing, caching, and evaluation modules into real operator workflows.
5. Decide explicitly whether the project remains custom orchestration or migrates to native Bedrock Agents.

## Phase 0: Documentation Alignment

Goal: make the repository describe the current system accurately.

Steps:

1. Update `README.md` to distinguish custom Python agents from native Amazon Bedrock Agents.
2. Remove or qualify the AWS Lambda claim until a Lambda handler and deployment path exist.
3. Replace shell examples using `python` with `python3` or add documented virtualenv setup that provides `python`.
4. Add links from `README.md` to `agents.md` and `ROADMAP.md`.
5. Update status language from "complete and correct" to "offline-validated; live AWS validation pending."

Exit criteria:

- A new reader can tell exactly what is implemented, what is simulated/offline, and what still requires live AWS validation.

## Phase 1: Local Runtime Hygiene

Goal: make local setup deterministic for a returning developer.

Steps:

1. Add automatic `.env` loading in the CLI startup path using `python-dotenv`.
2. Add a `pyproject.toml` or setup instructions that define a clean virtual environment workflow.
3. Add a `Makefile` or `scripts/` helpers for common commands:
   - `test`
   - `cli-help`
   - `intake-sample`
   - `live-smoke`
4. Add startup config validation for required live-run variables:
   - `BEDROCK_KB_ID`
   - `BEDROCK_MODEL_ID`
   - `AWS_REGION`
5. Add a small environment diagnostic command, for example:
   - `python3 -m app.cli doctor`

Exit criteria:

- A developer can clone the repo, create a virtualenv, install dependencies, run tests, and understand missing AWS config in under 10 minutes.

## Phase 2: Wire Existing Runtime Configuration

Goal: make existing config modules affect the actual CLI runtime.

Status: completed on 2026-06-01 for prompt caching, prompt routing, retry count, and escalation threshold wiring.

Steps:

1. Load `PromptCachingConfig` and pass it into `BedrockAnalysisService` and `BedrockValidationService`.
2. Load `PromptRoutingConfig` and pass it into both Bedrock services.
3. Replace hard-coded `_MAX_ATTEMPTS = 2` with configured `MAX_AGENT_RETRIES`.
4. Replace hard-coded `ESCALATION_CONFIDENCE_THRESHOLD = 0.60` with configured `ESCALATION_CONFIDENCE_THRESHOLD`.
5. Add CLI and service tests proving env config changes runtime behavior.

Exit criteria:

- `.env.example` options are not just documented; they are honored by the pipeline.

## Phase 3: Live AWS Smoke Validation

Goal: prove the pipeline works against real Bedrock and a real Knowledge Base.

Steps:

1. Create a narrow live smoke test script that is skipped unless explicitly enabled.
2. Validate AWS identity and region before making Bedrock calls.
3. Query the configured Knowledge Base directly and confirm at least one chunk is returned.
4. Run one full CLI case using a sample document and submitter note.
5. Save the resulting output JSON and note:
   - model ID
   - KB ID
   - region
   - retrieved citation count
   - latency
   - failure mode, if any
6. Document the exact live validation result in a `docs/live-validation.md` file.

Exit criteria:

- One sample document produces a valid `CaseOutput` with at least one citation from a live Bedrock KB query.

## Phase 4: Claim-Level Grounding

Goal: make the "every claim is cited" claim true at the schema level.

Steps:

1. Extend `AnalysisOutput` with structured findings or claim objects.
2. Require each finding/recommendation to include supporting `chunk_id` values.
3. Update the analysis prompt to produce claim-level evidence references.
4. Update validation to check each claim against the cited chunks, not just the overall evidence set.
5. Update `CaseOutput` so citations can be attached to specific findings and recommendations.
6. Update evaluation fixtures and scorers to measure claim-level citation coverage.

Exit criteria:

- Unsupported claim detection is based on explicit claim-to-evidence mappings.

## Phase 5: Runtime Safety and Guardrails

Goal: turn safety modules into enforced runtime gates.

Steps:

1. Apply Bedrock Guardrails to operator input when `CASEOPS_ENABLE_GUARDRAILS=true`.
2. Apply Bedrock Guardrails to generated output before writing the final JSON.
3. Run deterministic `evaluate_safety()` on every `CaseOutput`.
4. Add a runtime decision policy:
   - allow
   - warn
   - escalate
   - block
5. Persist safety assessment artifacts next to each output.
6. Surface safety status in the CLI summary.

Exit criteria:

- Unsafe or policy-violating output cannot silently pass as a normal successful result.

## Phase 6: Evaluation as an Operator Workflow

Goal: make evaluation easy to run outside tests.

Steps:

1. Add CLI commands for evaluation:
   - `eval run`
   - `eval safety`
   - `eval compare`
   - `eval dashboard`
2. Write evaluation artifacts to predictable directories under `outputs/`.
3. Add a small guide explaining how to compare baseline vs optimized model settings.
4. Optionally publish CloudWatch metrics when `CASEOPS_ENABLE_EVALUATION_METRICS=true`.

Exit criteria:

- Evaluation is a documented product workflow, not only a unit-test capability.

## Phase 7: Deployment Foundation

Goal: make the system deployable.

Steps:

1. Add a Lambda-compatible handler for the pipeline.
2. Add infrastructure-as-code using one stack choice:
   - AWS SAM
   - AWS CDK
   - Terraform
3. Define IAM permissions narrowly for:
   - S3 read/write
   - Bedrock runtime
   - Bedrock Knowledge Bases retrieve
   - CloudWatch Logs
   - CloudWatch Metrics
   - Guardrails, if enabled
4. Add deployment docs for dev/staging.
5. Add CI that runs tests on pull requests.

Exit criteria:

- The project can be deployed and invoked in a repeatable AWS environment.

## Phase 8: Native Bedrock Agents Decision

Goal: decide whether to migrate from custom orchestration to native Bedrock Agents.

Option A: keep custom orchestration.

- Best if you value debuggability, clear tests, and Python control flow.
- Rename claims from "Bedrock Agents" to "Bedrock-powered custom agents."
- Continue improving runtime safety, evaluation, and deployment.

Option B: add native Bedrock Agents.

- Best if you want to demonstrate Bedrock Agents specifically.
- Add agent definitions, aliases, action groups, Lambda tools, and `invoke_agent`.
- Keep existing Python agents as tool/action implementations where possible.

Recommended order:

1. Finish Phases 0-7 first.
2. Build one small Bedrock Agent proof of concept.
3. Compare operational complexity against the current supervisor workflow.
4. Migrate only if the native agent version provides clear portfolio or product value.

Exit criteria:

- The repository has an explicit architecture decision record explaining why it uses custom orchestration or native Bedrock Agents.

## Suggested Priority Order

1. Phase 0: Documentation Alignment
2. Phase 1: Local Runtime Hygiene
3. Phase 2: Wire Existing Runtime Configuration
4. Phase 3: Live AWS Smoke Validation
5. Phase 4: Claim-Level Grounding
6. Phase 5: Runtime Safety and Guardrails
7. Phase 6: Evaluation as an Operator Workflow
8. Phase 7: Deployment Foundation
9. Phase 8: Native Bedrock Agents Decision

## Near-Term Checklist

- [x] Add `.env` loading to the CLI.
- [x] Wire prompt routing and prompt caching into `_build_pipeline_deps()`.
- [x] Make escalation threshold configurable.
- [x] Make retry count configurable.
- [x] Add `doctor` or `check-config` CLI command.
- [x] Update README AWS service claims.
- [ ] Add live Bedrock smoke test script.
- [ ] Capture one successful live KB retrieval result.
- [ ] Add claim-level citation fields.
- [ ] Wire runtime safety assessment before output persistence.
- [ ] Add CI.
