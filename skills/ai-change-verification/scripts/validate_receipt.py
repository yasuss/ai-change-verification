"""Strict, pure-local validator for the ACV machine-readable review receipt.

Stage A validates schema + internal semantic coherence only. It does not establish
live currentness, trusted capture authority, or final review readiness against a
live repository. Those authority checks belong to the Stage B finalizer.
"""
import argparse
import json
import pathlib

MAX_BYTES = 1048576
MAX_DEPTH = 64
PRODUCT = "ai-change-verification"
CURRENT_SCHEMA_VERSION = "1.2"
HISTORICAL_SCHEMA_VERSIONS = {"1.0", "1.1"}
DECISION_ACCEPTABLE_SCHEMA_VERSIONS = {"1.2"}

TOP_FIELDS = {
    "schema_version", "product", "subject", "baseline_subject", "verifier",
    "obligations", "intent_conflicts", "verification_plan", "evidence",
    "verification_surface", "findings", "attention", "readiness",
    "receipt_persistence", "limitations",
}
REQUIRED_TOP_FIELDS = {
    "schema_version", "product", "subject", "verifier", "obligations",
    "intent_conflicts", "verification_plan", "evidence",
    "verification_surface", "findings", "attention", "readiness",
    "receipt_persistence", "limitations",
}

SHAPES = {
    "subject": {"scope_type", "subject_digest", "closure_status", "freshness", "limitations"},
    "baseline_subject": {"subject_digest"},
    "verifier": {"skill_revision", "host", "host_version", "limitations"},
    "obligation": {"id", "text", "provenance", "adjudicability", "material", "state", "evidence_ids"},
    "conflict": {"id", "material", "state", "sources", "summary"},
    "check": {"id", "source", "covers", "safety_class", "selected", "reason", "material_context_keys", "operation_contract", "result_interpretation", "check_contract_digest"},
    "op_command": {"kind", "argv", "cwd"},
    "op_tool": {"kind", "tool", "operation"},
    "op_external": {"kind", "provider", "resource_kind"},
    "result_exit": {"kind", "success_exit_codes"},
    "result_captured": {"kind"},
    "context_binding": {"name", "value"},
    "output_identity": {"sanitized_sha256", "redacted", "truncated"},
    "source_observation": {"provider", "observation_id", "sanitized_sha256"},
    "normalization": {"host_profile", "adapter_id", "adapter_version", "source_observation"},
    "execution": {"argv", "cwd", "started_at", "duration_ms", "exit_code", "invocation_id"},
    "tool_observation": {"tool", "version", "operation", "observation_id", "observed_at"},
    "external_result": {"provider", "resource_kind", "result_id", "observed_at", "canonical_uri"},
    "baseline_basis": {"baseline_evidence_id", "comparison_rule"},
    "evidence_command": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "baseline_basis", "freshness", "check_id", "check_contract_digest", "observed_subject_digest", "material_context", "result_fingerprint", "output", "normalization", "execution", "limitations"},
    "evidence_tool": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "baseline_basis", "freshness", "check_id", "check_contract_digest", "observed_subject_digest", "material_context", "result_fingerprint", "output", "normalization", "observation", "limitations"},
    "evidence_external": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "baseline_basis", "freshness", "check_id", "check_contract_digest", "observed_subject_digest", "material_context", "result_fingerprint", "output", "normalization", "external", "limitations"},
    "evidence_interpretation": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "freshness", "source_evidence_ids", "summary", "limitations"},
    "surface": {"id", "kind", "change", "consequence", "evidence_ids", "summary"},
    "adjudication_basis": {"kind", "verifier_id", "source_evidence_ids"},
    "finding": {"id", "disposition", "origin", "support", "summary", "evidence_ids", "adjudication"},
    "attention": {"priority", "reason_code", "target", "evidence_ids", "summary"},
    "readiness": {"state", "reason_codes", "summary"},
    "persistence": {"mode", "path", "subject_contamination_check"},
}

REQUIRED = {
    "subject": {"scope_type", "subject_digest", "closure_status", "freshness", "limitations"},
    "baseline_subject": {"subject_digest"},
    "verifier": {"skill_revision", "host", "host_version", "limitations"},
    "obligation": {"id", "text", "provenance", "adjudicability", "material", "state", "evidence_ids"},
    "conflict": {"id", "material", "state", "sources", "summary"},
    "check": {"id", "source", "covers", "safety_class", "selected", "reason", "material_context_keys", "operation_contract", "result_interpretation", "check_contract_digest"},
    "op_command": {"kind", "argv", "cwd"},
    "op_tool": {"kind", "tool", "operation"},
    "op_external": {"kind", "provider", "resource_kind"},
    "result_exit": {"kind", "success_exit_codes"},
    "result_captured": {"kind"},
    "context_binding": {"name", "value"},
    "output_identity": {"sanitized_sha256", "redacted", "truncated"},
    "source_observation": {"provider"},
    "normalization": {"host_profile", "adapter_id", "adapter_version", "source_observation"},
    "execution": {"argv", "cwd", "started_at", "duration_ms", "exit_code", "invocation_id"},
    "tool_observation": {"tool", "version", "operation", "observation_id", "observed_at"},
    "external_result": {"provider", "resource_kind", "result_id", "observed_at"},
    "baseline_basis": {"baseline_evidence_id", "comparison_rule"},
    "evidence_command": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "freshness", "check_id", "check_contract_digest", "observed_subject_digest", "material_context", "output", "execution", "limitations"},
    "evidence_tool": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "freshness", "check_id", "check_contract_digest", "observed_subject_digest", "material_context", "observation", "limitations"},
    "evidence_external": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "freshness", "check_id", "check_contract_digest", "observed_subject_digest", "material_context", "external", "limitations"},
    "evidence_interpretation": {"id", "kind", "capture_origin", "subject_relationship", "outcome", "reliability", "baseline_attribution", "baseline_comparability", "freshness", "source_evidence_ids", "summary", "limitations"},
    "surface": {"id", "kind", "change", "consequence", "evidence_ids", "summary"},
    "adjudication_basis": {"kind", "verifier_id", "source_evidence_ids"},
    "finding": {"id", "disposition", "origin", "support", "summary", "evidence_ids"},
    "attention": {"priority", "reason_code", "target", "evidence_ids", "summary"},
    "readiness": {"state", "reason_codes", "summary"},
    "persistence": {"mode", "path", "subject_contamination_check"},
}

