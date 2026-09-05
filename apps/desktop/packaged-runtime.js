const fs = require("fs");
const path = require("path");

function resolvePackagedWorker(
  resourcesPath,
  expectedVersion,
  platform = process.platform,
) {
  const runtimeRoot = path.resolve(resourcesPath, "runtime");
  const manifestPath = path.join(runtimeRoot, "runtime-manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (manifest.schema !== 2) {
    throw new Error(`unsupported runtime manifest schema: ${manifest.schema}`);
  }
  if (
    typeof manifest.openprogram !== "string" ||
    manifest.openprogram !== expectedVersion
  ) {
    throw new Error(
      `runtime version mismatch: expected ${expectedVersion}, got ${manifest.openprogram}`,
    );
  }
  if (typeof manifest.python !== "string" || !manifest.python) {
    throw new Error("runtime manifest has no Python executable");
  }
  const python = path.resolve(runtimeRoot, manifest.python);
  const relative = path.relative(runtimeRoot, python);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("runtime manifest Python path escapes runtime resources");
  }
  if (!fs.existsSync(python)) {
    throw new Error(`embedded Python is missing: ${python}`);
  }
  let workerPython = python;
  if (platform === "win32") {
    workerPython = path.join(path.dirname(python), "pythonw.exe");
    if (!fs.existsSync(workerPython)) {
      throw new Error(`embedded windowless Python is missing: ${workerPython}`);
    }
  }
  const requiredCapabilities = [
    "web",
    "providers",
    "mcp",
    "memory",
    "channels",
    "search",
    "tui.ink",
    "browser.playwright",
    "model.gpa_detector",
    "program.gui",
    "program.research",
    "program.wiki",
  ];
  for (const capability of requiredCapabilities) {
    const state = manifest.capabilities?.[capability];
    if (state?.present !== true || state?.verified !== true) {
      throw new Error(`runtime capability is incomplete: ${capability}`);
    }
  }
  const resolveAsset = (key) => {
    const value = manifest.assets?.[key];
    if (typeof value !== "string" || !value) {
      throw new Error(`runtime manifest has no ${key} asset`);
    }
    const asset = path.resolve(runtimeRoot, value);
    const assetRelative = path.relative(runtimeRoot, asset);
    if (assetRelative.startsWith("..") || path.isAbsolute(assetRelative)) {
      throw new Error(`runtime ${key} asset escapes runtime resources`);
    }
    if (!fs.existsSync(asset)) {
      throw new Error(`runtime ${key} asset is missing: ${asset}`);
    }
    return asset;
  };
  return {
    // pythonw avoids allocating even a hidden console host for the persistent
    // Windows worker. Keep python.exe for short synchronous commands whose
    // stdout is part of the Desktop authentication protocol.
    command: workerPython,
    authCommand: python,
    args: ["-I", "-B", "-m", "openprogram", "worker", "start"],
    env: {
      PLAYWRIGHT_BROWSERS_PATH: resolveAsset("playwright"),
      GPA_MODEL_PATH: resolveAsset("gpa_detector"),
    },
  };
}

module.exports = { resolvePackagedWorker };
