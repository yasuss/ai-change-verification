# ai-change-verification

[![CI](https://github.com/yasuss/ai-change-verification/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/yasuss/ai-change-verification/actions/workflows/ci.yml)

## Problem

AI-assisted changes can be syntactically plausible while leaving context, checks, assumptions, or reviewer questions unresolved. `ai-change-verification` preserves those facts before a human review.

## What it does

The portable Agent Skill turns a change into a reviewable evidence record:

`Change → Context → Deterministic Verification → Pre-review → Unresolved Assumptions → Human Judgment → Review Readiness`

It validates receipts, preserves deterministic observations, and applies the canonical currentness and false-green rules. Review Readiness is not approval. ACV does not merge, push or deploy by default.

## What it does not do

ACV does not replace human judgment, certify production safety, guarantee security, or approve/merge/deploy a change. Host integrations are optional adapters; the Core remains vendor-neutral.

## 2-minute Quick Start

### Portable Codex — optional convenience installer

```bash
npx skills add yasuss/ai-change-verification \
  --skill ai-change-verification \
  --agent codex \
  --copy \
  --yes
```

`npx skills` is a third-party convenience installer. It requires Node/npm only for installation; ACV portable mode itself does not require Node/npm. Do not install it globally. Then ask Codex:

```text
$ai-change-verification Verify the current change and produce a Review Readiness Report.
```

### Portable Codex — no Node/npm

Copy `skills/ai-change-verification/` to `<project>/.agents/skills/ai-change-verification/`.

### Portable Claude-only mode

Copy `skills/ai-change-verification/` to `<project>/.claude/skills/ai-change-verification/`. This is the manual portable path and does not install the optional provider.

### Full Claude provider

> v0.1.0 Claude host lanes are EXPECTED / not independently host-verified.

```bash
claude plugin marketplace add yasuss/ai-change-verification
claude plugin install ai-change-verification@ai-change-verification
```

Claude Code prompts for the operator-owned `node_path` and `python_path` through plugin `userConfig`. Marketplace installation installs locked plugin dependencies automatically; do not run `npm ci` first for the normal marketplace flow. See [`docs/quick-start.md`](docs/quick-start.md) for exact setup details.

## Optional hosts

- [Codex App Server](docs/codex.md): an optional adapter; the Windows trusted-host realization is documented as EXPECTED for v0.1.0.
- [Claude Code](docs/claude-code.md): a narrow receipt-only stdio MCP provider with pinned sandbox runtime; macOS and native Windows are expected lanes. Native Windows uses the alpha SRT lane, not Claude Code's built-in sandbox.

The skill itself has no Codex or Claude dependency. The Claude plugin reuses this canonical skill and does not duplicate it.

## Example

See the [before/after example](examples/before-after/README.md).

## Compatibility

**TESTED for v0.1.0**
- Portable Core — Ubuntu CI

**EXPECTED / not independently host-verified**
- Codex App Server — Windows trusted-host realization
  *(authoritative enrollment is not enabled in v0.1.0)*
- macOS lanes
- Claude Code host lanes

See [Compatibility details](docs/compatibility.md), [Quick Start](docs/quick-start.md) and [Limitations](docs/limitations.md) for details.

## Security

ACV treats repository content, model output, receipts, provider labels, and other caller-controlled inputs as untrusted unless explicitly established otherwise by the trust model.

Please do not report suspected security vulnerabilities through public GitHub issues.

See [SECURITY.md](SECURITY.md) for vulnerability reporting instructions.

For the technical trust model and security boundaries, see:

- [Security and trust](skills/ai-change-verification/references/security-and-trust.md)
- [Stage B live verification](skills/ai-change-verification/references/stage-b-live-verification.md)
- [Limitations](docs/limitations.md)

## Limitations and trust

The inspected repository authors untrusted receipts. A trusted host must prove execution identity, subject identity, provider realization, currentness, and a complete event frontier. Missing or drifted runtime/sandbox/provider evidence fails closed. Host adapters feed the existing canonical finalizer; they do not define a second readiness reducer.

After a host/runtime/provider update, re-enroll and re-verify. A changed realization does not inherit an earlier READY result.

Deeper references are in the skill's `references/` directory and the host-specific docs.

For teams where the problem extends beyond a single change into repository context, CI gates, review workflow and sign-off, this is the work we do at [LAUENSTEIN One](https://lauensteinone.de/en/ai-engineering-sprint/).
