import contextlib
import dataclasses
import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest


PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PUBLIC_ROOT / "skills/ai-change-verification/scripts/finalize_verification.py"


@contextlib.contextmanager
def load_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("MODULE_SPEC_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    if previous is not None:
        raise RuntimeError("MODULE_NAME_COLLISION")
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    except BaseException:
        if sys.modules.get(module_name) is module:
            sys.modules.pop(module_name, None)
        raise
    else:
        if sys.modules.get(module_name) is module:
            sys.modules.pop(module_name, None)


with load_module("acv_stage_b_core_public_test", SCRIPT) as core:
    pass


def ready_receipt():
    path = PUBLIC_ROOT / "tests/fixtures/receipt_minimal_ready.json"
    return json.loads(path.read_text(encoding="utf-8"))


def qualifying_session(receipt, bindings=True, statuses=("PASS",), session_id="run-1", store=None):
    check = next(item for item in receipt["verification_plan"] if item["selected"])
    capabilities = core.ProviderCapabilities(
        admission_visibility_complete=True,
        effective_operation_identity=True,
        terminal_outcome_identity=True,
        material_execution_context_binding=True,
        final_event_frontier=True,
        drain_acknowledgement=True,
        realization_accepted=True,
    )
    session = core.RunBoundEvidenceSession(session_id, receipt["subject"]["subject_digest"], "provider-1", "profile-1", capabilities, core.derive_verification_contract_id(receipt))
    input_bindings = (core.InvocationInputBinding(core.InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"config": "fixed"}),) if bindings else ()
    operation = check["operation_contract"]
    if store is not None:
        session.attach_store(store, core.derive_decision_key(receipt), session_id)
    session.register_invocation("inv-" + session_id, check["id"], operation=operation, input_bindings=input_bindings)
    session.activate_invocation("inv-" + session_id)
    for sequence, status in enumerate(statuses, 1):
        session.ingest_event(core.ProviderEvent(f"event-{session_id}-{sequence}", "inv-" + session_id, check["id"], status, operation=operation, sequence=sequence))
    session.seal(len(statuses))
    session.acknowledge_drain(len(statuses))
    return session


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        if isinstance(self.row, BaseException):
            raise self.row
        return self.row


class _PragmaConnection:
    def __init__(self, *, journal="wal", synchronous=2, foreign_keys=1, query_overrides=None, active=False):
        self.journal = journal
        self.synchronous = synchronous
        self.foreign_keys = foreign_keys
        self.query_overrides = query_overrides or {}
        self.in_transaction = active
        self.statements = []

    def execute(self, statement, *args):
        del args
        self.statements.append(statement)
        if statement == "PRAGMA journal_mode=WAL":
            return _Cursor(("wal",))
        if statement == "PRAGMA journal_mode":
            return _Cursor(self.query_overrides.get("journal_mode", (self.journal,)))
        if statement == "PRAGMA synchronous=FULL":
            return _Cursor(None)
        if statement == "PRAGMA synchronous":
            return _Cursor(self.query_overrides.get("synchronous", (self.synchronous,)))
        if statement == "PRAGMA foreign_keys=ON":
            return _Cursor(None)
        if statement == "PRAGMA foreign_keys":
            return _Cursor(self.query_overrides.get("foreign_keys", (self.foreign_keys,)))
        if statement == "PRAGMA integrity_check":
            return _Cursor(self.query_overrides.get("integrity_check", ("ok",)))
        raise AssertionError(f"unexpected SQL: {statement}")


class _SetterShapeConnection:
    def __init__(self, connection):
        self.connection = connection
        self.statements = []

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, statement, *args):
        self.statements.append(statement)
        cursor = self.connection.execute(statement, *args)
        if statement in {"PRAGMA synchronous=FULL", "PRAGMA foreign_keys=ON"}:
            class _ForbiddenFetch:
                def fetchone(self):
                    raise AssertionError("setter result must not be read")

            return _ForbiddenFetch()
        return cursor


