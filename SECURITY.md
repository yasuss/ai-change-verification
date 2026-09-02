# Security Policy

Security and evidence integrity are core properties of AI Change Verification.

This file describes how to report a suspected vulnerability.

For the technical trust model, security boundaries, currentness rules, and trusted-host architecture, see:

- [Security and trust](skills/ai-change-verification/references/security-and-trust.md)
- [Stage B live verification](skills/ai-change-verification/references/stage-b-live-verification.md)
- [Limitations](docs/limitations.md)

## Supported versions

Security fixes are provided for the latest public release.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| Earlier / unreleased versions | No |

## Reporting a vulnerability

Please do **not** open a public GitHub issue for a suspected security vulnerability.

Send the report privately to:

**it-support@lauensteinone.de**

If possible, include:

- the affected ACV version or commit;
- operating system and relevant host/provider environment;
- a minimal reproduction;
- the security or trust boundary you believe can be bypassed;
- the observed result;
- the result you expected.

Please do not include customer data, private source code, credentials, secrets, or other sensitive information unless it is necessary to understand the issue.

## Examples of security-relevant issues

We especially want to hear about issues that could allow:

- stale, incomplete, fabricated, or mismatched evidence to be accepted as current;
- authoritative readiness to be produced without the required trusted-host boundary;
- subject or snapshot identity checks to be bypassed;
- provider or runtime identity to be spoofed;
- an incomplete or inconsistent event history to produce a false `CURRENT_READY`;
- recovery behavior to incorrectly upgrade or replace authoritative state;
- execution outside the intended repository or provider boundary;
- unintended or destructive command execution;
- sandbox or host-isolation bypasses;
- secret or credential disclosure;
- malformed receipts or inputs to corrupt trust or decision state.

## Security boundaries

A security report should not assume that ACV provides guarantees outside its documented trust model.

ACV does not:

- certify that code is bug-free or secure;
- replace security review or security scanning;
- make model output authoritative;
- guarantee that the machine owner or host operator is honest;
- extend trusted-execution guarantees to untested environments;
- treat `READY_FOR_HUMAN_REVIEW` or `CURRENT_READY` as approval to merge, deploy, or release.

Expected or untested compatibility must not be interpreted as security-verified compatibility.

## Disclosure

Please allow reasonable time to investigate and, where appropriate, prepare a fix before publishing vulnerability details.

There is currently no guaranteed response-time or remediation SLA for this open-source project.
