"use strict";

const fs = require("node:fs");
const path = require("node:path");

const INSTALL_LOCK_NAME = ".openprogram-app-install.lock";

/**
 * Where the App installers (the release `install-app.sh` and the local
 * `scripts/refresh-local-app.sh`) hold their lock while they replace the
 * App bundle and its runtime: a file next to the App bundle whose first
 * line is the installer's PID. Only the macOS bundle layout has it, so the
 * path is always a POSIX one, whichever platform asks.
 */
function installLockPath(execPath, platform = process.platform) {
  if (platform !== "darwin" || typeof execPath !== "string" || !execPath) return null;
  // <dir>/OpenProgram.app/Contents/MacOS/OpenProgram → <dir>
  const bundle = path.posix.resolve(execPath, "..", "..", "..");
  if (!bundle.endsWith(".app")) return null;
  return path.posix.join(path.posix.dirname(bundle), INSTALL_LOCK_NAME);
}

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error && error.code === "EPERM";
  }
}

/**
 * True while an installer holds the lock: the file exists and the PID on
 * its first line is a live process. A stale lock left by a crashed
 * installer does not count, so the App never waits on one forever.
 */
function installInProgress(lockPath, { readFileSync = fs.readFileSync, isAlive = processAlive } = {}) {
  if (!lockPath) return false;
  let text;
  try {
    text = readFileSync(lockPath, "utf8");
  } catch {
    return false;
  }
  const pid = Number.parseInt(String(text).split(/\r?\n/, 1)[0].trim(), 10);
  if (!Number.isInteger(pid) || pid <= 0) return false;
  return isAlive(pid);
}

module.exports = { INSTALL_LOCK_NAME, installLockPath, installInProgress };
