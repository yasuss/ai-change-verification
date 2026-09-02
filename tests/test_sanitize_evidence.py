import json
import os
import pathlib
import subprocess
import sys
import unittest


PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PUBLIC_ROOT / "skills/ai-change-verification/scripts/sanitize_evidence.py"


class SanitizeEvidenceTests(unittest.TestCase):
    def run_sanitizer(self, data, *args, encoding=None):
        env = os.environ.copy()
        if encoding:
            env["PYTHONIOENCODING"] = encoding
        result = subprocess.run([sys.executable, str(SCRIPT), *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=True)
        return json.loads(result.stdout.decode("ascii"))

    def test_secret_redaction_and_digest(self):
        result = self.run_sanitizer(b"token=ghp_abcdefghijklmnopqrstuvwxyz123456\n")
        self.assertTrue(result["redacted"])
        self.assertNotIn("ghp_", result["text"])
        self.assertEqual(len(result["sanitized_sha256"]), 64)

    def test_caps_are_bounded(self):
        result = self.run_sanitizer(b"one\ntwo\nthree\n", "--max-lines", "2", "--max-bytes", "100")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode("utf-8")), 100)

    def test_ansi_osc_and_bidi_are_neutralized(self):
        data = "normal\x1b[31m red\x1b[0m\x1b]8;;https://evil.example\x07link\u202e\n".encode("utf-8")
        result = self.run_sanitizer(data)
        self.assertTrue(result["control_sequences_neutralized"])
        self.assertNotIn("\x1b", result["text"])
        self.assertNotIn("\u202e", result["text"])

    def test_invalid_utf8_is_replaced(self):
        result = self.run_sanitizer(b"ok\xff\n")
        self.assertTrue(result["invalid_text_replaced"])
        self.assertIn("�", result["text"])

    def test_non_utf8_stdout_preserves_unicode_logical_text(self):
        result = self.run_sanitizer("Привет\n".encode("utf-8"), encoding="cp1252")
        self.assertIn("Привет", result["text"])
