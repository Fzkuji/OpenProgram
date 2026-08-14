const fs = require("fs");
const path = require("path");

function resolvePackagedWorker(resourcesPath, expectedVersion) {
  const runtimeRoot = path.resolve(resourcesPath, "runtime");
  const manifestPath = path.join(runtimeRoot, "runtime-manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (manifest.schema !== 1) {
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
  return {
    command: python,
    args: ["-I", "-B", "-m", "openprogram", "worker", "start"],
  };
}

module.exports = { resolvePackagedWorker };
