"""Receipt-only Claude bridge; intended to run under the pinned SRT."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import uuid
from typing import Any

MAX_OUTPUT_BYTES = 1024 * 1024
class BridgeError(RuntimeError): pass

def _load_module(name: str, path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None: raise BridgeError("CANONICAL_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    if name in sys.modules: raise BridgeError("MODULE_NAME_COLLISION")
    sys.modules[name] = module
    try: spec.loader.exec_module(module)
    except BaseException: sys.modules.pop(name, None); raise
    return module

def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False

def _safe_subject_file(root: pathlib.Path, supplied: str) -> pathlib.Path:
    raw = pathlib.Path(supplied); candidate = (root / raw if not raw.is_absolute() else raw).resolve()
    if not _inside(candidate, root) or not candidate.is_file(): raise BridgeError("RECEIPT_MUST_BE_FILE_INSIDE_PROJECT")
    current = root.resolve()
    for part in candidate.relative_to(current).parts:
        current /= part
        if current.is_symlink() or (current.exists() and bool(getattr(current.stat(), "st_file_attributes", 0) & 0x0400)): raise BridgeError("RECEIPT_REPARSE_PATH_REJECTED")
    return candidate

def _bounded(data: bytes) -> tuple[str, bool]:
    return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), len(data) > MAX_OUTPUT_BYTES

def _canonical_core(root: pathlib.Path) -> tuple[Any, Any, Any]:
    skill = root / "skills" / "ai-change-verification" / "scripts"
    return tuple(_load_module("acv_claude_core_" + uuid.uuid4().hex, skill / name) for name in ("finalize_verification.py", "change_snapshot.py", "validate_receipt.py"))

def verify_receipt(receipt_path: str) -> dict[str, Any]:
    project_value = os.environ.get("CLAUDE_PROJECT_DIR"); plugin_value = os.environ.get("CLAUDE_PLUGIN_ROOT"); data_value = os.environ.get("CLAUDE_PLUGIN_DATA"); python_value = os.environ.get("ACV_PYTHON_PATH")
    if not project_value or not plugin_value or not data_value or not python_value: raise BridgeError("OPERATOR_RUNTIME_CONFIGURATION_MISSING")
    project = pathlib.Path(project_value).resolve(); plugin = pathlib.Path(plugin_value).resolve(); data = pathlib.Path(data_value).resolve(); python = pathlib.Path(python_value).resolve()
    if _inside(plugin, project) or _inside(data, project): raise BridgeError("TRUSTED_ROOT_INSIDE_SUBJECT")
    if _inside(python, project) or not python.is_file(): raise BridgeError("PYTHON_RUNTIME_INVALID")
    receipt = _safe_subject_file(project, receipt_path); finalizer, snapshot, validator = _canonical_core(plugin)
    value = json.loads(receipt.read_text(encoding="utf-8")); validator.validate(value)
    before = snapshot.capture_snapshot(project)
    if not before.get("complete") or before.get("snapshot_id") != value["subject"]["subject_digest"]: raise BridgeError("PRE_SUBJECT_MISMATCH")
    selected = [c for c in value.get("verification_plan", []) if c.get("selected") is True]
    if not selected or any(c.get("operation_contract", {}).get("kind") != "COMMAND_EXECUTION" for c in selected): raise BridgeError("NO_QUALIFYING_COMMAND_CONTRACT")
    events = []
    for check in selected:
        operation = check["operation_contract"]; cwd = (project / pathlib.PurePath(operation["cwd"])).resolve()
        if not _inside(cwd, project) or not cwd.is_dir(): raise BridgeError("CWD_ESCAPES_SUBJECT")
        run = subprocess.run(operation["argv"], cwd=cwd, shell=False, capture_output=True, timeout=120, check=False)
        stdout, stdout_clipped = _bounded(run.stdout); stderr, stderr_clipped = _bounded(run.stderr)
        events.append({"invocation_id":"acv-invocation-"+uuid.uuid4().hex,"check_id":check["id"],"argv":list(operation["argv"]),"logical_cwd":operation["cwd"],"resolved_cwd":str(cwd),"exit_code":run.returncode,"outcome":"PASS" if run.returncode in set(check["result_interpretation"]["success_exit_codes"]) else "FAIL","stdout":stdout,"stderr":stderr,"stdout_clipped":stdout_clipped,"stderr_clipped":stderr_clipped})
    after = snapshot.capture_snapshot(project)
    if after.get("snapshot_id") != before.get("snapshot_id"): raise BridgeError("CURRENT_SUBJECT_CHANGED")
    return {"capture_status":"COMPLETE","provider":"claude-code","subject_before":before,"subject_after":after,"events":events,"canonical_finalizer":finalizer.__name__,"review_readiness":"NOT_READY_FOR_HUMAN_REVIEW" if any(e["outcome"] == "FAIL" for e in events) else "DELEGATE_TO_CANONICAL_STAGE_B","current_readiness":"NOT_CURRENT_READY" if any(e["outcome"] == "FAIL" for e in events) else "DELEGATE_TO_CANONICAL_STAGE_B"}

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed Claude ACV bridge."); parser.add_argument("--receipt", required=True); args = parser.parse_args()
    try: print(json.dumps(verify_receipt(args.receipt), ensure_ascii=True, sort_keys=True)); return 0
    except (BridgeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc: print(json.dumps({"capture_status":"UNAVAILABLE","provider_evidence":"NON_AUTHORITATIVE","error":str(exc)}, ensure_ascii=True, sort_keys=True)); return 2
if __name__ == "__main__": raise SystemExit(main())
