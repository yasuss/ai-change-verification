# Codex App Server

Codex is an optional host adapter. The canonical skill does not depend on it. The operator runs `setup_provider.py` outside the subject repository and supplies the external provider directory and Codex executable. The setup performs fixed identity and App Server probes before atomically writing an adjacent profile on a supported realization.

For v0.1.0, the Windows trusted-host realization is EXPECTED / not independently host-verified and is not part of the tested release compatibility set. Authoritative Windows enrollment is not available for v0.1.0. Provider identity and state are never selected by the target repository. A future host enrollment must bind the exact release, OS/architecture, Codex version, runtime, provider realization and required semantic cases before that realization is used.

After a selected operation run, the adapter records an operation ID, acquisition epoch, invocation and provider-event journal, and the canonical provenance inputs before the Core finalizer advances the decision head. The durable local SQLite state is the source for currentness; an interrupted result projection can be recovered by operation ID without rerunning the selected operation. An active or unknown acquisition is not current, and a new acquisition invalidates an earlier current result until fresh finalization. Network-share state paths are outside the strong local durability profile.

The adapter does not turn a provider label into authority: the current policy,
accepted provider/protocol/store realizations, fresh post-finalization subject
snapshot, and complete epoch reconciliation are all required. Recovery reads
only the canonical current publication head and performs zero selected-command
executions. A persisted event identifier may be replayed only with identical
canonical content; conflicting content and illegal lifecycle no-ops fail
closed.

Recovery reconstructs current authority solely from the current trusted
installation/runtime. A historical finalization never expands the set of
currently accepted realizations and never supplies the current policy
identity. A session is eligible for authoritative finalization only when its
acquisition was durably registered before invocation admission; activation
requires an OPEN acquisition, and incomplete epochs cannot advance the
canonical head. Provider CLI success means CURRENT_READY only. Publication
state is transport state and is not equivalent to readiness.

The V11 provider also admits only a strict raw receipt and binds the validated
receipt contract separately from the product/subject decision key. Its durable
event stream uses an exact acquisition-local frontier, and post-finalization
currentness is based on a fresh provider/profile/configuration probe. Operators
can explicitly abort one exact incomplete operation with `abort-incomplete`;
this is a recorded nonzero outcome with no command execution.
