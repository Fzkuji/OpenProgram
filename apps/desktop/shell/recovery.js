"use strict";
// The windows the shell owns: first-run setup and the recovery page. Neither
// needs Python, the worker, or any code from a version.
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const pointers = require("./pointer");
const store = require("./store");
const { restoreBuiltIn, seed } = require("./seed");

const STOP_WAIT_MS = 20_000;
// How long a Windows worker gets to shut down in order before it is ended.
// It looks for the request four times a second and its shutdown fits in five.
const ORDERLY_STOP_MS = 8_000;
const REASONS = new Set(["failed", "menu", "shift"]);
const THEME_PREFS = "theme-prefs.json";

function workerPid(state, isAlive = store.processAlive) {
  try {
    const pid = Number.parseInt(fs.readFileSync(path.join(state, "worker.pid"), "utf8").split(/\s+/)[0], 10);
    return Number.isInteger(pid) && pid > 0 && isAlive(pid) ? pid : null;
  } catch {
    return null;
  }
}

function waitFor(condition, timeoutMs, intervalMs = 100) {
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (condition() || Date.now() - started >= timeoutMs) {
        clearInterval(timer);
        resolve(condition());
      }
    }, intervalMs);
  });
}

/**
 * Ask a Windows worker to stop in order. No signal can ask there, so the
 * request is a file naming the PID, which the worker's main loop reads
 * (`request_stop` in operations/worker/lifecycle.py writes the same file).
 */
