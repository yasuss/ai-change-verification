import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const EXPECTED_SRT_VERSION = "0.0.74";
const require = createRequire(import.meta.url);

function packageRoot() {
  const entry = require.resolve("@anthropic-ai/sandbox-runtime");
  return path.resolve(path.dirname(entry), "..");
}

function exactSrtVersion() {
  const packageFile = path.join(packageRoot(), "package.json");
  const version = JSON.parse(fs.readFileSync(packageFile, "utf8")).version;
  if (version !== EXPECTED_SRT_VERSION) throw new Error("SRT_VERSION_DRIFT");
  return version;
}

function bundledSrtWinPath() {
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  const executable = path.join(packageRoot(), "vendor", "srt-win", arch, "srt-win.exe");
  if (!fs.statSync(executable).isFile()) throw new Error("SRT_WINDOWS_BINARY_MISSING");
  return executable;
}

function mode(argv) {
  const selected = argv.slice(2).filter((value) => value.startsWith("--"));
  if (selected.length === 0) return "--status";
  if (selected.length !== 1 || !["--status", "--install", "--uninstall"].includes(selected[0])) throw new Error("INVALID_MODE");
  return selected[0];
}

async function main() {
  const selected = mode(process.argv);
  const version = exactSrtVersion();
  if (process.platform !== "win32") {
    if (selected !== "--status") throw new Error("WINDOWS_MODE_ON_NON_WINDOWS");
    console.log(JSON.stringify({ platform: process.platform, status: "NOT_APPLICABLE", srt_version: version }));
    return;
  }
  const srt = await import("@anthropic-ai/sandbox-runtime");
  const srtWin = srt.resolveSrtWin({ path: bundledSrtWinPath() });
  if (selected === "--status") {
    const status = await srt.checkWindowsSandboxStatusAsync({ srtWin });
    console.log(JSON.stringify({ platform: process.platform, srt_version: version, status }));
    return;
  }
  console.error(`SYSTEM STATE CHANGE: ${selected} for pinned SRT ${version}; explicit operator/UAC action required.`);
  if (selected === "--install") {
    const result = await srt.installWindowsSandboxAsync({ srtWin });
    console.log(JSON.stringify({ mode: selected, srt_version: version, result }));
    return;
  }
  const result = srt.uninstallWindowsSandbox({ srtWin });
  console.log(JSON.stringify({ mode: selected, srt_version: version, result }));
}

main().catch((error) => { console.error(error instanceof Error ? error.message : "WINDOWS_SANDBOX_SETUP_FAILED"); process.exitCode = 1; });
