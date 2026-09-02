"""Read-only Git/worktree snapshot for bounded Scope Closure."""

import argparse
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
from typing import Optional

SCHEMA_VERSION = "1.0"
ALLOWED_GIT_SUBCOMMANDS = {"rev-parse", "symbolic-ref", "ls-files", "ls-tree", "merge-base"}


def _run_git(repo, subcommand, *args):
    if subcommand not in ALLOWED_GIT_SUBCOMMANDS:
        raise ValueError("git subcommand is not allowlisted")
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", subcommand, *args],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def _zpaths(raw):
    return [os.fsdecode(value) for value in raw.split(b"\0") if value]


def _index_records(raw):
    records = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        left, encoded_path = item.split(b"\t", 1)
        mode, object_id, stage = left.split()
        records.append((os.fsdecode(encoded_path), os.fsdecode(mode), os.fsdecode(object_id), os.fsdecode(stage)))
    return records


def _tree_records(raw):
    records = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        left, encoded_path = item.split(b"\t", 1)
        mode, kind, object_id = left.split()
        records[os.fsdecode(encoded_path)] = (os.fsdecode(mode), os.fsdecode(kind), os.fsdecode(object_id))
    return records


def _hash_regular(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {"path": path.as_posix(), "kind": "symlink"}, False
    if not stat.S_ISREG(info.st_mode):
        return {"path": path.as_posix(), "kind": "special"}, False
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": path.as_posix(), "size": info.st_size, "sha256": digest.hexdigest()}, True


def _surface_digest(repo, paths):
    records = []
    complete = True
    limitations = []
    for relative in sorted(set(paths)):
        record, regular = _hash_regular(repo / pathlib.PurePosixPath(relative))
        records.append(record)
        if not regular:
            complete = False
            limitations.append("NON_REGULAR:" + relative)
    encoded = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), complete, limitations


def _capture_once(repo: pathlib.Path, base_ref: Optional[str]) -> dict:
    repo = pathlib.Path(repo)
    index_raw = _run_git(repo, "ls-files", "-z", "--stage")
    cached_raw = _run_git(repo, "ls-files", "-z", "--cached")
    modified_raw = _run_git(repo, "ls-files", "-z", "--modified", "--deleted")
    untracked_raw = _run_git(repo, "ls-files", "-z", "--others", "--exclude-standard")
    index_records = _index_records(index_raw)
    cached_paths = _zpaths(cached_raw)
    modified_paths = _zpaths(modified_raw)
    untracked_paths = _zpaths(untracked_raw)
    limitations = []

    try:
        head_sha = os.fsdecode(_run_git(repo, "rev-parse", "--verify", "HEAD").strip())
    except subprocess.CalledProcessError:
        head_sha = None
    try:
        branch = os.fsdecode(_run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip())
    except subprocess.CalledProcessError:
        branch = None

    base_sha = None
    merge_base_sha = None
    if base_ref is not None:
        if not base_ref or base_ref.startswith("-"):
            raise ValueError("unsafe base ref")
        base_sha = os.fsdecode(_run_git(repo, "rev-parse", "--verify", base_ref + "^{commit}").strip())
        if head_sha:
            merge_base_sha = os.fsdecode(_run_git(repo, "merge-base", "--", head_sha, base_sha).strip())

    head_tree = {}
    if head_sha:
        head_tree = _tree_records(_run_git(repo, "ls-tree", "-r", "-z", "--full-tree", head_sha))
    staged_paths = []
    for relative, mode, object_id, stage in index_records:
        previous = head_tree.get(relative)
        if stage != "0" or previous is None or previous[0] != mode or previous[2] != object_id:
            staged_paths.append(relative)
        if mode == "160000":
            limitations.append("GITLINK_NOT_RECURSIVELY_INSPECTED:" + relative)

    worktree_digest, worktree_complete, worktree_limits = _surface_digest(repo, cached_paths)
    untracked_digest, untracked_complete, untracked_limits = _surface_digest(repo, untracked_paths)
    limitations.extend(worktree_limits)
    limitations.extend(untracked_limits)
    index_digest = hashlib.sha256(index_raw).hexdigest()
    state = {
        "schema_version": SCHEMA_VERSION,
        "scope_mode": "base-comparison" if base_ref is not None else "working-tree",
        "head_sha": head_sha,
        "branch": branch,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "merge_base_sha": merge_base_sha,
        "staged_paths": sorted(set(staged_paths)),
        "unstaged_paths": sorted(set(modified_paths)),
        "untracked_paths": sorted(set(untracked_paths)),
        "staged_count": len(set(staged_paths)),
        "unstaged_count": len(set(modified_paths)),
        "untracked_count": len(set(untracked_paths)),
        "index_state_sha256": index_digest,
        "worktree_state_sha256": worktree_digest,
        "untracked_state_sha256": untracked_digest,
        "complete": bool(worktree_complete and untracked_complete and not limitations),
        "limitations": sorted(set(limitations)),
    }
    identity_material = dict(state)
    state["snapshot_id"] = hashlib.sha256(
        json.dumps(identity_material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return state


def capture_snapshot(repo: pathlib.Path, base_ref: Optional[str] = None, max_attempts: int = 3) -> dict:
    if max_attempts < 2:
        raise ValueError("max_attempts must be at least two")
    previous = None
    for _attempt in range(max_attempts):
        current = _capture_once(repo, base_ref)
        if previous is not None and previous.get("snapshot_id") == current.get("snapshot_id"):
            return current
        previous = current
    result = dict(previous)
    result["complete"] = False
    result["limitations"] = sorted(set(list(result.get("limitations", [])) + ["STATE_CHANGED_DURING_CAPTURE"]))
    result["snapshot_id"] = hashlib.sha256(
        json.dumps({key: value for key, value in result.items() if key != "snapshot_id"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description="capture a read-only Git/worktree snapshot")
    parser.add_argument("--base")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = capture_snapshot(pathlib.Path.cwd(), args.base)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("SNAPSHOT_ERROR = " + type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=True, indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    main()
