const fs = require("fs");
const path = require("path");

function resolvePackagedWorker(resourcesPath) {
  const runtimeRoot = path.resolve(resourcesPath, "runtime");
  const manifestPath = path.join(runtimeRoot, "runtime-manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
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
