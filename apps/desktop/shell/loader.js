"use strict";
// The Electron entry point. It is the only main-process code a thin install
// signs: it decides how this App starts, then hands over to the real main
// process, which in managed mode comes from a sealed version under <root>.
//
//   developer  <root>/developer.json exists: main.js from that checkout.
//   embedded   Resources/instance.json or Resources/runtime exists, or this
//              is an unpackaged checkout: today's paths, <root> is not read.
//   managed    otherwise: seed <root> if it is new, then read `current`.
const electron = require("electron");
const { execFileSync, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");
const compat = require("./compat.json");
const pointers = require("./pointer");
const store = require("./store");
const { createShellWindows, stopWorker } = require("./recovery");

const START_TIMEOUT_MS = 30_000;
const RELAUNCH_POLL_MS = 1000;

/** Whether Shift is held right now. A failure to tell means no. */
function shiftHeld(platform = process.platform, run = spawnSync) {
  const options = { encoding: "utf8", timeout: 1500, windowsHide: true };
  if (platform === "darwin") {
    const result = run("/usr/bin/osascript", [
      "-l", "JavaScript", "-e", 'ObjC.import("AppKit"); String($.NSEvent.modifierFlags)',
    ], options);
    const flags = Number.parseInt(String(result.stdout || "").trim(), 10);
    return Number.isInteger(flags) && (flags & (1 << 17)) !== 0;
  }
  if (platform === "win32") {
    const system = process.env.SystemRoot || "C:\\Windows";
    const result = run(path.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), [
      "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
      "Add-Type -AssemblyName System.Windows.Forms; " +
        "if ([System.Windows.Forms.Control]::ModifierKeys -band [System.Windows.Forms.Keys]::Shift) { exit 7 }",
    ], options);
    return result.status === 7;
  }
  return false;
}

/** Let `require("node-pty")` inside a version find the copy in this archive. */
function addArchiveModules(appPath) {
  const previous = process.env.NODE_PATH;
  process.env.NODE_PATH = [path.join(appPath, "node_modules"), previous].filter(Boolean).join(path.delimiter);
  Module._initPaths();
  if (previous === undefined) delete process.env.NODE_PATH;
  else process.env.NODE_PATH = previous;
}

