import importlib.util
import pathlib
import subprocess
import tempfile
import unittest


PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PUBLIC_ROOT / "skills/ai-change-verification/scripts/change_snapshot.py"


def load_snapshot():
    spec = importlib.util.spec_from_file_location("acv_change_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


class ChangeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.module = load_snapshot()

    def repo(self):
        td = tempfile.TemporaryDirectory()
        root = pathlib.Path(td.name)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.name", "ACV Tests")
        git(root, "config", "user.email", "acv-tests@invalid.local")
        (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (root / "tracked.txt").write_text("one\n", encoding="utf-8")
        git(root, "add", ".gitignore", "tracked.txt")
        git(root, "commit", "-q", "-m", "base")
        return td, root

    def test_stable_unchanged_repeat(self):
        td, root = self.repo()
        with td:
            first = self.module.capture_snapshot(root)
            second = self.module.capture_snapshot(root)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertTrue(first["complete"])

    def test_tracked_content_and_staging_change_identity(self):
        td, root = self.repo()
        with td:
            first = self.module.capture_snapshot(root)
            (root / "tracked.txt").write_text("two\n", encoding="utf-8")
            changed = self.module.capture_snapshot(root)
            git(root, "add", "tracked.txt")
            staged = self.module.capture_snapshot(root)
        self.assertNotEqual(first["snapshot_id"], changed["snapshot_id"])
        self.assertNotEqual(changed["snapshot_id"], staged["snapshot_id"])
        self.assertIn("tracked.txt", changed["unstaged_paths"])
        self.assertIn("tracked.txt", staged["staged_paths"])

    def test_untracked_add_and_content_change_identity(self):
        td, root = self.repo()
        with td:
            first = self.module.capture_snapshot(root)
            (root / "new.txt").write_text("one\n", encoding="utf-8")
            added = self.module.capture_snapshot(root)
            (root / "new.txt").write_text("two\n", encoding="utf-8")
            changed = self.module.capture_snapshot(root)
        self.assertNotEqual(first["snapshot_id"], added["snapshot_id"])
        self.assertNotEqual(added["snapshot_id"], changed["snapshot_id"])
        self.assertEqual(changed["untracked_paths"], ["new.txt"])

    def test_ignored_file_does_not_change_identity(self):
        td, root = self.repo()
        with td:
            first = self.module.capture_snapshot(root)
            (root / "ignored.txt").write_text("secret\n", encoding="utf-8")
            second = self.module.capture_snapshot(root)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_explicit_base_and_option_like_base(self):
        td, root = self.repo()
        with td:
            result = self.module.capture_snapshot(root, "HEAD")
            self.assertEqual(result["base_ref"], "HEAD")
            with self.assertRaises(ValueError):
                self.module.capture_snapshot(root, "--bad")

    def test_helper_does_not_mutate_repository(self):
        td, root = self.repo()
        with td:
            before = git(root, "status", "--porcelain=v1", "--untracked-files=all")
            self.module.capture_snapshot(root)
            after = git(root, "status", "--porcelain=v1", "--untracked-files=all")
        self.assertEqual(before, after)

    def test_symlink_target_is_not_followed_when_supported(self):
        td, root = self.repo()
        with td:
            link = root / "link.txt"
            try:
                link.symlink_to(root / "tracked.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            result = self.module.capture_snapshot(root)
        self.assertFalse(result["complete"])
        self.assertTrue(any("NON_REGULAR:link.txt" in item for item in result["limitations"]))

    def test_deterministic_injected_state_drift_is_incomplete(self):
        original = self.module._capture_once
        counter = {"value": 0}

        def alternating(_repo, _base):
            counter["value"] += 1
            return {"snapshot_id": str(counter["value"]), "complete": True, "limitations": []}

        self.module._capture_once = alternating
        try:
            result = self.module.capture_snapshot(pathlib.Path("."), None, max_attempts=2)
        finally:
            self.module._capture_once = original
        self.assertFalse(result["complete"])
        self.assertIn("STATE_CHANGED_DURING_CAPTURE", result["limitations"])
