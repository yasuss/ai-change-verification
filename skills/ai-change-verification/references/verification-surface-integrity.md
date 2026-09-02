# Verification Surface Integrity

Inspect tests and assertions, fixtures and snapshots, CI and path filters, package scripts, build configuration, suppressions, thresholds, generated baselines, active instructions, and review policy.

Record whether evidence is `PRE_EXISTING_BASE`, `ADDED_OR_CHANGED_BY_SUBJECT`, `EXTERNAL_OR_TRUSTED_OBSERVATION`, or `UNKNOWN`. This origin is visibility, not a fixed trust ranking.

Use `UNCHANGED`, `STRENGTHENED`, or `WEAKENED` for the surface. `WEAKENED` alone does not force a readiness state: if `ADEQUATE_INDEPENDENT_COVERAGE_REMAINS`, retain the evidence and route a material change to Human Attention. If required evidence is removed or insufficient, use `NOT_READY_FOR_HUMAN_REVIEW` or `BLOCKED_ON_MISSING_EVIDENCE` as appropriate. A policy or instruction change may be `SUBJECT_CONTROLLED_CHANGED`.