ENUMS = {
    "subject.closure_status": {"CLOSED", "SCOPE_AMBIGUOUS"},
    "subject.freshness": {"CURRENT", "STALE", "UNKNOWN"},
    "obligation.state": {"SUPPORTED", "CONTRADICTED", "UNPROVEN", "NOT_APPLICABLE"},
    "conflict.state": {"RESOLVED", "UNRESOLVED"},
    "check.safety_class": {"READ_ONLY_EXPECTED", "REPO_LOCAL_MUTATION", "INSTALL_OR_NETWORK", "CREDENTIAL_OR_EXTERNAL_SERVICE", "DESTRUCTIVE_OR_IRREVERSIBLE"},
    "operation.kind": {"COMMAND_EXECUTION", "TOOL_OBSERVATION", "EXTERNAL_RESULT"},
    "result.kind": {"EXIT_CODE", "CAPTURED_OUTCOME"},
    "evidence.kind": {"COMMAND_EXECUTION", "TOOL_OBSERVATION", "EXTERNAL_RESULT", "INTERPRETATION"},
    "evidence.capture_origin": {"HELPER_CAPTURE", "HOST_TOOL_OBSERVATION", "TRUSTED_EXTERNAL", "LLM_INTERPRETATION"},
    "evidence.subject_relationship": {"CURRENT_SUBJECT", "BASELINE_SUBJECT", "EXTERNAL_SUBJECT", "UNKNOWN", "NOT_APPLICABLE"},
    "evidence.outcome": {"OBSERVED_PASS", "OBSERVED_FAIL", "NOT_RUN", "INCONCLUSIVE"},
    "evidence.reliability": {"UNKNOWN", "REPEATED_CONSISTENT", "KNOWN_FLAKY", "NOT_APPLICABLE"},
    "evidence.baseline_attribution": {"NEW", "PRE_EXISTING", "UNKNOWN", "NOT_APPLICABLE"},
    "evidence.baseline_comparability": {"COMPARABLE", "NOT_COMPARABLE", "UNKNOWN", "NOT_APPLICABLE"},
    "evidence.freshness": {"CURRENT", "STALE", "UNKNOWN", "NOT_APPLICABLE"},
    "baseline.comparison_rule": {"BASELINE_PASS_CURRENT_FAIL", "MATCHING_FAILURE_FINGERPRINT"},
    "surface.change": {"UNCHANGED", "STRENGTHENED", "WEAKENED"},
    "finding.disposition": {"FINDING", "REVIEWER_LEAD", "UNRESOLVED_RISK", "REJECTED_CANDIDATE"},
    "finding.origin": {"DETERMINISTIC_TOOL", "LLM_INTERPRETATION", "HUMAN_REVIEW", "OTHER_SUPPORTED_SOURCE"},
    "finding.support": {"EVIDENCE_LINKED", "EVIDENCE_ADJUDICATED", "NOT_APPLICABLE"},
    "adjudication.kind": {"STRUCTURED_TOOL_RESULT", "DETERMINISTIC_PARSER", "INDEPENDENT_ADJUDICATION", "SUPPORTED_SEMANTIC_ORACLE"},
    "attention.priority": {"MUST_INSPECT", "SHOULD_INSPECT", "CONTEXT_FYI"},
    "readiness.state": {"READY_FOR_HUMAN_REVIEW", "NOT_READY_FOR_HUMAN_REVIEW", "BLOCKED_ON_MISSING_EVIDENCE"},
    "persistence.mode": {"EXTERNAL_FILE", "INLINE", "REPO_OPERATIONAL_EXCLUDED", "UNKNOWN"},
}

MECHANICAL_ORIGINS = {"HELPER_CAPTURE", "HOST_TOOL_OBSERVATION", "TRUSTED_EXTERNAL"}
MECHANICAL_KINDS = {"COMMAND_EXECUTION", "TOOL_OBSERVATION", "EXTERNAL_RESULT"}
OBSERVED_OUTCOMES = {"OBSERVED_PASS", "OBSERVED_FAIL"}
HEX_LOWER = "0123456789abcdef"
CHECK_CONTRACT_DOMAIN = b"ACV-CHECK-CONTRACT-v1\0"


class ReceiptError(Exception):
    pass


def _reject_constant(_value):
    raise ReceiptError("JSON_NONFINITE")


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _check_depth(value, depth=0):
    if depth > MAX_DEPTH:
        raise ReceiptError("RECEIPT_TOO_DEEP")
    if isinstance(value, dict):
        for child in value.values():
            _check_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_depth(child, depth + 1)


def _string(value, path, nonempty=False):
    if not isinstance(value, str):
        raise ReceiptError(f"TYPE_INVALID:{path}")
    if nonempty and not value:
        raise ReceiptError(f"EMPTY_REQUIRED_STRING:{path}")


def _boolean(value, path):
    if type(value) is not bool:
        raise ReceiptError(f"TYPE_INVALID:{path}")


def _integer(value, path, minimum=None):
    if type(value) is not int:
        raise ReceiptError(f"TYPE_INVALID:{path}")
    if minimum is not None and value < minimum:
        raise ReceiptError(f"VALUE_INVALID:{path}")


def _int_or_none(value, path):
    if value is not None and type(value) is not int:
        raise ReceiptError(f"TYPE_INVALID:{path}")


