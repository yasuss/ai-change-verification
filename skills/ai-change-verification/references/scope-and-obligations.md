# Scope and obligations

## Scope Closure

Close the inspected subject across committed base difference, staged and unstaged tracked files, untracked non-ignored files, material generated or lock files, submodules, exclusions, and dirty state. A change snapshot can help classify staged, unstaged, and untracked state. If closure cannot be established for “my change”, report `SCOPE_AMBIGUOUS`.

## Independently adjudicable ledger

Each material obligation has one claim, a source, provenance, materiality, an adjudicability marker, a state, and evidence references. States are `SUPPORTED`, `CONTRADICTED`, `UNPROVEN`, and `NOT_APPLICABLE`. A compound obligation must be split before adjudication. `INTENT_CONFLICT` records conflicting sources, their authority, resolution attempt, and residual consequence. An LLM interpretation alone cannot support an obligation.

The context is open-world: named checklists are priorities, not an exhaustive whitelist. Expand context only for a concrete decision-changing question.
