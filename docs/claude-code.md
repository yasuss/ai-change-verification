# Claude Code

The optional Claude integration is a plugin containing one narrow local stdio MCP provider. It exposes only `verify_receipt(receipt_path)`, where the receipt resolves inside the current project. The provider constructs a fixed bridge invocation and runs it under the pinned Anthropic Sandbox Runtime `0.0.74`; the MCP SDK is pinned to `1.30.0`.

Trusted-provider use requires Claude Code >= 2.1.207. Operator configuration supplies plugin `userConfig` values `node_path` and `python_path`; `.mcp.json` maps them to the provider command and `ACV_PYTHON_PATH`. `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, and `${CLAUDE_PROJECT_DIR}` retain their documented plugin purposes. Trusted implementation, state, provider realization, and runtime identity remain outside the target repository. Missing/drifted SRT, failed capability probe, malformed bridge output, subject drift, and realization drift are non-authoritative.

Claude provider / macOS is an expected lane awaiting independent host evidence. Claude provider / native Windows is an expected lane with an alpha SRT dependency. Claude Code itself supports native Windows, but its built-in sandbox does not support native Windows; ACV does not use that built-in sandbox as its trust boundary. ACV uses pinned Anthropic Sandbox Runtime `0.0.74`. On native Windows, run `setup_windows_sandbox.mjs --status`; an operator may explicitly run `--install` with one UAC/elevation step if required. The provider never auto-installs or auto-elevates and never falls back unsandboxed.

The setup helper is read-only by default. `--install` and `--uninstall` are explicit modes and verify the exact locked SRT version before calling its programmatic Windows API.

The bridge validates receipts with the canonical validator, selects only existing `COMMAND_EXECUTION` contracts, executes exact argv with `shell=False`, and delegates decision semantics to the canonical Stage B finalizer/currentness model. Hooks are not required and are not authoritative. Claude Code on macOS is an expected lane awaiting independent host evidence.

The Claude path is a **Trusted Capture Provider** for the canonical Stage B Core. When all selected operations succeed, its host-level result is `DELEGATE_TO_CANONICAL_STAGE_B`; that status is not `CURRENT_READY`. Canonical Stage B finalization and currentness must resolve the durable decision head, subject, authority, policy, provenance closure, and acquisition lineage.
