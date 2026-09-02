import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PUBLIC_ROOT / "skills/ai-change-verification/scripts/validate_receipt.py"
SCHEMA_PATH = PUBLIC_ROOT / "skills/ai-change-verification/schemas/verification-receipt.schema.json"
READY = PUBLIC_ROOT / "tests/fixtures/receipt_minimal_ready.json"
BLOCKED = PUBLIC_ROOT / "tests/fixtures/receipt_minimal_blocked.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("acv_receipt_contract_validator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReceiptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def run_file(self, path):
        return subprocess.run([sys.executable, str(SCRIPT), str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def run_data(self, data):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "receipt.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return self.run_file(path)

    def ready(self):
        return json.loads(READY.read_text(encoding="utf-8"))

    def rebind_check(self, data, index=0):
        check = data["verification_plan"][index]
        check["check_contract_digest"] = self.validator.compute_check_contract_digest(check)
        for evidence in data["evidence"]:
            if evidence.get("check_id") == check["id"]:
                evidence["check_contract_digest"] = check["check_contract_digest"]

    def test_minimal_ready_and_blocked_receipts(self):
        self.assertEqual(self.run_file(READY).returncode, 0)
        self.assertEqual(self.run_file(BLOCKED).returncode, 0)

    def test_duplicate_key_has_reason_code(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "duplicate.json"
            path.write_bytes(b'{"schema_version":"1.2","schema_version":"2.0"}')
            result = self.run_file(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JSON_DUPLICATE_KEY", result.stdout)

    def test_version_dispatch_precedes_current_schema_unknown_fields(self):
        for historical in ("1.0", "1.1"):
            data = self.ready(); data["schema_version"] = historical; data["future_field"] = True
            self.assertIn("SCHEMA_VERSION_NOT_ACCEPTABLE_FOR_CURRENT_DECISION", self.run_data(data).stdout)
        data = self.ready(); data["schema_version"] = "1.3"; data["future_field"] = True
        self.assertIn("UNSUPPORTED_SCHEMA_VERSION", self.run_data(data).stdout)
        data = self.ready(); data["future_field"] = True
        self.assertIn("UNKNOWN_FIELD", self.run_data(data).stdout)

    def test_unknown_field_and_dangling_reference_are_rejected(self):
        data = self.ready(); data["readiness"]["unexpected"] = True
        self.assertIn("UNKNOWN_FIELD", self.run_data(data).stdout)
        data = self.ready(); data["obligations"][0]["evidence_ids"] = ["missing"]
        self.assertIn("DANGLING_EVIDENCE_REFERENCE", self.run_data(data).stdout)

    def test_external_result_does_not_require_fake_local_command(self):
        data = self.ready()
        check = data["verification_plan"][0]
        check["operation_contract"] = {"kind":"EXTERNAL_RESULT","provider":"ci.example","resource_kind":"check-run"}
        check["result_interpretation"] = {"kind":"CAPTURED_OUTCOME"}
        check["material_context_keys"] = []
        self.rebind_check(data)
        data["evidence"][0] = {
            "id":"E-1","kind":"EXTERNAL_RESULT","capture_origin":"TRUSTED_EXTERNAL",
            "subject_relationship":"CURRENT_SUBJECT","outcome":"OBSERVED_PASS","reliability":"UNKNOWN",
            "baseline_attribution":"NOT_APPLICABLE","baseline_comparability":"NOT_APPLICABLE","freshness":"CURRENT",
            "check_id":"C-1","check_contract_digest":check["check_contract_digest"],
            "observed_subject_digest":data["subject"]["subject_digest"],"material_context":[],
            "external":{"provider":"ci.example","resource_kind":"check-run","result_id":"run-123","observed_at":"2026-08-30T16:00:00Z"},
            "limitations":[]}
        self.assertEqual(self.run_data(data).returncode, 0)


    def test_check_contract_content_identity_is_canonical_for_set_like_fields(self):
        data = self.ready()
        check = data["verification_plan"][0]
        check["covers"] = ["O-2", "O-1"]
        check["material_context_keys"] = ["z", "a"]
        check["result_interpretation"] = {"kind":"EXIT_CODE","success_exit_codes":[1,0]}
        other = json.loads(json.dumps(check))
        other["covers"] = ["O-1", "O-2"]
        other["material_context_keys"] = ["a", "z"]
        other["result_interpretation"]["success_exit_codes"] = [0,1]
        self.assertEqual(self.validator.compute_check_contract_digest(check), self.validator.compute_check_contract_digest(other))

    def test_policy_state_is_not_part_of_stage_a_1_2_schema(self):
        data = self.ready(); data["verifier"]["policy_state"] = "BANANA"
        result = self.run_data(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UNKNOWN_FIELD", result.stdout)


class ReceiptSchemaContractTests(unittest.TestCase):
    def schema(self):
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_schema_identity_is_version_specific_and_local_only(self):
        schema = self.schema()
        self.assertEqual(schema["$id"], "urn:ai-change-verification:schema:verification-receipt:1.2")
        self.assertEqual(schema["properties"]["schema_version"], {"const":"1.2"})
        self.assertEqual(schema["properties"]["product"], {"const":"ai-change-verification"})
        refs = []
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "$ref": refs.append(child)
                    walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(schema)
        self.assertTrue(refs)
        self.assertTrue(all(ref.startswith("#/") for ref in refs), refs)

    def test_closed_vocabularies_are_machine_constrained(self):
        schema = self.schema()
        self.assertEqual(set(schema["properties"]["subject"]["properties"]["freshness"]["enum"]), {"CURRENT","STALE","UNKNOWN"})
        command = schema["$defs"]["evidence_command_execution"]["properties"]
        self.assertEqual(set(command["freshness"]["enum"]), {"CURRENT","STALE","UNKNOWN","NOT_APPLICABLE"})
        self.assertEqual(set(command["baseline_comparability"]["enum"]), {"COMPARABLE","NOT_COMPARABLE","UNKNOWN","NOT_APPLICABLE"})
        self.assertEqual(set(command["subject_relationship"]["enum"]), {"CURRENT_SUBJECT","BASELINE_SUBJECT","EXTERNAL_SUBJECT","UNKNOWN","NOT_APPLICABLE"})
        self.assertNotIn("policy_state", schema["properties"]["verifier"]["properties"])

    def test_typed_evidence_variants_are_closed(self):
        schema = self.schema()
        refs = [item["$ref"] for item in schema["$defs"]["evidence"]["oneOf"]]
        self.assertEqual(refs, ["#/$defs/evidence_command_execution","#/$defs/evidence_tool_observation","#/$defs/evidence_external_result","#/$defs/evidence_interpretation"])
        for name in ("evidence_command_execution","evidence_tool_observation","evidence_external_result","evidence_interpretation"):
            self.assertFalse(schema["$defs"][name]["additionalProperties"])
        self.assertEqual(schema["$defs"]["evidence_interpretation"]["properties"]["capture_origin"], {"const":"LLM_INTERPRETATION"})
        self.assertEqual(schema["$defs"]["evidence_interpretation"]["properties"]["freshness"], {"const":"NOT_APPLICABLE"})

    def test_check_contract_and_subject_identity_are_structurally_constrained(self):
        schema = self.schema()
        self.assertEqual(schema["properties"]["subject"]["properties"]["subject_digest"]["pattern"], "^[0-9a-f]{64}$")
        check = schema["$defs"]["check"]
        for field in ("operation_contract","result_interpretation","material_context_keys","check_contract_digest"):
            self.assertIn(field, check["required"])
        self.assertEqual(check["properties"]["check_contract_digest"]["pattern"], "^[0-9a-f]{64}$")
        self.assertIn("invocation_id", schema["$defs"]["execution"]["required"])

    def test_finding_origin_and_support_are_orthogonal(self):
        schema = self.schema()["$defs"]["finding"]["properties"]
        self.assertEqual(set(schema["disposition"]["enum"]), {"FINDING","REVIEWER_LEAD","UNRESOLVED_RISK","REJECTED_CANDIDATE"})
        self.assertEqual(set(schema["origin"]["enum"]), {"DETERMINISTIC_TOOL","LLM_INTERPRETATION","HUMAN_REVIEW","OTHER_SUPPORTED_SOURCE"})
        self.assertEqual(set(schema["support"]["enum"]), {"EVIDENCE_LINKED","EVIDENCE_ADJUDICATED","NOT_APPLICABLE"})


if __name__ == "__main__":
    unittest.main()