def _array(value, path, min_items=0):
    if not isinstance(value, list):
        raise ReceiptError(f"TYPE_INVALID:{path}")
    if len(value) < min_items:
        raise ReceiptError(f"EMPTY_REQUIRED_ARRAY:{path}")


def _string_array(value, path, min_items=0, nonempty_items=False, unique=False):
    _array(value, path, min_items)
    for item in value:
        _string(item, path + "[]", nonempty_items)
    if unique and len(set(value)) != len(value):
        raise ReceiptError(f"DUPLICATE_ARRAY_VALUE:{path}")


def _int_array(value, path, min_items=0, unique=False):
    _array(value, path, min_items)
    for item in value:
        _integer(item, path + "[]")
    if unique and len(set(value)) != len(value):
        raise ReceiptError(f"DUPLICATE_ARRAY_VALUE:{path}")


def _enum(value, path):
    _string(value, path)
    if value not in ENUMS[path]:
        raise ReceiptError(f"ENUM_INVALID:{path}")


def _shape(value, name):
    if not isinstance(value, dict):
        raise ReceiptError(f"TYPE_INVALID:{name}")
    unknown = set(value) - SHAPES[name]
    if unknown:
        raise ReceiptError("UNKNOWN_FIELD")
    if not REQUIRED[name].issubset(value):
        raise ReceiptError("RECEIPT_REQUIRED_FIELD_MISSING")


def _sha256(value, path):
    _string(value, path, True)
    if len(value) != 64 or any(char not in HEX_LOWER for char in value):
        raise ReceiptError(f"SHA256_INVALID:{path}")


def _ids(records):
    seen = set()
    for record in records:
        record_id = record["id"]
        if record_id in seen:
            raise ReceiptError("DUPLICATE_ID")
        seen.add(record_id)
    return seen


def _references(records, evidence_ids):
    for record in records:
        for evidence_id in record["evidence_ids"]:
            if evidence_id not in evidence_ids:
                raise ReceiptError("DANGLING_EVIDENCE_REFERENCE")


def _validate_context(records):
    _array(records, "evidence.material_context")
    names = set()
    for item in records:
        _shape(item, "context_binding")
        _string(item["name"], "context_binding.name", True)
        _string(item["value"], "context_binding.value", True)
        if item["name"] in names:
            raise ReceiptError("DUPLICATE_CONTEXT_BINDING")
        names.add(item["name"])


def _validate_output(value):
    _shape(value, "output_identity")
    _sha256(value["sanitized_sha256"], "output.sanitized_sha256")
    _boolean(value["redacted"], "output.redacted")
    _boolean(value["truncated"], "output.truncated")


def _validate_normalization(value):
    _shape(value, "normalization")
    for key in ("host_profile", "adapter_id", "adapter_version"):
        _string(value[key], "normalization." + key, True)
    source = value["source_observation"]
    _shape(source, "source_observation")
    _string(source["provider"], "source_observation.provider", True)
    if "observation_id" in source:
        _string(source["observation_id"], "source_observation.observation_id", True)
    if "sanitized_sha256" in source:
        _sha256(source["sanitized_sha256"], "source_observation.sanitized_sha256")
    if "observation_id" not in source and "sanitized_sha256" not in source:
        raise ReceiptError("NORMALIZATION_SOURCE_UNBOUND")


def _dispatch(value):
    if not isinstance(value, dict):
        raise ReceiptError("RECEIPT_SHAPE_INVALID")
    if "product" not in value or "schema_version" not in value:
        raise ReceiptError("RECEIPT_REQUIRED_FIELD_MISSING")
    _string(value["product"], "product", True)
    _string(value["schema_version"], "schema_version", True)
    if value["product"] != PRODUCT:
        raise ReceiptError("PRODUCT_INVALID")
    version = value["schema_version"]
    if version in HISTORICAL_SCHEMA_VERSIONS:
        raise ReceiptError("SCHEMA_VERSION_NOT_ACCEPTABLE_FOR_CURRENT_DECISION")
    if version not in DECISION_ACCEPTABLE_SCHEMA_VERSIONS or version != CURRENT_SCHEMA_VERSION:
        raise ReceiptError("UNSUPPORTED_SCHEMA_VERSION")



def _sha256_hex(data):
    """Pure-Python SHA-256 used to keep the validator inside the approved import boundary."""
    k = (
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
    )
    h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]
    msg = bytearray(data)
    bit_length = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg.extend(bit_length.to_bytes(8, "big"))

    def rotr(value, bits):
        return ((value >> bits) | (value << (32 - bits))) & 0xffffffff

    for offset in range(0, len(msg), 64):
        chunk = msg[offset:offset + 64]
        w = [0] * 64
        for i in range(16):
            w[i] = int.from_bytes(chunk[i * 4:(i + 1) * 4], "big")
        for i in range(16, 64):
            s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
            s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
            w[i] = (w[i - 16] + s0 + w[i - 7] + s1) & 0xffffffff
        a,b,c,d,e,f,g,hh = h
        for i in range(64):
            s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
            ch = (e & f) ^ ((~e) & g)
            temp1 = (hh + s1 + ch + k[i] + w[i]) & 0xffffffff
            s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
            maj = (a & b) ^ (a & c) ^ (b & c)
            temp2 = (s0 + maj) & 0xffffffff
            hh,g,f,e,d,c,b,a = g,f,e,(d + temp1) & 0xffffffff,c,b,a,(temp1 + temp2) & 0xffffffff
        h = [(x + y) & 0xffffffff for x,y in zip(h,(a,b,c,d,e,f,g,hh))]
    return "".join(f"{value:08x}" for value in h)

