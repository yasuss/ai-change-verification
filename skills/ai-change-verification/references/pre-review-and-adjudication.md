# Pre-review and adjudication

Review candidates separately for intent alignment, repository standards, correctness and risk, verification adequacy, downstream compatibility, and Verification Surface Integrity.

Machine receipt v1.2 separates three finding dimensions:

- disposition: `FINDING`, `REVIEWER_LEAD`, `UNRESOLVED_RISK`, or `REJECTED_CANDIDATE`;
- origin: `DETERMINISTIC_TOOL`, `LLM_INTERPRETATION`, `HUMAN_REVIEW`, or `OTHER_SUPPORTED_SOURCE`;
- support: `EVIDENCE_LINKED`, `EVIDENCE_ADJUDICATED`, or `NOT_APPLICABLE`.

`EVIDENCE_LINKED` means the finding has valid qualifying evidence references; it does not claim that a deterministic validator proved every sentence of arbitrary natural-language summary text. `EVIDENCE_ADJUDICATED` requires an explicit supported adjudication basis and current mechanical source observation. Finding prose never creates mechanical execution truth or readiness authority.

The historical v1.1 token `EVIDENCE_BACKED_FINDING` is superseded because it mixed finding disposition with support strength.

Do not use majority agreement as adjudication. Human review remains the decision authority.

Adjudication should preserve adversarial falsification: actively test whether a candidate finding, obligation state, or readiness interpretation can be disproved by qualifying evidence before surfacing it to the reviewer.


## Material Review Frontier projection

The review frontier is process state, not a new finding vocabulary or readiness system. Before final reporting, map each retained material question through the existing machine semantics:

- `ANSWERED`: the decision-relevant question was investigated far enough to decide what it means for review. Preserve the resulting `FINDING`, `REVIEWER_LEAD`, `REJECTED_CANDIDATE`, or no-material-issue conclusion as appropriate. `ANSWERED` never means `OBSERVED_PASS`, `EVIDENCE_ADJUDICATED`, approval, or production safety.
- `OPEN`: material investigation is incomplete. Emit `UNRESOLVED_RISK` and a `MUST_INSPECT` Human Attention item with the next concrete reviewer action.
- `BLOCKED`: required context is unavailable, forbidden, or inaccessible. Emit `UNRESOLVED_RISK` and `MUST_INSPECT`; use existing readiness semantics to distinguish missing-evidence blockage from other non-ready states.
- `NOT_MATERIAL`: state the qualified reason in the human report; do not invent evidence or a finding merely to fill a table.

Do not upgrade an `OPEN` or `BLOCKED` question because a model is confident, because multiple reviewers agree, or because repository text claims the change is safe. The existing receipt validator and Stage B finalizer remain the only machine/readiness authorities.
