# Limitations

Review Readiness is not approval. A human must judge intent, risk, unresolved assumptions, and whether the evidence is sufficient.

ACV does not merge, push or deploy by default. It does not make model output authoritative, and it cannot prove that a host machine owner is honest. Provider/runtime drift, subject drift, incomplete capture, malformed evidence, and sandbox failure are non-authoritative and rejected.

The Claude sandbox runtime is pinned but research-preview software; its native-Windows support is alpha. Claude Code's built-in sandbox does not support native Windows, so ACV uses its separately pinned SRT dependency instead. Independent macOS, Claude/macOS, and Claude/native-Windows installation evidence is deferred. Synthetic checks and CI must not be read as real host evidence.

The Codex App Server Windows trusted-host realization is EXPECTED / not independently host-verified for v0.1.0. Portable Core remains available and does not require Codex.

Stage B durable authority is bounded to the supported local SQLite/WAL/VFS profile. Network-share and unknown mapped-storage locations are not granted the same durability claim. Currentness is a point-in-time result of the canonical decision head, current subject, authority and policy identities, persisted provenance, and acquisition state. A new acquisition, interrupted/unknown attempt, legacy state, or same-subject trusted negative lineage requires fresh verification; a presentation result cannot upgrade it.

Multiple operations may be admitted in one acquisition epoch, but no operation
can hide another participant from finalization. The current head is advanced
only by an atomic, complete-epoch reconciliation; publication recovery is a
projection repair and never a command rerun. These guarantees do not extend to
an untested provider host or an unsupported filesystem profile.

Recovery does not derive current authority or policy from the historical
finalization it evaluates. Durable acquisition registration precedes
authoritative admission, incomplete epochs do not finalize, and activation
cannot cross the persisted seal boundary. A provider process exits zero only
for CURRENT_READY; historical Review Readiness and PUBLISHED transport state
do not change that result.

V11 strict receipt admission and exact event-frontier proofs apply to the
provider/Core paths covered by the release candidate. They do not turn an
untested host, provider runtime, filesystem, or external coordinator into a
trusted realization. Explicit abort records an incomplete operation; it does
not repair or erase its negative/unknown lineage.
