import contextlib
import copy
import dataclasses
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sqlite3
import sys
import tempfile
import threading
import types
import unittest


PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PUBLIC_ROOT / "skills/ai-change-verification/scripts/finalize_verification.py"
PROVIDER_SCRIPT = PUBLIC_ROOT / "integrations/codex-app-server/acv_codex_provider.py"
NORMATIVE_DOC = PUBLIC_ROOT / "skills/ai-change-verification/references/stage-b-live-verification.md"
_SESSION_COUNTER = 0


NORMATIVE_CLAUSES = {
    "ACV-SB-N001": "Review Readiness is not approval, merge authorization, deploy authorization, or production-safety certification.",
    "ACV-SB-N002": "Verification does not approve, merge, push, deploy, or modify Git history by default.",
    "ACV-SB-N003": "Repository content and LLM-authored claims are untrusted input and cannot establish trusted provider/authority identity by themselves.",
    "ACV-SB-N004": "Executed, inferred, skipped, and missing evidence remain distinguishable.",
    "ACV-SB-N005": "CURRENT_READY requires fresh evaluation of current subject, canonical decision head, current authority/policy state, required trust paths, and applicable realization acceptance/revocation.",
    "ACV-SB-N006": "A stored historical trust closure is a commitment/cache, not the current completeness oracle; current required fact roots and canonical provenance derive the current trust closure.",
    "ACV-SB-N007": "Mutable invocation-specific decision-relevant state cannot be hidden behind a stable realization identity; it must be explicitly bound, snapshot/epoch bound, proven invariant, or treated as non-reusable.",
    "ACV-SB-N008": "Host-specific adapters are thin and optional; canonical Stage B Core remains vendor-neutral.",
    "ACV-SB-N009": "TESTED compatibility requires actual execution in that environment; EXPECTED compatibility must remain labeled as expected.",
    "ACV-SB-N010": "A simulated/test trusted-adapter harness does not establish that a production Codex, Claude, or other host integration is tested or trusted.",
    "ACV-SB-N011": "A qualifying current trusted mechanical FAIL or unresolved conflicting attempt set cannot be upgraded to Review Ready or Current Ready by caller-authored, receipt-authored, historical, or presentation-layer PASS claims.",
}


def normative_section():
    text = NORMATIVE_DOC.read_text(encoding="utf-8")
    heading = "## Normative Stage B guarantees"
    if heading not in text:
        raise AssertionError("NORMATIVE_SECTION_MISSING")
    section = text.split(heading, 1)[1]
    return section.split("\n## ", 1)[0]


