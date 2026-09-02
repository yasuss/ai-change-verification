import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATOR = PUBLIC_ROOT / "skills/ai-change-verification/scripts/validate_receipt.py"
SANITIZER = PUBLIC_ROOT / "skills/ai-change-verification/scripts/sanitize_evidence.py"
READY = PUBLIC_ROOT / "tests/fixtures/receipt_minimal_ready.json"


def load_snapshot():
    script = PUBLIC_ROOT / "skills/ai-change-verification/scripts/change_snapshot.py"
    spec = importlib.util.spec_from_file_location("acv_semantic_snapshot", script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def load_validator():
    spec = importlib.util.spec_from_file_location("acv_semantic_receipt_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


class BaseReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()
        cls.ready_fixture = json.loads(READY.read_text(encoding="utf-8"))

    def ready(self):
        return json.loads(json.dumps(self.ready_fixture))

    def result(self, data):
        try:
            self.validator.validate(data); return True, "PASS"
        except self.validator.ReceiptError as error:
            return False, str(error)

    def assert_reason(self, data, reason):
        ok, actual = self.result(data)
        self.assertFalse(ok); self.assertEqual(actual, reason)

    def rebind(self, data, check_index=0):
        check = data["verification_plan"][check_index]
        check["check_contract_digest"] = self.validator.compute_check_contract_digest(check)
        for ev in data["evidence"]:
            if ev.get("check_id") == check["id"]:
                ev["check_contract_digest"] = check["check_contract_digest"]

    def set_command_fail(self, data, evidence_index=0):
        ev = data["evidence"][evidence_index]
        ev["execution"]["exit_code"] = 1
        ev["outcome"] = "OBSERVED_FAIL"


class SemanticRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()
        cls.ready_fixture = json.loads(READY.read_text(encoding="utf-8"))

    def ready(self):
        return json.loads(json.dumps(self.ready_fixture))

    def result(self, data):
        try:
            self.validator.validate(data); return True, "PASS"
        except self.validator.ReceiptError as error:
            return False, str(error)

    def assert_reason(self, data, reason):
        ok, actual = self.result(data)
        self.assertFalse(ok); self.assertEqual(actual, reason)

    def rebind(self, data, check_index=0):
        check = data["verification_plan"][check_index]
        check["check_contract_digest"] = self.validator.compute_check_contract_digest(check)
        for ev in data["evidence"]:
            if ev.get("check_id") == check["id"]:
                ev["check_contract_digest"] = check["check_contract_digest"]

    def set_command_fail(self, data, evidence_index=0):
        ev = data["evidence"][evidence_index]
        ev["execution"]["exit_code"] = 1
        ev["outcome"] = "OBSERVED_FAIL"

    def validate_cli(self, data):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "receipt.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return subprocess.run([sys.executable, str(VALIDATOR), str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_forged_schema_valid_llm_execution_receipt(self):
        data = self.ready(); data["evidence"][0]["capture_origin"] = "LLM_INTERPRETATION"
        self.assert_reason(data, "MECHANICAL_EVIDENCE_ORIGIN_INVALID")

    def test_interpretation_cannot_originate_observed_pass(self):
        data = self.ready(); data["evidence"].append({
            "id":"E-2","kind":"INTERPRETATION","capture_origin":"LLM_INTERPRETATION",
            "subject_relationship":"NOT_APPLICABLE","outcome":"OBSERVED_PASS","reliability":"NOT_APPLICABLE",
            "baseline_attribution":"NOT_APPLICABLE","baseline_comparability":"NOT_APPLICABLE","freshness":"NOT_APPLICABLE",
            "source_evidence_ids":["E-1"],"summary":"looks good","limitations":[]})
        self.assert_reason(data, "INTERPRETATION_OUTCOME_INVALID")

    def test_normalization_requires_bound_source_observation(self):
        data = self.ready(); data["evidence"][0]["normalization"] = {"host_profile":"codex/windows","adapter_id":"adapter","adapter_version":"1","source_observation":{"provider":"codex"}}
        self.assert_reason(data, "NORMALIZATION_SOURCE_UNBOUND")
        data = self.ready(); data["evidence"][0]["normalization"] = {"host_profile":"codex/windows","adapter_id":"adapter","adapter_version":"1","source_observation":{"provider":"codex","observation_id":"obs-123","sanitized_sha256":"f"*64}}
        self.assertEqual(self.result(data), (True,"PASS"))

    def test_state_drift_during_capture(self):
        module = load_snapshot(); original = module._capture_once; state={"n":0}
        def changing(_repo,_base): state["n"] += 1; return {"snapshot_id":str(state["n"]),"complete":True,"limitations":[]}
        module._capture_once = changing
        try: result = module.capture_snapshot(pathlib.Path("."), max_attempts=2)
        finally: module._capture_once = original
        self.assertFalse(result["complete"])

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/"x.json"; path.write_bytes(b'{"schema_version":"1.2","schema_version":"1.3"}')
            result=subprocess.run([sys.executable,str(VALIDATOR),str(path)],stdout=subprocess.PIPE,text=True)
        self.assertIn("JSON_DUPLICATE_KEY", result.stdout)

    def test_duplicate_record_id_rejected(self):
        data=self.ready(); data["evidence"].append(json.loads(json.dumps(data["evidence"][0])))
        self.assert_reason(data,"DUPLICATE_ID")

    def test_dangling_reference_rejected(self):
        data=self.ready(); data["obligations"][0]["evidence_ids"]=["E-missing"]
        self.assert_reason(data,"DANGLING_EVIDENCE_REFERENCE")
        data=self.ready(); data["evidence"][0]["check_id"]="C-missing"
        self.assert_reason(data,"DANGLING_CHECK_REFERENCE")

    def test_unknown_field_rejected(self):
        data=self.ready(); data["readiness"]["extra"]="x"; self.assert_reason(data,"UNKNOWN_FIELD")

    def test_nonfinite_json_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/"x.json"; path.write_bytes(b'{"schema_version":"1.2","product":"ai-change-verification","x":NaN}')
            result=subprocess.run([sys.executable,str(VALIDATOR),str(path)],stdout=subprocess.PIPE,text=True)
        self.assertIn("JSON_NONFINITE",result.stdout)

    def test_receipt_size_and_depth_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            large=pathlib.Path(td)/"large.json"; large.write_bytes(b'{"schema_version":"1.2","pad":"'+b"x"*1048600+b'"}')
            result=subprocess.run([sys.executable,str(VALIDATOR),str(large)],stdout=subprocess.PIPE,text=True); self.assertIn("RECEIPT_TOO_LARGE",result.stdout)
            deep=pathlib.Path(td)/"deep.json"; nesting="["*72+"0"+"]"*72
            deep.write_text('{"schema_version":"1.2","product":"ai-change-verification","limitations":'+nesting+"}",encoding="utf-8")
            result=subprocess.run([sys.executable,str(VALIDATOR),str(deep)],stdout=subprocess.PIPE,text=True)
        self.assertIn("RECEIPT_TOO_DEEP",result.stdout)

    def test_ansi_osc_bidi_controls_neutralized(self):
        raw="ok\x1b[2J\x1b]8;;url\x07x\u202e".encode("utf-8")
        result=subprocess.run([sys.executable,str(SANITIZER)],input=raw,stdout=subprocess.PIPE,check=True)
        output=json.loads(result.stdout.decode("ascii")); self.assertTrue(output["control_sequences_neutralized"]); self.assertNotIn("\x1b",output["text"])

    def test_sanitizer_non_utf8_stdout_portability(self):
        env=os.environ.copy(); env["PYTHONIOENCODING"]="cp1252"
        result=subprocess.run([sys.executable,str(SANITIZER)],input="Unicode: Привет\n".encode("utf-8"),stdout=subprocess.PIPE,env=env,check=True)
        self.assertIn("Привет",json.loads(result.stdout.decode("ascii"))["text"])


    # Legacy V4 static-regression names retained as active v1.2 regressions.
    def test_subject_authored_test_not_automatically_downgraded(self):
        data = self.ready()
        self.assertEqual(self.result(data), (True, "PASS"))
        forged = self.ready()
        forged["evidence"][0]["capture_origin"] = "LLM_INTERPRETATION"
        self.assert_reason(forged, "MECHANICAL_EVIDENCE_ORIGIN_INVALID")

    def test_non_comparable_baseline_cannot_claim_new(self):
        data = self.ready()
        data["evidence"][0]["baseline_attribution"] = "NEW"
        data["evidence"][0]["baseline_comparability"] = "UNKNOWN"
        self.assert_reason(data, "BASELINE_NOT_COMPARABLE")

    def test_legitimate_test_removal_can_remain_ready_with_adequate_independent_coverage(self):
        data = self.ready()
        data["verification_surface"] = [{
            "id": "VS-1", "kind": "TEST", "change": "WEAKENED",
            "consequence": "ADEQUATE_INDEPENDENT_COVERAGE_REMAINS",
            "evidence_ids": ["E-1"], "summary": "redundant test removed"
        }]
        data["attention"] = [{
            "priority": "SHOULD_INSPECT", "reason_code": "VERIFICATION_SURFACE_CHANGED",
            "target": "tests", "evidence_ids": ["E-1"], "summary": "inspect"
        }]
        self.assertEqual(self.result(data), (True, "PASS"))

    def test_compound_and_conflicting_obligations(self):
        data = self.ready()
        data["intent_conflicts"] = [{
            "id": "IC-1", "material": True, "state": "UNRESOLVED",
            "sources": ["USER", "REPO_CONTRACT"], "summary": "conflict"
        }]
        self.assert_reason(data, "UNRESOLVED_INTENT_CONFLICT")
        data = self.ready()
        data["obligations"][0]["adjudicability"] = "COMPOUND"
        self.assert_reason(data, "OBLIGATION_NOT_INDEPENDENT")

    def test_receipt_artifact_contamination(self):
        data = self.ready()
        data["receipt_persistence"] = {
            "mode": "REPO_OPERATIONAL_EXCLUDED",
            "path": ".acv/receipt.json",
            "subject_contamination_check": "UNKNOWN"
        }
        self.assert_reason(data, "RECEIPT_CONTAMINATION_UNPROVEN")


class ReceiptSemanticClosureTests(BaseReceiptTests):
    def test_version_identity_mutants_are_rejected_for_intended_reason(self):
        for old in ("1.0","1.1"):
            data=self.ready(); data["schema_version"]=old; data["future_field"]=True
            self.assert_reason(data,"SCHEMA_VERSION_NOT_ACCEPTABLE_FOR_CURRENT_DECISION")
        data=self.ready(); data["schema_version"]="1.3"; data["future_field"]=True; self.assert_reason(data,"UNSUPPORTED_SCHEMA_VERSION")
        data=self.ready(); data["product"]="other-product"; self.assert_reason(data,"PRODUCT_INVALID")

    def test_subject_digest_and_current_subject_binding(self):
        data=self.ready(); data["subject"]["subject_digest"]="not-a-digest"; self.assert_reason(data,"SHA256_INVALID:subject.subject_digest")
        data=self.ready(); data["evidence"][0]["observed_subject_digest"]="b"*64; self.assert_reason(data,"CURRENT_SUBJECT_BINDING_MISMATCH")
        data=self.ready(); data["evidence"][0]["observed_subject_digest"]="sha256:notreally"; self.assert_reason(data,"SHA256_INVALID:evidence.observed_subject_digest")

    def test_closed_freshness_and_comparability_vocabularies(self):
        data=self.ready(); data["subject"]["freshness"]="BANANA"; self.assert_reason(data,"ENUM_INVALID:subject.freshness")
        data=self.ready(); data["evidence"][0]["freshness"]="BANANA"; self.assert_reason(data,"ENUM_INVALID:evidence.freshness")
        data=self.ready(); data["evidence"][0]["baseline_comparability"]="MAYBE"; self.assert_reason(data,"ENUM_INVALID:evidence.baseline_comparability")

    def test_policy_state_removed_from_stage_a(self):
        data=self.ready(); data["verifier"]["policy_state"]="BANANA"; self.assert_reason(data,"UNKNOWN_FIELD")

    def test_command_outcome_is_derived_from_check_rule(self):
        data=self.ready(); data["evidence"][0]["execution"]["exit_code"]=1; self.assert_reason(data,"COMMAND_OUTCOME_MISMATCH")
        data=self.ready(); data["evidence"][0]["outcome"]="OBSERVED_FAIL"; self.assert_reason(data,"COMMAND_OUTCOME_MISMATCH")
        data=self.ready(); data["evidence"][0]["execution"]["exit_code"]=None; self.assert_reason(data,"COMMAND_OUTCOME_WITHOUT_RESULT")
        data=self.ready(); data["evidence"][0]["execution"]["exit_code"]=None; data["evidence"][0]["outcome"]="INCONCLUSIVE"; data["obligations"][0]["state"]="UNPROVEN"; data["obligations"][0]["evidence_ids"]=[]; data["readiness"]={"state":"NOT_READY_FOR_HUMAN_REVIEW","reason_codes":["INCONCLUSIVE"],"summary":"not ready"}; self.assertEqual(self.result(data),(True,"PASS"))

    def test_check_operation_contract_rejects_echo_substitution(self):
        data=self.ready(); data["evidence"][0]["execution"]["argv"]=["echo","nothing was tested"]
        self.assert_reason(data,"CHECK_OPERATION_MISMATCH")

    def test_check_contract_digest_binds_covers_and_result_rule(self):
        data=self.ready(); data["verification_plan"][0]["covers"]=[]
        self.assert_reason(data,"CHECK_CONTRACT_DIGEST_MISMATCH")
        data=self.ready(); data["verification_plan"][0]["result_interpretation"]["success_exit_codes"]=[0,1]
        self.assert_reason(data,"CHECK_CONTRACT_DIGEST_MISMATCH")

    def test_unselected_check_cannot_have_observed_decision_evidence(self):
        data=self.ready(); data["verification_plan"][0]["selected"]=False; self.rebind(data)
        self.assert_reason(data,"EVIDENCE_FOR_UNSELECTED_CHECK")

    def test_selected_check_must_cover_adjudicated_obligation(self):
        data=self.ready(); data["obligations"].append({"id":"O-2","text":"other","provenance":"USER","adjudicability":"INDEPENDENT","material":False,"state":"NOT_APPLICABLE","evidence_ids":[]})
        data["verification_plan"][0]["covers"]=["O-2"]; self.rebind(data)
        self.assert_reason(data,"SUPPORTED_WITHOUT_CURRENT_OBSERVED_PASS")

    def test_material_context_must_match_declared_check_dimensions(self):
        data=self.ready(); data["evidence"][0]["material_context"]=[]
        self.assert_reason(data,"MATERIAL_CONTEXT_BINDING_MISMATCH")

    def test_supported_and_contradicted_require_current_bound_matching_evidence(self):
        for outcome in ("NOT_RUN","INCONCLUSIVE"):
            data=self.ready(); data["evidence"][0]["execution"]["exit_code"]=None; data["evidence"][0]["outcome"]=outcome
            self.assert_reason(data,"SUPPORTED_WITHOUT_CURRENT_OBSERVED_PASS")
        data=self.ready(); data["obligations"][0]["state"]="CONTRADICTED"; self.set_command_fail(data); data["readiness"]={"state":"NOT_READY_FOR_HUMAN_REVIEW","reason_codes":["MATERIAL_OBLIGATION_CONTRADICTED"],"summary":"fix"}; self.assertEqual(self.result(data),(True,"PASS"))

    def test_baseline_new_requires_explicit_comparable_basis_and_is_derived(self):
        data=self.ready(); self.set_command_fail(data); data["readiness"]={"state":"NOT_READY_FOR_HUMAN_REVIEW","reason_codes":["FAIL"],"summary":"not ready"}; data["obligations"][0]["state"]="CONTRADICTED"
        data["evidence"][0].update({"baseline_attribution":"NEW","baseline_comparability":"COMPARABLE"})
        self.assert_reason(data,"BASELINE_BASIS_MISSING")
        data["baseline_subject"]={"subject_digest":"b"*64}
        base=json.loads(json.dumps(data["evidence"][0])); base["id"]="E-B"; base["subject_relationship"]="BASELINE_SUBJECT"; base["observed_subject_digest"]="b"*64; base["outcome"]="OBSERVED_PASS"; base["execution"]["exit_code"]=0; base["execution"]["invocation_id"]="inv-base"; base["baseline_attribution"]="NOT_APPLICABLE"; base["baseline_comparability"]="NOT_APPLICABLE"; base.pop("baseline_basis",None)
        data["evidence"].append(base); data["evidence"][0]["baseline_basis"]={"baseline_evidence_id":"E-B","comparison_rule":"BASELINE_PASS_CURRENT_FAIL"}
        self.assertEqual(self.result(data),(True,"PASS"))
        data["evidence"][0]["baseline_attribution"]="PRE_EXISTING"; self.assert_reason(data,"BASELINE_ATTRIBUTION_MISMATCH")

    def test_preexisting_requires_matching_failure_property_identity(self):
        data=self.ready(); self.set_command_fail(data); data["obligations"][0]["state"]="CONTRADICTED"; data["readiness"]={"state":"NOT_READY_FOR_HUMAN_REVIEW","reason_codes":["FAIL"],"summary":"not ready"}; data["baseline_subject"]={"subject_digest":"b"*64}
        current=data["evidence"][0]; current.update({"baseline_attribution":"PRE_EXISTING","baseline_comparability":"COMPARABLE","result_fingerprint":"f"*64,"baseline_basis":{"baseline_evidence_id":"E-B","comparison_rule":"MATCHING_FAILURE_FINGERPRINT"}})
        base=json.loads(json.dumps(current)); base["id"]="E-B"; base["subject_relationship"]="BASELINE_SUBJECT"; base["observed_subject_digest"]="b"*64; base["execution"]["invocation_id"]="inv-base"; base["baseline_attribution"]="NOT_APPLICABLE"; base["baseline_comparability"]="NOT_APPLICABLE"; base.pop("baseline_basis",None)
        data["evidence"].append(base); self.assertEqual(self.result(data),(True,"PASS"))
        data["evidence"][1]["result_fingerprint"]="e"*64; self.assert_reason(data,"BASELINE_PROPERTY_IDENTITY_MISMATCH")

    def test_repeated_consistent_requires_distinct_source_invocations(self):
        data=self.ready(); data["evidence"][0]["reliability"]="REPEATED_CONSISTENT"; self.assert_reason(data,"REPEATED_CONSISTENT_WITHOUT_DISTINCT_INVOCATIONS")
        e2=json.loads(json.dumps(data["evidence"][0])); e2["id"]="E-2"; data["evidence"].append(e2); data["obligations"][0]["evidence_ids"].append("E-2")
        self.assert_reason(data,"REPEATED_CONSISTENT_WITHOUT_DISTINCT_INVOCATIONS")
        data["evidence"][1]["execution"]["invocation_id"]="inv-2"; data["evidence"][1]["execution"]["started_at"]="2026-08-30T16:01:00Z"
        self.assertEqual(self.result(data),(True,"PASS"))

    def test_finding_truth_calibration_and_adjudication_basis(self):
        data=self.ready(); data["findings"]=[{"id":"F-1","disposition":"FINDING","origin":"LLM_INTERPRETATION","support":"EVIDENCE_LINKED","summary":"Remote code execution lets attackers take over production","evidence_ids":["E-1"]}]
        self.assertEqual(self.result(data),(True,"PASS"))  # linkage, not semantic entailment
        data["findings"][0]["support"]="EVIDENCE_ADJUDICATED"; self.assert_reason(data,"ADJUDICATED_FINDING_WITHOUT_BASIS")
        data["findings"][0]["adjudication"]={"kind":"SUPPORTED_SEMANTIC_ORACLE","verifier_id":"oracle:v1","source_evidence_ids":["E-1"]}
        self.assertEqual(self.result(data),(True,"PASS"))

    def test_ready_rules_and_weakened_surface_attention(self):
        data=self.ready(); data["subject"]["closure_status"]="SCOPE_AMBIGUOUS"; self.assert_reason(data,"READY_WITHOUT_SCOPE_CLOSURE")
        data=self.ready(); data["obligations"][0]["state"]="UNPROVEN"; data["obligations"][0]["evidence_ids"]=[]; self.assert_reason(data,"READY_WITH_UNPROVEN_MATERIAL_OBLIGATION")
        data=self.ready(); data["verification_surface"]=[{"id":"VS-1","kind":"TEST","change":"WEAKENED","consequence":"ADEQUATE_INDEPENDENT_COVERAGE_REMAINS","evidence_ids":["E-1"],"summary":"x"}]; self.assert_reason(data,"WEAKENING_WITHOUT_HUMAN_ATTENTION")
        data["attention"]=[{"priority":"SHOULD_INSPECT","reason_code":"VERIFICATION_SURFACE_CHANGED","target":"tests","evidence_ids":["E-1"],"summary":"inspect"}]; self.assertEqual(self.result(data),(True,"PASS"))

    def test_receipt_artifact_contamination(self):
        data=self.ready(); data["receipt_persistence"]={"mode":"REPO_OPERATIONAL_EXCLUDED","path":".acv/receipt.json","subject_contamination_check":"UNKNOWN"}; self.assert_reason(data,"RECEIPT_CONTAMINATION_UNPROVEN")


if __name__ == "__main__":
    unittest.main()
