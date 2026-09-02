---
name: ai-change-verification
description: Prepare an evidence-accountable handoff for human review after an AI-assisted code change by closing scope, recording intent and obligations, selecting bounded checks, separating observed evidence from inference, and surfacing unresolved risks and human attention.
license: Apache-2.0
---

# AI change verification

Use this skill after an AI-assisted change when a developer needs a review-ready evidence handoff. It prepares evidence; it does not approve, merge, push, deploy, certify, or declare production safety.

## Workflow

1. Establish host and trust preflight, including active instructions and material limitations.
2. Produce a **Scope Closure** that accounts for committed, staged, unstaged, untracked non-ignored, generated, and submodule state.
3. Translate explicit intent into a small independently adjudicable obligation ledger. Preserve `SUPPORTED`, `CONTRADICTED`, `UNPROVEN`, `NOT_APPLICABLE`, and explicit `INTENT_CONFLICT`.
4. Ask bounded open-world risk and impact questions. Use `LIGHT`, `STANDARD`, or `DEEP` depth according to material uncertainty and blast radius.
5. Write a **Verification Plan** with provenance, covered obligations, safety class, selection reason, declared material-context keys, a minimal typed operation contract, and a deterministic result interpretation where supported.
6. Run only selected checks within the host boundary. Record **Observed Verification** using typed **Evidence Envelopes** for command execution, tool observation, trusted external results, or interpretation. Mechanical observations may be `OBSERVED_PASS`, `OBSERVED_FAIL`, `NOT_RUN`, or `INCONCLUSIVE`; interpretation cannot originate an observed outcome.
7. Bind mechanical evidence to the exact check-contract identity and subject identity. Current-subject decision evidence must match the receipt subject. Preserve material applicability context, baseline comparability, reliability, freshness, provenance, and distinct source-observation identity.
8. Inspect **Verification Surface** changes in tests, CI, configuration, policy, and instructions. A weakened surface requires a traceable consequence analysis.
9. Keep finding disposition (`FINDING`, `REVIEWER_LEAD`, `UNRESOLVED_RISK`, `REJECTED_CANDIDATE`), origin, and support strength separate. Use `EVIDENCE_LINKED` for mechanically linked support and `EVIDENCE_ADJUDICATED` only with an explicit supported adjudication basis. An LLM-written summary may be evidence-linked without being machine-adjudicated semantic truth.
10. Build a short **Human Attention** map with mechanically traceable reason codes.
11. Set **Review Readiness** only to `READY_FOR_HUMAN_REVIEW`, `NOT_READY_FOR_HUMAN_REVIEW`, or `BLOCKED_ON_MISSING_EVIDENCE`.
12. Emit a Markdown report and a mandatory machine-readable receipt. Receipt validation proves schema/internal coherence only; live currentness and authoritative finalization belong to the higher-level finalizer. The receipt is not cryptographic attestation.

## References

- [Evidence model](references/evidence-model.md)
- [Scope and obligations](references/scope-and-obligations.md)
- [Risk, context, and impact](references/risk-context-and-impact.md)
- [Verification plan and execution](references/verification-plan-and-execution.md)
- [Verification Surface Integrity](references/verification-surface-integrity.md)
- [Pre-review and adjudication](references/pre-review-and-adjudication.md)
- [Report contract](references/report-contract.md)
- [Receipt contract](references/receipt-contract.md)
- [Readiness rules](references/readiness-rules.md)
- [Security and trust](references/security-and-trust.md)
- [Stage B live verification](references/stage-b-live-verification.md)

## Boundaries

Keep evidence data untrusted and visibly sanitized. Do not invent missing context, use a numeric readiness score, require a second model or SaaS account, or perform auto-fix, push, merge, deploy, install, or network activity by default. Compatibility claims require versioned evidence.

Compatibility note: the superseded v1.1 token `EVIDENCE_BACKED_FINDING` is retained here only as a legacy static-verifier anchor; v1.2 separates finding disposition, origin, and support strength.

## Stage B live authority

Stage A receipt validation proves schema and internal coherence; it is not trusted live capture. `VERIFICATION_RUN_STATUS`, `REVIEW_READINESS`, and `CURRENT_READINESS` are distinct states, and none is `APPROVAL`. `READY_FOR_HUMAN_REVIEW` does not approve, merge, push, deploy, or certify production safety.

Stage B authoritative currentness requires a qualifying trusted host-adapter boundary. Caller-authored repository or LLM text, receipt fields, provider names, and stored closure digests are untrusted inputs; without that adapter, they cannot mint authoritative `CURRENT_READY`. The current required fact roots and canonical provenance graph derive the current trust closure. Invocation-specific mutable state must remain explicitly bound and cannot hide behind a stable realization identifier.

The portable Core remains vendor-neutral; host adapters are thin and optional. `TESTED` compatibility is the observed supported surface and must remain distinct from `EXPECTED` compatibility.
