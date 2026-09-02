"""Thin, optional Codex App Server provider for ACV Stage B."""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import os
import pathlib
import queue
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Iterable, Mapping, Optional


PROVIDER_VERSION = "1"
PROVIDER_KIND = "ACV_CODEX_APP_SERVER_COMMAND_EXEC_V1"
WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0 = "WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0"
SCHEMA_DIGEST_EXCLUDED_PATHS = {"json/codex_app_server_protocol.v2.schemas.json"}
SCHEMA_DIGEST_ALGORITHM = "SHA256(UTF-8 concatenation of sorted relative_path + NUL + per-file-sha256 + LF; excluding exactly json/codex_app_server_protocol.v2.schemas.json)"
MAX_PROTOCOL_LINE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 120
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_RECEIPT_DEPTH = 64

# The initialize request deliberately omits experimentalApi; experimental APIs
# are not part of the provider v1 trust boundary.


class ProviderError(RuntimeError):
    """A fail-closed provider error with a stable reason."""


class UnsupportedProviderRealization(ProviderError):
    """The installed runtime does not match its accepted profile."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_schema_set_digest(schema_root: pathlib.Path) -> tuple[str, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for kind in ("json", "ts"):
        root = schema_root / kind
        if not root.is_dir():
            raise UnsupportedProviderRealization("SCHEMA_SET_MISSING")
    for excluded in sorted(SCHEMA_DIGEST_EXCLUDED_PATHS):
        excluded_path = schema_root / pathlib.PurePosixPath(excluded)
        if not excluded_path.is_file():
            raise UnsupportedProviderRealization("SCHEMA_DIGEST_EXCLUDED_PATH_MISSING:" + excluded)
    for kind in ("json", "ts"):
        root = schema_root / kind
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
            relative = path.relative_to(schema_root).as_posix()
            if relative in SCHEMA_DIGEST_EXCLUDED_PATHS:
                continue
            records.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    records.sort(key=lambda item: item["path"])
    material = b"".join((item["path"] + "\0" + item["sha256"] + "\n").encode("utf-8") for item in records)
    return _sha_bytes(material), records


def _module_from_path(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ProviderError("MODULE_SPEC_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    if name in sys.modules:
        raise ProviderError("MODULE_NAME_COLLISION")
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            sys.modules.pop(name, None)
        raise
    return module


@contextlib.contextmanager
def _loaded_module(name: str, path: pathlib.Path):
    module = _module_from_path(name, path)
    try:
        yield module
    finally:
        if sys.modules.get(name) is module:
            sys.modules.pop(name, None)


def _is_reparse(path: pathlib.Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x0400)


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _assert_external(path: pathlib.Path, subject_root: pathlib.Path, reason: str) -> pathlib.Path:
    resolved = path.resolve()
    if _inside(resolved, subject_root):
        raise ProviderError(reason)
    return resolved


def _assert_safe_tree(path: pathlib.Path, root: pathlib.Path) -> None:
    current = root.resolve()
    try:
        relative = path.resolve().relative_to(current)
    except ValueError as exc:
        raise ProviderError("PATH_ESCAPES_ROOT") from exc
    for part in relative.parts:
        current /= part
        if current.exists() and _is_reparse(current):
            raise ProviderError("REPARSE_PATH_REJECTED")


def resolve_subject_cwd(subject_root: pathlib.Path, logical_cwd: str) -> pathlib.Path:
    if not isinstance(logical_cwd, str) or not logical_cwd or pathlib.PureWindowsPath(logical_cwd).is_absolute() or pathlib.PurePosixPath(logical_cwd).is_absolute():
        raise ProviderError("CWD_MUST_BE_SUBJECT_RELATIVE")
    candidate = (subject_root / pathlib.PurePath(logical_cwd)).resolve()
    _assert_safe_tree(candidate, subject_root)
    if not candidate.is_dir() or not _inside(candidate, subject_root):
        raise ProviderError("CWD_ESCAPES_SUBJECT")
    return candidate


def _load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderError("JSON_LOAD_FAILED") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderError("RECEIPT_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise ProviderError("RECEIPT_NONFINITE_NUMBER")


def _check_json_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_RECEIPT_DEPTH:
        raise ProviderError("RECEIPT_MAX_DEPTH_EXCEEDED")
    if isinstance(value, Mapping):
        for child in value.values():
            _check_json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_json_depth(child, depth + 1)


def _load_strict_receipt(path: pathlib.Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_RECEIPT_BYTES:
            raise ProviderError("RECEIPT_TOO_LARGE")
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_nonfinite)
        _check_json_depth(value)
    except ProviderError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderError("RECEIPT_STRICT_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise ProviderError("RECEIPT_INVALID")
    return value


def _load_validated_receipt(path: pathlib.Path, validator: Any) -> Mapping[str, Any]:
    value = _load_strict_receipt(path)
    try:
        validator.validate(value)
    except Exception as exc:
        raise ProviderError("STAGE_A_RECEIPT_NOT_VALID") from exc
    return value


def validate_profile(profile: Mapping[str, Any]) -> None:
    required = {"profile_version", "provider_kind", "codex_executable", "codex_executable_sha256", "codex_version", "schema_set_sha256", "schema_digest_algorithm", "schema_digest_excluded_paths", "platformFamily", "platformOs", "experimentalApi", "preflight_md_sha256", "preflight_json_sha256", "tested_capabilities", "expected_CODEX_HOME"}
    optional = {"config_requirements_sha256", "canonical_install_files", "provider_source_sha256"}
    if set(profile) - required - optional:
        raise ProviderError("PROFILE_UNKNOWN_FIELD")
    if not required.issubset(profile):
        raise ProviderError("PROFILE_REQUIRED_FIELD_MISSING")
    if profile["profile_version"] != PROVIDER_VERSION or profile["provider_kind"] != PROVIDER_KIND:
        raise UnsupportedProviderRealization("PROFILE_IDENTITY_MISMATCH")
    for name in ("codex_executable", "codex_executable_sha256", "codex_version", "schema_set_sha256", "preflight_md_sha256", "preflight_json_sha256"):
        if not isinstance(profile[name], str) or not profile[name]:
            raise ProviderError("PROFILE_FIELD_INVALID:" + name)
    if profile["schema_digest_algorithm"] != SCHEMA_DIGEST_ALGORITHM:
        raise UnsupportedProviderRealization("SCHEMA_DIGEST_ALGORITHM_MISMATCH")
    expected_exclusions = sorted(SCHEMA_DIGEST_EXCLUDED_PATHS)
    if type(profile["schema_digest_excluded_paths"]) is not list or profile["schema_digest_excluded_paths"] != expected_exclusions:
        raise UnsupportedProviderRealization("SCHEMA_DIGEST_EXCLUSIONS_MISMATCH")
    supported_platforms = {
        "windows": {"windows"},
        "darwin": {"macos"},
        "linux": {"linux"},
    }
    if profile["platformFamily"] not in supported_platforms or profile["platformOs"] not in supported_platforms[profile["platformFamily"]]:
        raise UnsupportedProviderRealization("PLATFORM_PROFILE_MISMATCH")
    if profile["experimentalApi"] is not False:
        raise UnsupportedProviderRealization("EXPERIMENTAL_API_FORBIDDEN")
    if profile["expected_CODEX_HOME"] != "NOT_SET":
        raise UnsupportedProviderRealization("CODEX_HOME_PROFILE_UNSUPPORTED")
    if not isinstance(profile["tested_capabilities"], Mapping):
        raise ProviderError("PROFILE_CAPABILITIES_INVALID")


@dataclasses.dataclass(frozen=True)
class RuntimeRealization:
    profile_id: str
    provider_realization_id: str
    profile: Mapping[str, Any]
    initialize: Mapping[str, Any]
    config_requirements: Mapping[str, Any]
    config_requirements_sha256: str
    schema_files: tuple[Mapping[str, Any], ...]


def build_current_stage_b_context(core_module: Any, realization: RuntimeRealization, store: Any, subject_digest: str, protocol_id: str) -> tuple[dict[str, Any], str, Any]:
    """Build current policy and authority only from the live trusted runtime."""
    policy_snapshot = {
        "sandbox": "workspaceWrite",
        "networkAccess": False,
        "config_requirements_sha256": realization.config_requirements_sha256,
    }
    policy_id = core_module.derive_policy_snapshot_id(policy_snapshot)
    authority = core_module.AuthoritySnapshot(
        "acv-current-authority-" + realization.provider_realization_id,
        subject_digest,
        policy_snapshot_id=policy_id,
        authority_root_id="acv-local-install",
        accepted_realization_ids=(realization.provider_realization_id, protocol_id, store.profile_id),
        topology_complete=True,
        complete=True,
        self_authorized=False,
    )
    return policy_snapshot, policy_id, authority


class AppServerClient:
    """Bounded JSON-lines JSON-RPC client for app-server stdio."""

    def __init__(self, executable: pathlib.Path, timeout: float = COMMAND_TIMEOUT_SECONDS) -> None:
        self.executable = executable
        self.timeout = timeout
        self.process = subprocess.Popen([str(executable), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._queue: queue.Queue[tuple[str, Optional[bytes]]] = queue.Queue()
        self._request_id = 0
        self._seen_response_ids: set[int] = set()
        self.stderr_lines = 0
        for stream_name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            threading.Thread(target=self._reader, args=(stream_name, stream), daemon=True).start()

    def _reader(self, stream_name: str, stream: Any) -> None:
        for raw in iter(stream.readline, b""):
            self._queue.put((stream_name, raw))
        self._queue.put((stream_name, None))

    def _send(self, message: Mapping[str, Any]) -> int:
        self._request_id += 1
        request_id = self._request_id
        payload = dict(message)
        if "id" in payload:
            payload["id"] = request_id
        data = (_canonical(payload).decode("utf-8") + "\n").encode("utf-8")
        if len(data) > MAX_PROTOCOL_LINE_BYTES or self.process.stdin is None:
            raise ProviderError("PROTOCOL_MESSAGE_TOO_LARGE_OR_UNAVAILABLE")
        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProviderError("APP_SERVER_CRASHED") from exc
        return request_id

    def _request(self, method: str, params: Any) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
        request_id = self._send({"jsonrpc": "2.0", "id": 0, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        messages: list[Mapping[str, Any]] = []
        while time.monotonic() < deadline:
            try:
                stream, raw = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                continue
            if stream == "stderr":
                self.stderr_lines += 1
                continue
            if len(raw) > MAX_PROTOCOL_LINE_BYTES:
                raise ProviderError("PROTOCOL_MESSAGE_TOO_LARGE")
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProviderError("MALFORMED_JSON_RPC") from exc
            if not isinstance(message, Mapping):
                raise ProviderError("PROTOCOL_MESSAGE_INVALID")
            if "id" in message and isinstance(message["id"], int):
                if message["id"] in self._seen_response_ids:
                    raise ProviderError("CONFLICTING_RESPONSE_ID")
                self._seen_response_ids.add(message["id"])
            if message.get("id") == request_id and ("result" in message or "error" in message):
                if "error" in message:
                    raise ProviderError("APP_SERVER_REQUEST_FAILED")
                messages.append(message)
                return message, messages
            if "id" in message and message.get("id") != request_id:
                raise ProviderError("CONFLICTING_RESPONSE_ID")
            if "method" in message:
                messages.append(message)
        raise ProviderError("APP_SERVER_TIMEOUT")

    def initialize(self) -> Mapping[str, Any]:
        response, _ = self._request("initialize", {"clientInfo": {"name": "acv-codex-provider", "version": PROVIDER_VERSION}, "capabilities": {}})
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise ProviderError("INITIALIZE_RESULT_INVALID")
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        return result

    def config_requirements(self) -> Mapping[str, Any]:
        response, _ = self._request("configRequirements/read", None)
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise ProviderError("CONFIG_REQUIREMENTS_RESULT_INVALID")
        return result

    def execute(self, argv: list[str], cwd: pathlib.Path, process_id: str, sandbox_policy: Mapping[str, Any]) -> dict[str, Any]:
        response, messages = self._request("command/exec", {"command": argv, "cwd": str(cwd), "processId": process_id, "streamStdoutStderr": True, "outputBytesCap": MAX_OUTPUT_BYTES, "sandboxPolicy": dict(sandbox_policy)})
        deltas: list[dict[str, Any]] = []
        for message in messages:
            if message.get("method") != "command/exec/outputDelta":
                continue
            params = message.get("params")
            if not isinstance(params, Mapping) or params.get("processId") != process_id:
                raise ProviderError("OUTPUT_DELTA_IDENTITY_INVALID")
            try:
                raw = base64.b64decode(params["deltaBase64"], validate=True)
            except (KeyError, ValueError, TypeError) as exc:
                raise ProviderError("OUTPUT_DELTA_INVALID") from exc
            deltas.append({"stream": params.get("stream"), "bytes": len(raw), "capReached": bool(params.get("capReached"))})
        result = response.get("result")
        if not isinstance(result, Mapping) or type(result.get("exitCode")) is not int:
            raise ProviderError("COMMAND_EXEC_RESULT_INVALID")
        return {"exit_code": int(result["exitCode"]), "stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""), "output_deltas": deltas, "cap_reached": any(item["capReached"] for item in deltas), "response_after_output": True}

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

    def __enter__(self) -> "AppServerClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class CodexProvider:
    """Provider installed outside the subject; profile is always adjacent."""

    def __init__(self, provider_dir: Optional[pathlib.Path] = None) -> None:
        self.provider_dir = pathlib.Path(provider_dir or pathlib.Path(__file__).resolve().parent).resolve()
        self.profile_path = self.provider_dir / "provider_profile.json"
        self.install_publish_root = self.provider_dir.parents[1]
        self.skill_root = self.install_publish_root / "skills" / "ai-change-verification"

    def _load_core(self) -> tuple[Any, Any, Any]:
        paths = (self.skill_root / "scripts" / "finalize_verification.py", self.skill_root / "scripts" / "change_snapshot.py", self.skill_root / "scripts" / "validate_receipt.py")
        if not all(path.is_file() for path in paths):
            raise ProviderError("CANONICAL_INSTALL_INCOMPLETE")
        suffix = uuid.uuid4().hex
        return tuple(_module_from_path(name + "_" + suffix, path) for name, path in zip(("acv_installed_finalize", "acv_installed_snapshot", "acv_installed_validator"), paths))

    def _realization(self, subject_root: pathlib.Path) -> tuple[RuntimeRealization, AppServerClient, tuple[Any, Any, Any]]:
        _assert_external(self.provider_dir, subject_root, "ADAPTER_INSTALL_INSIDE_SUBJECT")
        if not self.profile_path.is_file() or self.profile_path.parent != self.provider_dir:
            raise UnsupportedProviderRealization("ADJACENT_PROFILE_REQUIRED")
        profile = _load_json(self.profile_path)
        if not isinstance(profile, Mapping):
            raise ProviderError("PROFILE_INVALID")
        validate_profile(profile)
        if profile["platformFamily"] == "windows":
            raise UnsupportedProviderRealization(WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0)
        expected_source = profile.get("provider_source_sha256")
        if expected_source is not None and sha256_file(self.provider_dir / "acv_codex_provider.py") != expected_source:
            raise UnsupportedProviderRealization("PROVIDER_SOURCE_HASH_MISMATCH")
        executable = pathlib.Path(profile["codex_executable"])
        if not executable.is_absolute() or _inside(executable, subject_root) or not executable.is_file():
            raise UnsupportedProviderRealization("CODEX_RUNTIME_INVALID")
        if sha256_file(executable) != profile["codex_executable_sha256"]:
            raise UnsupportedProviderRealization("CODEX_BINARY_HASH_MISMATCH")
        version = subprocess.run([str(executable), "--version"], capture_output=True, timeout=30, check=False, text=True, encoding="utf-8", errors="strict")
        if version.returncode != 0 or version.stdout.strip() != profile["codex_version"]:
            raise UnsupportedProviderRealization("CODEX_VERSION_MISMATCH")
        if os.environ.get("CODEX_HOME") is not None:
            raise UnsupportedProviderRealization("CODEX_HOME_DRIFT")
        with tempfile.TemporaryDirectory(prefix="acv-codex-schema-") as temp:
            root = pathlib.Path(temp)
            json_run = subprocess.run([str(executable), "app-server", "generate-json-schema", "--out", str(root / "json")], capture_output=True, timeout=60, check=False)
            ts_run = subprocess.run([str(executable), "app-server", "generate-ts", "--out", str(root / "ts")], capture_output=True, timeout=60, check=False)
            if json_run.returncode != 0 or ts_run.returncode != 0:
                raise UnsupportedProviderRealization("SCHEMA_GENERATION_FAILED")
            schema_digest, schema_files = canonical_schema_set_digest(root)
        if schema_digest != profile["schema_set_sha256"]:
            raise UnsupportedProviderRealization("SCHEMA_DIGEST_MISMATCH")
        with AppServerClient(executable) as probe:
            initialize = probe.initialize()
            config = probe.config_requirements()
        if initialize.get("platformFamily") != profile["platformFamily"] or initialize.get("platformOs") != profile["platformOs"]:
            raise UnsupportedProviderRealization("INITIALIZE_PLATFORM_MISMATCH")
        config_digest = _sha_bytes(_canonical(config))
        if config.get("requirements") not in (None, {}):
            raise UnsupportedProviderRealization("CONFIG_REQUIREMENTS_UNSUPPORTED")
        if profile.get("config_requirements_sha256") is not None and profile["config_requirements_sha256"] != config_digest:
            raise UnsupportedProviderRealization("CONFIG_REQUIREMENTS_MISMATCH")
        profile_id = _sha_bytes(b"provider-profile\0" + _canonical(profile))
        realization_id = _sha_bytes(b"codex-provider-realization\0" + _canonical({"profile_id": profile_id, "codex_executable": str(executable), "codex_executable_sha256": profile["codex_executable_sha256"], "codex_version": version.stdout.strip(), "schema_set_sha256": schema_digest, "platformFamily": initialize.get("platformFamily"), "platformOs": initialize.get("platformOs"), "experimentalApi": False, "provider_source_sha256": profile.get("provider_source_sha256")}))
        client = AppServerClient(executable)
        client.initialize()
        client.config_requirements()
        return RuntimeRealization(profile_id, realization_id, profile, initialize, config, config_digest, tuple(schema_files)), client, self._load_core()

    def _fresh_runtime_realization(self, subject_root: pathlib.Path) -> tuple[RuntimeRealization, tuple[Any, Any, Any]]:
        """Re-probe the live runtime for currentness without executing a selected check."""
        _assert_external(self.provider_dir, subject_root, "ADAPTER_INSTALL_INSIDE_SUBJECT")
        profile = _load_json(self.profile_path)
        if not isinstance(profile, Mapping):
            raise ProviderError("PROFILE_INVALID")
        validate_profile(profile)
        expected_source = profile.get("provider_source_sha256")
        if expected_source is not None and sha256_file(self.provider_dir / "acv_codex_provider.py") != expected_source:
            raise UnsupportedProviderRealization("PROVIDER_SOURCE_HASH_MISMATCH")
        executable = pathlib.Path(profile["codex_executable"])
        if not executable.is_absolute() or _inside(executable, subject_root) or not executable.is_file() or sha256_file(executable) != profile["codex_executable_sha256"]:
            raise UnsupportedProviderRealization("CODEX_RUNTIME_INVALID")
        version = subprocess.run([str(executable), "--version"], capture_output=True, timeout=30, check=False, text=True, encoding="utf-8", errors="strict")
        if version.returncode != 0 or version.stdout.strip() != profile["codex_version"]:
            raise UnsupportedProviderRealization("CODEX_VERSION_MISMATCH")
        if os.environ.get("CODEX_HOME") is not None:
            raise UnsupportedProviderRealization("CODEX_HOME_DRIFT")
        with AppServerClient(executable) as probe:
            initialize = probe.initialize()
            config = probe.config_requirements()
        if initialize.get("platformFamily") != profile["platformFamily"] or initialize.get("platformOs") != profile["platformOs"]:
            raise UnsupportedProviderRealization("INITIALIZE_PLATFORM_MISMATCH")
        config_digest = _sha_bytes(_canonical(config))
        if config.get("requirements") not in (None, {}):
            raise UnsupportedProviderRealization("CONFIG_REQUIREMENTS_UNSUPPORTED")
        if profile.get("config_requirements_sha256") is not None and profile["config_requirements_sha256"] != config_digest:
            raise UnsupportedProviderRealization("CONFIG_REQUIREMENTS_MISMATCH")
        profile_id = _sha_bytes(b"provider-profile\0" + _canonical(profile))
        realization_id = _sha_bytes(b"codex-provider-realization\0" + _canonical({"profile_id": profile_id, "codex_executable": str(executable), "codex_executable_sha256": profile["codex_executable_sha256"], "codex_version": version.stdout.strip(), "schema_set_sha256": profile["schema_set_sha256"], "platformFamily": initialize.get("platformFamily"), "platformOs": initialize.get("platformOs"), "experimentalApi": False, "provider_source_sha256": profile.get("provider_source_sha256")}))
        return RuntimeRealization(profile_id, realization_id, profile, initialize, config, config_digest, ()), self._load_core()

    def run(self, receipt_path: pathlib.Path, subject_root: pathlib.Path, state_dir: pathlib.Path) -> dict[str, Any]:
        subject_root = pathlib.Path(subject_root).resolve()
        finalize, snapshot, validator = self._load_core()
        receipt = _load_validated_receipt(pathlib.Path(receipt_path).resolve(), validator)
        pre = snapshot.capture_snapshot(subject_root)
        if not pre.get("complete") or pre.get("snapshot_id") != receipt["subject"]["subject_digest"]:
            raise ProviderError("PRE_SUBJECT_MISMATCH")
        selected = [dict(check) for check in receipt.get("verification_plan", []) if check.get("selected") is True]
        if any(check.get("operation_contract", {}).get("kind") != "COMMAND_EXECUTION" for check in selected):
            raise ProviderError("UNSUPPORTED_CAPTURE_NON_COMMAND")
        for check in selected:
            if validator.compute_check_contract_digest(check) != check.get("check_contract_digest"):
                raise ProviderError("CHECK_CONTRACT_FREEZE_MISMATCH")
        state_path = pathlib.Path(state_dir).resolve()
        _assert_external(state_path, subject_root, "STATE_DIR_INSIDE_SUBJECT")
        realization, client, core = self._realization(subject_root)
        capabilities = core[0].ProviderCapabilities(True, True, True, True, True, True, True)
        session = core[0].RunBoundEvidenceSession(str(uuid.uuid4()), pre["snapshot_id"], realization.provider_realization_id, realization.profile_id, capabilities)
        events: list[dict[str, Any]] = []
        frontier = 0
        try:
            for check in selected:
                operation = check["operation_contract"]
                resolved_cwd = resolve_subject_cwd(subject_root, operation["cwd"])
                invocation_id = "acv-invocation-" + uuid.uuid4().hex
                process_id = "acv-process-" + uuid.uuid4().hex
                bindings = (
                    core[0].InvocationInputBinding(core[0].InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"check_contract_digest": check["check_contract_digest"], "argv": operation["argv"], "logical_cwd": operation["cwd"], "resolved_cwd": str(resolved_cwd)}),
                    core[0].InvocationInputBinding(core[0].InvocationBindingMode.NON_REUSABLE_OPAQUE_STATE.value, {"config_requirements_sha256": realization.config_requirements_sha256, "environment": "NON_REUSABLE_OPAQUE_STATE"}, reusable=False),
                    core[0].InvocationInputBinding(core[0].InvocationBindingMode.TRUSTED_INVOCATION_SNAPSHOT_DIGEST_OR_EPOCH.value, {"sandbox_policy": "workspaceWrite;networkAccess=false;writableRoots=subject"}),
                )
                session.register_invocation(invocation_id, check["id"], operation=operation, input_bindings=bindings)
                started = time.time()
                result = client.execute(operation["argv"], resolved_cwd, process_id, {"type": "workspaceWrite", "networkAccess": False, "writableRoots": [str(subject_root)]})
                status = "PASS" if result["exit_code"] in set(check["result_interpretation"]["success_exit_codes"]) else "FAIL"
                frontier += 1
                payload = {"exit_code": result["exit_code"], "stdout_sha256": _sha_bytes(str(result["stdout"]).encode("utf-8")), "stderr_sha256": _sha_bytes(str(result["stderr"]).encode("utf-8")), "cap_reached": result["cap_reached"], "process_id": process_id}
                session.ingest_event(core[0].ProviderEvent("acv-event-" + uuid.uuid4().hex, invocation_id, check["id"], status, operation=operation, payload=payload, sequence=frontier))
                events.append({"invocation_id": invocation_id, "process_id": process_id, "check_id": check["id"], "argv": operation["argv"], "logical_cwd": operation["cwd"], "resolved_cwd": str(resolved_cwd), "exit_code": result["exit_code"], "outcome": status, "cap_reached": result["cap_reached"], "duration_ms": int((time.time() - started) * 1000), "output_deltas": result["output_deltas"]})
            session.seal(frontier)
            session.acknowledge_drain(frontier)
        finally:
            client.close()
        post = snapshot.capture_snapshot(subject_root)
        output: dict[str, Any] = {"provider_realization_id": realization.provider_realization_id, "provider_capability_profile_id": realization.profile_id, "subject_before": pre, "subject_after": post, "config_requirements_sha256": realization.config_requirements_sha256, "events": events, "final_event_frontier": frontier, "drained_through": frontier}
        if not post.get("complete") or post.get("snapshot_id") != pre.get("snapshot_id"):
            output.update({"verification_run_status": "INVALID", "review_readiness": "NOT_READY_FOR_HUMAN_REVIEW", "reason_codes": ["CURRENT_SUBJECT_CHANGED"]})
            return output
        state_path.mkdir(parents=True, exist_ok=True)
        store = finalize.DecisionStore(state_path / "decisions.sqlite3")
        try:
            authority = finalize.AuthoritySnapshot("acv-authority-" + realization.provider_realization_id, pre["snapshot_id"], policy_snapshot_id=realization.config_requirements_sha256, authority_root_id="acv-local-install", accepted_realization_ids=(realization.provider_realization_id,), topology_complete=True, complete=True, self_authorized=False)
            roots = finalize.derive_required_fact_roots(receipt)
            graph = {root: finalize.TransformRecord(realization.provider_realization_id, finalize.TransformMode.EXPLICIT_INPUT_TRANSFORM.value) for root in roots}
            decision_key = finalize.derive_decision_key(receipt)
            current_head = store.current_head(decision_key)
            expected_generation = 0 if current_head is None else current_head["generation"]
            expected_head = None if current_head is None else current_head["head_object_id"]
            final = finalize.finalize_verification(receipt, session, store, authority, policy_snapshot={"sandbox": "workspaceWrite", "networkAccess": False, "config_requirements_sha256": realization.config_requirements_sha256}, decision_protocol_realization_id="acv-stage-b-core-v1", provenance_graph=graph, expected_generation=expected_generation, expected_head=expected_head)
            current = finalize.evaluate_current_readiness(final, store, post["snapshot_id"], authority, "acv-stage-b-core-v1", realization.provider_realization_id) if final.committed else None
            output.update({"finalization": dataclasses.asdict(final), "current_readiness": dataclasses.asdict(current) if current else None, "verification_run_status": final.verification_run_status, "review_readiness": final.review_readiness, "reason_codes": list(final.reason_codes)})
            return output
        finally:
            store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the installed ACV Codex provider against a validated receipt.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--subject-root", required=True)
    verify.add_argument("--state-dir", required=True)
    verify.add_argument("--result", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        subject = pathlib.Path(args.subject_root).resolve()
        result_path = pathlib.Path(args.result).resolve()
        _assert_external(result_path, subject, "RESULT_INSIDE_SUBJECT")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result = CodexProvider().run(pathlib.Path(args.receipt), subject, pathlib.Path(args.state_dir))
        result_path.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"verification_run_status": result.get("verification_run_status"), "review_readiness": result.get("review_readiness"), "provider_realization_id": result.get("provider_realization_id")}, ensure_ascii=True, sort_keys=True))
        current = result.get("current_readiness") or {}
        return 0 if current.get("state") == "CURRENT_READY" else 2
    except (ProviderError, OSError, subprocess.SubprocessError) as exc:
        print("CODEX_PROVIDER_ERROR = " + str(exc), file=sys.stderr)
        return 2


_V8_LegacyCodexProvider = CodexProvider


class CodexProvider(_V8_LegacyCodexProvider):
    """V8 adapter: durable journal before command execution, canonical Core finalization."""

    def run(self, receipt_path: pathlib.Path, subject_root: pathlib.Path, state_dir: pathlib.Path) -> dict[str, Any]:
        subject_root = pathlib.Path(subject_root).resolve()
        receipt = _load_json(pathlib.Path(receipt_path).resolve())
        if not isinstance(receipt, Mapping):
            raise ProviderError("RECEIPT_INVALID")
        finalize, snapshot, validator = self._load_core()
        try:
            validator.validate(receipt)
        except Exception as exc:
            raise ProviderError("STAGE_A_RECEIPT_NOT_VALID") from exc
        pre = snapshot.capture_snapshot(subject_root)
        if not pre.get("complete") or pre.get("snapshot_id") != receipt["subject"]["subject_digest"]:
            raise ProviderError("PRE_SUBJECT_MISMATCH")
        selected = [dict(check) for check in receipt.get("verification_plan", []) if check.get("selected") is True]
        if any(check.get("operation_contract", {}).get("kind") != "COMMAND_EXECUTION" for check in selected):
            raise ProviderError("UNSUPPORTED_CAPTURE_NON_COMMAND")
        for check in selected:
            if validator.compute_check_contract_digest(check) != check.get("check_contract_digest"):
                raise ProviderError("CHECK_CONTRACT_FREEZE_MISMATCH")
        state_path = pathlib.Path(state_dir).resolve()
        _assert_external(state_path, subject_root, "STATE_DIR_INSIDE_SUBJECT")
        state_path.mkdir(parents=True, exist_ok=True)
        realization, client, core = self._realization(subject_root)
        protocol_id = core[0].derive_core_realization_id()
        decision_key = core[0].derive_decision_key(receipt)
        operation_id = "operation-" + uuid.uuid4().hex
        store = core[0].DecisionStore(state_path / "decisions.sqlite3")
        session = core[0].RunBoundEvidenceSession(str(uuid.uuid4()), pre["snapshot_id"], realization.provider_realization_id, realization.profile_id, core[0].ProviderCapabilities(True, True, True, True, True, True, True))
        session.attach_store(store, decision_key, operation_id)
        events: list[dict[str, Any]] = []
        frontier = 0
        try:
            try:
                for check in selected:
                    operation = check["operation_contract"]
                    resolved_cwd = resolve_subject_cwd(subject_root, operation["cwd"])
                    invocation_id = "acv-invocation-" + uuid.uuid4().hex
                    process_id = "acv-process-" + uuid.uuid4().hex
                    bindings = (
                        core[0].InvocationInputBinding(core[0].InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"check_contract_digest": check["check_contract_digest"], "argv": operation["argv"], "logical_cwd": operation["cwd"], "resolved_cwd": str(resolved_cwd)}),
                        core[0].InvocationInputBinding(core[0].InvocationBindingMode.NON_REUSABLE_OPAQUE_STATE.value, {"config_requirements_sha256": realization.config_requirements_sha256, "environment": "NON_REUSABLE_OPAQUE_STATE"}, reusable=False),
                        core[0].InvocationInputBinding(core[0].InvocationBindingMode.TRUSTED_INVOCATION_SNAPSHOT_DIGEST_OR_EPOCH.value, {"sandbox_policy": "workspaceWrite;networkAccess=false;writableRoots=subject"}),
                    )
                    session.register_invocation(invocation_id, check["id"], operation=operation, input_bindings=bindings)
                    session.activate_invocation(invocation_id)
                    started = time.time()
                    result = client.execute(operation["argv"], resolved_cwd, process_id, {"type": "workspaceWrite", "networkAccess": False, "writableRoots": [str(subject_root)]})
                    status = "PASS" if result["exit_code"] in set(check["result_interpretation"]["success_exit_codes"]) else "FAIL"
                    frontier += 1
                    payload = {"exit_code": result["exit_code"], "stdout_sha256": _sha_bytes(str(result["stdout"]).encode("utf-8")), "stderr_sha256": _sha_bytes(str(result["stderr"]).encode("utf-8")), "cap_reached": result["cap_reached"], "process_id": process_id}
                    session.ingest_event(core[0].ProviderEvent("acv-event-" + uuid.uuid4().hex, invocation_id, check["id"], status, operation=operation, payload=payload, sequence=frontier))
                    events.append({"invocation_id": invocation_id, "process_id": process_id, "check_id": check["id"], "argv": operation["argv"], "logical_cwd": operation["cwd"], "resolved_cwd": str(resolved_cwd), "exit_code": result["exit_code"], "outcome": status, "cap_reached": result["cap_reached"], "duration_ms": int((time.time() - started) * 1000), "output_deltas": result["output_deltas"]})
                session.seal(frontier)
                session.acknowledge_drain(frontier)
            finally:
                client.close()
            post = snapshot.capture_snapshot(subject_root)
            output: dict[str, Any] = {"operation_id": operation_id, "provider_realization_id": realization.provider_realization_id, "provider_capability_profile_id": realization.profile_id, "subject_before": pre, "subject_after": post, "config_requirements_sha256": realization.config_requirements_sha256, "events": events, "final_event_frontier": frontier, "drained_through": frontier}
            if not post.get("complete") or post.get("snapshot_id") != pre.get("snapshot_id"):
                output.update({"verification_run_status": "INVALID", "review_readiness": "NOT_READY_FOR_HUMAN_REVIEW", "reason_codes": ["CURRENT_SUBJECT_CHANGED"]})
                return output
            policy_snapshot = {"sandbox": "workspaceWrite", "networkAccess": False, "config_requirements_sha256": realization.config_requirements_sha256}
            policy_id = core[0].derive_policy_snapshot_id(policy_snapshot)
            authority = core[0].AuthoritySnapshot("acv-authority-" + realization.provider_realization_id, pre["snapshot_id"], policy_snapshot_id=policy_id, authority_root_id="acv-local-install", accepted_realization_ids=(realization.provider_realization_id, protocol_id, store.profile_id), topology_complete=True, complete=True, self_authorized=False)
            roots = core[0].derive_required_fact_roots(receipt)
            graph: dict[str, object] = {}
            check_roots: dict[str, str] = {}
            for check in selected:
                event = next(item for item in session.events.values() if item.check_id == check["id"])
                record = session.invocations[event.invocation_id]
                binding_key = "binding:" + event.invocation_id
                invocation_key = "invocation:" + event.invocation_id
                event_key = "event:" + event.event_id
                check_root = f"check:{check['id']}:{check['check_contract_digest']}"
                graph[binding_key] = {"kind": "INVOCATION_INPUT_BINDING", "producer_realization_id": realization.provider_realization_id, "value": [core[0]._plain(item) for item in record["input_bindings"]]}
                graph[invocation_key] = {"kind": "INVOCATION_RECORD", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [binding_key], "invocation_id": event.invocation_id, "check_id": check["id"]}
                graph[event_key] = {"kind": "PROVIDER_EVENT_RECORD", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [invocation_key], "event_id": event.event_id, "status": event.status, "sequence": event.sequence}
                graph[check_root] = {"kind": "CHECK_RESULT_FACT", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [event_key], "check_id": check["id"], "outcome": event.status}
                check_roots[check["id"]] = check_root
            for obligation in (item for item in receipt.get("obligations", []) if item.get("material") is True):
                obligation_root = "obligation:" + obligation["id"]
                graph[obligation_root] = {"kind": "OBLIGATION_FACT", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [check_roots[key] for key in sorted(check_roots)] if check_roots else [], "obligation_id": obligation["id"]}
            head = store.current_head(decision_key)
            final = core[0].finalize_verification(receipt, session, store, authority, policy_snapshot=policy_snapshot, decision_protocol_realization_id=protocol_id, provenance_graph=graph, expected_generation=0 if head is None else head["generation"], expected_head=None if head is None else head["head_object_id"])
            current = core[0].evaluate_current_readiness(final, store, post["snapshot_id"], authority, protocol_id, realization.provider_realization_id, current_policy_snapshot_id=policy_id) if final.committed else None
            output.update({"finalization": dataclasses.asdict(final), "current_readiness": dataclasses.asdict(current) if current else None, "verification_run_status": final.verification_run_status, "review_readiness": final.review_readiness, "reason_codes": list(final.reason_codes), "decision_protocol_realization_id": protocol_id, "policy_snapshot_id": policy_id})
            return output
        finally:
            store.close()


def _v8_atomic_write(path: pathlib.Path, value: Mapping[str, Any]) -> str:
    data = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_bytes(data)
    with temporary.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return _sha_bytes(data)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        subject = pathlib.Path(args.subject_root).resolve()
        result_path = pathlib.Path(args.result).resolve()
        _assert_external(result_path, subject, "RESULT_INSIDE_SUBJECT")
        result = CodexProvider().run(pathlib.Path(args.receipt), subject, pathlib.Path(args.state_dir))
        result_digest = _v8_atomic_write(result_path, result)
        operation_id = result.get("operation_id")
        if operation_id:
            with DecisionStore(pathlib.Path(args.state_dir).resolve() / "decisions.sqlite3") as store:
                store.mark_projection_published(operation_id, result_digest)
        print(json.dumps({"verification_run_status": result.get("verification_run_status"), "review_readiness": result.get("review_readiness"), "provider_realization_id": result.get("provider_realization_id")}, ensure_ascii=True, sort_keys=True))
        current = result.get("current_readiness") or {}
        return 0 if current.get("state") == "CURRENT_READY" else 2
    except (ProviderError, OSError, subprocess.SubprocessError) as exc:
        print("CODEX_PROVIDER_ERROR = " + str(exc), file=sys.stderr)
        return 2


_V9_LegacyCodexProvider = CodexProvider


class CodexProvider(_V9_LegacyCodexProvider):
    """Codex capture provider with V9 finalization and publication recovery."""

    def run(self, receipt_path: pathlib.Path, subject_root: pathlib.Path, state_dir: pathlib.Path) -> dict[str, Any]:
        subject_root = pathlib.Path(subject_root).resolve()
        receipt = _load_json(pathlib.Path(receipt_path).resolve())
        if not isinstance(receipt, Mapping):
            raise ProviderError("RECEIPT_INVALID")
        finalize, snapshot, validator = self._load_core()
        try:
            validator.validate(receipt)
        except Exception as exc:
            raise ProviderError("STAGE_A_RECEIPT_NOT_VALID") from exc
        pre = snapshot.capture_snapshot(subject_root)
        if not pre.get("complete") or pre.get("snapshot_id") != receipt["subject"]["subject_digest"]:
            raise ProviderError("PRE_SUBJECT_MISMATCH")
        selected = [dict(check) for check in receipt.get("verification_plan", []) if check.get("selected") is True]
        if not selected or any(check.get("operation_contract", {}).get("kind") != "COMMAND_EXECUTION" for check in selected):
            raise ProviderError("UNSUPPORTED_CAPTURE_NON_COMMAND")
        for check in selected:
            if validator.compute_check_contract_digest(check) != check.get("check_contract_digest"):
                raise ProviderError("CHECK_CONTRACT_FREEZE_MISMATCH")
        state_path = pathlib.Path(state_dir).resolve()
        _assert_external(state_path, subject_root, "STATE_DIR_INSIDE_SUBJECT")
        state_path.mkdir(parents=True, exist_ok=True)
        realization, client, core = self._realization(subject_root)
        core_module = core[0]
        protocol_id = core_module.derive_core_realization_id()
        verification_contract_id = core_module.derive_verification_contract_id(receipt)
        decision_key = core_module.derive_decision_key(receipt)
        operation_id = "operation-" + uuid.uuid4().hex
        store = core_module.DecisionStore(state_path / "decisions.sqlite3")
        session = core_module.RunBoundEvidenceSession(str(uuid.uuid4()), pre["snapshot_id"], realization.provider_realization_id, realization.profile_id, core_module.ProviderCapabilities(True, True, True, True, True, True, True), verification_contract_id)
        session.attach_store(store, decision_key, operation_id)
        events: list[dict[str, Any]] = []
        frontier = 0
        try:
            try:
                for check in selected:
                    operation = check["operation_contract"]
                    resolved_cwd = resolve_subject_cwd(subject_root, operation["cwd"])
                    invocation_id = "acv-invocation-" + uuid.uuid4().hex
                    process_id = "acv-process-" + uuid.uuid4().hex
                    bindings = (
                        core_module.InvocationInputBinding(core_module.InvocationBindingMode.CANONICAL_EXPLICIT_INPUT.value, {"check_contract_digest": check["check_contract_digest"], "argv": operation["argv"], "logical_cwd": operation["cwd"], "resolved_cwd": str(resolved_cwd)}),
                        core_module.InvocationInputBinding(core_module.InvocationBindingMode.NON_REUSABLE_OPAQUE_STATE.value, {"config_requirements_sha256": realization.config_requirements_sha256, "environment": "NON_REUSABLE_OPAQUE_STATE"}, reusable=False),
                        core_module.InvocationInputBinding(core_module.InvocationBindingMode.TRUSTED_INVOCATION_SNAPSHOT_DIGEST_OR_EPOCH.value, {"sandbox_policy": "workspaceWrite;networkAccess=false;writableRoots=subject"}),
                    )
                    session.register_invocation(invocation_id, check["id"], operation=operation, input_bindings=bindings)
                    session.activate_invocation(invocation_id)
                    started = time.time()
                    result = client.execute(operation["argv"], resolved_cwd, process_id, {"type": "workspaceWrite", "networkAccess": False, "writableRoots": [str(subject_root)]})
                    status = "PASS" if result["exit_code"] in set(check["result_interpretation"]["success_exit_codes"]) else "FAIL"
                    frontier += 1
                    payload = {"exit_code": result["exit_code"], "stdout_sha256": _sha_bytes(str(result["stdout"]).encode("utf-8")), "stderr_sha256": _sha_bytes(str(result["stderr"]).encode("utf-8")), "cap_reached": result["cap_reached"], "process_id": process_id}
                    event = core_module.ProviderEvent("acv-event-" + uuid.uuid4().hex, invocation_id, check["id"], status, operation=operation, payload=payload, sequence=frontier)
                    session.ingest_event(event)
                    events.append({"invocation_id": invocation_id, "process_id": process_id, "check_id": check["id"], "argv": operation["argv"], "logical_cwd": operation["cwd"], "resolved_cwd": str(resolved_cwd), "exit_code": result["exit_code"], "outcome": status, "cap_reached": result["cap_reached"], "duration_ms": int((time.time() - started) * 1000), "output_deltas": result["output_deltas"]})
                session.seal(frontier)
                session.acknowledge_drain(frontier)
            finally:
                client.close()
            post = snapshot.capture_snapshot(subject_root)
            session.post_execution_snapshot_id = post.get("snapshot_id", "")
            output: dict[str, Any] = {"operation_id": operation_id, "provider_realization_id": realization.provider_realization_id, "provider_capability_profile_id": realization.profile_id, "subject_before": pre, "subject_after": post, "config_requirements_sha256": realization.config_requirements_sha256, "events": events, "final_event_frontier": frontier, "drained_through": frontier, "command_execution_count": len(events)}
            if not post.get("complete") or post.get("snapshot_id") != pre.get("snapshot_id"):
                output.update({"verification_run_status": "INVALID", "review_readiness": "NOT_READY_FOR_HUMAN_REVIEW", "reason_codes": ["CURRENT_SUBJECT_CHANGED"]})
                return output
            policy_snapshot, policy_id, authority = build_current_stage_b_context(core_module, realization, store, pre["snapshot_id"], protocol_id)
            graph: dict[str, object] = {}
            check_roots: dict[str, str] = {}
            for check in selected:
                event = next(item for item in session.events.values() if item.check_id == check["id"])
                record = session.invocations[event.invocation_id]
                binding_key = "binding:" + event.invocation_id
                invocation_key = "invocation:" + event.invocation_id
                event_key = "event:" + event.event_id
                check_root = f"check:{check['id']}:{check['check_contract_digest']}"
                graph[binding_key] = {"kind": "INVOCATION_INPUT_BINDING", "producer_realization_id": realization.provider_realization_id, "value": [core_module._plain(item) for item in record["input_bindings"]]}
                graph[invocation_key] = {"kind": "INVOCATION_RECORD", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [binding_key], "invocation_id": event.invocation_id, "check_id": check["id"]}
                graph[event_key] = {"kind": "PROVIDER_EVENT_RECORD", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [invocation_key], "event_id": event.event_id, "status": event.status, "sequence": event.sequence}
                graph[check_root] = {"kind": "CHECK_RESULT_FACT", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [event_key], "check_id": check["id"], "outcome": event.status}
                check_roots[check["id"]] = check_root
            for obligation in (item for item in receipt.get("obligations", []) if item.get("material") is True):
                graph["obligation:" + obligation["id"]] = {"kind": "OBLIGATION_FACT", "producer_realization_id": realization.provider_realization_id, "direct_input_object_ids": [check_roots[key] for key in sorted(check_roots)], "obligation_id": obligation["id"]}
            head = store.current_head(decision_key)
            final = core_module.finalize_verification(receipt, session, store, authority, policy_snapshot=policy_snapshot, decision_protocol_realization_id=protocol_id, provenance_graph=graph, expected_generation=0 if head is None else head["generation"], expected_head=None if head is None else head["head_object_id"])
            current_snapshot = snapshot.capture_snapshot(subject_root) if final.committed else None
            current = None
            reprobe_passed = False
            if final.committed and current_snapshot:
                try:
                    fresh_realization, fresh_core = self._fresh_runtime_realization(subject_root)
                    fresh_core_module = fresh_core[0]
                    fresh_protocol_id = fresh_core_module.derive_core_realization_id()
                    fresh_policy, fresh_policy_id, fresh_authority = build_current_stage_b_context(fresh_core_module, fresh_realization, store, current_snapshot["snapshot_id"], fresh_protocol_id)
                    current = fresh_core_module.evaluate_current_readiness(final, store, current_snapshot["snapshot_id"], fresh_authority, fresh_protocol_id, fresh_realization.provider_realization_id, current_policy_snapshot_id=fresh_policy_id, current_subject_snapshot_id=current_snapshot["snapshot_id"])
                    reprobe_passed = True
                except Exception:
                    current = core_module.CurrentReadinessResult("REVERIFY_REQUIRED", ("CURRENT_RUNTIME_REPROBE_FAILED",), final.finalization_id, False, final.generation)
            output.update({"post_execution_snapshot": post, "currentness_snapshot": current_snapshot, "finalization": dataclasses.asdict(final), "current_readiness": dataclasses.asdict(current) if current else None, "verification_run_status": final.verification_run_status, "review_readiness": final.review_readiness, "reason_codes": list(final.reason_codes), "decision_protocol_realization_id": protocol_id, "policy_snapshot_id": policy_id, "current_runtime_reprobe_passed": reprobe_passed})
            return output
        finally:
            store.close()

    def recover(self, receipt_path: pathlib.Path, subject_root: pathlib.Path, state_dir: pathlib.Path, operation_id: Optional[str] = None) -> dict[str, Any]:
        subject_root = pathlib.Path(subject_root).resolve()
        finalize, snapshot, validator = self._load_core()
        receipt = _load_validated_receipt(pathlib.Path(receipt_path).resolve(), validator)
        state_path = pathlib.Path(state_dir).resolve()
        _assert_external(state_path, subject_root, "STATE_DIR_INSIDE_SUBJECT")
        realization, client, core = self._realization(subject_root)
        client.close()
        core_module = core[0]
        store = core_module.DecisionStore(state_path / "decisions.sqlite3")
        try:
            decision_key = core_module.derive_decision_key(receipt)
            verification_contract_id = core_module.derive_verification_contract_id(receipt)
            pending = store.pending_publications_for_decision(decision_key)
            if operation_id:
                pending = [item for item in pending if item["operation_id"] == operation_id]
            if len(pending) == 0:
                raise ProviderError("NO_RECOVERABLE_CURRENT_PUBLICATION")
            if len(pending) != 1:
                raise ProviderError("RECOVERY_OPERATION_AMBIGUOUS")
            final_payload = store.get_finalization(pending[0]["finalization_id"])
            if final_payload.get("verification_contract_id") != verification_contract_id:
                raise ProviderError("VERIFICATION_CONTRACT_MISMATCH")
            current_snapshot = snapshot.capture_snapshot(subject_root)
            protocol_id = core_module.derive_core_realization_id()
            policy_snapshot, policy_id, authority = build_current_stage_b_context(core_module, realization, store, current_snapshot["snapshot_id"], protocol_id)
            current = core_module.evaluate_current_readiness(final_payload, store, current_snapshot["snapshot_id"], authority, protocol_id, realization.provider_realization_id, current_policy_snapshot_id=policy_id, current_subject_snapshot_id=current_snapshot["snapshot_id"])
            result = {"operation_id": pending[0]["operation_id"], "recovery_command_execution_count": 0, "currentness_snapshot": current_snapshot, "current_readiness": dataclasses.asdict(current), "verification_run_status": "COMPLETE" if current.current else "INCOMPLETE", "review_readiness": final_payload.get("review_readiness"), "finalization": final_payload}
            return result
        finally:
            store.close()

    def abort_incomplete(self, receipt_path: pathlib.Path, subject_root: pathlib.Path, state_dir: pathlib.Path, operation_id: str) -> dict[str, Any]:
        """Explicitly abort one exact OPEN/SEALED acquisition without executing commands."""
        subject_root = pathlib.Path(subject_root).resolve()
        _, _, validator = self._load_core()
        receipt = _load_validated_receipt(pathlib.Path(receipt_path).resolve(), validator)
        state_path = pathlib.Path(state_dir).resolve()
        _assert_external(state_path, subject_root, "STATE_DIR_INSIDE_SUBJECT")
        core_module = self._load_core()[0]
        decision_key = core_module.derive_decision_key(receipt)
        verification_contract_id = core_module.derive_verification_contract_id(receipt)
        store = core_module.DecisionStore(state_path / "decisions.sqlite3")
        try:
            row = store.connection.execute("SELECT acquisition_id,state,verification_contract_id FROM acquisitions WHERE operation_id=? AND decision_key=?", (operation_id, decision_key)).fetchone()
            if row is None or row[2] != verification_contract_id:
                raise ProviderError("ABORT_OPERATION_NOT_EXACT")
            store.abort_acquisition(row[0], "EXPLICIT_OPERATOR_ABORT")
            return {"operation_id": operation_id, "decision_key": decision_key, "verification_contract_id": verification_contract_id, "acquisition_id": row[0], "previous_state": row[1], "state": "ABORTED", "abort_reason": "EXPLICIT_OPERATOR_ABORT", "command_execution_count": 0, "verification_run_status": "INCOMPLETE", "review_readiness": "NOT_READY_FOR_HUMAN_REVIEW", "reason_codes": ["EXPLICIT_OPERATOR_ABORT"]}
        finally:
            store.close()


def _v9_atomic_write(path: pathlib.Path, value: Mapping[str, Any]) -> str:
    data = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_bytes(data)
    with temporary.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return _sha_bytes(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the installed ACV Codex provider against a validated receipt.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "recover", "abort-incomplete"):
        command = subparsers.add_parser(name)
        command.add_argument("--receipt", required=True)
        command.add_argument("--subject-root", required=True)
        command.add_argument("--state-dir", required=True)
        command.add_argument("--result", required=True)
        if name == "recover":
            command.add_argument("--operation-id")
        elif name == "abort-incomplete":
            command.add_argument("--operation-id", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        subject = pathlib.Path(args.subject_root).resolve()
        result_path = pathlib.Path(args.result).resolve()
        _assert_external(result_path, subject, "RESULT_INSIDE_SUBJECT")
        provider = CodexProvider()
        if args.command == "recover":
            result = provider.recover(pathlib.Path(args.receipt), subject, pathlib.Path(args.state_dir), args.operation_id)
        elif args.command == "abort-incomplete":
            result = provider.abort_incomplete(pathlib.Path(args.receipt), subject, pathlib.Path(args.state_dir), args.operation_id)
        else:
            result = provider.run(pathlib.Path(args.receipt), subject, pathlib.Path(args.state_dir))
        result_digest = _v9_atomic_write(result_path, result)
        operation_id = result.get("operation_id")
        if operation_id and args.command != "abort-incomplete":
            finalize_module, _, _ = provider._load_core()
            with finalize_module.DecisionStore(pathlib.Path(args.state_dir).resolve() / "decisions.sqlite3") as store:
                store.mark_projection_published(operation_id, result_digest)
        print(json.dumps({"verification_run_status": result.get("verification_run_status"), "review_readiness": result.get("review_readiness"), "current_readiness": result.get("current_readiness")}, ensure_ascii=True, sort_keys=True))
        current = result.get("current_readiness") or {}
        return 2 if args.command == "abort-incomplete" else (0 if current.get("state") == "CURRENT_READY" else 2)
    except (ProviderError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print("CODEX_PROVIDER_ERROR = " + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print("CODEX_PROVIDER_ERROR = UNEXPECTED_" + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
