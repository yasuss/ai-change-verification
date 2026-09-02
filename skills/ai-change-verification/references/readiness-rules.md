# Readiness rules

Review Readiness has exactly three receipt states:

- `READY_FOR_HUMAN_REVIEW` — the receipt's declared evidence is internally sufficient and material unknowns are bounded;
- `NOT_READY_FOR_HUMAN_REVIEW` — required evidence or a material control is inadequate, including a known material contradicted obligation;
- `BLOCKED_ON_MISSING_EVIDENCE` — a critical decision cannot be made from available evidence.

Readiness is not approval, merge safety, production safety, bug-free status, or a numeric score. A material `UNPROVEN` or `CONTRADICTED` obligation cannot coexist with READY. A contradiction requires current-subject mechanically captured `OBSERVED_FAIL` evidence from a selected check that covers the obligation.

A blocking `UNRESOLVED_RISK` disposition or unresolved material `INTENT_CONFLICT` cannot coexist with READY. A weakened Verification Surface can remain READY only with explicit `ADEQUATE_INDEPENDENT_COVERAGE_REMAINS`, sufficient current mechanical evidence, and traceable Human Attention.

Stage A receipt validation checks internal readiness coherence only. Live subject/currentness/applicability and authoritative final readiness must be recomputed by the higher-level finalizer before a live handoff claim.
