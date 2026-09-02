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
