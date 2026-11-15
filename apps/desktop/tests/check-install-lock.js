const assert = require("node:assert/strict");
const { INSTALL_LOCK_NAME, installLockPath, installInProgress } = require("../main/self-update/install-lock");

// The lock sits next to the App bundle, where both installers put it.
assert.equal(
  installLockPath("/Applications/OpenProgram.app/Contents/MacOS/OpenProgram", "darwin"),
  `/Applications/${INSTALL_LOCK_NAME}`,
);
assert.equal(
  installLockPath("/Users/me/Apps/OpenProgram.app/Contents/MacOS/OpenProgram", "darwin"),
  `/Users/me/Apps/${INSTALL_LOCK_NAME}`,
);
assert.equal(installLockPath("/repo/node_modules/electron/dist/Electron", "darwin"), null, "a dev Electron is not a bundle");
assert.equal(installLockPath("C:\\Program Files\\OpenProgram\\OpenProgram.exe", "win32"), null, "only the macOS layout has the lock");
assert.equal(installLockPath("", "darwin"), null);

// The lock counts only while the installer whose PID it names is alive.
const files = new Map();
const alive = new Set([4242]);
const io = {
  readFileSync(path) {
    if (!files.has(path)) { const error = new Error("ENOENT"); error.code = "ENOENT"; throw error; }
    return files.get(path);
  },
  isAlive: (pid) => alive.has(pid),
};
const lock = "/Applications/.openprogram-app-install.lock";
assert.equal(installInProgress(lock, io), false, "no lock file: no install");
files.set(lock, "4242\n");
assert.equal(installInProgress(lock, io), true, "a live installer holds the lock");
files.set(lock, "4242\nextra\n");
assert.equal(installInProgress(lock, io), true, "only the first line is the PID");
files.set(lock, "99\n");
assert.equal(installInProgress(lock, io), false, "a stale lock from a dead installer does not count");
files.set(lock, "not a pid\n");
assert.equal(installInProgress(lock, io), false);
files.set(lock, "");
assert.equal(installInProgress(lock, io), false);
assert.equal(installInProgress(null, io), false, "no lock path (other platforms): never installing");

console.log("install lock checks passed");
