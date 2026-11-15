const fs = require("fs");
const path = require("path");

// Which local instance this shell serves. The stable App carries its own
// runtime under Resources/runtime and talks to port 18100. The development
// App ("OpenProgram Dev", installed by scripts/install-dev-app.sh) is the
// same shell with Resources/instance.json naming the development
// instance: its profile, its port and the runtime that
// scripts/refresh-dev-instance.sh keeps under ~/.openprogram-dev.
const INSTANCE_FILE = "instance.json";
const STABLE = Object.freeze({
  development: false,
  name: "OpenProgram",
  profile: null,
  port: 18100,
  // The browser tool attaches to web tabs here (programs/applications/browser/chrome.py).
  debugPort: 9223,
  runtimeRoot: null,
});

function loadInstance(resourcesPath, isPackaged) {
  if (!isPackaged || typeof resourcesPath !== "string") return STABLE;
  let raw;
  try {
    raw = fs.readFileSync(path.join(resourcesPath, INSTANCE_FILE), "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return STABLE;
    throw error;
  }
  const value = JSON.parse(raw);
  if (typeof value.name !== "string" || !/^OpenProgram [A-Za-z]+$/.test(value.name)) {
    throw new Error(`${INSTANCE_FILE} has no valid name`);
  }
  if (typeof value.profile !== "string" || !/^[a-z][a-z0-9-]*$/.test(value.profile)) {
    throw new Error(`${INSTANCE_FILE} has no valid profile`);
  }
  if (!Number.isInteger(value.port) || value.port < 1024 || value.port > 65535 || value.port === STABLE.port) {
    throw new Error(`${INSTANCE_FILE} has no valid port`);
  }
  if (!Number.isInteger(value.debugPort) || value.debugPort < 1024 || value.debugPort > 65535 ||
      value.debugPort === STABLE.debugPort || value.debugPort === value.port) {
    throw new Error(`${INSTANCE_FILE} has no valid debugPort`);
  }
  if (typeof value.runtime !== "string" || !path.isAbsolute(value.runtime)) {
    throw new Error(`${INSTANCE_FILE} has no absolute runtime path`);
  }
  return Object.freeze({
    development: true,
    name: value.name,
    profile: value.profile,
    port: value.port,
    debugPort: value.debugPort,
    runtimeRoot: path.resolve(value.runtime),
  });
}

// The development runtime's own manifest names its Python; the worker runs
// under the profile so it reads and writes only that instance's state.
function resolveInstanceWorker(instance) {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(instance.runtimeRoot, "runtime-manifest.json"), "utf8"),
  );
  if (typeof manifest.python !== "string" || !manifest.python) {
    throw new Error("development runtime manifest has no Python executable");
  }
  const python = path.resolve(instance.runtimeRoot, manifest.python);
  const relative = path.relative(instance.runtimeRoot, python);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("development runtime Python path escapes the runtime");
  }
  return {
    command: python,
    authCommand: python,
    args: ["-I", "-B", "-m", "openprogram", "worker", "start"],
    env: { OPENPROGRAM_PROFILE: instance.profile },
  };
}

module.exports = { INSTANCE_FILE, loadInstance, resolveInstanceWorker };
