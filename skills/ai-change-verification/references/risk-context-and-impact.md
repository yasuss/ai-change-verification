# Risk, context, and impact

Begin with subject, intent, repository contracts, and nearest applicable instructions. Expand only when a concrete question could change an evidence or readiness decision.

Use `LIGHT` for low-impact changes with bounded uncertainty, `STANDARD` for ordinary material changes, and `DEEP` for auth, secrets, crypto, sensitive data, migrations, public protocols, concurrency, verification-surface changes, policy changes, or broad blast radius. Material uncertainty needs escalation or an explicit evidence gap.

Record the decision each context source informs. A missing source is not silently replaced by inference.

For decision-relevant uncertainty, use the [Material Review Frontier](review-frontier.md). Seed it from the actual diff, intent, obligations, repository contracts, and changed trust/integration/verification surfaces rather than from a fixed checklist. Search before broad reading, inspect only the narrowest context that can answer the current question, and stop expansion when the material frontier is closed or explicitly unresolved.

A material question that remains `OPEN` or `BLOCKED` is not merely a human-report note. It must be represented by the existing `UNRESOLVED_RISK` finding semantics and `MUST_INSPECT` Human Attention so that receipt/readiness rules fail closed without adding a second source of authority.