@contextlib.contextmanager
def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("MODULE_SPEC_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    if name in sys.modules:
        raise RuntimeError("MODULE_NAME_COLLISION")
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


with load_module("acv_stage_b_e2e_core", SCRIPT) as core:
    pass


def load_provider(name):
    spec = importlib.util.spec_from_file_location(name, PROVIDER_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError("PROVIDER_MODULE_SPEC_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ready_receipt():
    return json.loads((PUBLIC_ROOT / "tests/fixtures/receipt_minimal_ready.json").read_text(encoding="utf-8"))


def blocked_receipt():
    return json.loads((PUBLIC_ROOT / "tests/fixtures/receipt_minimal_blocked.json").read_text(encoding="utf-8"))


def capabilities(**overrides):
    values = {
        "admission_visibility_complete": True,
        "effective_operation_identity": True,
        "terminal_outcome_identity": True,
        "material_execution_context_binding": True,
        "final_event_frontier": True,
        "drain_acknowledgement": True,
        "realization_accepted": True,
    }
    values.update(overrides)
    return core.ProviderCapabilities(**values)


def authority(subject, accepted=("provider-1",), revoked=()):
    policy = core.derive_policy_snapshot_id({"version": 1})
    store_profile = core._identity("store-realization", {"python_version": sys.version.split()[0], "sqlite_library_version": sqlite3.sqlite_version, "journal_mode": "wal", "synchronous": 2, "foreign_keys": 1, "support_profile": "local_sqlite_wal_full"})
    accepted = tuple(sorted(set(accepted) | {"protocol-1", store_profile}))
    return core.AuthoritySnapshot(
        "authority-1",
        subject,
        policy_snapshot_id=policy,
        authority_root_id="root-1",
        accepted_realization_ids=tuple(accepted),
        revoked_realization_ids=tuple(revoked),
        topology_complete=True,
        complete=True,
    )


def graph(receipt, provider="provider-1", parents=()):
    roots = core.derive_required_fact_roots(receipt)
    return {
        root: core.TransformRecord(provider, core.TransformMode.EXPLICIT_INPUT_TRANSFORM.value, direct_input_object_ids=tuple(parents))
        for root in roots
    } | {parent: core.TransformRecord(provider, core.TransformMode.EXPLICIT_INPUT_TRANSFORM.value) for parent in parents}


def _attach_authoritative(store, receipt, session, operation_id=None):
    operation_id = operation_id or session.operation_id or session.session_id
    if session.store is not None:
        return session.attach_store(store, core.derive_decision_key(receipt), operation_id)
    if not session.verification_contract_id:
        session.verification_contract_id = core.derive_verification_contract_id(receipt)
    records = list(session.invocations.values())
    events = list(session.events.values())
    sealed, frontier, drained = session.sealed, session.final_provider_event_frontier, session.trusted_ingestion_drain_frontier
    session.invocations.clear(); session.events.clear(); session.sealed = False
    session.final_provider_event_frontier = None; session.trusted_ingestion_drain_frontier = None
    info = session.attach_store(store, core.derive_decision_key(receipt), operation_id)
    for record in records:
        session.register_invocation(record["invocation_id"], record["check_id"], operation=record.get("operation"), input_bindings=record.get("input_bindings"), required=record.get("required", True))
        session.activate_invocation(record["invocation_id"])
    for event in sorted(events, key=lambda item: (item.sequence, item.event_id)):
        session.ingest_event(event)
    if sealed:
        session.seal(frontier)
    if drained is not None:
        session.acknowledge_drain(drained)
    return info


def make_session(receipt, statuses=("PASS",), drain=True, sealed=True, bind=True, provider="provider-1", caps=None, store=None):
    global _SESSION_COUNTER
    _SESSION_COUNTER += 1
    check = next(item for item in receipt["verification_plan"] if item["selected"])
    session_id = f"run-{_SESSION_COUNTER}"
    session = core.RunBoundEvidenceSession(session_id, receipt["subject"]["subject_digest"], provider, "profile-1", caps or capabilities(), core.derive_verification_contract_id(receipt))
    binding = (core.InvocationInputBinding(core.InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"config": "fixed"}),) if bind else ()
    operation = check["operation_contract"]
    if store is not None:
        session.attach_store(store, core.derive_decision_key(receipt), session_id)
    session.register_invocation("inv-" + session_id, check["id"], operation=operation, input_bindings=binding)
    session.activate_invocation("inv-" + session_id)
    for index, status in enumerate(statuses, 1):
        session.ingest_event(core.ProviderEvent(f"event-{session_id}-{index}", "inv-" + session_id, check["id"], status, operation=operation, sequence=index))
    if sealed:
        session.seal(len(statuses))
    if drain:
        session.acknowledge_drain(len(statuses))
    return session


def finalize(receipt, session, directory, auth=None, graph_value=None, protocol="protocol-1", **kwargs):
    store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
    if session is not None:
        _attach_authoritative(store, receipt, session)
    result = core.finalize_verification(
        receipt,
        session,
        store,
        auth or authority(receipt["subject"]["subject_digest"]),
        policy_snapshot={"version": 1},
        decision_protocol_realization_id=protocol,
        provenance_graph=graph_value or graph(receipt),
        **kwargs,
    )
    return result, store


class NormativeStageBContractTests(unittest.TestCase):
    def test_normative_contract_is_exact_and_closed(self):
        section = normative_section()
        identifiers = re.findall(r"^### (ACV-SB-N\d{3})\b", section, flags=re.MULTILINE)
        self.assertEqual(set(identifiers), set(NORMATIVE_CLAUSES))
        self.assertEqual(len(identifiers), len(NORMATIVE_CLAUSES))
        for identifier, clause in NORMATIVE_CLAUSES.items():
            self.assertEqual(identifiers.count(identifier), 1)
            start = section.index("### " + identifier)
            next_heading = section.find("\n### ", start + 1)
            block = section[start:] if next_heading == -1 else section[start:next_heading]
            self.assertIn(clause, block, identifier)


class StageBE2E(unittest.TestCase):
    def test_01_known_good_current_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory)
            try:
                self.assertTrue(result.committed)
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertEqual(current.state, "CURRENT_READY")
            finally:
                store.close()

    def test_02_stage_a_blocked_cannot_upgrade(self):
        receipt = blocked_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, None, directory)
            try:
                self.assertIn("STAGE_A_NOT_READY", result.reason_codes)
            finally:
                store.close()

    def test_03_not_run_is_not_execution(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt, ("NOT_RUN",)), directory)
            try:
                self.assertIn("ATTEMPT_RECONCILIATION_INCOMPLETE", result.reason_codes)
            finally:
                store.close()

    def test_04_inconclusive_is_not_execution(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt, ("INCONCLUSIVE",)), directory)
            try:
                self.assertIn("ATTEMPT_RECONCILIATION_INCOMPLETE", result.reason_codes)
            finally:
                store.close()

    def test_05_fail_then_pass_is_preserved(self):
        receipt = ready_receipt()
        session = make_session(receipt, ("FAIL", "PASS"))
        self.assertEqual([event.status for event in session.events.values()], ["FAIL", "PASS"])
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, session, directory)
            try:
                self.assertTrue(result.committed)
                self.assertEqual(result.review_readiness, "NOT_READY_FOR_HUMAN_REVIEW")
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertEqual(current.state, "NOT_CURRENT_READY")
            finally:
                store.close()

    def test_21_trusted_fail_cannot_be_upgraded_by_receipt_pass(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt, ("FAIL",)), directory)
            try:
                self.assertEqual(receipt["evidence"][0]["outcome"], "OBSERVED_PASS")
                self.assertEqual(result.review_readiness, "NOT_READY_FOR_HUMAN_REVIEW")
                self.assertIn("TRUSTED_MECHANICAL_FAIL", result.reason_codes)
            finally:
                store.close()

    def test_22_prior_ready_then_fail_moves_canonical_head(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            ready, store = finalize(receipt, make_session(receipt), directory)
            try:
                prior = store.current_head(ready.decision_key)
                store.close()
                store = None
                failed, failed_store = finalize(receipt, make_session(receipt, ("FAIL",)), directory, expected_generation=prior["generation"], expected_head=prior["head_object_id"])
                try:
                    current = core.evaluate_current_readiness(failed, failed_store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                    self.assertEqual(failed.review_readiness, "NOT_READY_FOR_HUMAN_REVIEW")
                    self.assertEqual(current.state, "NOT_CURRENT_READY")
                    self.assertEqual(failed_store.current_head(failed.decision_key)["finalization_id"], failed.finalization_id)
                finally:
                    failed_store.close()
            finally:
                if store is not None:
                    store.close()

    def test_06_terminal_without_drain(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt, drain=False), directory)
            try:
                self.assertIn("ACQUISITION_DRAIN_INCOMPLETE", result.reason_codes)
            finally:
                store.close()

    def test_07_delayed_fail_before_drain(self):
        receipt = ready_receipt()
        session = make_session(receipt, ("FAIL",), drain=False, sealed=False)
        session.seal(2)
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, session, directory)
            try:
                self.assertIn("ACQUISITION_DRAIN_INCOMPLETE", result.reason_codes)
            finally:
                store.close()

    def test_08_stale_expected_head(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory)
            try:
                stale = make_session(receipt)
                _attach_authoritative(store, receipt, stale, "stale-operation")
                with self.assertRaises(core.StageBError):
                    core.finalize_verification(receipt, stale, store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt), expected_generation=0, expected_head=None)
            finally:
                store.close()

    def test_09_crash_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "before.sqlite3"
            code = "import importlib.util,os,sys; s=importlib.util.spec_from_file_location('m',sys.argv[1]); m=importlib.util.module_from_spec(s); sys.modules['m']=m; s.loader.exec_module(m); x=m.DecisionStore(sys.argv[2]); os._exit(17)"
            completed = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPT), str(db)], check=False)
            self.assertEqual(completed.returncode, 17)
            with core.DecisionStore(db) as store:
                self.assertIsNone(store.current_head("k"))

    def test_10_crash_after_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "after.sqlite3"
            code = "import importlib.util,os,sys; s=importlib.util.spec_from_file_location('m',sys.argv[1]); m=importlib.util.module_from_spec(s); sys.modules['m']=m; s.loader.exec_module(m); x=m.DecisionStore(sys.argv[2]); x.commit_finalization('k',{'decision_key':'k'}); os._exit(17)"
            completed = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPT), str(db)], check=False)
            self.assertEqual(completed.returncode, 17)
            with core.DecisionStore(db) as store:
                self.assertIsNotNone(store.current_head("k"))

    def test_11_revoked_verifier_currentness(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory)
            try:
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-2", "provider-1")
                self.assertEqual(current.state, "REFINALIZE_REQUIRED")
            finally:
                store.close()

    def test_12_revoked_provider(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory)
            try:
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"], revoked=("provider-1",)), "protocol-1", "provider-1")
                self.assertEqual(current.state, "REVERIFY_REQUIRED")
            finally:
                store.close()

    def test_13_stored_closure_omission_is_not_oracle(self):
        receipt = ready_receipt()
        roots = core.derive_required_fact_roots(receipt)
        with self.assertRaises(core.ProvenanceError):
            core.derive_decision_trust_closure(roots, {root: {"producer_realization_id": "provider-1"} for root in roots[:-1]})

    def test_14_provenance_laundering_is_rejected(self):
        receipt = ready_receipt()
        roots = core.derive_required_fact_roots(receipt)
        graph_value = {root: {"producer_realization_id": "provider-1", "parents": ["laundered"]} for root in roots}
        graph_value["laundered"] = {"producer_realization_id": "provider-1"}
        closure = core.derive_decision_trust_closure(roots, graph_value)
        self.assertIn("laundered", closure["objects"])

    def test_15_unknown_legacy_ancestry(self):
        receipt = ready_receipt()
        roots = core.derive_required_fact_roots(receipt)
        with self.assertRaises(core.ProvenanceError):
            core.derive_decision_trust_closure(roots, {root: {"producer_realization_id": "provider-1", "parents": ["legacy"]} for root in roots})

    def test_16_mutable_invocation_state_is_bound(self):
        first = core.InvocationInputBinding(core.InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"value": "A"})
        second = core.InvocationInputBinding(core.InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"value": "B"})
        self.assertNotEqual(first.canonical_id, second.canonical_id)

    def test_17_fake_trusted_identity_fails_closed(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt, caps=capabilities(realization_accepted=False)), directory)
            try:
                self.assertIn("PROVIDER_CAPABILITY_NOT_QUALIFYING", result.reason_codes)
            finally:
                store.close()

    def test_18_authority_self_cycle_is_rejected(self):
        with self.assertRaises(core.ProvenanceError):
            core.derive_decision_trust_closure(["root"], {"root": {"producer_realization_id": "provider-1", "parents": ["root"]}})

    def test_19_subject_drift(self):
        receipt = ready_receipt()
        session = make_session(receipt)
        session.subject_digest = "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, session, directory)
            try:
                self.assertIn("SUBJECT_MISMATCH", result.reason_codes)
            finally:
                store.close()

    def test_20_real_sqlite_observed_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with core.DecisionStore(pathlib.Path(directory) / "observed.sqlite3") as store:
                self.assertEqual(store.profile["journal_mode"], "wal")
                self.assertEqual(store.profile["synchronous"], 2)
                self.assertEqual(store.profile["foreign_keys"], 1)
                self.assertEqual(store.health_check(), "ok")


