import hashlib
import json
import pathlib
import re
import unittest


PUBLIC = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_BADGE = "[![CI](https://github.com/yasuss/ai-change-verification/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/yasuss/ai-change-verification/actions/workflows/ci.yml)"
EXPECTED_CI = """name: public-product

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: '3.11'
      - uses: actions/setup-node@v7
        with:
          node-version: '24'
          package-manager-cache: false
      - name: Run deterministic Python checks
        run: python -B -m unittest discover -s tests -p 'test_*.py'
      - name: Install locked Node dependencies
        run: npm ci --ignore-scripts
      - name: Check Claude MCP syntax
        run: node --check integrations/claude-code/acv_claude_mcp_server.mjs
"""
EXPECTED_DOCS_SHA = "b6ccb1e810096484f1c0dcd38e539ecd20cf32cebc063fdb42fdc1931e1a0fe5"
EXPECTED_SECURITY_SHA = "f8ffe559db66924144505c25ab8124729e043e204927309a8073199dd373db9a"
FORBIDDEN_INTERNAL_VOCABULARY = re.compile(r"(?i)(?<![A-Za-z])(?:M-[A-Z]+-\d+|H\d+|RC|verifier[- ]of[- ]verifier|hostile|circuit[- ]breaker)(?![A-Za-z])")


