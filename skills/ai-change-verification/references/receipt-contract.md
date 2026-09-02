# Receipt contract

The machine-readable receipt preserves execution provenance, evidence semantics, obligations, findings, readiness, and limitations. Persistence modes are `EXTERNAL_FILE`, `INLINE`, `REPO_OPERATIONAL_EXCLUDED`, and `UNKNOWN`; external persistence is preferred. A repo-local operational receipt requires a successful subject-contamination check.

Observed mechanical evidence cannot originate from `LLM_INTERPRETATION`; its capture origin must remain visible. Reject duplicate JSON keys, duplicate IDs, dangling references, unknown fields in a supported closed schema, non-finite values, excessive size/depth, stale evidence silently supporting a current obligation, and unsupported readiness claims. Use explicit reason codes such as `JSON_DUPLICATE_KEY`, `DANGLING_EVIDENCE_REFERENCE`, and `RECEIPT_TOO_LARGE` rather than generic green/red prose.

The receipt is not cryptographic attestation. Store bounded sanitized summaries and digests, not raw secret-bearing output.

## Machine contract v1.2

Receipt dispatch identity is:

```text
product = ai-change-verification
schema_version = 1.2
```

The v1.2 JSON Schema resource has version-specific `$id`:

```text
urn:ai-change-verification:schema:verification-receipt:1.2
```

The audited pre-release v1.1 contract is superseded and remains recognizable only as historical. `policy_state` is not part of Stage A v1.2 because Stage A does not define authoritative policy composition.

### Version dispatch and anti-downgrade

Parse safety checks happen before trusted dispatch: byte/depth bounds, UTF-8, duplicate-key rejection, and non-finite rejection. Then inspect only `product` and `schema_version`.

- current decision-acceptable v1.2 → exact closed v1.2 validation;
- recognized historical v1.0/v1.1 → `SCHEMA_VERSION_NOT_ACCEPTABLE_FOR_CURRENT_DECISION`;
- unknown/newer version → `UNSUPPORTED_SCHEMA_VERSION`.

`SUPPORTED_FOR_PARSING` and `ACCEPTABLE_FOR_CURRENT_DECISION` remain distinct. Do not infer compatibility from major/minor naming. The same `schema_version` denotes the same published machine shape and decision semantics.

### Closed schema and local resolution

Known versions remain closed; v1.2 has no generic `extensions` escape hatch. Unknown fields fail closed. Schema `$id` and `$ref` are identifiers, not runtime download instructions. Canonical validation uses bundled/pre-registered resources only and performs no implicit network fetch.

### Subject and check binding

Current and observed subject identities are lower-case SHA-256 values. Current-subject decision evidence must match the receipt subject digest. Baseline-dependent attribution requires an identified baseline subject and explicit comparable evidence basis.

Each check contains a minimal typed operation contract, declared material-context keys, result interpretation, and `check_contract_digest`. The digest uses the narrow ACV canonical contract JSON encoding: supported scalar/list/object values only, lexicographically sorted object keys, UTF-8, no insignificant whitespace, non-ASCII strings unescaped, and domain prefix `ACV-CHECK-CONTRACT-v1\0` before SHA-256. Set-like contract fields (`covers`, `material_context_keys`, and `success_exit_codes`) are normalized in sorted order before hashing. The exact included fields are the validator's `_check_contract_payload` contract.

### Internal-coherence claim boundary

`RECEIPT_VALIDATION = PASS` means the v1.2 receipt is structurally valid and internally coherent under these rules. It does **not** independently prove that receipt-authored `CURRENT` states were re-observed against the live repository, that capture IDs are authentic, or that final readiness is authoritative for the live subject. Those live authority checks belong to the higher-level finalizer.

`validate_receipt.py` mirrors the closed-vocabulary and epistemic constraints without external runtime dependencies.
