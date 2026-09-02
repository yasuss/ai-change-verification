# ACV Codex App Server Provider

This is an optional host adapter. It is installed outside the subject repository
and submits only validated `COMMAND_EXECUTION` contracts to the exact adjacent
Codex App Server runtime. The portable ACV Core remains unchanged.

For v0.1.0, the Windows trusted-host realization is documented as EXPECTED /
not independently host-verified. Authoritative Windows enrollment is not part
of this release. Portable Core remains usable without this adapter.

The installed directory must contain `acv_codex_provider.py` and an adjacent
`provider_profile.json`. The profile is host-specific and binds the absolute
Codex executable, executable hash, exact version, generated schema-set digest,
platform, preflight evidence and `experimentalApi=false`. The source repository
contains only the profile schema; a profile is generated for an enrolled host.

Production entry point:

```text
python acv_codex_provider.py verify --receipt RECEIPT --subject-root SUBJECT --state-dir EXTERNAL_STATE --result EXTERNAL_RESULT
```

The CLI accepts no arbitrary command, shell, profile, binary, provider-event or
authority input. It uses subprocess argv vectors without a shell, read-only or
workspace-write policy with network disabled, bounded JSON-RPC stdio handling,
live binary/version/schema checks, `configRequirements/read`, subject snapshots,
and the existing canonical durable decision store.

Raw stdout/stderr are untrusted data. `capReached` is recorded; truncated output
cannot satisfy an output-dependent check. The provider owns only its controlled
session frontier and does not claim completeness for arbitrary Codex activity.

The adapter is a Trusted Capture Provider for the canonical Core. It opens the external local SQLite state before a selected operation, persists the operation and acquisition journal before activation, writes terminal events through to durable state, and submits persisted provenance to canonical finalization. The Core owns the decision head and currentness; an interrupted projection is recoverable by operation ID without rerunning an operation. Network-share state paths are rejected by the strong local durability profile.

Admission derives a separate contract identity from the complete validated
receipt and binds it to the durable acquisition/finalization. The provider's
receipt path is strict raw UTF-8 JSON admission with duplicate-key, finite-number,
size, and depth limits. After committing a finalization, `verify` captures a
fresh subject and re-probes the live provider/runtime/configuration before
currentness is evaluated. `abort-incomplete` requires an explicit operation ID,
changes only OPEN or SEALED durable state to ABORTED, executes zero selected
commands, records a stable reason, and exits nonzero. Recovery remains
canonical-head-only and zero-command.