class PublicProductContractTests(unittest.TestCase):
    def test_required_public_surface_and_deleted_paths(self):
        required = [
            "README.md", "LICENSE", "SECURITY.md", ".gitattributes", ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json", ".mcp.json", "package.json", "package-lock.json",
            "docs/quick-start.md", "docs/compatibility.md", "docs/limitations.md",
            "docs/codex.md", "docs/claude-code.md", "examples/before-after/README.md",
            "examples/before-after/before.md", "examples/before-after/after-review-readiness-report.md",
            ".github/workflows/ci.yml", "integrations/codex-app-server/setup_provider.py",
            "integrations/claude-code/acv_claude_mcp_server.mjs",
            "integrations/claude-code/acv_claude_bridge.py",
            "integrations/claude-code/setup_windows_sandbox.mjs",
            "integrations/claude-code/provider-realization.json",
            "skills/ai-change-verification/references/stage-b-core-realization.json",
        ]
        for relative in required:
            self.assertTrue((PUBLIC / relative).is_file(), relative)
        for relative in (
            "docs/testing/README.md", "docs/testing/codex-macos.md",
            "docs/testing/claude-macos.md", "docs/testing/claude-windows-native.md",
            "docs/post-release-compatibility-test.md",
            "examples/compatibility-test-return.template.json",
        ):
            self.assertFalse((PUBLIC / relative).exists(), relative)

    def test_gitattributes_exact_checkout_policy(self):
        self.assertEqual((PUBLIC / ".gitattributes").read_text(encoding="utf-8"), "* text=auto eol=lf\n")

    def test_readme_public_compatibility_and_badge(self):
        text = (PUBLIC / "README.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# ai-change-verification\n\n" + EXPECTED_BADGE + "\n"))
        self.assertEqual(text.count(EXPECTED_BADGE), 1)
        compatibility = """## Compatibility

**TESTED for v0.1.0**
- Portable Core — Ubuntu CI

**EXPECTED / not independently host-verified**
- Codex App Server — Windows trusted-host realization
  *(authoritative enrollment is not enabled in v0.1.0)*
- macOS lanes
- Claude Code host lanes

See [Compatibility details](docs/compatibility.md), [Quick Start](docs/quick-start.md) and [Limitations](docs/limitations.md) for details.
"""
        self.assertEqual(text.count(compatibility), 1)
        self.assertNotIn("## Expected Compatibility", text)
        self.assertNotIn("| Environment |", text)
        self.assertNotIn("Claude Code's built-in sandbox does not support native Windows", text)
        self.assertNotIn("NOT_CLAIMED", text)
        self.assertNotIn("other compatible Agent Skills environments", text)
        self.assertEqual(text.count("See [Compatibility details](docs/compatibility.md), [Quick Start](docs/quick-start.md) and [Limitations](docs/limitations.md) for details."), 1)
        self.assertIn("Quick Start", text)
        self.assertIn("Review Readiness is not approval", text)
        self.assertNotIn("it-support@lauensteinone.de", text)
        self.assertNotIn("Codex App Server — Windows, exact enrolled runtime", text)

    def test_docs_compatibility_is_exact_contract(self):
        path = PUBLIC / "docs/compatibility.md"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), EXPECTED_DOCS_SHA)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("**TESTED for v0.1.0**"), 1)
        self.assertEqual(text.count("**EXPECTED / not independently host-verified**"), 1)
        self.assertIn("- Codex App Server — Windows trusted-host realization", text)
        self.assertNotIn("Codex App Server — Windows, exact enrolled runtime", text)
        self.assertNotIn("# Expected Compatibility", text)

    def test_windows_codex_lane_is_expected_and_fail_closed(self):
        readme = (PUBLIC / "README.md").read_text(encoding="utf-8")
        quick_start = (PUBLIC / "docs/quick-start.md").read_text(encoding="utf-8")
        codex = (PUBLIC / "docs/codex.md").read_text(encoding="utf-8")
        setup = (PUBLIC / "integrations/codex-app-server/setup_provider.py").read_text(encoding="utf-8")
        provider = (PUBLIC / "integrations/codex-app-server/acv_codex_provider.py").read_text(encoding="utf-8")
        marker = "WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0"
        self.assertIn("- Codex App Server — Windows trusted-host realization\n  *(authoritative enrollment is not enabled in v0.1.0)*", readme)
        self.assertIn("Windows trusted-host realization is documented as EXPECTED", quick_start)
        self.assertIn("Windows trusted-host realization is EXPECTED / not independently host-verified", codex)
        self.assertNotIn(marker, quick_start)
        self.assertNotIn(marker, codex)
        self.assertIn(marker, setup)
        self.assertIn(marker, provider)
        self.assertNotIn("windowsSandbox/setupStart", setup)
        self.assertEqual((PUBLIC / ".github/workflows/ci.yml").read_text(encoding="utf-8").count("workflow_dispatch:"), 1)

    def test_claude_warning_is_adjacent_to_install_block(self):
        text = (PUBLIC / "README.md").read_text(encoding="utf-8")
        warning = "> v0.1.0 Claude host lanes are EXPECTED / not independently host-verified."
        command = "claude plugin marketplace add yasuss/ai-change-verification"
        self.assertEqual(text.count(warning), 1)
        self.assertEqual(text.count(command), 1)
        self.assertIn(warning + "\n\n```bash\n" + command, text)

    def test_public_copy_has_no_release_forensics(self):
        surfaces = [
            PUBLIC / "README.md", PUBLIC / "docs/compatibility.md", PUBLIC / "docs/quick-start.md",
            PUBLIC / "docs/codex.md", PUBLIC / "docs/limitations.md",
            PUBLIC / "integrations/codex-app-server/README.md",
        ]
        forbidden = re.compile(r"(?i)(fail.?closed|qualification (?:failed|did not complete)|did not complete successfully|release[- ]host|could not complete|CreateRestrictedToken failed|RestrictedToken|(?:R3|R4|R5|R6) recovery|WINDOWS_TRUSTED_REALIZATION_NOT_VERIFIED_V0_1_0)")
        for path in surfaces:
            self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), str(path))

    def test_security_public_contract(self):
        security = PUBLIC / "SECURITY.md"
        self.assertEqual(hashlib.sha256(security.read_bytes()).hexdigest(), EXPECTED_SECURITY_SHA)
        security_text = security.read_text(encoding="utf-8")
        readme = (PUBLIC / "README.md").read_text(encoding="utf-8")
        self.assertEqual(readme.count("[SECURITY.md](SECURITY.md)"), 1)
        self.assertEqual(security_text.count("it-support@lauensteinone.de"), 1)
        self.assertNotIn("public GitHub Issues", readme)
        self.assertNotIn("SLA", readme)
        self.assertIn("public github issue", security_text.lower())
        for relative in re.findall(r"\]\(([^)#]+)", security_text):
            if not relative.startswith(("http:", "https:", "mailto:")):
                self.assertTrue((security.parent / relative).resolve().is_file(), relative)

    def test_readme_attribution_is_present_once(self):
        text = (PUBLIC / "README.md").read_text(encoding="utf-8")
        url = "https://lauensteinone.de/en/ai-engineering-sprint/"
        paragraph = "For teams where the problem extends beyond a single change into repository context, CI gates, review workflow and sign-off, this is the work we do at [LAUENSTEIN One](" + url + ")."
        self.assertEqual(text.count(url), 1)
        self.assertEqual(text.count(paragraph), 1)

    def test_user_facing_docs_do_not_expose_internal_campaign_vocabulary(self):
        surfaces = [PUBLIC / "docs/quick-start.md", PUBLIC / "docs/limitations.md", PUBLIC / "docs/codex.md", PUBLIC / "docs/claude-code.md"]
        surfaces.extend((PUBLIC / "integrations").glob("*/README.md"))
        surfaces.extend((PUBLIC / "examples").rglob("*.md"))
        for path in surfaces:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(FORBIDDEN_INTERNAL_VOCABULARY.search(text), str(path))
            lowered = text.lower()
            for phrase in ("user-tested", "smoke test", "ci host matrix", "external host guides", "post-release compatibility test"):
                self.assertNotIn(phrase, lowered, str(path))

    def test_example_and_all_public_markdown_links_resolve(self):
        surfaces = [PUBLIC / "README.md", *((PUBLIC / "docs").glob("*.md"))]
        surfaces.extend((PUBLIC / "integrations").glob("*/README.md"))
        surfaces.extend((PUBLIC / "examples").rglob("*.md"))
        for doc in surfaces:
            for link in re.findall(r"\]\(([^)#]+)", doc.read_text(encoding="utf-8")):
                if link.startswith(("http:", "https:", "mailto:")):
                    continue
                self.assertTrue((doc.parent / link).resolve().is_file(), (doc, link))

    def test_ci_is_one_minimal_non_deployment_job(self):
        ci = (PUBLIC / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(ci, EXPECTED_CI)
        self.assertNotIn("matrix", ci)
        self.assertNotIn("windows-latest", ci)
        self.assertNotIn("macos-latest", ci)
        self.assertNotIn("git push", ci)
        self.assertNotIn("deploy", ci.lower())

    def test_exact_dependency_pins_and_lock(self):
        package = json.loads((PUBLIC / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((PUBLIC / "package-lock.json").read_text(encoding="utf-8"))
        expected = {"@anthropic-ai/sandbox-runtime": "0.0.74", "@modelcontextprotocol/sdk": "1.30.0"}
        self.assertEqual(package["dependencies"], expected)
        self.assertEqual(lock["packages"][""]["dependencies"], expected)
        self.assertEqual(lock["packages"]["node_modules/@anthropic-ai/sandbox-runtime"]["version"], "0.0.74")
        self.assertEqual(lock["packages"]["node_modules/@modelcontextprotocol/sdk"]["version"], "1.30.0")

    def test_canonical_skill_is_vendor_neutral(self):
        skill = (PUBLIC / "skills/ai-change-verification/SKILL.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("codex", skill)
        self.assertNotIn("claude", skill)

    def test_realization_hashes_match(self):
        realization = json.loads((PUBLIC / "integrations/claude-code/provider-realization.json").read_text(encoding="utf-8"))
        for relative, expected in realization["files"].items():
            self.assertEqual(hashlib.sha256((PUBLIC / relative).read_bytes()).hexdigest(), expected, relative)

    def test_stage_b_core_realization_manifest_is_exact(self):
        path = PUBLIC / "skills/ai-change-verification/references/stage-b-core-realization.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], "ACV_STAGE_B_CORE_REALIZATION_MANIFEST_V1")
        self.assertEqual(payload["domain"], "ACV-STAGE-B-CORE-REALIZATION-v1")
        self.assertEqual(payload["files"], [
            {"path": "scripts/finalize_verification.py", "role": "stage_b_authority"},
            {"path": "scripts/validate_receipt.py", "role": "stage_a_decision_validator"},
            {"path": "scripts/change_snapshot.py", "role": "subject_snapshot"},
            {"path": "schemas/verification-receipt.schema.json", "role": "receipt_schema"},
            {"path": "references/stage-b-live-verification.md", "role": "normative_stage_b_contract"},
        ])


if __name__ == "__main__":
    unittest.main()
