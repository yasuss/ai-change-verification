import importlib.util
import json
import pathlib
import tempfile
import unittest


PUBLIC = pathlib.Path(__file__).resolve().parents[1]
BRIDGE_PATH = PUBLIC / "integrations/claude-code/acv_claude_bridge.py"
SETUP_PATH = PUBLIC / "integrations/codex-app-server/setup_provider.py"
MCP_PATH = PUBLIC / "integrations/claude-code/acv_claude_mcp_server.mjs"
WINDOWS_SETUP_PATH = PUBLIC / "integrations/claude-code/setup_windows_sandbox.mjs"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load("acv_claude_bridge_test", BRIDGE_PATH)
setup = load("acv_codex_setup_test", SETUP_PATH)


class ClaudeProviderContractTests(unittest.TestCase):
    def test_mcp_tool_schema_is_receipt_only(self):
        source = MCP_PATH.read_text(encoding="utf-8")
        schema = source[source.index("inputSchema"):source.index("inputSchema") + 260]
        self.assertIn("receipt_path", schema)
        for forbidden in ("command", "argv", "cwd", "state_dir", "provider_profile", "provider_id", "provider_realization_id", "binary_path", "python_path", "node_path", "readiness"):
            self.assertNotIn(forbidden, schema)

    def test_receipt_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "receipt.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(bridge.BridgeError):
                bridge._safe_subject_file(root, "../receipt.json")

    def test_symlink_escape_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); outside = pathlib.Path(temp).parent / (root.name + "-outside")
            outside.mkdir()
            try:
                (outside / "receipt.json").write_text("{}", encoding="utf-8")
                (root / "link.json").symlink_to(outside / "receipt.json")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(bridge.BridgeError):
                bridge._safe_subject_file(root, "link.json")

    def test_runtime_configuration_is_operator_owned(self):
        source = MCP_PATH.read_text(encoding="utf-8")
        self.assertIn("CLAUDE_PLUGIN_ROOT", source)
        self.assertIn("CLAUDE_PLUGIN_DATA", source)
        self.assertIn("ACV_PYTHON_PATH", source)
        self.assertIn("TRUSTED_PATH_INSIDE_SUBJECT", source)
        self.assertIn("process.execPath", source)
        self.assertIn("realpathSync(process.execPath)", source)

    def test_plugin_user_config_is_required_for_trusted_runtimes(self):
        plugin = json.loads((PUBLIC / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        for key in ("node_path", "python_path"):
            self.assertEqual(plugin["userConfig"][key]["type"], "file")
            self.assertTrue(plugin["userConfig"][key]["required"])

    def test_mcp_uses_user_config_not_ambient_runtime_paths(self):
        mcp = json.loads((PUBLIC / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["acv"]
        self.assertEqual(mcp["command"], "${user_config.node_path}")
        self.assertEqual(mcp["env"]["ACV_PYTHON_PATH"], "${user_config.python_path}")
        self.assertNotIn("${ACV_NODE_PATH}", json.dumps(mcp))
        self.assertNotIn("${ACV_PYTHON_PATH}", json.dumps(mcp))

    def test_trusted_provider_minimum_is_documented(self):
        docs = (PUBLIC / "docs/claude-code.md").read_text(encoding="utf-8")
        integration = (PUBLIC / "integrations/claude-code/README.md").read_text(encoding="utf-8")
        self.assertIn("Claude Code >= 2.1.207", docs)
        self.assertIn("Claude Code >= 2.1.207", integration)

    def test_srt_missing_and_malformed_output_fail_closed(self):
        source = MCP_PATH.read_text(encoding="utf-8")
        self.assertIn("SRT_UNAVAILABLE", source)
        self.assertIn("MALFORMED_BRIDGE_OUTPUT", source)
        self.assertIn("NON_AUTHORITATIVE", source)
        self.assertNotIn("shell:true", source)
        self.assertIn("shell:false", source)

    def test_claude_expected_lane_and_builtin_sandbox_boundary(self):
        compatibility = (PUBLIC / "docs/compatibility.md").read_text(encoding="utf-8")
        claude = (PUBLIC / "docs/claude-code.md").read_text(encoding="utf-8")
        expected_section = compatibility.split("**EXPECTED / not independently host-verified**", 1)[1]
        self.assertIn("**EXPECTED / not independently host-verified**", compatibility)
        self.assertIn("Claude Code host lanes", expected_section)
        tested_section = compatibility.split("**EXPECTED / not independently host-verified**", 1)[0]
        self.assertNotIn("Claude Code", tested_section)
        self.assertIn("alpha SRT dependency", claude)
        self.assertIn("built-in sandbox does not support native Windows", claude)
        self.assertIn("awaiting independent host evidence", claude)

    def test_windows_setup_is_explicit_and_exact(self):
        source = WINDOWS_SETUP_PATH.read_text(encoding="utf-8")
        for token in ("--status", "--install", "--uninstall", "checkWindowsSandboxStatusAsync", "installWindowsSandboxAsync", "uninstallWindowsSandbox", "EXPECTED_SRT_VERSION", "return \"--status\""):
            self.assertIn(token, source)
        self.assertNotIn("npx", source.lower())
        self.assertNotIn("shell:", source.lower())

    def test_portable_mode_keeps_optional_host_dependencies_optional(self):
        quick = (PUBLIC / "docs/quick-start.md").read_text(encoding="utf-8")
        portable = quick.split("## Codex provider", 1)[0]
        self.assertIn("does not require Codex, Claude Code, Node, npm, SRT, MCP SDK", portable)

    def test_provider_uses_direct_pinned_srt_cli_not_npm_shim(self):
        source = MCP_PATH.read_text(encoding="utf-8")
        self.assertIn('require.resolve("@anthropic-ai/sandbox-runtime/dist/cli.js")', source)
        self.assertNotIn("node_modules/.bin", source)
        self.assertNotIn("npx", source.lower())

    def test_no_unsandboxed_fallback_or_raw_srt_command_mode(self):
        source = MCP_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[\"']-c[\"']")
        self.assertIn('"-s"', source)
        self.assertIn("fixedPolicy", source)

    def test_literal_argv_and_output_cap_are_bounded(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        for literal in ("shell=False", "MAX_OUTPUT_BYTES", "capture_output=True", "argv"):
            self.assertIn(literal, source)
        text, clipped = bridge._bounded(("spaces ; $() quotes backslash unicode ✓" * 100000).encode("utf-8"))
        self.assertTrue(clipped)
        self.assertLessEqual(len(text.encode("utf-8")), bridge.MAX_OUTPUT_BYTES * 2)

    def test_exit7_is_not_ready(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn('"FAIL"', source)
        self.assertIn("NOT_READY_FOR_HUMAN_REVIEW", source)
        self.assertIn("NOT_CURRENT_READY", source)

    def test_canonical_loader_registers_module_before_exec(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn("sys.modules[name] = module", source)
        self.assertIn("finalize_verification.py", source)

    def test_hooks_are_not_authoritative(self):
        plugin = json.loads((PUBLIC / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("hooks", plugin)
        self.assertIn("canonical_finalizer", BRIDGE_PATH.read_text(encoding="utf-8"))

    def test_codex_setup_is_external_and_platform_aware(self):
        source = SETUP_PATH.read_text(encoding="utf-8")
        self.assertIn("resolve_codex_path", source)
        self.assertIn("PROVIDER_INSTALL_INSIDE_SUBJECT", source)
        self.assertIn("platform.system", source)
        self.assertIn("_atomic_write", source)
        self.assertNotIn("--command", source)
        self.assertNotIn("--argv", source)

    def test_codex_setup_has_executed_probe_verifier(self):
        source = SETUP_PATH.read_text(encoding="utf-8")
        for marker in ("initialize", "configRequirements/read", "command/exec", "sys.exit(7)", "outputBytesCap", "capReached", "provider_preflight.json", "provider_preflight.md", "validate_enrollment_evidence", "--replace"):
            self.assertIn(marker, source)

    def test_codex_setup_writes_profile_only_after_probe_and_preflight(self):
        source = SETUP_PATH.read_text(encoding="utf-8")
        self.assertLess(source.index("_probe_app_server"), source.index('_atomic_write(provider_dir / "provider_profile.json"'))
        self.assertNotIn("OPERATOR_PREFLIGHT_REQUIRED", source)

    def test_codex_setup_verifier_rejects_unexecuted_capability_claims(self):
        profile = {"preflight_md_sha256": "a" * 64, "preflight_json_sha256": "b" * 64, "config_requirements_sha256": "c" * 64, "provider_source_sha256": "d" * 64, "schema_digest_excluded_paths": ["json/codex_app_server_protocol.v2.schemas.json"], "tested_capabilities": {key: True for key in ("command_exec", "output_delta", "capReached", "cwd", "terminal_exit_code", "config_requirements")}}
        preflight = {"schema_digest_excluded_paths": ["json/codex_app_server_protocol.v2.schemas.json"], "probes": {"command_exec_pass": {"exit_code": 0}, "command_exec_exit7": {"exit_code": 7}, "cwd": {"requested": "C:/probe", "observed": "C:/probe"}, "output_delta": {"observed": True}, "capReached": {"observed": True}}}
        setup.validate_enrollment_evidence(profile, preflight)
        for key in ("command_exec", "output_delta", "capReached", "cwd", "terminal_exit_code", "config_requirements"):
            changed = dict(profile, tested_capabilities=dict(profile["tested_capabilities"], **{key: False}))
            with self.assertRaises(setup.SetupError):
                setup.validate_enrollment_evidence(changed, preflight)

    def test_codex_setup_verifier_rejects_exit7_cwd_cap_and_placeholder_mutants(self):
        profile = {"preflight_md_sha256": "a" * 64, "preflight_json_sha256": "b" * 64, "config_requirements_sha256": "c" * 64, "provider_source_sha256": "d" * 64, "schema_digest_excluded_paths": ["json/codex_app_server_protocol.v2.schemas.json"], "tested_capabilities": {key: True for key in ("command_exec", "output_delta", "capReached", "cwd", "terminal_exit_code", "config_requirements")}}
        preflight = {"schema_digest_excluded_paths": ["json/codex_app_server_protocol.v2.schemas.json"], "probes": {"command_exec_pass": {"exit_code": 0}, "command_exec_exit7": {"exit_code": 7}, "cwd": {"requested": "C:/probe", "observed": "C:/probe"}, "output_delta": {"observed": True}, "capReached": {"observed": True}}}
        for mutant in (dict(preflight, probes=dict(preflight["probes"], command_exec_exit7={"exit_code": 0})), dict(preflight, probes=dict(preflight["probes"], cwd={"requested": "C:/probe", "observed": "C:/other"})), dict(preflight, probes=dict(preflight["probes"], capReached={"observed": False})), dict(profile, preflight_md_sha256="OPERATOR_PREFLIGHT_REQUIRED")):
            with self.assertRaises(setup.SetupError):
                setup.validate_enrollment_evidence(mutant if "tested_capabilities" in mutant else profile, mutant if "probes" in mutant else preflight)

    def test_codex_setup_rejects_platform_mismatch_and_subject_local_install(self):
        source = SETUP_PATH.read_text(encoding="utf-8")
        self.assertIn("PLATFORM_FACT_MISMATCH", source)
        self.assertIn("PROVIDER_INSTALL_INSIDE_SUBJECT", source)

    def test_codex_target_local_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); executable = root / "codex"
            executable.write_text("fake", encoding="utf-8")
            with self.assertRaises(setup.SetupError):
                setup.resolve_codex_path(str(executable), root)

    def test_provider_realization_binds_decision_files(self):
        realization = json.loads((PUBLIC / "integrations/claude-code/provider-realization.json").read_text(encoding="utf-8"))
        required = {".claude-plugin/plugin.json", ".mcp.json", "package.json", "package-lock.json", "integrations/claude-code/acv_claude_mcp_server.mjs", "integrations/claude-code/acv_claude_bridge.py"}
        self.assertTrue(required.issubset(realization["files"]))

    def test_portable_mode_does_not_depend_on_trusted_provider(self):
        quick = (PUBLIC / "docs/quick-start.md").read_text(encoding="utf-8")
        portable = quick.split("## Codex provider", 1)[0].lower()
        self.assertIn("does not require codex, claude code, node, npm, srt, mcp sdk", portable)

if __name__ == "__main__":
    unittest.main()
