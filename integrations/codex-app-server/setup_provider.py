"""Operator-side Codex App Server enrollment helper."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import queue
import time
from typing import Any, Mapping

APP_SERVER_TIMEOUT = 60
MAX_PROBE_OUTPUT = 4096
WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0 = "WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0"
SCHEMA_DIGEST_EXCLUDED_PATHS = {"json/codex_app_server_protocol.v2.schemas.json"}
SCHEMA_DIGEST_ALGORITHM = "SHA256(UTF-8 concatenation of sorted relative_path + NUL + per-file-sha256 + LF; excluding exactly json/codex_app_server_protocol.v2.schemas.json)"

class SetupError(RuntimeError):
    """Fail-closed setup error."""

def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def config_requirements_digest(result: Mapping[str, Any]) -> str:
    if not isinstance(result, Mapping):
        raise SetupError("CONFIG_REQUIREMENTS_RESULT_INVALID")
    return hashlib.sha256(_canonical_json(result)).hexdigest()

def validate_config_requirements(result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping) or result.get("requirements") not in (None, {}):
        raise SetupError("CONFIG_REQUIREMENTS_UNSUPPORTED")

def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def resolve_codex_path(operator_path: str | None, subject_root: pathlib.Path) -> pathlib.Path:
    candidate = pathlib.Path(operator_path) if operator_path else pathlib.Path(shutil.which("codex") or "")
    if not candidate:
        raise SetupError("CODEX_EXECUTABLE_NOT_FOUND")
    resolved = candidate.resolve()
    if _inside(resolved, subject_root):
        raise SetupError("CODEX_EXECUTABLE_INSIDE_SUBJECT")
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SetupError("CODEX_EXECUTABLE_NOT_EXECUTABLE")
    return resolved

def platform_profile() -> tuple[str, str]:
    system = platform.system().lower()
    if system == "darwin": return "darwin", "macos"
    if system == "windows": return "windows", "windows"
    if system == "linux": return "linux", "linux"
    raise SetupError("UNSUPPORTED_PLATFORM")

def run_fixed(executable: pathlib.Path, args: list[str], *, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run fixed enrollment probes; no subject-provided command is accepted."""
    return subprocess.run([str(executable), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=APP_SERVER_TIMEOUT, check=False)


def _atomic_write_bytes(path: pathlib.Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".acv-write.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def _json_line(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def _app_server_request(executable: pathlib.Path, request: Mapping[str, Any], *, cwd: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one isolated fixed JSON-RPC request against the local App Server."""
    init = {"method": "initialize", "id": 1, "params": {"clientInfo": {"name": "acv-codex-enrollment", "version": "1"}, "capabilities": {"experimentalApi": False, "requestAttestation": False}}}
    process = subprocess.Popen([str(executable), "app-server", "--stdio"], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    messages: list[dict[str, Any]] = []
    lines: queue.Queue[bytes | None] = queue.Queue()
    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=pump, daemon=True).start()
    def send(value: Mapping[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(_json_line(value)); process.stdin.flush()
    def wait_for(identifier: Any) -> dict[str, Any]:
        deadline = time.monotonic() + APP_SERVER_TIMEOUT
        while time.monotonic() < deadline:
            try: line = lines.get(timeout=0.2)
            except queue.Empty: continue
            if line is None: break
            try: value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError): continue
            if isinstance(value, dict):
                messages.append(value)
                if value.get("id") == identifier: return value
        raise SetupError("APP_SERVER_REQUEST_FAILED:%s" % request.get("method"))
    try:
        if request.get("method") == "initialize":
            send(request)
            response = wait_for(request.get("id"))
        else:
            send(init); init_response = wait_for(init.get("id"))
            if "error" in init_response: raise SetupError("APP_SERVER_REQUEST_FAILED:initialize")
            send({"method": "initialized"}); send(request); response = wait_for(request.get("id"))
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("APP_SERVER_TRANSPORT_FAILED") from exc
    finally:
        try:
            if process.stdin is not None: process.stdin.close()
        except OSError: pass
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    if "error" in response: raise SetupError("APP_SERVER_REQUEST_FAILED:%s" % request.get("method"))
    return response, [item for item in messages if item.get("method")]


def _response_result(response: Mapping[str, Any], method: str) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict): raise SetupError("APP_SERVER_RESULT_INVALID:%s" % method)
    return result


def _exit_code(result: Mapping[str, Any]) -> int | None:
    for key in ("exitCode", "exit_code"):
        if isinstance(result.get(key), int): return int(result[key])
    return None


def _run_command_probe(executable: pathlib.Path, python_executable: pathlib.Path, cwd: pathlib.Path, code: str, *, cap: int = MAX_PROBE_OUTPUT, process_id: str) -> dict[str, Any]:
    response, notifications = _app_server_request(executable, {"method": "command/exec", "id": 2, "params": {"command": [str(python_executable), "-c", code], "cwd": str(cwd), "processId": process_id, "streamStdoutStderr": True, "outputBytesCap": cap, "sandboxPolicy": {"type": "readOnly", "networkAccess": False}}}, cwd=cwd)
    result = _response_result(response, "command/exec")
    deltas = [item for item in notifications if item.get("method") == "command/exec/outputDelta"]
    text_parts = []
    for item in deltas:
        params = item.get("params", {})
        encoded = params.get("deltaBase64")
        if isinstance(encoded, str):
            try: text_parts.append(base64.b64decode(encoded).decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError): pass
    return {"response": result, "output_deltas": deltas, "stdout": "".join(text_parts), "cap_reached": any(item.get("params", {}).get("capReached") is True for item in deltas)}


def _cap_reached(result: Mapping[str, Any]) -> bool:
    return any(isinstance(result.get(key), bool) and result.get(key) for key in ("capReached", "cap_reached"))


def _probe_app_server(executable: pathlib.Path, python_executable: pathlib.Path, probe_cwd: pathlib.Path) -> dict[str, Any]:
    initialize, _ = _app_server_request(executable, {"method": "initialize", "id": 2, "params": {"clientInfo": {"name": "acv-codex-enrollment", "version": "1"}, "capabilities": {"experimentalApi": False, "requestAttestation": False}}}, cwd=probe_cwd)
    init_result = _response_result(initialize, "initialize")
    family, os_name = platform_profile()
    if init_result.get("platformFamily") != family or init_result.get("platformOs") != os_name: raise SetupError("PLATFORM_FACT_MISMATCH")
    config, _ = _app_server_request(executable, {"method": "configRequirements/read", "id": 2, "params": None}, cwd=probe_cwd)
    config_result = _response_result(config, "configRequirements/read")
    validate_config_requirements(config_result)
    config_digest = config_requirements_digest(config_result)
    passed = _run_command_probe(executable, python_executable, probe_cwd, "print('ACV_ENROLLMENT_PASS')", process_id="acv-pass")
    if _exit_code(passed["response"]) != 0: raise SetupError("COMMAND_EXEC_PASS_FAILED")
    exit7 = _run_command_probe(executable, python_executable, probe_cwd, "import sys; sys.exit(7)", process_id="acv-exit7")
    if _exit_code(exit7["response"]) != 7: raise SetupError("COMMAND_EXEC_EXIT7_FAILED")
    cwd_probe = _run_command_probe(executable, python_executable, probe_cwd, "import os; print(os.getcwd())", process_id="acv-cwd")
    observed_cwd = cwd_probe["stdout"].strip()
    if _exit_code(cwd_probe["response"]) != 0 or pathlib.Path(observed_cwd).resolve() != probe_cwd.resolve(): raise SetupError("CWD_PROBE_FAILED")
    cap = _run_command_probe(executable, python_executable, probe_cwd, "print('X' * 8192)", cap=64, process_id="acv-cap")
    if not cap["cap_reached"]: raise SetupError("CAP_REACHED_PROBE_FAILED")
    return {"initialize": {"platformFamily": family, "platformOs": os_name, "userAgent": init_result.get("userAgent")}, "config_requirements": config_result, "config_requirements_sha256": config_digest, "command_exec_pass": {"exit_code": 0, "output_delta_count": len(passed["output_deltas"])}, "command_exec_exit7": {"exit_code": 7}, "cwd": {"requested": str(probe_cwd), "observed": observed_cwd}, "output_delta": {"observed": bool(passed["output_deltas"])}, "capReached": {"observed": True}, "terminal_exit_code": {"exit_code": 7}}


def validate_enrollment_evidence(profile: Mapping[str, Any], preflight: Mapping[str, Any]) -> None:
    """Reject profiles whose claimed capabilities are not backed by probes."""
    for key in ("preflight_md_sha256", "preflight_json_sha256", "config_requirements_sha256", "provider_source_sha256"):
        value = profile.get(key)
        if not isinstance(value, str) or len(value) != 64 or not all(char in "0123456789abcdef" for char in value.lower()):
            raise SetupError("PREFLIGHT_IDENTITY_INVALID:" + key)
    expected_exclusions = sorted(SCHEMA_DIGEST_EXCLUDED_PATHS)
    if type(preflight.get("schema_digest_excluded_paths")) is not list or preflight.get("schema_digest_excluded_paths") != expected_exclusions:
        raise SetupError("SCHEMA_DIGEST_EXCLUSIONS_INVALID")
    if type(profile.get("schema_digest_excluded_paths")) is not list or profile.get("schema_digest_excluded_paths") != expected_exclusions:
        raise SetupError("SCHEMA_DIGEST_EXCLUSIONS_INVALID")
    probes = preflight.get("probes")
    caps = profile.get("tested_capabilities")
    if not isinstance(probes, Mapping) or not isinstance(caps, Mapping):
        raise SetupError("EXECUTED_PROBES_MISSING")
    required = ("command_exec", "output_delta", "capReached", "cwd", "terminal_exit_code", "config_requirements")
    if any(caps.get(key) is not True for key in required):
        raise SetupError("UNEXECUTED_CAPABILITY")
    if probes.get("command_exec_pass", {}).get("exit_code") != 0 or probes.get("command_exec_exit7", {}).get("exit_code") != 7:
        raise SetupError("EXIT_CODE_PROBES_INVALID")
    if probes.get("output_delta", {}).get("observed") is not True or probes.get("capReached", {}).get("observed") is not True:
        raise SetupError("OUTPUT_CAP_PROBES_INVALID")
    if probes.get("cwd", {}).get("observed") != probes.get("cwd", {}).get("requested"):
        raise SetupError("CWD_PROBE_INVALID")

def _schema_digest(schema_root: pathlib.Path) -> str:
    records = []
    for kind in ("json", "ts"):
        root = schema_root / kind
        if not root.is_dir(): raise SetupError("SCHEMA_SET_MISSING")
    for excluded in sorted(SCHEMA_DIGEST_EXCLUDED_PATHS):
        excluded_path = schema_root / pathlib.PurePosixPath(excluded)
        if not excluded_path.is_file(): raise SetupError("SCHEMA_DIGEST_EXCLUDED_PATH_MISSING:" + excluded)
    for kind in ("json", "ts"):
        root = schema_root / kind
        for item in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(schema_root).as_posix()):
            relative = item.relative_to(schema_root).as_posix()
            if relative in SCHEMA_DIGEST_EXCLUDED_PATHS:
                continue
            records.append(relative + "\0" + sha256_file(item) + "\n")
    records.sort()
    if not records: raise SetupError("SCHEMA_SET_EMPTY")
    return hashlib.sha256("".join(records).encode("utf-8")).hexdigest()

def _atomic_write(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".provider_profile.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def enroll(*, codex_path: str | None, provider_dir: pathlib.Path, subject_root: pathlib.Path, schema_root: pathlib.Path | None = None, replace: bool = False) -> dict[str, Any]:
    provider_dir = provider_dir.resolve(); subject_root = subject_root.resolve()
    if _inside(provider_dir, subject_root): raise SetupError("PROVIDER_INSTALL_INSIDE_SUBJECT")
    family, os_name = platform_profile()
    if family == "windows":
        raise SetupError(WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0)
    provider_dir.mkdir(parents=True, exist_ok=True)
    profile_path = provider_dir / "provider_profile.json"
    if profile_path.exists() and not replace: raise SetupError("TRUSTED_PROFILE_EXISTS_USE_REPLACE")
    executable = resolve_codex_path(codex_path, subject_root)
    version_run = run_fixed(executable, ["--version"])
    if version_run.returncode != 0 or not version_run.stdout.strip(): raise SetupError("CODEX_VERSION_PROBE_FAILED")
    schema_dir = pathlib.Path(schema_root).resolve() if schema_root else pathlib.Path(tempfile.mkdtemp(prefix="acv-codex-schema-"))
    try:
        if schema_root is None:
            json_run = run_fixed(executable, ["app-server", "generate-json-schema", "--out", str(schema_dir / "json")])
            ts_run = run_fixed(executable, ["app-server", "generate-ts", "--out", str(schema_dir / "ts")])
            if json_run.returncode != 0 or ts_run.returncode != 0: raise SetupError("SCHEMA_GENERATION_FAILED")
        schema_sha = _schema_digest(schema_dir)
    finally:
        if schema_root is None: shutil.rmtree(schema_dir, ignore_errors=True)
    python_executable = pathlib.Path(sys.executable).resolve()
    source = provider_dir / "acv_codex_provider.py"
    if not source.is_file(): raise SetupError("PROVIDER_SOURCE_MISSING")
    probe_dir = pathlib.Path(tempfile.mkdtemp(prefix="acv-codex-enrollment-cwd-")).resolve()
    try:
        evidence = _probe_app_server(executable, python_executable, probe_dir)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    preflight_json = {"schema_version":"1","provider_kind":"ACV_CODEX_APP_SERVER_COMMAND_EXEC_V1","codex_executable":str(executable),"codex_executable_sha256":sha256_file(executable),"codex_version":version_run.stdout.strip(),"schema_set_sha256":schema_sha,"schema_digest_excluded_paths":sorted(SCHEMA_DIGEST_EXCLUDED_PATHS),"platformFamily":family,"platformOs":os_name,"probes":evidence}
    preflight_json_bytes = (json.dumps(preflight_json, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    preflight_md_bytes = ("# ACV Codex App Server enrollment preflight\n\nAll required probes executed successfully.\n\n```json\n" + preflight_json_bytes.decode("utf-8") + "```\n").encode("utf-8")
    profile = {"profile_version":"1","provider_kind":"ACV_CODEX_APP_SERVER_COMMAND_EXEC_V1","codex_executable":str(executable),"codex_executable_sha256":sha256_file(executable),"codex_version":version_run.stdout.strip(),"schema_set_sha256":schema_sha,"schema_digest_algorithm":SCHEMA_DIGEST_ALGORITHM,"schema_digest_excluded_paths":sorted(SCHEMA_DIGEST_EXCLUDED_PATHS),"platformFamily":family,"platformOs":os_name,"experimentalApi":False,"preflight_md_sha256":hashlib.sha256(preflight_md_bytes).hexdigest(),"preflight_json_sha256":hashlib.sha256(preflight_json_bytes).hexdigest(),"expected_CODEX_HOME":"NOT_SET","config_requirements_sha256":evidence["config_requirements_sha256"],"tested_capabilities":{"command_exec":True,"output_delta":evidence["output_delta"]["observed"],"capReached":evidence["capReached"]["observed"],"cwd":True,"terminal_exit_code":evidence["terminal_exit_code"]["exit_code"] == 7,"config_requirements":True},"provider_source_sha256":sha256_file(source)}
    validate_enrollment_evidence(profile, preflight_json)
    _atomic_write_bytes(provider_dir / "provider_preflight.json", preflight_json_bytes)
    _atomic_write_bytes(provider_dir / "provider_preflight.md", preflight_md_bytes)
    _atomic_write(provider_dir / "provider_profile.json", profile)
    return profile

def main() -> int:
    parser = argparse.ArgumentParser(description="Enroll an external Codex App Server runtime.")
    parser.add_argument("--codex-path"); parser.add_argument("--provider-dir", required=True); parser.add_argument("--subject-root", required=True); parser.add_argument("--schema-root"); parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        profile = enroll(codex_path=args.codex_path, provider_dir=pathlib.Path(args.provider_dir), subject_root=pathlib.Path(args.subject_root), schema_root=pathlib.Path(args.schema_root) if args.schema_root else None, replace=args.replace)
        print(json.dumps({"status":"PASS","platformFamily":profile["platformFamily"],"platformOs":profile["platformOs"],"codex_version":profile["codex_version"],"tested_capabilities":profile["tested_capabilities"]}, ensure_ascii=True, sort_keys=True)); return 0
    except (OSError, SetupError, subprocess.SubprocessError) as exc:
        print("CODEX_SETUP_ERROR = " + str(exc)); return 2

if __name__ == "__main__": raise SystemExit(main())
