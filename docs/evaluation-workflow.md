# Evaluation Operator Workflow

Last updated: 2026-06-06

The evaluation workflow is local by default and does not require live Bedrock
calls. It scores already-produced `CaseOutput` JSON files, writes artifacts
under `outputs/`, and can optionally publish CloudWatch metrics when
`CASEOPS_ENABLE_EVALUATION_METRICS=true`.

## Commands

### Run Output Evaluation

Use `eval run` when you have one candidate output JSON per evaluation case.
Files must be named `{case_id}.json`.

```bash
python3 -m app.cli eval run \
  --candidates-dir outputs/eval_candidates \
  --dataset-dir data/evaluation \
  --run-id eval-local-001
```

Artifacts:

```text
outputs/evaluation_runs/eval-local-001/
  summary.json
  case_results.json
  report.md
```

### Run Safety Evaluation

Use `eval safety` to run the curated adversarial safety suite.

```bash
python3 -m app.cli eval safety \
  --suite-id safety-local-001
```

Artifacts:

```text
outputs/safety_runs/safety-local-001/
  summary.json
  case_results.json
  report.md
```

### Compare Baseline vs. Optimized Outputs

Use `eval compare` when comparing two model, prompt, routing, or retrieval
configurations. Each directory must contain matching `{case_id}.json` files.

```bash
python3 -m app.cli eval compare \
  --baseline-dir outputs/baseline_candidates \
  --optimized-dir outputs/optimized_candidates \
  --dataset-dir data/evaluation \
  --run-id cmp-local-001
```

The comparison result reports:

- average baseline and optimized scores
- score delta
- improved, regressed, and unchanged case IDs
- safety status changes
- missing candidate files on either side

Artifacts:

```text
outputs/comparison_runs/cmp-local-001/
  summary.json
  case_results.json
  report.md
```

### Build the Dashboard Body

Use `eval dashboard` to generate the CloudWatch dashboard JSON body locally.

```bash
python3 -m app.cli eval dashboard
```

Artifact:

```text
outputs/evaluation_dashboard/dashboard.json
```

## Optional CloudWatch Metrics

Set these variables to publish metrics from `eval run`, `eval safety`, and
`eval compare`:

```bash
CASEOPS_ENABLE_EVALUATION_METRICS=true
CASEOPS_METRICS_NAMESPACE=CaseOps/Evaluation
CASEOPS_ENVIRONMENT=development
AWS_REGION=us-east-1
```

CloudWatch metrics are optional. If disabled, evaluation still writes all local
artifacts and reports.

## Practical Baseline vs. Optimized Loop

1. Run the pipeline for each evaluation case with the current config and save
   files as `outputs/baseline_candidates/{case_id}.json`.
2. Change one thing: model ID, prompt routing, caching, retrieval filter, or
   prompt text.
3. Run the pipeline again and save files as
   `outputs/optimized_candidates/{case_id}.json`.
4. Run `python3 -m app.cli eval compare ...`.
5. Treat regressions and safety status changes as release blockers unless they
   are explained and accepted.