def authority(subject_digest):
    store_profile = core._identity("store-realization", {"python_version": sys.version.split()[0], "sqlite_library_version": sqlite3.sqlite_version, "journal_mode": "wal", "synchronous": 2, "foreign_keys": 1, "support_profile": "local_sqlite_wal_full"})
    policy = core.derive_policy_snapshot_id({"version": 1})
    return core.AuthoritySnapshot(
        "authority-1",
        subject_digest,
        policy_snapshot_id=policy,
        authority_root_id="root-1",
        accepted_realization_ids=("provider-1", "protocol-1", store_profile),
        topology_complete=True,
        complete=True,
    )


def graph(receipt):
    roots = core.derive_required_fact_roots(receipt)
    return {
        root: core.TransformRecord("provider-1", core.TransformMode.EXPLICIT_INPUT_TRANSFORM.value)
        for root in roots
    }


class StageBCoreTests(unittest.TestCase):
    def test_v8_missing_required_identity_and_reuse_fail_closed(self):
        self.assertFalse(core.AuthoritySnapshot("a", "s", complete=True, topology_complete=True).accepts(None))
        self.assertEqual(core.evaluate_evidence_reuse(), "REVERIFY_REQUIRED")
        self.assertEqual(core.evaluate_evidence_reuse(True, True, True, True, True, True), "REVERIFY_REQUIRED")

    def test_v8_policy_identity_is_domain_separated(self):
        self.assertNotEqual(core.derive_policy_snapshot_id({"version": 1}), core._identity("policy-snapshot", {"version": 1}))
        self.assertEqual(core.derive_policy_snapshot_id({"version": 1}), core.derive_policy_snapshot_id({"version": 1}))

    def test_v8_store_objects_are_content_addressed_and_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "objects.sqlite3"
            with core.DecisionStore(path) as store:
                object_id = store.put_object("CHECK_RESULT_FACT", {"value": 1})
                self.assertEqual(store.get_object(object_id)["value"], 1)
                with self.assertRaises(core.ProvenanceError):
                    store.put_object("CHECK_RESULT_FACT", {"value": 2}, object_id=object_id)
            with core.DecisionStore(path) as reopened:
                self.assertEqual(reopened.get_object(object_id)["value"], 1)

    def test_v8_new_acquisition_invalidates_prior_currentness(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                first = core.finalize_verification(receipt, qualifying_session(receipt, store=store), store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
                acquisition = store.register_acquisition(first.decision_key, receipt["subject"]["subject_digest"], "operation-2", "provider-1", "profile-1", core.derive_verification_contract_id(receipt))
                current = core.evaluate_current_readiness(first, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertEqual(acquisition["epoch"], 2)
                self.assertEqual(current.state, "NOT_CURRENT_READY")
                self.assertIn("ACQUISITION_IN_PROGRESS", current.reason_codes)
            finally:
                store.close()

    def test_v8_projection_cannot_override_canonical_subject_or_outcome(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                failed = core.finalize_verification(receipt, qualifying_session(receipt, statuses=("FAIL",), store=store), store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
                fake = dataclasses.replace(failed, review_readiness="READY_FOR_HUMAN_REVIEW", subject_digest=receipt["subject"]["subject_digest"] if hasattr(failed, "subject_digest") else None)
                current = core.evaluate_current_readiness(fake, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertNotEqual(current.state, "CURRENT_READY")
            finally:
                store.close()
    def test_product_internal_loader_validates_receipt(self):
        core._validate_stage_a_receipt(ready_receipt())

    def test_public_loader_dataclass_registration_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "probe.py"
            path.write_text(
                "from __future__ import annotations\n"
                "import sys\n"
                "from dataclasses import dataclass\n"
                "registered_during_exec = sys.modules.get(__name__) is not None\n"
                "@dataclass\n"
                "class Probe:\n"
                "    value: str\n",
                encoding="utf-8",
            )
            name = "acv_public_loader_probe"
            self.assertNotIn(name, sys.modules)
            with load_module(name, path) as module:
                self.assertTrue(module.registered_during_exec)
                self.assertEqual(module.Probe("ok").value, "ok")
                self.assertIs(sys.modules[name], module)
            self.assertNotIn(name, sys.modules)

    def test_public_loader_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "failing.py"
            path.write_text("raise RuntimeError('intentional failure')\n", encoding="utf-8")
            name = "acv_public_loader_failure"
            with self.assertRaises(RuntimeError):
                with load_module(name, path):
                    pass
            self.assertNotIn(name, sys.modules)

    def test_public_loader_rejects_module_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "probe.py"
            path.write_text("value = 1\n", encoding="utf-8")
            name = "acv_public_loader_collision"
            sentinel = object()
            sys.modules[name] = sentinel
            try:
                with self.assertRaisesRegex(RuntimeError, "MODULE_NAME_COLLISION"):
                    with load_module(name, path):
                        pass
                self.assertIs(sys.modules[name], sentinel)
            finally:
                sys.modules.pop(name, None)

    def test_decision_key_excludes_run_id(self):
        receipt = ready_receipt()
        first = core.derive_decision_key(receipt)
        receipt["run_id"] = "different"
        self.assertEqual(first, core.derive_decision_key(receipt))

    def test_duplicate_event_is_idempotent_and_conflict_is_invalid(self):
        receipt = ready_receipt()
        session = qualifying_session(receipt)
        event = core.ProviderEvent("event-run-1-1", "inv-run-1", "C-1", "PASS", operation=receipt["verification_plan"][0]["operation_contract"], sequence=1)
        self.assertEqual(session.ingest_event(event), "IDEMPOTENT")
        with self.assertRaises(core.StageBError):
            session.ingest_event(core.ProviderEvent("event-run-1-1", "inv-run-1", "C-1", "FAIL", sequence=1))

    def test_finalize_and_current_readiness(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                result = core.finalize_verification(receipt, qualifying_session(receipt, store=store), store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
                self.assertEqual(result.verification_run_status, "COMPLETE")
                self.assertEqual(result.review_readiness, "READY_FOR_HUMAN_REVIEW")
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertEqual(current.state, "CURRENT_READY")
            finally:
                store.close()

    def test_trusted_fail_is_committed_nonready_and_not_current_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                result = core.finalize_verification(receipt, qualifying_session(receipt, statuses=("FAIL",), store=store), store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
                self.assertTrue(result.committed)
                self.assertEqual(result.review_readiness, "NOT_READY_FOR_HUMAN_REVIEW")
                self.assertIn("TRUSTED_MECHANICAL_FAIL", result.reason_codes)
                current = core.evaluate_current_readiness(result, store, receipt["subject"]["subject_digest"], authority(receipt["subject"]["subject_digest"]), "protocol-1", "provider-1")
                self.assertEqual(current.state, "NOT_CURRENT_READY")
                self.assertEqual(current.finalization_id, result.finalization_id)
            finally:
                store.close()

    def test_same_run_fail_then_pass_is_not_clean_ready(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                result = core.finalize_verification(receipt, qualifying_session(receipt, statuses=("FAIL", "PASS"), store=store), store, authority(receipt["subject"]["subject_digest"]), policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
                self.assertTrue(result.committed)
                self.assertEqual(result.review_readiness, "NOT_READY_FOR_HUMAN_REVIEW")
                self.assertIn("TRUSTED_ATTEMPT_CONFLICT", result.reason_codes)
            finally:
                store.close()

    def test_prior_ready_then_fail_advances_head_and_later_clean_run_can_recover(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            auth = authority(receipt["subject"]["subject_digest"])
            try:
                ready = core.finalize_verification(receipt, qualifying_session(receipt, store=store), store, auth, policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt))
                ready_head = store.current_head(ready.decision_key)
                failed = core.finalize_verification(receipt, qualifying_session(receipt, statuses=("FAIL",), session_id="run-2", store=store), store, auth, policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt), expected_generation=ready_head["generation"], expected_head=ready_head["head_object_id"])
                failed_head = store.current_head(failed.decision_key)
                self.assertEqual(failed.generation, 2)
                self.assertNotEqual(failed_head["finalization_id"], ready.finalization_id)
                current_failed = core.evaluate_current_readiness(failed, store, receipt["subject"]["subject_digest"], auth, "protocol-1", "provider-1")
                self.assertEqual(current_failed.state, "NOT_CURRENT_READY")
                clean = core.finalize_verification(receipt, qualifying_session(receipt, session_id="run-3", store=store), store, auth, policy_snapshot={"version": 1}, decision_protocol_realization_id="protocol-1", provenance_graph=graph(receipt), expected_generation=failed_head["generation"], expected_head=failed_head["head_object_id"])
                self.assertEqual(clean.review_readiness, "NOT_READY_FOR_HUMAN_REVIEW")
                self.assertIn("TRUSTED_MECHANICAL_FAIL", clean.reason_codes)
                current_clean = core.evaluate_current_readiness(clean, store, receipt["subject"]["subject_digest"], auth, "protocol-1", "provider-1")
                self.assertEqual(current_clean.state, "NOT_CURRENT_READY")
                self.assertIn("PRIOR_TRUSTED_FAILURE_UNRESOLVED", current_clean.reason_codes)
                count = store.connection.execute("SELECT COUNT(*) FROM finalizations WHERE decision_key = ?", (receipt and clean.decision_key,)).fetchone()[0]
                self.assertEqual(count, 3)
            finally:
                store.close()

    def test_stage_a_blocked_cannot_be_upgraded(self):
        receipt = ready_receipt()
        receipt["readiness"]["state"] = "BLOCKED_ON_MISSING_EVIDENCE"
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                result = core.finalize_verification(receipt, None, store, None)
                self.assertEqual(result.review_readiness, "BLOCKED_ON_MISSING_EVIDENCE")
                self.assertIn("STAGE_A_NOT_READY", result.reason_codes)
            finally:
                store.close()

    def test_seal_and_drain_are_required(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                session = qualifying_session(receipt, store=store)
                session.trusted_ingestion_drain_frontier = None
                result = core.finalize_verification(receipt, session, store, authority(receipt["subject"]["subject_digest"]), provenance_graph=graph(receipt))
                self.assertIn("ACQUISITION_DRAIN_INCOMPLETE", result.reason_codes)
            finally:
                store.close()

    def test_missing_invocation_binding_fails_closed(self):
        receipt = ready_receipt()
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                session = qualifying_session(receipt, bindings=False, store=store)
                result = core.finalize_verification(receipt, session, store, authority(receipt["subject"]["subject_digest"]), provenance_graph=graph(receipt))
                self.assertIn("INVOCATION_INPUT_BINDING_MISSING", result.reason_codes)
            finally:
                store.close()

    def test_store_expected_head_and_corruption_boundaries(self):
        with self.assertRaises(core.StoreCapabilityError):
            core.DecisionStore(":memory:")
        with tempfile.TemporaryDirectory() as directory:
            store = core.DecisionStore(pathlib.Path(directory) / "decisions.sqlite3")
            try:
                payload = {"decision_key": "k", "subject_digest": "s"}
                store.commit_finalization("k", payload, expected_generation=0, expected_head=None)
                with self.assertRaises(core.StageBError):
                    store.commit_finalization("k", payload, expected_generation=0, expected_head=None)
            finally:
                store.close()

    def test_runtime_profile_records_effective_file_backed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "profile.sqlite3"
            store = core.DecisionStore(path)
            try:
                self.assertEqual(store.profile["journal_mode"], "wal")
                self.assertEqual(store.profile["synchronous"], 2)
                self.assertEqual(store.profile["foreign_keys"], 1)
                self.assertEqual(store.health_check(), "ok")
                row = store.connection.execute(
                    "SELECT profile_json FROM store_profile WHERE profile_id = ?",
                    (store.profile_id,),
                ).fetchone()
                recorded = json.loads(row[0])
                self.assertEqual(recorded["journal_mode"], "wal")
                self.assertEqual(recorded["synchronous"], 2)
                self.assertEqual(recorded["foreign_keys"], 1)
            finally:
                store.close()

    def test_setter_shape_is_not_used_as_effective_state(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(str(pathlib.Path(directory) / "setter-shape.sqlite3"), isolation_level=None)
            try:
                sync_setter_row = connection.execute("PRAGMA synchronous=FULL").fetchone()
                foreign_keys_setter_row = connection.execute("PRAGMA foreign_keys=ON").fetchone()
                self.assertTrue(sync_setter_row is None or len(sync_setter_row) == 1)
                self.assertTrue(foreign_keys_setter_row is None or len(foreign_keys_setter_row) == 1)
                proxy = _SetterShapeConnection(connection)
                store = core.DecisionStore.__new__(core.DecisionStore)
                store.connection = proxy
                store._configure()
                self.assertEqual(store.profile["synchronous"], 2)
                self.assertEqual(store.profile["foreign_keys"], 1)
                self.assertIn("PRAGMA synchronous=FULL", proxy.statements)
                self.assertIn("PRAGMA synchronous", proxy.statements)
                self.assertIn("PRAGMA foreign_keys=ON", proxy.statements)
                self.assertIn("PRAGMA foreign_keys", proxy.statements)
            finally:
                connection.close()

    def test_wrong_effective_profile_fails_closed(self):
        for pragma, value in (("journal_mode", ("delete",)), ("synchronous", (1,)), ("foreign_keys", (0,))):
            with self.subTest(pragma=pragma):
                connection = _PragmaConnection(query_overrides={pragma: value})
                store = core.DecisionStore.__new__(core.DecisionStore)
                store.connection = connection
                with self.assertRaises(core.StoreCapabilityError):
                    store._configure()

    def test_missing_or_malformed_profile_rows_fail_closed(self):
        for pragma, value in (("journal_mode", None), ("synchronous", ()), ("foreign_keys", (1, 2))):
            with self.subTest(pragma=pragma):
                connection = _PragmaConnection(query_overrides={pragma: value})
                store = core.DecisionStore.__new__(core.DecisionStore)
                store.connection = connection
                with self.assertRaises(core.StoreCapabilityError):
                    store._configure()

    def test_configuration_rejects_active_transaction(self):
        connection = _PragmaConnection(active=True)
        store = core.DecisionStore.__new__(core.DecisionStore)
        store.connection = connection
        with self.assertRaises(core.StoreCapabilityError):
            store._configure()
        self.assertEqual(connection.statements, [])

    def test_health_check_requires_exact_ok(self):
        for result in (("not ok",), None, ("ok", "extra")):
            with self.subTest(result=result):
                connection = _PragmaConnection(query_overrides={"integrity_check": result})
                store = core.DecisionStore.__new__(core.DecisionStore)
                store.connection = connection
                with self.assertRaises(core.StoreCapabilityError):
                    store.health_check()

    def test_provenance_detects_missing_parent_and_cycle(self):
        with self.assertRaises(core.ProvenanceError):
            core.derive_decision_trust_closure(["root"], {"root": {"producer_realization_id": "p", "parents": ["missing"]}})
        with self.assertRaises(core.ProvenanceError):
            core.derive_decision_trust_closure(["root"], {"root": {"producer_realization_id": "p", "parents": ["child"]}, "child": {"producer_realization_id": "p", "parents": ["root"]}})

    def test_reuse_outcomes(self):
        self.assertEqual(core.evaluate_evidence_reuse(), "REVERIFY_REQUIRED")
        self.assertEqual(core.evaluate_evidence_reuse(provenance_complete=False, raw_evidence_sufficient=True), "REVERIFY_REQUIRED")
        self.assertEqual(core.evaluate_evidence_reuse(invocation_inputs_bound=False), "REVERIFY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