def _canonical_json(value):
    """ACV canonical contract JSON v1.

    The contract subset uses only null/bool/int/string/list/object. Object keys are
    sorted lexicographically, no insignificant whitespace is emitted, and strings
    use UTF-8 JSON with non-ASCII characters unescaped. No floats are permitted in
    the contract subset. This is intentionally narrower than a general JSON
    canonicalization system.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            _canonical_json(key) + ":" + _canonical_json(value[key])
            for key in sorted(value)
        ) + "}"
    raise ReceiptError("CHECK_CONTRACT_CANONICALIZATION_INVALID")


def _check_contract_payload(check):
    result_interpretation = check["result_interpretation"]
    if result_interpretation.get("kind") == "EXIT_CODE":
        result_interpretation = {
            "kind": "EXIT_CODE",
            "success_exit_codes": sorted(result_interpretation["success_exit_codes"]),
        }
    return {
        "id": check["id"],
        "source": check["source"],
        "covers": sorted(check["covers"]),
        "safety_class": check["safety_class"],
        "selected": check["selected"],
        "material_context_keys": sorted(check["material_context_keys"]),
        "operation_contract": check["operation_contract"],
        "result_interpretation": result_interpretation,
    }


def compute_check_contract_digest(check):
    payload = _canonical_json(_check_contract_payload(check)).encode("utf-8")
    return _sha256_hex(CHECK_CONTRACT_DOMAIN + payload)


def _validate_operation_contract(value):
    if not isinstance(value, dict) or "kind" not in value:
        raise ReceiptError("RECEIPT_REQUIRED_FIELD_MISSING")
    _enum(value["kind"], "operation.kind")
    kind = value["kind"]
    shape = {
        "COMMAND_EXECUTION": "op_command",
        "TOOL_OBSERVATION": "op_tool",
        "EXTERNAL_RESULT": "op_external",
    }[kind]
    _shape(value, shape)
    if kind == "COMMAND_EXECUTION":
        _string_array(value["argv"], "operation.argv", min_items=1, nonempty_items=True)
        _string(value["cwd"], "operation.cwd", True)
    elif kind == "TOOL_OBSERVATION":
        _string(value["tool"], "operation.tool", True)
        _string(value["operation"], "operation.operation", True)
    else:
        _string(value["provider"], "operation.provider", True)
        _string(value["resource_kind"], "operation.resource_kind", True)


def _validate_result_interpretation(value):
    if not isinstance(value, dict) or "kind" not in value:
        raise ReceiptError("RECEIPT_REQUIRED_FIELD_MISSING")
    _enum(value["kind"], "result.kind")
    if value["kind"] == "EXIT_CODE":
        _shape(value, "result_exit")
        _int_array(value["success_exit_codes"], "result.success_exit_codes", min_items=1, unique=True)
    else:
        _shape(value, "result_captured")


def _validate_baseline_basis(value):
    _shape(value, "baseline_basis")
    _string(value["baseline_evidence_id"], "baseline_basis.baseline_evidence_id", True)
    _enum(value["comparison_rule"], "baseline.comparison_rule")


def _validate_evidence(record):
    _enum(record.get("kind"), "evidence.kind")
    kind = record["kind"]
    shape = {
        "COMMAND_EXECUTION": "evidence_command",
        "TOOL_OBSERVATION": "evidence_tool",
        "EXTERNAL_RESULT": "evidence_external",
        "INTERPRETATION": "evidence_interpretation",
    }[kind]
    _shape(record, shape)
    _string(record["id"], "evidence.id", True)
    _enum(record["capture_origin"], "evidence.capture_origin")
    _enum(record["subject_relationship"], "evidence.subject_relationship")
    _enum(record["outcome"], "evidence.outcome")
    if record["reliability"] == "STABLE":
        raise ReceiptError("FORBIDDEN_RELIABILITY")
    _enum(record["reliability"], "evidence.reliability")
    _enum(record["baseline_attribution"], "evidence.baseline_attribution")
    _enum(record["baseline_comparability"], "evidence.baseline_comparability")
    _enum(record["freshness"], "evidence.freshness")
    _string_array(record["limitations"], "evidence.limitations")

    if kind == "INTERPRETATION":
        if record["capture_origin"] != "LLM_INTERPRETATION":
            raise ReceiptError("INTERPRETATION_ORIGIN_INVALID")
        if record["outcome"] != "INCONCLUSIVE":
            raise ReceiptError("INTERPRETATION_OUTCOME_INVALID")
        if record["subject_relationship"] != "NOT_APPLICABLE":
            raise ReceiptError("INTERPRETATION_SUBJECT_RELATIONSHIP_INVALID")
        if record["reliability"] != "NOT_APPLICABLE" or record["baseline_attribution"] != "NOT_APPLICABLE" or record["baseline_comparability"] != "NOT_APPLICABLE" or record["freshness"] != "NOT_APPLICABLE":
            raise ReceiptError("INTERPRETATION_PROVENANCE_INVALID")
        _string_array(record["source_evidence_ids"], "evidence.source_evidence_ids", min_items=1, nonempty_items=True, unique=True)
        _string(record["summary"], "evidence.summary")
        return

    if record["capture_origin"] not in MECHANICAL_ORIGINS:
        raise ReceiptError("MECHANICAL_EVIDENCE_ORIGIN_INVALID")
    if kind in {"COMMAND_EXECUTION", "TOOL_OBSERVATION"} and record["capture_origin"] not in {"HELPER_CAPTURE", "HOST_TOOL_OBSERVATION"}:
        raise ReceiptError("LOCAL_OBSERVATION_ORIGIN_INVALID")
    if kind == "EXTERNAL_RESULT" and record["capture_origin"] not in {"TRUSTED_EXTERNAL", "HOST_TOOL_OBSERVATION"}:
        raise ReceiptError("EXTERNAL_RESULT_ORIGIN_INVALID")
    _string(record["check_id"], "evidence.check_id", True)
    _sha256(record["check_contract_digest"], "evidence.check_contract_digest")
    _sha256(record["observed_subject_digest"], "evidence.observed_subject_digest")
    _validate_context(record["material_context"])
    if "result_fingerprint" in record:
        _sha256(record["result_fingerprint"], "evidence.result_fingerprint")
    if "baseline_basis" in record:
        _validate_baseline_basis(record["baseline_basis"])
    if "output" in record:
        _validate_output(record["output"])
    if kind == "COMMAND_EXECUTION" and "output" not in record:
        raise ReceiptError("RECEIPT_REQUIRED_FIELD_MISSING")
    if "normalization" in record:
        _validate_normalization(record["normalization"])

    if kind == "COMMAND_EXECUTION":
        execution = record["execution"]
        _shape(execution, "execution")
        _string_array(execution["argv"], "execution.argv", min_items=1, nonempty_items=True)
        _string(execution["cwd"], "execution.cwd", True)
        _string(execution["started_at"], "execution.started_at", True)
        _integer(execution["duration_ms"], "execution.duration_ms", 0)
        _int_or_none(execution["exit_code"], "execution.exit_code")
        _string(execution["invocation_id"], "execution.invocation_id", True)
    elif kind == "TOOL_OBSERVATION":
        observation = record["observation"]
        _shape(observation, "tool_observation")
        _string(observation["tool"], "tool_observation.tool", True)
        _string(observation["version"], "tool_observation.version")
        _string(observation["operation"], "tool_observation.operation", True)
        _string(observation["observation_id"], "tool_observation.observation_id", True)
        _string(observation["observed_at"], "tool_observation.observed_at", True)
    else:
        external = record["external"]
        _shape(external, "external_result")
        _string(external["provider"], "external.provider", True)
        _string(external["resource_kind"], "external.resource_kind", True)
        _string(external["result_id"], "external.result_id", True)
        _string(external["observed_at"], "external.observed_at", True)
        if "canonical_uri" in external:
            _string(external["canonical_uri"], "external.canonical_uri", True)


def _operation_matches(left, right):
    if left["kind"] != right["kind"] or left.get("check_contract_digest") != right.get("check_contract_digest"):
        return False
    if left.get("observed_subject_digest") != right.get("observed_subject_digest") or left.get("material_context") != right.get("material_context"):
        return False
    if left["kind"] == "COMMAND_EXECUTION":
        return left["execution"]["argv"] == right["execution"]["argv"] and left["execution"]["cwd"] == right["execution"]["cwd"]
    if left["kind"] == "TOOL_OBSERVATION":
        keys = ("tool", "version", "operation")
        return all(left["observation"][key] == right["observation"][key] for key in keys)
    if left["kind"] == "EXTERNAL_RESULT":
        keys = ("provider", "resource_kind")
        return all(left["external"][key] == right["external"][key] for key in keys)
    return False


def _source_identity(record):
    if record["kind"] == "COMMAND_EXECUTION":
        return "COMMAND:" + record["execution"]["invocation_id"]
    if record["kind"] == "TOOL_OBSERVATION":
        return "TOOL:" + record["observation"]["observation_id"]
    if record["kind"] == "EXTERNAL_RESULT":
        return "EXTERNAL:" + record["external"]["provider"] + ":" + record["external"]["result_id"]
    return None


def _current_mechanical_observation(records):
    return any(
        record["kind"] in MECHANICAL_KINDS
        and record["capture_origin"] in MECHANICAL_ORIGINS
        and record["subject_relationship"] == "CURRENT_SUBJECT"
        and record["outcome"] in OBSERVED_OUTCOMES
        and record["freshness"] == "CURRENT"
        for record in records
    )


def _check_actual_operation(record, check):
    contract = check["operation_contract"]
    if record["kind"] != contract["kind"]:
        raise ReceiptError("CHECK_OPERATION_KIND_MISMATCH")
    if record["kind"] == "COMMAND_EXECUTION":
        if record["execution"]["argv"] != contract["argv"] or record["execution"]["cwd"] != contract["cwd"]:
            raise ReceiptError("CHECK_OPERATION_MISMATCH")
    elif record["kind"] == "TOOL_OBSERVATION":
        if record["observation"]["tool"] != contract["tool"] or record["observation"]["operation"] != contract["operation"]:
            raise ReceiptError("CHECK_OPERATION_MISMATCH")
    else:
        if record["external"]["provider"] != contract["provider"] or record["external"]["resource_kind"] != contract["resource_kind"]:
            raise ReceiptError("CHECK_OPERATION_MISMATCH")


def _check_outcome_coherence(record, check):
    rule = check["result_interpretation"]
    if record["kind"] == "COMMAND_EXECUTION":
        if rule["kind"] != "EXIT_CODE":
            raise ReceiptError("RESULT_INTERPRETATION_KIND_MISMATCH")
        exit_code = record["execution"]["exit_code"]
        if exit_code is None:
            if record["outcome"] not in {"NOT_RUN", "INCONCLUSIVE"}:
                raise ReceiptError("COMMAND_OUTCOME_WITHOUT_RESULT")
            return
        expected = "OBSERVED_PASS" if exit_code in rule["success_exit_codes"] else "OBSERVED_FAIL"
        if record["outcome"] != expected:
            raise ReceiptError("COMMAND_OUTCOME_MISMATCH")
    else:
        if rule["kind"] != "CAPTURED_OUTCOME":
            raise ReceiptError("RESULT_INTERPRETATION_KIND_MISMATCH")


def _qualifies_for_obligation(record, check, obligation_id, outcome):
    return (
        record["kind"] in MECHANICAL_KINDS
        and record["capture_origin"] in MECHANICAL_ORIGINS
        and record["subject_relationship"] == "CURRENT_SUBJECT"
        and record["outcome"] == outcome
        and record["freshness"] == "CURRENT"
        and check["selected"] is True
        and obligation_id in check["covers"]
    )


def validate(value):
    _dispatch(value)
    unknown = set(value) - TOP_FIELDS
    if unknown:
        raise ReceiptError("UNKNOWN_FIELD")
    if not REQUIRED_TOP_FIELDS.issubset(value):
        raise ReceiptError("RECEIPT_REQUIRED_FIELD_MISSING")

    _shape(value["subject"], "subject")
    if "baseline_subject" in value:
        _shape(value["baseline_subject"], "baseline_subject")
    _shape(value["verifier"], "verifier")
    for key in ("obligations", "intent_conflicts", "verification_plan", "evidence", "verification_surface", "findings", "attention", "limitations"):
        _array(value[key], key)
    _string_array(value["limitations"], "limitations")

    _string(value["subject"]["scope_type"], "subject.scope_type")
    _sha256(value["subject"]["subject_digest"], "subject.subject_digest")
    _enum(value["subject"]["closure_status"], "subject.closure_status")
    _enum(value["subject"]["freshness"], "subject.freshness")
    _string_array(value["subject"]["limitations"], "subject.limitations")
    if "baseline_subject" in value:
        _sha256(value["baseline_subject"]["subject_digest"], "baseline_subject.subject_digest")
        if value["baseline_subject"]["subject_digest"] == value["subject"]["subject_digest"]:
            raise ReceiptError("BASELINE_SUBJECT_EQUALS_CURRENT")

    for key in ("skill_revision", "host", "host_version"):
        _string(value["verifier"][key], "verifier." + key)
    _string_array(value["verifier"]["limitations"], "verifier.limitations")

    for record in value["obligations"]:
        _shape(record, "obligation")
        _string(record["id"], "obligation.id", True)
        _string(record["text"], "obligation.text")
        _string(record["provenance"], "obligation.provenance")
        _string(record["adjudicability"], "obligation.adjudicability", True)
        _boolean(record["material"], "obligation.material")
        _enum(record["state"], "obligation.state")
        _string_array(record["evidence_ids"], "obligation.evidence_ids", nonempty_items=True, unique=True)

    for record in value["intent_conflicts"]:
        _shape(record, "conflict")
        _string(record["id"], "conflict.id", True)
        _boolean(record["material"], "conflict.material")
        _enum(record["state"], "conflict.state")
        _string_array(record["sources"], "conflict.sources", min_items=2, nonempty_items=True, unique=True)
        _string(record["summary"], "conflict.summary")

    for record in value["verification_plan"]:
        _shape(record, "check")
        _string(record["id"], "check.id", True)
        _string(record["source"], "check.source")
        _string_array(record["covers"], "check.covers", nonempty_items=True, unique=True)
        _enum(record["safety_class"], "check.safety_class")
        _boolean(record["selected"], "check.selected")
        _string(record["reason"], "check.reason")
        _string_array(record["material_context_keys"], "check.material_context_keys", nonempty_items=True, unique=True)
        _validate_operation_contract(record["operation_contract"])
        _validate_result_interpretation(record["result_interpretation"])
        if record["operation_contract"]["kind"] == "COMMAND_EXECUTION" and record["result_interpretation"]["kind"] != "EXIT_CODE":
            raise ReceiptError("RESULT_INTERPRETATION_KIND_MISMATCH")
        if record["operation_contract"]["kind"] != "COMMAND_EXECUTION" and record["result_interpretation"]["kind"] != "CAPTURED_OUTCOME":
            raise ReceiptError("RESULT_INTERPRETATION_KIND_MISMATCH")
        _sha256(record["check_contract_digest"], "check.check_contract_digest")
        if record["check_contract_digest"] != compute_check_contract_digest(record):
            raise ReceiptError("CHECK_CONTRACT_DIGEST_MISMATCH")

    for record in value["evidence"]:
        _validate_evidence(record)

    for record in value["verification_surface"]:
        _shape(record, "surface")
        _string(record["id"], "surface.id", True)
        _string(record["kind"], "surface.kind")
        _enum(record["change"], "surface.change")
        _string(record["consequence"], "surface.consequence")
        _string_array(record["evidence_ids"], "surface.evidence_ids", nonempty_items=True, unique=True)
        _string(record["summary"], "surface.summary")

    for record in value["findings"]:
        _shape(record, "finding")
        _string(record["id"], "finding.id", True)
        _enum(record["disposition"], "finding.disposition")
        _enum(record["origin"], "finding.origin")
        _enum(record["support"], "finding.support")
        _string(record["summary"], "finding.summary")
        _string_array(record["evidence_ids"], "finding.evidence_ids", nonempty_items=True, unique=True)
        if record["support"] == "EVIDENCE_ADJUDICATED":
            if "adjudication" not in record:
                raise ReceiptError("ADJUDICATED_FINDING_WITHOUT_BASIS")
            basis = record["adjudication"]
            _shape(basis, "adjudication_basis")
            _enum(basis["kind"], "adjudication.kind")
            _string(basis["verifier_id"], "adjudication.verifier_id", True)
            _string_array(basis["source_evidence_ids"], "adjudication.source_evidence_ids", min_items=1, nonempty_items=True, unique=True)
        elif "adjudication" in record:
            raise ReceiptError("ADJUDICATION_BASIS_WITHOUT_ADJUDICATED_SUPPORT")
        if record["disposition"] == "FINDING" and record["support"] == "NOT_APPLICABLE":
            raise ReceiptError("FINDING_WITHOUT_EVIDENCE_SUPPORT")

    for record in value["attention"]:
        _shape(record, "attention")
        _enum(record["priority"], "attention.priority")
        _string(record["reason_code"], "attention.reason_code")
        _string(record["target"], "attention.target")
        _string_array(record["evidence_ids"], "attention.evidence_ids", nonempty_items=True, unique=True)
        _string(record["summary"], "attention.summary")

    _shape(value["readiness"], "readiness")
    if value["readiness"]["state"] not in ENUMS["readiness.state"]:
        _string(value["readiness"]["state"], "readiness.state")
        raise ReceiptError("READINESS_STATE_INVALID")
    _string_array(value["readiness"]["reason_codes"], "readiness.reason_codes", min_items=1, nonempty_items=True, unique=True)
    _string(value["readiness"]["summary"], "readiness.summary")

    _shape(value["receipt_persistence"], "persistence")
    _enum(value["receipt_persistence"]["mode"], "persistence.mode")
    _string(value["receipt_persistence"]["path"], "persistence.path")
    _string(value["receipt_persistence"]["subject_contamination_check"], "persistence.subject_contamination_check", True)

    evidence_ids = _ids(value["evidence"])
    evidence = {record["id"]: record for record in value["evidence"]}
    obligation_ids = _ids(value["obligations"])
    check_ids = _ids(value["verification_plan"])
    checks = {record["id"]: record for record in value["verification_plan"]}
    for records in (value["intent_conflicts"], value["verification_surface"], value["findings"]):
        _ids(records)
    for records in (value["obligations"], value["verification_surface"], value["findings"], value["attention"]):
        _references(records, evidence_ids)

    for finding in value["findings"]:
        if finding["support"] == "EVIDENCE_ADJUDICATED":
            for evidence_id in finding["adjudication"]["source_evidence_ids"]:
                if evidence_id not in evidence_ids:
                    raise ReceiptError("DANGLING_EVIDENCE_REFERENCE")

    for record in value["evidence"]:
        if record["kind"] == "INTERPRETATION":
            for evidence_id in record["source_evidence_ids"]:
                if evidence_id not in evidence_ids or evidence_id == record["id"]:
                    raise ReceiptError("DANGLING_INTERPRETATION_REFERENCE")
            continue
        if record["check_id"] not in check_ids:
            raise ReceiptError("DANGLING_CHECK_REFERENCE")
        check = checks[record["check_id"]]
        if record["check_contract_digest"] != check["check_contract_digest"]:
            raise ReceiptError("EVIDENCE_CHECK_CONTRACT_MISMATCH")
        _check_actual_operation(record, check)
        _check_outcome_coherence(record, check)
        context_names = sorted(item["name"] for item in record["material_context"])
        if context_names != sorted(check["material_context_keys"]):
            raise ReceiptError("MATERIAL_CONTEXT_BINDING_MISMATCH")
        if record["outcome"] in OBSERVED_OUTCOMES and check["selected"] is not True:
            raise ReceiptError("EVIDENCE_FOR_UNSELECTED_CHECK")
        if record["subject_relationship"] == "CURRENT_SUBJECT" and record["observed_subject_digest"] != value["subject"]["subject_digest"]:
            raise ReceiptError("CURRENT_SUBJECT_BINDING_MISMATCH")
        if record["subject_relationship"] == "BASELINE_SUBJECT":
            if "baseline_subject" not in value:
                raise ReceiptError("BASELINE_SUBJECT_MISSING")
            if record["observed_subject_digest"] != value["baseline_subject"]["subject_digest"]:
                raise ReceiptError("BASELINE_SUBJECT_BINDING_MISMATCH")

    for check in value["verification_plan"]:
        for obligation_id in check["covers"]:
            if obligation_id not in obligation_ids:
                raise ReceiptError("DANGLING_OBLIGATION_REFERENCE")

    for record in value["evidence"]:
        if record["kind"] == "INTERPRETATION":
            continue
        attribution = record["baseline_attribution"]
        if attribution in {"NEW", "PRE_EXISTING"}:
            if record["baseline_comparability"] != "COMPARABLE":
                raise ReceiptError("BASELINE_NOT_COMPARABLE")
            if "baseline_subject" not in value or "baseline_basis" not in record:
                raise ReceiptError("BASELINE_BASIS_MISSING")
            basis = record["baseline_basis"]
            baseline_id = basis["baseline_evidence_id"]
            if baseline_id not in evidence or baseline_id == record["id"]:
                raise ReceiptError("BASELINE_EVIDENCE_INVALID")
            baseline = evidence[baseline_id]
            if baseline["kind"] not in MECHANICAL_KINDS or baseline["subject_relationship"] != "BASELINE_SUBJECT":
                raise ReceiptError("BASELINE_EVIDENCE_INVALID")
            if record["subject_relationship"] != "CURRENT_SUBJECT" or record["freshness"] != "CURRENT" or baseline["freshness"] != "CURRENT":
                raise ReceiptError("BASELINE_EVIDENCE_INVALID")
            if baseline["check_contract_digest"] != record["check_contract_digest"] or baseline["material_context"] != record["material_context"]:
                raise ReceiptError("BASELINE_NOT_COMPARABLE")
            rule = basis["comparison_rule"]
            if attribution == "NEW":
                if rule != "BASELINE_PASS_CURRENT_FAIL" or baseline["outcome"] != "OBSERVED_PASS" or record["outcome"] != "OBSERVED_FAIL":
                    raise ReceiptError("BASELINE_ATTRIBUTION_MISMATCH")
            else:
                if rule != "MATCHING_FAILURE_FINGERPRINT" or baseline["outcome"] != "OBSERVED_FAIL" or record["outcome"] != "OBSERVED_FAIL":
                    raise ReceiptError("BASELINE_ATTRIBUTION_MISMATCH")
                if "result_fingerprint" not in baseline or "result_fingerprint" not in record or baseline["result_fingerprint"] != record["result_fingerprint"]:
                    raise ReceiptError("BASELINE_PROPERTY_IDENTITY_MISMATCH")
        elif "baseline_basis" in record:
            raise ReceiptError("BASELINE_BASIS_WITHOUT_DERIVED_ATTRIBUTION")

        if record["reliability"] == "REPEATED_CONSISTENT":
            if record["subject_relationship"] != "CURRENT_SUBJECT" or record["freshness"] != "CURRENT" or record["outcome"] not in OBSERVED_OUTCOMES:
                raise ReceiptError("REPEATED_CONSISTENT_WITHOUT_REPETITION")
            peers = [
                peer for peer in value["evidence"]
                if peer["kind"] in MECHANICAL_KINDS
                and peer["subject_relationship"] == "CURRENT_SUBJECT"
                and peer["outcome"] == record["outcome"]
                and peer["freshness"] == "CURRENT"
                and _operation_matches(peer, record)
            ]
            identities = {_source_identity(peer) for peer in peers}
            if None in identities:
                identities.remove(None)
            if len(identities) < 2:
                raise ReceiptError("REPEATED_CONSISTENT_WITHOUT_DISTINCT_INVOCATIONS")

    for obligation in value["obligations"]:
        if obligation["material"] and obligation["adjudicability"] != "INDEPENDENT":
            raise ReceiptError("OBLIGATION_NOT_INDEPENDENT")
        if obligation["state"] in {"SUPPORTED", "CONTRADICTED"}:
            ids = obligation["evidence_ids"]
            if not ids:
                raise ReceiptError("SUPPORTED_WITHOUT_EVIDENCE" if obligation["state"] == "SUPPORTED" else "CONTRADICTED_WITHOUT_EVIDENCE")
            records = [evidence[evidence_id] for evidence_id in ids]
            if obligation["state"] == "SUPPORTED" and all(record["kind"] == "INTERPRETATION" for record in records):
                raise ReceiptError("SUPPORTED_BY_INFERENCE_ONLY")
            target_outcome = "OBSERVED_PASS" if obligation["state"] == "SUPPORTED" else "OBSERVED_FAIL"
            if obligation["state"] == "SUPPORTED" and all(record["freshness"] == "STALE" for record in records):
                raise ReceiptError("STALE_EVIDENCE_SUPPORT")
            qualifying = []
            for record in records:
                if record["kind"] in MECHANICAL_KINDS:
                    check = checks[record["check_id"]]
                    if _qualifies_for_obligation(record, check, obligation["id"], target_outcome):
                        qualifying.append(record)
            if not qualifying:
                if obligation["state"] == "SUPPORTED":
                    raise ReceiptError("SUPPORTED_WITHOUT_CURRENT_OBSERVED_PASS")
                raise ReceiptError("CONTRADICTED_WITHOUT_CURRENT_OBSERVED_FAIL")

    for finding in value["findings"]:
        records = [evidence[evidence_id] for evidence_id in finding["evidence_ids"]]
        if finding["support"] in {"EVIDENCE_LINKED", "EVIDENCE_ADJUDICATED"} and not records:
            raise ReceiptError("FINDING_WITHOUT_EVIDENCE_SUPPORT")
        if finding["support"] == "EVIDENCE_ADJUDICATED":
            basis_records = [evidence[evidence_id] for evidence_id in finding["adjudication"]["source_evidence_ids"]]
            if not _current_mechanical_observation(basis_records):
                raise ReceiptError("ADJUDICATED_FINDING_WITHOUT_CURRENT_OBSERVATION")

    readiness = value["readiness"]["state"]
    if readiness == "READY_FOR_HUMAN_REVIEW":
        if value["subject"]["closure_status"] != "CLOSED":
            raise ReceiptError("READY_WITHOUT_SCOPE_CLOSURE")
        if value["subject"]["freshness"] != "CURRENT":
            raise ReceiptError("READY_WITH_STALE_SUBJECT")
        if any(obligation["material"] and obligation["state"] == "UNPROVEN" for obligation in value["obligations"]):
            raise ReceiptError("READY_WITH_UNPROVEN_MATERIAL_OBLIGATION")
        if any(obligation["material"] and obligation["state"] == "CONTRADICTED" for obligation in value["obligations"]):
            raise ReceiptError("READY_WITH_CONTRADICTED_MATERIAL_OBLIGATION")
        if any(conflict["material"] and conflict["state"] == "UNRESOLVED" for conflict in value["intent_conflicts"]):
            raise ReceiptError("UNRESOLVED_INTENT_CONFLICT")
        if any(finding["disposition"] == "UNRESOLVED_RISK" for finding in value["findings"]):
            raise ReceiptError("BLOCKING_UNRESOLVED_RISK")

    for surface in value["verification_surface"]:
        if surface["change"] == "WEAKENED" and readiness == "READY_FOR_HUMAN_REVIEW":
            records = [evidence[evidence_id] for evidence_id in surface["evidence_ids"]]
            if surface["consequence"] != "ADEQUATE_INDEPENDENT_COVERAGE_REMAINS" or not records or not _current_mechanical_observation(records):
                raise ReceiptError("WEAKENING_WITHOUT_COVERAGE")
            linked = any(attention["reason_code"] == "VERIFICATION_SURFACE_CHANGED" and set(attention["evidence_ids"]) & set(surface["evidence_ids"]) for attention in value["attention"])
            if not linked:
                raise ReceiptError("WEAKENING_WITHOUT_HUMAN_ATTENTION")

    persistence = value["receipt_persistence"]
    if persistence["mode"] == "REPO_OPERATIONAL_EXCLUDED" and persistence["subject_contamination_check"] != "PASS":
        raise ReceiptError("RECEIPT_CONTAMINATION_UNPROVEN")
    return value


def main():
    parser = argparse.ArgumentParser(description="validate one ACV receipt file")
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        raw = pathlib.Path(args.path).read_bytes()
        if len(raw) > MAX_BYTES:
            raise ReceiptError("RECEIPT_TOO_LARGE")
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_pairs_no_duplicates, parse_constant=_reject_constant)
        _check_depth(value)
        validate(value)
    except UnicodeDecodeError:
        print("RECEIPT_VALIDATION = FAIL")
        print("- JSON_INVALID_UTF8")
        raise SystemExit(1)
    except ReceiptError as error:
        print("RECEIPT_VALIDATION = FAIL")
        print("- " + str(error))
        raise SystemExit(1)
    except (OSError, ValueError, TypeError, KeyError):
        print("RECEIPT_VALIDATION = FAIL")
        print("- JSON_INVALID")
        raise SystemExit(1)
    print("RECEIPT_VALIDATION = PASS")


if __name__ == "__main__":
    main()
