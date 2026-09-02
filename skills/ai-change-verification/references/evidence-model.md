# Evidence model

Evidence is a bounded record of what a check observed. Keep evidence record shape, capture origin, subject relationship, and interpretation separate.

## Outcomes and provenance

Mechanical Observed Verification may be `OBSERVED_PASS`, `OBSERVED_FAIL`, `NOT_RUN`, or `INCONCLUSIVE`. Mechanical evidence uses `HELPER_CAPTURE`, `HOST_TOOL_OBSERVATION`, or `TRUSTED_EXTERNAL` as applicable. `LLM_INTERPRETATION` may summarize already-captured evidence but cannot originate an observed pass/fail.

Subject relationship is one of `CURRENT_SUBJECT`, `BASELINE_SUBJECT`, `EXTERNAL_SUBJECT`, `UNKNOWN`, or `NOT_APPLICABLE`. Current-subject mechanical evidence used for decision support must bind to the exact current `subject_digest`. Baseline evidence binds to the explicit baseline subject when one is declared.

## Typed Evidence Envelopes

Machine receipt v1.2 uses four record kinds:

- `COMMAND_EXECUTION` — exact argv/cwd, invocation identity, start time, duration, exit code, selected-check binding, observed-subject digest, material context, and sanitized output identity.
- `TOOL_OBSERVATION` — host/tool/version/operation/observation identity without inventing a shell command.
- `EXTERNAL_RESULT` — provider/resource/result identity and observed subject without inventing local cwd/exit-code fields.
- `INTERPRETATION` — references underlying evidence and remains `INCONCLUSIVE`; it cannot originate mechanical evidence.

Mechanical records bind to `check_id`, `check_contract_digest`, and `observed_subject_digest`. The check contract declares `material_context_keys`; evidence records exactly those decision-relevant name/value bindings. Receipt validation checks internal binding. Live recapture/currentness is a separate finalizer responsibility.

## Check-contract identity

The decision-relevant check contract includes check identity/source, covered obligations, safety class, selected state, declared material-context keys, typed operation contract, and result interpretation. A content-derived `check_contract_digest` uses the documented ACV canonical contract encoding and the `ACV-CHECK-CONTRACT-v1` domain tag. Changing a decision-relevant contract field invalidates prior evidence binding.

## Result semantics

For `COMMAND_EXECUTION`, observed pass/fail is recomputed from the bound check's `EXIT_CODE` rule. `OBSERVED_PASS` with a failing exit, `OBSERVED_FAIL` with a configured success exit, or observed pass/fail with no mechanical result is invalid. Tool/external records may use `CAPTURED_OUTCOME`; live authority for that captured structured outcome belongs to the trusted capture/finalizer path.

## Normalization provenance

A thin host normalization adapter may translate provider representation into an already-defined vendor-neutral ACV semantic only. If normalization is used, bind the normalized record to `host_profile`, adapter identity/version, and the exact source observation by immutable observation ID and/or sanitized content digest. Unknown/unmappable provider values fail closed; an adapter cannot invent stronger outcome, trust, freshness, subject, baseline, or readiness semantics.

## Reliability, baseline comparability, and freshness

Reliability is one of `UNKNOWN`, `REPEATED_CONSISTENT`, `KNOWN_FLAKY`, or `NOT_APPLICABLE`. `REPEATED_CONSISTENT` requires at least two distinct source observation/invocation identities with compatible check contract, subject, context, operation, and outcome; duplicate receipt rows do not count.

Baseline attribution is `NEW`, `PRE_EXISTING`, `UNKNOWN`, or `NOT_APPLICABLE`. `NEW` and `PRE_EXISTING` require an explicit comparable baseline basis. v1.2 derives `NEW` only for the supported baseline-pass/current-fail rule, and derives `PRE_EXISTING` only for matching failure/property fingerprints under a comparable check/context. Unsupported or ambiguous attribution remains `UNKNOWN`.

Evidence freshness is one of `CURRENT`, `STALE`, `UNKNOWN`, or `NOT_APPLICABLE`; subject freshness is `CURRENT`, `STALE`, or `UNKNOWN`. These are closed receipt states, but receipt-only validation does not independently establish live currentness.

Compatibility note: the superseded v1.1 relationship token `ADDED_OR_CHANGED_BY_SUBJECT` is retained only as a legacy static-verifier anchor. v1.2 uses explicit `CURRENT_SUBJECT`, `BASELINE_SUBJECT`, and `EXTERNAL_SUBJECT` relationships instead.
