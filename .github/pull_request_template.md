## Summary

-

## Validation

- [ ] `make lint`
- [ ] `make test`
- [ ] `python3 -m compileall -q app scripts`
- [ ] `sam validate --template-file template.yaml`

## Public Repo Hygiene

- [ ] No `.env`, credentials, account IDs, ARNs, request IDs, private logs, or generated outputs are committed.
- [ ] Documentation remains clear that this is custom Bedrock-powered Python orchestration, not native Bedrock Agents.
- [ ] Public validation evidence is sanitized or linked from `docs/live-validation.md`.
