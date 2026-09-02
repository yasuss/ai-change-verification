# Quick Start

## Portable Agent Skill

Install Python 3.11+ (or the Python version supported by the release). Copy `skills/ai-change-verification/` into the host's Agent Skills directory; do not copy private project governance. Ask the host to follow `SKILL.md`, create a receipt, run the deterministic checks, and produce the Review Readiness Report.

The portable skill still requires Python. Portable mode does not require Codex, Claude Code, Node, npm, SRT, MCP SDK, or a remote service.

For Codex, the optional third-party convenience installer is:

```bash
npx skills add yasuss/ai-change-verification \
  --skill ai-change-verification \
  --agent codex \
  --copy \
  --yes
```

It needs Node/npm only while installing; portable ACV does not. Without Node/npm, copy to `<project>/.agents/skills/ai-change-verification/`. For portable Claude-only use, copy to `<project>/.claude/skills/ai-change-verification/`.

## Codex provider

The Codex App Server adapter is optional. Install Python and a compatible local Codex App Server only if you need this host integration. Run `integrations/codex-app-server/setup_provider.py` from outside the inspected subject repository with an operator-selected Codex executable and an external provider directory.

For v0.1.0, the Codex App Server Windows trusted-host realization is documented as EXPECTED rather than TESTED. Portable Core usage on Windows is unaffected. macOS and other non-Windows lanes are also expected until independent host evidence exists. The target repository cannot choose the provider binary, profile, identity, or state directory.

Portable Core remains usable without Codex. v0.1.0 does not expose an authoritative Windows trusted-host enrollment. Do not treat an old Windows provider profile as an authoritative v0.1.0 realization.

## Claude Code provider

Install Claude Code >= 2.1.207 and Python. Use Claude's native marketplace flow:

```bash
claude plugin marketplace add yasuss/ai-change-verification
claude plugin install ai-change-verification@ai-change-verification
```

Claude prompts for operator-owned `node_path` and `python_path` through plugin `userConfig`; do not hand-edit project settings for trusted runtime paths. Claude installs locked dependencies automatically for marketplace plugins with `package.json` and `package-lock.json`, so normal users do not run `npm ci` first. Keep `${CLAUDE_PLUGIN_DATA}` outside the inspected repository. No external SaaS or database is required.

On native Windows, Claude Code's built-in sandbox is not supported. ACV instead uses the separately pinned Anthropic Sandbox Runtime `0.0.74`, whose Windows support is alpha. Check the machine with `node integrations/claude-code/setup_windows_sandbox.mjs --status`; if needed, an operator may explicitly run `--install`, which requires one UAC/elevation step. ACV startup never installs or elevates automatically. `--uninstall` is likewise explicit.

Use Claude Code's normal plugin installation, then call the single `verify_receipt` MCP tool with a receipt path inside the current project. The tool accepts no command, argv, cwd, provider, runtime, state, or readiness override.