function timedOut(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

/**
 * A trial's desktop check: this Electron loads a candidate's main process, out
 * of the owner's sight and with its own browser data, and says whether it came up.
 */
function runDesktopTrial(app, entry) {
  globalThis.__OPENPROGRAM_SHELL_LOADER__ = true;
  addArchiveModules(app.getAppPath());
  app.setPath("userData", path.join(process.env.HOME || process.cwd(), "desktop-trial"));
  if (process.platform === "darwin") app.setActivationPolicy?.("accessory");
  app.on("browser-window-created", (_event, win) => {
    win.setOpacity?.(0);
    // Many Linux window managers ignore opacity, so the window stays unmapped.
    if (process.platform === "linux") {
      win.hide?.();
      win.on?.("show", () => win.hide?.());
    }
  });
  const finish = (code, line) => {
    (code === 0 ? console.log : console.error)(line);
    app.exit(code);
  };
  let main;
  console.log("OPENPROGRAM_SHELL_STARTED");
  try {
    main = require(entry);
    if (typeof main.start !== "function") throw new Error("the version exports no start()");
  } catch (error) {
    finish(1, error && error.stack ? error.stack : String(error));
    return { mode: "desktop-trial", phase: "failed" };
  }
  timedOut(
    Promise.resolve(main.start({
      runtimeRoot: process.env.HOME,
      versionId: "trial",
      shellVersion: app.getVersion(),
      openRecovery: async () => {},
      stopWorker: async () => {},
      resolveWorker: () => { throw new Error("a trial's worker is already running"); },
    })),
    START_TIMEOUT_MS,
    `the first window did not load within ${START_TIMEOUT_MS / 1000} s`,
  ).then(
    () => finish(0, "OPENPROGRAM_MAIN_READY"),
    (error) => finish(1, error && error.stack ? error.stack : String(error)),
  );
  return { mode: "desktop-trial", phase: "started" };
}

/** Start this App again, from a path that still exists after this process ends. */
function restartShell(app, env = process.env, argv = process.argv) {
  const options = store.relaunchOptions(env, argv);
  if (options) app.relaunch(options);
  else app.relaunch();
  app.exit(0);
}

/** Linux shows a window's icon from the window itself, not from the App bundle. */
function useLinuxWindowIcon(app, platform = process.platform) {
  if (platform !== "linux") return;
  const icon = path.join(__dirname, "icon.png");
  app.on("browser-window-created", (_event, win) => win.setIcon?.(icon));
}

/**
 * Windows files toasts, the taskbar button and its pin under an application
 * id. The installer's Start menu shortcut carries this one (`build.appId`);
 * a process that keeps Electron's default shows no toast and pins a second icon.
 */
const WINDOWS_APP_ID = "ai.openprogram.desktop";

function useWindowsAppIdentity(app, platform = process.platform) {
  if (platform !== "win32") return;
  app.setAppUserModelId?.(WINDOWS_APP_ID);
}

function appBundle(executable = process.execPath) {
  return path.resolve(executable, "..", "..", "..");
}

function declinedMovePath(app) {
  return path.join(app.getPath("userData"), "declined-move-to-applications");
}

/** Whether to offer the move: macOS, packaged, outside any Applications folder, not refused here before. */
function moveOfferNeeded(app, dialog, platform = process.platform, executable = process.execPath) {
  if (platform !== "darwin" || !app.isPackaged || !dialog || typeof app.isInApplicationsFolder !== "function") return false;
  if (app.isInApplicationsFolder()) return false;
  try {
    return fs.readFileSync(declinedMovePath(app), "utf8") !== appBundle(executable);
  } catch {
    return true;
  }
}

function canWrite(directory) {
  try {
    fs.accessSync(directory, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Offer to move an App opened from a disk image or Downloads. A person without
 * administrator rights can't write /Applications, so it goes to their own
 * Applications folder, which macOS treats the same way.
 */
async function offerMoveToApplications({ app, dialog }, {
  executable = process.execPath,
  home = os.homedir(),
  sharedFolderWritable = canWrite("/Applications"),
  copyBundle = (source, target) => execFileSync("/usr/bin/ditto", [source, target]),
} = {}) {
  const bundle = appBundle(executable);
  const { response } = await dialog.showMessageBox({
    type: "question",
    message: "Move OpenProgram to Applications?",
    detail: "It's running from a temporary place right now. In Applications it stays available after this window closes and can update itself.",
    buttons: ["Move to Applications", "Not Now"],
    defaultId: 0,
    cancelId: 1,
  });
  if (response !== 0) {
    try {
      fs.mkdirSync(path.dirname(declinedMovePath(app)), { recursive: true });
      fs.writeFileSync(declinedMovePath(app), bundle);
    } catch {
      // Not remembered: the offer comes back next time.
    }
    return "declined";
  }
  try {
    if (sharedFolderWritable) {
      if (app.moveToApplicationsFolder()) return new Promise(() => {}); // The App is restarting from there.
      return "cancelled";
    }
    const target = path.join(home, "Applications", path.basename(bundle));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.rmSync(target, { recursive: true, force: true });
    copyBundle(bundle, target);
    app.relaunch({ execPath: path.join(target, path.relative(bundle, executable)), args: process.argv.slice(1) });
    app.exit(0);
    return new Promise(() => {});
  } catch (error) {
    await dialog.showMessageBox({
      type: "warning",
      message: "OpenProgram couldn't be moved to Applications.",
      detail: `${error.message}\n\nIt will keep running from here. You can drag it to Applications in Finder.`,
    });
    return "failed";
  }
}

function runShell(
  { app, BrowserWindow, ipcMain, clipboard, Menu, dialog },
  { shiftHeld: isShiftHeld = shiftHeld, platform = process.platform } = {},
) {
  if (process.env.OPENPROGRAM_TRIAL === "1" && process.env.OPENPROGRAM_TRIAL_DESKTOP_MAIN) {
    return runDesktopTrial(app, process.env.OPENPROGRAM_TRIAL_DESKTOP_MAIN);
  }
  useLinuxWindowIcon(app);
  useWindowsAppIdentity(app, platform);
  const shellVersion = app.getVersion();
  const root = store.runtimeRoot();
  const seedDirectory = path.join(process.resourcesPath || "", "seed");
  const choice = store.chooseMode({ isPackaged: app.isPackaged, resourcesPath: process.resourcesPath, root });

  // Decided before anything starts, because a worker or a first-run setup begun
  // from a disk image would be left behind by the move. A main process is still
  // loaded before the App is ready, which the switches it sets need.
  const placement = moveOfferNeeded(app, dialog)
    ? app.whenReady().then(() => offerMoveToApplications({ app, dialog }))
    : null;
  const afterPlacement = (work) => (placement ? placement.then(work) : work());

  if (choice.mode === "embedded") {
    if (placement) {
      globalThis.__OPENPROGRAM_SHELL_LOADER__ = true;
      const embedded = require(path.join(__dirname, "..", "main.js"));
      void placement.then(() => embedded.start());
      return choice;
    }
    require(path.join(__dirname, "..", "main.js"));
    return choice;
  }
  addArchiveModules(app.getAppPath());
  // A main.js loaded by this file waits for start() instead of starting itself.
  globalThis.__OPENPROGRAM_SHELL_LOADER__ = true;

  const shellWindows = createShellWindows({
    app, BrowserWindow, ipcMain, clipboard, root, compat, shellVersion, seedDirectory,
  });
  const context = {
    runtimeRoot: root,
    shellVersion,
    runningRelease: () => store.runningRelease(root),
    openRecovery: (reason) => shellWindows.openRecovery(reason),
    switchingInProgress: () => store.switchingInProgress(root),
    recoveryDue: (unreachableMs) => store.recoveryDue(root, unreachableMs),
    stopWorker: () => stopWorker(root),
    relaunchOptions: () => store.relaunchOptions(),
  };

  if (choice.mode === "developer") {
    const entry = path.join(choice.developer.checkout, "apps", "desktop", "main.js");
    void require(entry).start({
      ...context,
      versionId: null,
      resolveWorker: () => store.resolveDeveloperWorker(choice.developer, root),
    });
    return choice;
  }

  const showOnly = (reason) => afterPlacement(() => {
    app.on("window-all-closed", () => app.quit());
    if (process.platform === "darwin") {
      void app.whenReady().then(() => Menu.setApplicationMenu(Menu.buildFromTemplate([
        { role: "appMenu" }, { role: "editMenu" },
      ])));
    }
    return reason === "setup" ? shellWindows.firstRun() : shellWindows.openRecovery(reason);
  });

  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return choice;
  }
  app.on("second-instance", (_event, argv = []) => {
    if (argv.includes(store.RECOVERY_ARGUMENT)) void shellWindows.openRecovery("menu");
    else shellWindows.focus();
  });

  if (store.needsSeed(root)) {
    void showOnly("setup");
    return { ...choice, phase: "setup" };
  }
  store.refreshIdentity(root, seedDirectory, shellVersion);
  if (isShiftHeld() || process.argv.includes(store.RECOVERY_ARGUMENT)) {
    void showOnly("shift");
    return { ...choice, phase: "shift" };
  }
  let pointer;
  try {
    pointer = pointers.readOrRecover(root);
  } catch (error) {
    store.appendRecoveryLog(root, `the pointer is unreadable: ${error.message}`);
    void showOnly("failed");
    return { ...choice, phase: "failed" };
  }
  const desktop = store.chooseDesktop(root, pointer, compat, shellVersion);
  if (!desktop) {
    store.appendRecoveryLog(root, `no main process can be loaded for ${pointer.version}`);
    void showOnly("failed");
    return { ...choice, phase: "failed" };
  }

  // A failed main process has registered IPC handlers and app listeners, so
  // the next candidate gets a fresh process rather than this one.
  const failed = (error) => {
    store.recordDesktopFailure(root, desktop.id, shellVersion, error);
    store.appendRecoveryLog(root, `main process of ${desktop.id} failed: ${error && error.message}`);
    restartShell(app);
  };
  let main;
  try {
    main = require(store.desktopEntry(root, desktop.id));
    if (typeof main.start !== "function") throw new Error("the version exports no start()");
  } catch (error) {
    failed(error);
    return { ...choice, phase: "failed" };
  }
  store.writeShellRecord(root, shellVersion, desktop.id, store.shellExecutable());
  // A switch whose desktop code changed asks for a fresh process to load it.
  const relaunchPoll = setInterval(() => {
    if (!store.takeRelaunchRequest(root)) return;
    clearInterval(relaunchPoll);
    restartShell(app);
  }, RELAUNCH_POLL_MS);
  relaunchPoll.unref?.();
  void afterPlacement(() => timedOut(
    Promise.resolve(main.start({
      ...context,
      versionId: desktop.id,
      resolveWorker: () => store.resolveWorker(root, compat),
    })),
    START_TIMEOUT_MS,
    `the first window did not load within ${START_TIMEOUT_MS / 1000} s`,
  ).then(
    () => store.writeHandshake(root, desktop.id, shellVersion),
    (error) => {
      const loaded = BrowserWindow.getAllWindows().some((win) => !win.webContents.isLoading());
      if (loaded) store.writeHandshake(root, desktop.id, shellVersion);
      else failed(error);
    },
  ));
  return { ...choice, phase: "started", version: desktop.id };
}

if (!globalThis.__OPENPROGRAM_SHELL_TEST__) {
  runShell(electron);
}

module.exports = {
  WINDOWS_APP_ID, addArchiveModules, moveOfferNeeded, offerMoveToApplications, restartShell, runShell, shiftHeld,
  useWindowsAppIdentity,
};