function requestWindowsStop(state, pid) {
  const target = path.join(state, "worker.stop");
  const staged = `${target}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(staged, `${pid}\n`, "utf8");
    fs.renameSync(staged, target);
    return true;
  } catch {
    fs.rmSync(staged, { force: true });
    return false;
  }
}

function endWindowsTree(pid) {
  try {
    execFileSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
  } catch {
    // Already gone; the wait that follows decides.
  }
}

/**
 * Stop the worker before `current` moves. POSIX sends SIGTERM. Windows asks
 * through `worker.stop` first, so checkpoints are written and the web server
 * closes, and ends the process tree only if it is still there after
 * `orderlyMs`. Throws when the worker outlives `waitMs`.
 */
async function stopWorker(root, platform = process.platform, {
  isAlive = store.processAlive, endTree = endWindowsTree,
  orderlyMs = ORDERLY_STOP_MS, waitMs = STOP_WAIT_MS, pollMs = 100,
} = {}) {
  const state = path.dirname(root);
  const pid = workerPid(state, isAlive);
  if (pid === null) return;
  const gone = () => !isAlive(pid);
  if (platform === "win32") {
    const started = Date.now();
    const asked = requestWindowsStop(state, pid);
    try {
      if (!asked || !await waitFor(gone, orderlyMs, pollMs)) {
        endTree(pid);
        store.appendRecoveryLog(root, asked
          ? `the worker (PID ${pid}) did not stop within ${orderlyMs} ms of being asked and was ended`
          : `the worker (PID ${pid}) could not be asked to stop and was ended`);
      }
      if (await waitFor(gone, Math.max(0, waitMs - (Date.now() - started)), pollMs)) return;
    } finally {
      fs.rmSync(path.join(state, "worker.stop"), { force: true });
    }
  } else {
    process.kill(pid, "SIGTERM");
    if (await waitFor(gone, waitMs, pollMs)) return;
  }
  throw new Error("The background process did not stop, so nothing was changed.");
}

/** Forget the failures that would send the chosen version straight back. */
function clearFailures(root, id) {
  fs.rmSync(store.failedMarker(root, id), { force: true });
  fs.rmSync(path.join(root, "boot", `${id}.attempts`), { force: true });
}

/** Point `current` at a retained version the way a person choosing one means it. */
async function useVersion({ root, compat, id, shellVersion, platform }) {
  if (!pointers.validId(id)) throw new Error("That isn't a version on this computer.");
  const manifest = store.readManifest(root, id);
  if (!manifest || !store.usable(root, manifest, compat)) {
    throw new Error("That version can't be started by this App.");
  }
  if (store.switchingInProgress(root)) {
    throw new Error("OpenProgram is switching versions right now. Try again when that finishes.");
  }
  let pointer = null;
  try {
    pointer = pointers.readOrRecover(root);
  } catch {
    pointer = null;
  }
  if (pointer && pointer.version === id) {
    clearFailures(root, id);
    return { moved: false };
  }
  await stopWorker(root, platform);
  const changes = { version: id, base: String(manifest.base), probation: "", probation_deadline: "" };
  if (pointer) pointers.compareAndSet(root, pointer.generation, "shell", changes);
  else {
    pointers.replaceLost(root, {
      generation: 1, version: id, base: String(manifest.base), writer: "shell",
      last_good: "", last_good_base: "", previous_good: "", previous_good_base: "",
      probation: "", probation_deadline: "", extras: [],
    });
  }
  clearFailures(root, id);
  store.appendRecoveryLog(root, `moved to ${id} from the recovery page (shell ${shellVersion})`);
  return { moved: true };
}

function pageTheme(userData) {
  try {
    const prefs = JSON.parse(fs.readFileSync(path.join(userData, THEME_PREFS), "utf8"));
    const mode = Object.entries(prefs).find(([key]) => /mode/i.test(key))?.[1];
    if (mode === "dark" || mode === "light") return mode;
  } catch {
    // No saved preference: the page follows the system.
  }
  return "system";
}

function createShellWindows({
  app, BrowserWindow, ipcMain, clipboard, root, compat, shellVersion, seedDirectory, platform = process.platform,
}) {
  let recoveryWindow = null;
  let recoveryReason = "failed";
  let setupWindow = null;
  let setupValue = { percent: 0, error: null };
  let setupWork = null;
  let handlersReady = false;

  const fromShellWindow = (event) => [recoveryWindow, setupWindow].some((win) =>
    win && !win.isDestroyed() && win.webContents === event.sender);

  function restart() {
    const options = store.relaunchOptions();
    if (options) app.relaunch(options);
    else app.relaunch();
    app.exit(0);
  }

  function reportSetup(value) {
    setupValue = { ...setupValue, ...value };
    if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("shell:setup", setupValue);
  }

  function runSetup(onFinished) {
    if (setupWork) return setupWork;
    reportSetup({ percent: 0, error: null });
    setupWork = seed({ root, seedDirectory, platform, onProgress: (percent) => reportSetup({ percent }) })
      .then(() => onFinished())
      .catch((error) => {
        store.appendRecoveryLog(root, `first-run setup failed: ${error.message}`);
        reportSetup({ error: error.message });
      })
      .finally(() => { setupWork = null; });
    return setupWork;
  }

  function registerHandlers(onSetupFinished) {
    if (handlersReady) return;
    handlersReady = true;
    const guarded = (handler) => (event, ...args) => {
      if (!fromShellWindow(event)) throw new Error("not a shell window");
      return handler(...args);
    };
    ipcMain.handle("shell:recovery-state", guarded(() => {
      let pointer = null;
      try {
        pointer = pointers.readOrRecover(root);
      } catch {
        pointer = null;
      }
      return {
        reason: recoveryReason,
        logPath: path.join(root, "recovery.log"),
        platform,
        versions: store.listVersions(root, compat, shellVersion, pointer),
      };
    }));
    ipcMain.handle("shell:recovery-use", guarded(async (id) => {
      try {
        await useVersion({ root, compat, id, shellVersion, platform });
      } catch (error) {
        return { ok: false, message: error.message };
      }
      setTimeout(restart, 150);
      return { ok: true };
    }));
    ipcMain.handle("shell:recovery-copy-log", guarded(() => {
      clipboard.writeText(path.join(root, "recovery.log"));
      return true;
    }));
    ipcMain.handle("shell:recovery-restore", guarded(async () => {
      try {
        const result = await restoreBuiltIn({ root, seedDirectory, platform });
        if (result.restored) setTimeout(restart, 150);
        return { ok: result.restored, message: result.message };
      } catch (error) {
        store.appendRecoveryLog(root, `restore failed: ${error.message}`);
        return { ok: false, message: error.message };
      }
    }));
    ipcMain.handle("shell:setup-retry", guarded(() => {
      void runSetup(onSetupFinished);
    }));
    ipcMain.on("shell:setup-ready", (event) => {
      if (fromShellWindow(event)) event.sender.send("shell:setup", setupValue);
    });
  }

  function pageWindow(page, query, size) {
    const win = new BrowserWindow({
      ...size,
      title: "OpenProgram",
      backgroundColor: "#00000000",
      show: true,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    void win.loadFile(path.join(__dirname, page), {
      query: { ...query, theme: pageTheme(app.getPath("userData")) },
    });
    return win;
  }

  async function openRecovery(reason = "failed") {
    await app.whenReady();
    recoveryReason = REASONS.has(reason) ? reason : "failed";
    registerHandlers(restart);
    if (recoveryWindow && !recoveryWindow.isDestroyed()) {
      recoveryWindow.show();
      recoveryWindow.focus();
      return recoveryWindow;
    }
    recoveryWindow = pageWindow("recovery.html", {}, { width: 760, height: 720, minWidth: 420, minHeight: 420 });
    recoveryWindow.on("closed", () => { recoveryWindow = null; });
    return recoveryWindow;
  }

  async function firstRun(onFinished = restart) {
    await app.whenReady();
    registerHandlers(onFinished);
    setupWindow = pageWindow("setup.html", {}, { width: 560, height: 360, resizable: false });
    setupWindow.on("closed", () => { setupWindow = null; });
    return runSetup(onFinished);
  }

  function focus() {
    const win = [recoveryWindow, setupWindow].find((item) => item && !item.isDestroyed());
    if (win) {
      win.show();
      win.focus();
    }
  }

  return { firstRun, focus, openRecovery };
}

module.exports = { createShellWindows, stopWorker, useVersion };