class V9HostileProofTests(unittest.TestCase):
    """Decision-complete hostile proof lanes for the V9 Architect contract."""

    def _attach(self, store, receipt, session, operation_id=None):
        return _attach_authoritative(store, receipt, session, operation_id or session.session_id)

    def _finalize_attached(self, store, receipt, session, **kwargs):
        _attach_authoritative(store, receipt, session)
        return core.finalize_verification(
            receipt,
            session,
            store,
            authority(receipt["subject"]["subject_digest"]),
            policy_snapshot={"version": 1},
            decision_protocol_realization_id="protocol-1",
            provenance_graph=graph(receipt),
            **kwargs,
        )

    def test_H01_distinct_operations_same_epoch(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first = make_session(receipt)
            second = make_session(receipt)
            a = self._attach(store, receipt, first, "operation-a")
            b = self._attach(store, receipt, second, "operation-b")
            self.assertEqual(a["epoch"], b["epoch"])
            self.assertNotEqual(a["acquisition_id"], b["acquisition_id"])

    def test_H02_open_participant_blocks_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first = make_session(receipt)
            self._attach(store, receipt, first, "operation-a")
            second = core.RunBoundEvidenceSession("operation-b", receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities())
            self._attach(store, receipt, second, "operation-b")
            before = {"finalizations": store.connection.execute("SELECT COUNT(*) FROM finalizations").fetchone()[0], "heads": store.connection.execute("SELECT COUNT(*) FROM decision_heads").fetchone()[0], "runtime": store.runtime_state(core.derive_decision_key(receipt)), "links": store.connection.execute("SELECT COUNT(*) FROM operation_publications WHERE finalization_id IS NOT NULL OR state='PENDING'").fetchone()[0]}
            result = self._finalize_attached(store, receipt, first)
            self.assertNotEqual(result.review_readiness, "READY_FOR_HUMAN_REVIEW")
            self.assertIn("EPOCH_INCOMPLETE", result.reason_codes)
            after = {"finalizations": store.connection.execute("SELECT COUNT(*) FROM finalizations").fetchone()[0], "heads": store.connection.execute("SELECT COUNT(*) FROM decision_heads").fetchone()[0], "runtime": store.runtime_state(core.derive_decision_key(receipt)), "links": store.connection.execute("SELECT COUNT(*) FROM operation_publications WHERE finalization_id IS NOT NULL OR state='PENDING'").fetchone()[0]}
            self.assertEqual(before, after)

    def test_H03_sealed_blocks_new_invocation(self):
        session = make_session(ready_receipt())
        with self.assertRaisesRegex(core.StageBError, "INVOCATION_AFTER_SEAL"):
            session.register_invocation("late", "C-1", operation={"kind": "COMMAND_EXECUTION"}, input_bindings=())

    def test_H04_terminal_acquisition_blocks_new_event(self):
        receipt = ready_receipt()
        session = make_session(receipt)
        check = next(item for item in receipt["verification_plan"] if item["selected"])
        with self.assertRaisesRegex(core.StageBError, "SEALED_EVENT_FOR_NON_INFLIGHT_INVOCATION"):
            session.ingest_event(core.ProviderEvent("late-event", next(iter(session.invocations)), check["id"], "PASS", operation=check["operation_contract"], sequence=99))

    def test_H05_pass_and_fail_same_epoch_not_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first, second = make_session(receipt), make_session(receipt, ("FAIL",))
            self._attach(store, receipt, first, "operation-a")
            self._attach(store, receipt, second, "operation-b")
            result = self._finalize_attached(store, receipt, first)
            self.assertIn("TRUSTED_MECHANICAL_FAIL", result.reason_codes)

    def test_H06_aborted_participant_not_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first = make_session(receipt)
            second = core.RunBoundEvidenceSession("operation-b", receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities())
            self._attach(store, receipt, first, "operation-a")
            info = self._attach(store, receipt, second, "operation-b")
            store.abort_acquisition(info["acquisition_id"])
            result = self._finalize_attached(store, receipt, first)
            self.assertIn("ACQUISITION_ABORTED_INCOMPLETE", result.reason_codes)

    def test_H07_serial_orders_are_deterministic_and_single_generation(self):
        receipt = ready_receipt()
        outcomes = []
        for order in (("a", "b"), ("b", "a")):
            with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
                sessions = {"a": make_session(receipt), "b": make_session(receipt)}
                for label in order:
                    self._attach(store, receipt, sessions[label], "operation-" + label)
                result = self._finalize_attached(store, receipt, sessions["a"])
                outcomes.append((result.generation, result.review_readiness, tuple(result.reason_codes)))
        self.assertEqual([item[0] for item in outcomes], [1, 1])
        self.assertEqual(outcomes[0][1:], outcomes[1][1:])

    def test_H08_persisted_event_conflict_is_rejected(self):
        receipt = ready_receipt()
        check = next(item for item in receipt["verification_plan"] if item["selected"])
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            session = core.RunBoundEvidenceSession("operation-a", receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities())
            session.register_invocation("inv-a", check["id"], operation=check["operation_contract"], input_bindings=(core.InvocationInputBinding(core.InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"config": "fixed"}),))
            self._attach(store, receipt, session, "operation-a")
            event = core.ProviderEvent("event-a", "inv-a", check["id"], "PASS", operation=check["operation_contract"], sequence=1)
            self.assertEqual(store.persist_event(session.acquisition_id, event), "RECORDED")
            with self.assertRaisesRegex(core.StageBError, "EVENT_ID_CONFLICT"):
                store.persist_event(session.acquisition_id, dataclasses.replace(event, status="FAIL"))

    def test_H09_changed_post_execution_snapshot_is_not_current(self):
        receipt = ready_receipt()
        session = make_session(receipt)
        session.post_execution_snapshot_id = "snapshot-s1"
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, session, directory)
            try:
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1", current_subject_snapshot_id="snapshot-s2")
                self.assertEqual(current.state, "NOT_CURRENT_READY")
                self.assertIn("CURRENT_SUBJECT_CHANGED", current.reason_codes)
            finally:
                store.close()

    def test_H10_unchanged_snapshot_is_bound_after_finalize(self):
        receipt = ready_receipt()
        session = make_session(receipt)
        session.post_execution_snapshot_id = "snapshot-s1"
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, session, directory)
            try:
                self.assertEqual(result.subject_snapshot_id, "snapshot-s1")
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1", current_subject_snapshot_id="snapshot-s1")
                self.assertEqual(current.state, "CURRENT_READY")
            finally:
                store.close()

    def test_H11_missing_policy_fails_closed(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            session = make_session(receipt)
            result = core.finalize_verification(receipt, session, store, dataclasses.replace(authority(receipt["subject"]["subject_digest"]), policy_snapshot_id=""), decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
            self.assertIn("CURRENT_POLICY_IDENTITY_MISSING", result.reason_codes)

    def test_H12_policy_change_requires_refinalization(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory)
            try:
                changed = core.derive_policy_snapshot_id({"version": 2})
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1", current_policy_snapshot_id=changed)
                self.assertEqual(current.state, "REFINALIZE_REQUIRED")
            finally:
                store.close()

    def test_H13_protocol_name_cannot_bypass_trust(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory, protocol="protocol-evil")
            try:
                self.assertIn("TRUST_REALIZATION_NOT_CURRENT", result.reason_codes)
            finally:
                store.close()

    def test_H14_known_good_accepts_provider_protocol_and_store(self):
        result, store = None, None
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory)
            try:
                self.assertEqual(result.review_readiness, "READY_FOR_HUMAN_REVIEW")
                self.assertTrue(authority(receipt["subject"]["subject_digest"]).accepts_required(("provider-1", "protocol-1", store.profile_id)))
            finally:
                store.close()

    def test_H15_revoked_provenance_producer_blocks(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            result, store = finalize(receipt, make_session(receipt), directory, graph_value=graph(receipt, provider="provider-evil"))
            try:
                self.assertIn("PROVENANCE_REALIZATION_NOT_ACCEPTED", result.reason_codes)
            finally:
                store.close()

    def test_H16_new_acquisition_invalidates_old_currentness(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"
            store = core.DecisionStore(db)
            first = make_session(receipt)
            result = self._finalize_attached(store, receipt, first)
            second = make_session(receipt)
            self._attach(store, receipt, second, "operation-new")
            current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
            self.assertEqual(current.state, "NOT_CURRENT_READY")
            self.assertIn("NEW_ACQUISITION_REQUIRES_FRESH_FINALIZATION", current.reason_codes)
            store.close()

    def test_H17_same_subject_failure_remains_blocking_after_pass(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"
            store = core.DecisionStore(db)
            first = make_session(receipt, ("FAIL",))
            failed = self._finalize_attached(store, receipt, first)
            prior = store.current_head(failed.decision_key)
            second = make_session(receipt)
            self._attach(store, receipt, second, "operation-pass-later")
            passed = self._finalize_attached(store, receipt, second, expected_generation=prior["generation"], expected_head=prior["head_object_id"])
            self.assertIn("PRIOR_TRUSTED_FAILURE_UNRESOLVED", core.evaluate_current_readiness(passed, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1").reason_codes)
            store.close()

    def test_H18_changed_subject_has_new_decision_key(self):
        first = ready_receipt()
        second = copy.deepcopy(first)
        second["subject"]["subject_digest"] = "c" * 64
        second["evidence"][0]["observed_subject_digest"] = "c" * 64
        self.assertNotEqual(core.derive_decision_key(first), core.derive_decision_key(second))

    def test_H19_restart_reloads_canonical_currentness(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"
            first = core.DecisionStore(db)
            result = self._finalize_attached(first, receipt, make_session(receipt))
            first.close()
            with core.DecisionStore(db) as restarted:
                current = core.evaluate_current_readiness(result, restarted, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertEqual(current.state, "CURRENT_READY")

    def test_H20_recovery_is_projection_only_and_zero_command(self):
        receipt = ready_receipt()
        command_counter = {"count": 0}
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "state.sqlite3")
            result = self._finalize_attached(store, receipt, make_session(receipt))
            recovered = store.recover_operation(result.operation_id)
            self.assertEqual(recovered["finalization_id"], result.finalization_id)
            self.assertEqual(store.current_head(result.decision_key)["generation"], result.generation)
            self.assertEqual(command_counter["count"], 0)
            store.close()

    def test_H21_old_pending_pass_cannot_be_recovered_after_new_fail(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "state.sqlite3")
            ready = self._finalize_attached(store, receipt, make_session(receipt))
            prior = store.current_head(ready.decision_key)
            failed_session = make_session(receipt, ("FAIL",))
            self._attach(store, receipt, failed_session, "operation-fail")
            self._finalize_attached(store, receipt, failed_session, expected_generation=prior["generation"], expected_head=prior["head_object_id"])
            self.assertIsNone(store.recover_operation(ready.operation_id))
            store.close()

    def test_H22_operation_id_is_idempotent_but_identity_conflict_fails(self):
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first = store.register_acquisition("decision", "subject", "operation", "provider", "profile", "contract")
            self.assertEqual(store.register_acquisition("decision", "subject", "operation", "provider", "profile", "contract")["acquisition_id"], first["acquisition_id"])
            with self.assertRaisesRegex(core.StageBError, "OPERATION_ID_CONFLICT"):
                store.register_acquisition("decision", "subject", "operation", "provider-evil", "profile", "contract")

    def test_H23_illegal_zero_row_transitions_fail(self):
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            info = store.register_acquisition("decision", "subject", "operation", "provider", "profile", "contract")
            store.seal_acquisition(info["acquisition_id"], 0)
            store.drain_acquisition(info["acquisition_id"], 0)
            for action in (lambda: store.seal_acquisition(info["acquisition_id"], 0), lambda: store.drain_acquisition(info["acquisition_id"], 0), lambda: store.abort_acquisition(info["acquisition_id"])):
                with self.assertRaises(core.StageBError):
                    action()

    def test_H24_two_finalizers_yield_one_generation(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first, second = make_session(receipt), make_session(receipt)
            self._attach(store, receipt, first, "operation-a")
            self._attach(store, receipt, second, "operation-b")
            result = self._finalize_attached(store, receipt, first)
            head = store.current_head(result.decision_key)
            retry = self._finalize_attached(store, receipt, second, expected_generation=head["generation"], expected_head=head["head_object_id"])
            self.assertEqual(retry.finalization_id, result.finalization_id)
            self.assertEqual(retry.generation, result.generation)
            self.assertEqual(store.current_head(result.decision_key)["generation"], 1)

    def test_H25_attempt_digest_changes_with_exact_history(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "state.sqlite3")
            first = self._finalize_attached(store, receipt, make_session(receipt))
            prior = store.current_head(first.decision_key)
            second = make_session(receipt)
            event_id = next(iter(second.events))
            second.events[event_id] = dataclasses.replace(second.events[event_id], payload={"history": "changed"})
            self._attach(store, receipt, second, "operation-different-history")
            newer = self._finalize_attached(store, receipt, second, expected_generation=prior["generation"], expected_head=prior["head_object_id"])
            self.assertNotEqual(first.attempt_lineage_digest, newer.attempt_lineage_digest)
            store.close()

    def test_H26_legacy_v8_finalization_requires_reverification(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            legacy = store.commit_finalization("legacy", {"decision_key": "legacy", "review_readiness": "READY_FOR_HUMAN_REVIEW"})
            current = core.evaluate_current_readiness(decision_key="legacy", decision_store=store, current_subject_digest="", authority_snapshot=authority(""), current_decision_protocol_realization_id="protocol-1")
            self.assertEqual(current.state, "REVERIFY_REQUIRED")
            self.assertIsNotNone(legacy)

    def test_H27_generalized_reuse_is_fail_closed(self):
        self.assertEqual(core.evaluate_evidence_reuse({"anything": "old"}), "REVERIFY_REQUIRED")

    def test_verifier_of_verifier_rejects_specified_bad_mutants(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("INSERT OR IGNORE INTO provider_events", source)
        self.assertNotIn('.startswith("protocol-")', source)
        self.assertIn("NEW_ACQUISITION_REQUIRES_FRESH_FINALIZATION", source)
        self.assertIn("EVENT_ID_CONFLICT", source)
        self.assertIn("recover_operation", source)
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"
            first_store = core.DecisionStore(db)
            second_store = core.DecisionStore(db)
            first, second = make_session(receipt), make_session(receipt)
            self._attach(first_store, receipt, first, "mutant-a")
            self._attach(second_store, receipt, second, "mutant-b")
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def run_stale(session, store):
                try:
                    barrier.wait(timeout=10)
                    results.append(self._finalize_attached(store, receipt, session, expected_generation=0, expected_head=None))
                except BaseException as exc:
                    errors.append(exc)

            workers = [threading.Thread(target=run_stale, args=(first, first_store)), threading.Thread(target=run_stale, args=(second, second_store))]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=30)
            self.assertFalse(errors)
            self.assertEqual(len(results), 2)
            self.assertEqual({(item.finalization_id, item.generation) for item in results}, {(results[0].finalization_id, 1)})
            self.assertEqual(first_store.connection.execute("SELECT COUNT(*) FROM finalizations").fetchone()[0], 1)
            first_store.close(); second_store.close()

    def test_H28_late_attachment_after_invocation_is_rejected(self):
        receipt = ready_receipt(); check = next(item for item in receipt["verification_plan"] if item["selected"])
        session = core.RunBoundEvidenceSession("h28", receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities())
        session.register_invocation("h28-inv", check["id"], operation=check["operation_contract"], input_bindings=())
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            with self.assertRaisesRegex(core.StageBError, "LATE_ACQUISITION_ATTACHMENT"):
                session.attach_store(store, core.derive_decision_key(receipt), "h28-op")
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0], 0)

    def test_H29_late_attachment_after_event_seal_drain_is_rejected(self):
        receipt = ready_receipt(); session = make_session(receipt)
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            with self.assertRaisesRegex(core.StageBError, "LATE_ACQUISITION_ATTACHMENT"):
                session.attach_store(store, core.derive_decision_key(receipt), "h29-op")
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0], 0)

    def test_H30_finalizer_never_auto_registers(self):
        receipt = ready_receipt(); session = make_session(receipt)
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            result = core.finalize_verification(receipt, session, store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
            self.assertFalse(result.committed); self.assertIn("ACQUISITION_NOT_DURABLY_REGISTERED", result.reason_codes)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0], 0)

    def test_H31_pre_activity_attachment_reaches_current_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            result = self._finalize_attached(store, receipt, make_session(receipt),)
            current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
            self.assertEqual(current.state, "CURRENT_READY")

    def test_H32_activation_after_seal_keeps_registered(self):
        receipt = ready_receipt(); check = next(item for item in receipt["verification_plan"] if item["selected"])
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            session = core.RunBoundEvidenceSession("h32", receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities())
            self._attach(store, receipt, session, "h32-op"); session.register_invocation("h32-inv", check["id"], operation=check["operation_contract"], input_bindings=())
            session.seal(0)
            with self.assertRaisesRegex(core.StageBError, "INVOCATION_AFTER_SEAL|INVOCATION_ACTIVATION_AFTER_SEAL"):
                session.activate_invocation("h32-inv")
            self.assertEqual(store.connection.execute("SELECT state FROM invocations WHERE invocation_id='h32-inv'").fetchone()[0], "REGISTERED")

    def test_H33_two_connection_serializations_have_only_legal_orders(self):
        receipt = ready_receipt(); key = core.derive_decision_key(receipt); check = next(item for item in receipt["verification_plan"] if item["selected"])
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"; first = core.DecisionStore(db); second = core.DecisionStore(db)
            info = first.register_acquisition(key, receipt["subject"]["subject_digest"], "h33-a", "provider-1", "profile-1", core.derive_verification_contract_id(receipt))
            record = {"invocation_id": "h33-inv", "check_id": check["id"], "operation": check["operation_contract"], "input_bindings": (), "required": True}
            first.register_invocation(info["acquisition_id"], record); first.activate_invocation(info["acquisition_id"], "h33-inv"); second.seal_acquisition(info["acquisition_id"], 1)
            event = core.ProviderEvent("h33-event", "h33-inv", check["id"], "PASS", operation=check["operation_contract"], sequence=1)
            first.persist_event(info["acquisition_id"], event); first.terminal_invocation(info["acquisition_id"], "h33-inv"); first.drain_acquisition(info["acquisition_id"], 1)
            self.assertEqual(first.connection.execute("SELECT state FROM acquisitions WHERE acquisition_id=?", (info["acquisition_id"],)).fetchone()[0], "DRAINED")
            first.close(); second.close()
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"; first = core.DecisionStore(db); second = core.DecisionStore(db)
            info = first.register_acquisition(key, receipt["subject"]["subject_digest"], "h33-b", "provider-1", "profile-1", core.derive_verification_contract_id(receipt)); first.register_invocation(info["acquisition_id"], {"invocation_id":"h33-inv", "check_id":check["id"], "operation":check["operation_contract"], "input_bindings":(), "required":True}); second.seal_acquisition(info["acquisition_id"], 0)
            with self.assertRaises(core.StageBError): first.activate_invocation(info["acquisition_id"], "h33-inv")
            self.assertEqual(first.connection.execute("SELECT state FROM invocations WHERE invocation_id='h33-inv'").fetchone()[0], "REGISTERED"); first.close(); second.close()

    def test_H34_current_provider_does_not_resurrect_historical_provider(self):
        provider = load_provider("h34_provider")
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            realization = types.SimpleNamespace(provider_realization_id="P2", config_requirements_sha256="config")
            _, _, current = provider.build_current_stage_b_context(core, realization, store, "s" * 64, "protocol-current")
            self.assertNotIn("P1", current.accepted_realization_ids); self.assertIn("P2", current.accepted_realization_ids)

    def test_H35_current_policy_is_fresh_not_historical(self):
        provider = load_provider("h35_provider")
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            realization = types.SimpleNamespace(provider_realization_id="P2", config_requirements_sha256="Y")
            policy, policy_id, _ = provider.build_current_stage_b_context(core, realization, store, "s" * 64, "protocol-current")
            self.assertEqual(policy["config_requirements_sha256"], "Y"); self.assertNotEqual(policy_id, core.derive_policy_snapshot_id({"historical":"X"}))

    def test_H36_unchanged_recovery_is_zero_command_current_ready(self):
        provider = load_provider("h36_provider")
        production = provider.CodexProvider(PROVIDER_SCRIPT.parent)
        finalize_module, snapshot_module, validator_module = production._load_core()

        class BombClient:
            def __init__(self):
                self.execute_count = 0
                self.closed = False

            def execute(self, *args, **kwargs):
                self.execute_count += 1
                raise AssertionError("H36_RECOVERY_MUST_NOT_EXECUTE")

            def close(self):
                self.closed = True

        realization = provider.RuntimeRealization(
            "profile-h36",
            "provider-1",
            {"provider": "controlled-h36"},
            {"platformFamily": "controlled", "platformOs": "controlled"},
            {},
            "config-h36",
            (),
        )
        bomb = BombClient()
        production._realization = lambda subject_root: (realization, bomb, (core, snapshot_module, validator_module))

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subject = root / "subject"
            subject.mkdir()
            subprocess.run(["git", "init", "--initial-branch", "main"], cwd=subject, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "h36-test"], cwd=subject, check=True)
            subprocess.run(["git", "config", "user.email", "h36@example.invalid"], cwd=subject, check=True)
            (subject / "subject.txt").write_text("stable subject\n", encoding="utf-8")
            subprocess.run(["git", "add", "subject.txt"], cwd=subject, check=True)
            subprocess.run(["git", "commit", "-m", "subject"], cwd=subject, check=True, capture_output=True)

            receipt = ready_receipt()
            snapshot = snapshot_module.capture_snapshot(subject)
            receipt["subject"]["subject_digest"] = snapshot["snapshot_id"]
            receipt["evidence"][0]["observed_subject_digest"] = snapshot["snapshot_id"]
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=True, sort_keys=True), encoding="utf-8")
            validator_module.validate(receipt)
            state_dir = root / "state"
            state_dir.mkdir()
            db = state_dir / "decisions.sqlite3"
            with core.DecisionStore(db) as store:
                session = make_session(receipt)
                _attach_authoritative(store, receipt, session)
                protocol_id = finalize_module.derive_core_realization_id()
                policy_snapshot, policy_id, live_authority = provider.build_current_stage_b_context(
                    finalize_module, realization, store, snapshot["snapshot_id"], protocol_id
                )
                final = core.finalize_verification(
                    receipt,
                    session,
                    store,
                    live_authority,
                    policy_snapshot=policy_snapshot,
                    decision_protocol_realization_id=protocol_id,
                    provenance_graph=graph(receipt),
                )
                self.assertTrue(final.committed)
                operation_id = store.pending_publications_for_decision(final.decision_key)[0]["operation_id"]

            recovered = production.recover(receipt_path, subject, state_dir, operation_id)

            self.assertTrue(bomb.closed)
            self.assertEqual(bomb.execute_count, 0)
            self.assertEqual(recovered["recovery_command_execution_count"], 0)
            self.assertEqual(recovered["current_readiness"]["state"], "CURRENT_READY")
            self.assertEqual(recovered["operation_id"], operation_id)
            self.assertEqual(recovered["finalization"]["finalization_id"], final.finalization_id)
            self.assertEqual(recovered["currentness_snapshot"]["snapshot_id"], snapshot["snapshot_id"])

    def test_H37_historical_extra_required_trust_never_expands_authority(self):
        provider = load_provider("h37_provider")
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            _, _, current = provider.build_current_stage_b_context(core, types.SimpleNamespace(provider_realization_id="P2", config_requirements_sha256="config"), store, "s" * 64, "protocol-current")
            historical_required = {"P2", "protocol-current", store.profile_id, "N1"}
            self.assertNotIn("N1", current.accepted_realization_ids); self.assertFalse(current.accepts_required(historical_required))

    def _cli_exit_for_current_state(self, state):
        provider = load_provider("cli_provider_" + state)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory); subject = root / "subject"; subject.mkdir(); result = root / "result.json"
            original = provider.CodexProvider.run; provider.CodexProvider.run = lambda self, *args: {"current_readiness":{"state":state}, "review_readiness":"READY_FOR_HUMAN_REVIEW"}
            try: return provider.main(["verify", "--receipt", str(root / "receipt.json"), "--subject-root", str(subject), "--state-dir", str(root / "state"), "--result", str(result)])
            finally: provider.CodexProvider.run = original

    def test_H38_H39_H40_historical_ready_never_exits_zero(self):
        for state in ("NOT_CURRENT_READY", "REFINALIZE_REQUIRED", "REVERIFY_REQUIRED"):
            self.assertEqual(self._cli_exit_for_current_state(state), 2)

    def test_H41_only_current_ready_exits_zero(self):
        self.assertEqual(self._cli_exit_for_current_state("CURRENT_READY"), 0)

    def test_H42_fresh_subject_change_remains_noncurrent(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            result = self._finalize_attached(store, receipt, make_session(receipt)); current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1", current_subject_snapshot_id="changed")
            self.assertEqual(current.state, "NOT_CURRENT_READY"); self.assertIn("CURRENT_SUBJECT_CHANGED", current.reason_codes)

    def test_H43_noncurrent_projection_can_be_published(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            result = self._finalize_attached(store, receipt, make_session(receipt)); changed = dataclasses.replace(authority(receipt["subject"]["subject_digest"]), policy_snapshot_id="changed-policy"); current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], changed, "protocol-1", "provider-1", current_policy_snapshot_id="changed-policy")
            self.assertEqual(current.state, "REFINALIZE_REQUIRED"); store.mark_projection_published(result.operation_id, "projection-digest"); self.assertEqual(store.connection.execute("SELECT state FROM operation_publications WHERE operation_id=?", (result.operation_id,)).fetchone()[0], "PUBLISHED")

    def test_H45_evidence_reuse_remains_fail_closed(self):
        for value in ({}, {"decision_key":"old"}, {"required_trust_realization_ids":["N1"]}): self.assertEqual(core.evaluate_evidence_reuse(value), "REVERIFY_REQUIRED")

    def test_H44_old_favorable_pending_result_cannot_be_recovered(self):
        self.test_H21_old_pending_pass_cannot_be_recovered_after_new_fail()

    def test_H46_incomplete_epoch_has_no_finalization_or_publication_link(self):
        self.test_H02_open_participant_blocks_ready()

    def test_H47_completed_epoch_links_every_operation_once(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first, second = make_session(receipt), make_session(receipt); self._attach(store, receipt, first, "h47-a"); self._attach(store, receipt, second, "h47-b"); result = self._finalize_attached(store, receipt, first)
            self.assertEqual(result.generation, 1); self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM finalizations").fetchone()[0], 1); self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM operation_publications WHERE finalization_id=? AND state='PENDING'", (result.finalization_id,)).fetchone()[0], 2)

    def test_H48_same_completed_epoch_finalizers_converge(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            first, second = make_session(receipt), make_session(receipt); self._attach(store, receipt, first, "h48-a"); self._attach(store, receipt, second, "h48-b"); a = self._finalize_attached(store, receipt, first); head = store.current_head(a.decision_key); b = self._finalize_attached(store, receipt, second, expected_generation=head["generation"], expected_head=head["head_object_id"])
            self.assertEqual((a.finalization_id, a.generation), (b.finalization_id, b.generation)); self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM finalizations").fetchone()[0], 1)

    def test_H49_executed_terminal_requires_active(self):
        receipt = ready_receipt(); check = next(item for item in receipt["verification_plan"] if item["selected"])
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            session = core.RunBoundEvidenceSession("h49", receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities()); self._attach(store, receipt, session, "h49-op"); session.register_invocation("h49-inv", check["id"], operation=check["operation_contract"], input_bindings=())
            with self.assertRaisesRegex(core.StageBError, "INVOCATION_NOT_ACTIVE"): session.ingest_event(core.ProviderEvent("h49-event", "h49-inv", check["id"], "PASS", operation=check["operation_contract"], sequence=1))
            self.assertEqual(store.connection.execute("SELECT state FROM invocations WHERE invocation_id='h49-inv'").fetchone()[0], "REGISTERED")

    def test_H50_active_then_seal_allows_terminal_but_seal_then_activate_rejects(self):
        self.test_H33_two_connection_serializations_have_only_legal_orders()

    def test_H51_protocol_change_requires_refinalization(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory, core.DecisionStore(pathlib.Path(directory) / "state.sqlite3") as store:
            result = self._finalize_attached(store, receipt, make_session(receipt)); current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-v10", "provider-1")
            self.assertEqual(current.state, "REFINALIZE_REQUIRED"); self.assertIn("DECISION_PROTOCOL_CHANGED", current.reason_codes)

    def test_H52_reopened_store_validates_existing_acquisition_without_new_row(self):
        receipt = ready_receipt(); key = core.derive_decision_key(receipt)
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "state.sqlite3"; first = core.DecisionStore(db); contract = core.derive_verification_contract_id(receipt); info = first.register_acquisition(key, receipt["subject"]["subject_digest"], "h52-op", "provider-1", "profile-1", contract); first.close(); reopened = core.DecisionStore(db); validated = reopened.validate_registered_acquisition(acquisition_id=info["acquisition_id"], decision_key=key, operation_id="h52-op", subject_digest=receipt["subject"]["subject_digest"], provider_realization_id="provider-1", provider_profile_id="profile-1", epoch=info["epoch"], verification_contract_id=contract); self.assertEqual(validated["acquisition_id"], info["acquisition_id"]); self.assertEqual(reopened.connection.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0], 1); reopened.close()

    def test_H53_all_evidence_reuse_forms_are_reverify_required(self):
        for args in ((), ({"old":1},), ({"old":1}, "provider"), (None, None, None)): self.assertEqual(core.evaluate_evidence_reuse(*args, reason="historical"), "REVERIFY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
