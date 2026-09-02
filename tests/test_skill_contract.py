import json
import pathlib
import unittest


PUBLIC_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = PUBLIC_ROOT / "skills/ai-change-verification/SKILL.md"


class SkillContractTests(unittest.TestCase):
    def test_frontmatter_is_exact_and_references_are_present(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: ai-change-verification\n"))
        header = text.split("---", 2)[1]
        self.assertEqual({line.split(":", 1)[0] for line in header.strip().splitlines()}, {"name", "description", "license"})
        self.assertIn("license: Apache-2.0", header)
        self.assertLessEqual(len(text.splitlines()), 500)
        names = [
            "evidence-model.md", "scope-and-obligations.md", "risk-context-and-impact.md",
            "verification-plan-and-execution.md", "verification-surface-integrity.md",
            "pre-review-and-adjudication.md", "report-contract.md", "receipt-contract.md",
            "readiness-rules.md", "security-and-trust.md",
        ]
        for name in names:
            self.assertIn("references/" + name, text)

    def test_public_contract_vocabulary_is_visible(self):
        text = SKILL.read_text(encoding="utf-8")
        for token in ("Scope Closure", "Verification Plan", "Observed Verification", "EVIDENCE_LINKED", "UNRESOLVED_RISK", "Human Attention", "machine-readable receipt", "check-contract"):
            self.assertIn(token, text)

    def test_receipt_schema_is_json_and_closed(self):
        schema = json.loads((PUBLIC_ROOT / "skills/ai-change-verification/schemas/verification-receipt.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        for name in ("evidence_command_execution", "evidence_tool_observation", "evidence_external_result", "evidence_interpretation"):
            self.assertFalse(schema["$defs"][name]["additionalProperties"])
