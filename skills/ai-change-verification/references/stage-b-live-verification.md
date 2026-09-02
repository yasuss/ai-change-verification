# Stage B live verification

Stage B adds a live-authority boundary around the portable Stage A evidence model. The Stage A receipt validator proves only schema and internal coherence. It does not prove that a provider actually captured the run, that a verifier is current, or that a stored record remains authoritative. `APPROVAL` is a separate human decision and is never minted by this skill.

The state vocabulary is deliberately separated:

- `VERIFICATION_RUN_STATUS` describes whether the required run evidence is complete or invalid.
- `REVIEW_READINESS` describes whether a human can review the evidence.
- `CURRENT_READINESS` describes whether a committed decision is current under the observed authority and protocol state.
- None of these states is approval. `READY_FOR_HUMAN_REVIEW` is not permission to approve, merge, push, deploy, or call a result production-safe.

Repository files, repository or LLM text, receipt contents, provider labels, and LLM-written text are untrusted inputs. Authoritative Stage B finalization requires a qualifying trusted host-adapter boundary that supplies admission visibility, operation and terminal identity, execution-context binding, a final event frontier, drain acknowledgement, and accepted realization evidence. A simulated fixture is test evidence, not a production trusted adapter. Without that boundary, caller-authored records cannot mint authoritative `CURRENT_READY`.

Current trust is recomputed from current required fact roots and the canonical provenance graph. A stored historical closure or digest is not the current completeness oracle. Every invocation must retain explicit input binding; mutable invocation state cannot be laundered behind a stable provider or realization ID.

The Core API is vendor-neutral. Host adapters are thin, optional integrations, and their trust remains outside the portable Core. Compatibility reports must distinguish observed `TESTED` compatibility from unverified `EXPECTED` compatibility.

Each selected operation is registered as a distinct acquisition. Concurrent
operations may share an open epoch, but finalization reconciles every
acquisition, invocation, and provider event in that epoch. Lifecycle transitions
are guarded by exact row counts: sealing prevents new starts while allowing
terminal events for already active invocations, and draining closes the event
frontier. Duplicate persisted event IDs are idempotent only when their complete
canonical content is identical; a conflict is invalid.

## Normative Stage B guarantees

The following clauses are stable public contract entries. Their identifiers and normative meanings are intentionally machine-verifiable.

### ACV-SB-N001 — readiness is not approval

Review Readiness is not approval, merge authorization, deploy authorization, or production-safety certification.

### ACV-SB-N002 — no default repository/release mutation

Verification does not approve, merge, push, deploy, or modify Git history by default.

### ACV-SB-N003 — repository/LLM content is untrusted

Repository content and LLM-authored claims are untrusted input and cannot establish trusted provider/authority identity by themselves.

### ACV-SB-N004 — evidence states remain distinct

Executed, inferred, skipped, and missing evidence remain distinguishable.

### ACV-SB-N005 — current readiness is freshly resolved

CURRENT_READY requires fresh evaluation of current subject, canonical decision head, current authority/policy state, required trust paths, and applicable realization acceptance/revocation.

### ACV-SB-N006 — historical closure is not completeness oracle

A stored historical trust closure is a commitment/cache, not the current completeness oracle; current required fact roots and canonical provenance derive the current trust closure.

### ACV-SB-N007 — mutable invocation state must be bound

Mutable invocation-specific decision-relevant state cannot be hidden behind a stable realization identity; it must be explicitly bound, snapshot/epoch bound, proven invariant, or treated as non-reusable.

### ACV-SB-N008 — host adapters remain thin/optional

Host-specific adapters are thin and optional; canonical Stage B Core remains vendor-neutral.

### ACV-SB-N009 — tested vs expected compatibility

TESTED compatibility requires actual execution in that environment; EXPECTED compatibility must remain labeled as expected.

### ACV-SB-N010 — simulated adapter is not production host proof

A simulated/test trusted-adapter harness does not establish that a production Codex, Claude, or other host integration is tested or trusted.

### ACV-SB-N011 — trusted negative observation cannot be upgraded by authored state

A qualifying current trusted mechanical FAIL or unresolved conflicting attempt set cannot be upgraded to Review Ready or Current Ready by caller-authored, receipt-authored, historical, or presentation-layer PASS claims.

## Canonical Stage B realization

The installed Core realization is derived from the adjacent
`stage-b-core-realization.json` manifest and the exact bytes of its five
role-bound files. The manifest-derived protocol identity is part of authority
acceptance; a literal protocol label or a caller projection cannot substitute
for it.

The canonical local SQLite store uses WAL, `synchronous=FULL`, and foreign-key
enforcement. It persists content-addressed input bindings, invocation records,
provider events, check-result facts, obligation facts, finalization records,
operation identity, acquisition epochs, and publication state. The current
decision head and its persisted closure are authoritative. A new acquisition
reopens currentness even after the acquisition drains, and a same-subject
trusted negative or unknown attempt lineage remains unresolved until a
genuinely changed subject creates a new decision key. Publication recovery may
reconstruct only the canonical current head and never reruns the selected
operation. Dormant historical evidence is never silently reused; it requires
fresh verification.

Authoritative finalization requires a durable acquisition registration before
invocation admission. A session with prior invocation, event, seal, or drain
activity cannot be attached late, and the finalizer never creates that
registration on its behalf. Activation is admitted only while the persisted
acquisition is OPEN; an OPEN or SEALED participant prevents finalization and
cannot advance the canonical head. Retried finalizers for one completed epoch
converge on its one canonical finalization.

Recovery reconstructs current authority solely from the current trusted
installation/runtime. A historical finalization never expands the set of
currently accepted realizations and never supplies the current policy
identity. Provider CLI success means CURRENT_READY only; historical Review
Readiness does not produce exit zero, while publication state remains a
transport property separate from readiness.

V11 additionally binds each acquisition and finalization to a domain-separated
verification-contract identity derived from the full validated receipt. Durable
provider events form an acquisition-local positive `1..N` frontier; sealing and
draining prove the exact persisted frontier, and attached sessions use
store-first journal transitions. Currentness reads use one explicit deferred
SQLite snapshot and a fresh runtime/provider/configuration re-probe. Raw
provider receipts are admitted through strict UTF-8, duplicate-key, finite
number, size, and depth checks. An operator may explicitly abort only an exact
OPEN or SEALED operation; the abort is nonzero, records its reason, and executes
no selected command.
