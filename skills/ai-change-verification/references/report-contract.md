# Report contract

The Markdown report is the canonical human output and follows this order:

1. Evidence Subject and Scope Closure;
2. Intent and independently adjudicable Obligation ledger;
3. bounded risk context, including the Material Review Frontier when material questions exist;
4. Verification Plan and check-contract identity;
5. Observed Verification and typed Evidence Envelopes;
6. baseline, reliability, freshness, material applicability, and Verification Surface;
7. candidate findings with separate disposition, origin, and support strength;
8. Human Attention;
9. Review Readiness;
10. limitations and Machine-Readable Receipt location.

Use explicit labels for observed, interpreted, evidence-linked, adjudicated, unproven, stale, inconclusive, rejected, and human-only judgment. Do not render an LLM-authored evidence-linked summary as if semantic entailment were machine-proven. The report must not claim approval, production safety, merge safety, live-currentness proof from receipt validation alone, or cryptographic attestation.

When the Material Review Frontier is non-empty, render a compact table with `question`, `decision_relevance`, `state`, `support_or_gap`, and `next_human_action`. It is a human projection of the review process, not a second machine schema. `OPEN` and `BLOCKED` material rows must have corresponding `UNRESOLVED_RISK` findings and `MUST_INSPECT` Human Attention entries in the existing receipt. `ANSWERED` must state what was actually inspected or why no further material issue remains; it cannot be rendered as a mechanical pass unless a qualifying Evidence Envelope independently establishes that result. Clean changes may omit the table.
