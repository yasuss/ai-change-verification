# Verification Plan and execution

The Verification Plan inventories each candidate check, its source, covered obligation or risk, safety class, selection decision, reason, declared material applicability dimensions, minimal operation contract, deterministic result interpretation where supported, and content identity.

Safety classes:

- `READ_ONLY_EXPECTED` — inspection expected not to mutate the subject.
- `REPO_LOCAL_MUTATION` — writes local state and belongs in disposable containment when practical.
- `INSTALL_OR_NETWORK` — requires explicit scope and network access.
- `CREDENTIAL_OR_EXTERNAL_SERVICE` — requires explicit credentials or external service.
- `DESTRUCTIVE_OR_IRREVERSIBLE` — never default to automatic execution.

Project-native command names do not establish safety. Run selected repository checks inside the host sandbox and approval boundary; ACV is not a generic arbitrary command runner. Selected checks should remain non-mutating when their class requires it.

Planning declares the material applicability dimensions for each selected check as bounded context keys. Deterministic capture records values for exactly those keys; later finalization compares them for freshness. Do not infer materiality by fingerprinting the full host.

The operation contract is intentionally small:

- command check: exact argv + repo-relative cwd;
- tool observation: tool + operation identity;
- external result: provider + resource-kind identity.

Do not build a generic workflow DSL. Mechanical evidence must match the planned operation contract and its `check_contract_digest`. A selected check used to support/contradict an obligation must explicitly cover that obligation.

For command checks, the check-bound `EXIT_CODE` rule deterministically maps a completed exit result to `OBSERVED_PASS` or `OBSERVED_FAIL`. Non-command observations use typed captured outcomes; authority for live provider/tool capture is outside the receipt-only validator.

Record evidence using the honest typed envelope:

- shell/process execution → `COMMAND_EXECUTION`;
- host/tool API observation → `TOOL_OBSERVATION`;
- trusted external run/check/artifact → `EXTERNAL_RESULT`;
- LLM synthesis → `INTERPRETATION` referencing captured evidence.

Do not fabricate argv/cwd/exit-code fields for non-command observations. An `OBSERVED_PASS` is limited to the bound check contract, current subject, and declared material context.
