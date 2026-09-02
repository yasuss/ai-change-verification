# Claude Code integration

This optional provider is a narrow receipt-only stdio MCP adapter. It reuses the canonical skill and runs the canonical Python bridge under pinned Anthropic Sandbox Runtime `0.0.74`; it does not use Claude Code command hooks as an authoritative boundary.

This trusted provider requires Claude Code >= 2.1.207. Declare operator-owned `node_path` and `python_path` through the plugin `userConfig` at the enable-time configuration prompt; `.mcp.json` consumes those values without project-controlled runtime-path variables. Native marketplace installation installs the exact locked dependencies automatically; local source/development may use `npm ci --ignore-scripts`. Keep provider implementation, state, realization and runtime paths outside the inspected repository.

Native Windows is an expected lane awaiting independent host evidence because the pinned SRT Windows lane is alpha. Claude Code's built-in sandbox is not supported on native Windows and is not ACV's trust boundary. Before using native Windows, run `node setup_windows_sandbox.mjs --status`; explicitly run `--install` only when needed and only with the operator's UAC/elevation confirmation. Startup never installs or elevates automatically.
