// OpenProgram desktop shell. Plain JS, no bundler.
const {
  app,
  BrowserWindow,
  clipboard,
  WebContentsView,
  Menu,
  dialog,
  ipcMain: nativeIpcMain,
  powerSaveBlocker,
  Notification,
  session,
  shell,
  nativeTheme,
} = require("electron");
const { execFile, execFileSync, spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const { Buffer } = require("buffer");
const { resolveAuthenticatedStartUrl, ownerAuthTokenFromStartUrl } = require("./main/worker/worker-start-url");
const webPort = require("./main/worker/web-port");
const { createSelfUpdateReopen, registerReopenIpc } = require("./main/self-update/self-update-reopen");
const { registerUiVerificationIpc } = require("./main/self-update/self-update-ui");
const { createUiVerificationGuard } = require("./main/self-update/self-update-ui-guard");
const uiVerificationGuard = createUiVerificationGuard(nativeIpcMain);
const { ipcMain } = uiVerificationGuard;
const { resolvePackagedWorker } = require("./main/self-update/packaged-runtime");
const { loadInstance, resolveInstanceWorker } = require("./main/instance");
const { useBrowserUserAgent, forgetClearanceOnChange } = require("./main/web-views/webtab-user-agent");
const { readClientHints, registerClientHints } = require("./main/web-views/webtab-client-hints");
const webTabPermissions = require("./main/web-views/webtab-permissions");
const {
  DesktopUpdateService,
  compareVersions,
  desktopUpdateFetch,
  parseUpgradeOutcome,
} = require("./main/self-update/update-service");
const { readRunningBuild } = require("./main/self-update/running-build");
const { shellSwap } = require("./main/self-update/app-replace");
const { DesktopAttentionService } = require("./main/windows/attention-service");
const {
  killWindowsProcessTree,
  resolveTerminalCommand,
  waitForTerminalPid,
} = require("./main/terminal/terminal-command");
const { readWindowsUpdateLine, verifyWindowsAuthenticode } = require("./main/self-update/windows-signature");
const {
  loadTransferDecisions,
  saveTransferDecisionsAtomic,
  putTransferDecision,
  ackTransferDecision,
} = require("./main/web-views/tab-transfer-store");
const {
  recordVisit,
  listHistory,
  importHistoryEntries,
  deleteHistoryEntry,
  clearHistory,
} = require("./main/web-views/browsing-history-store");
const {
  listBrowserSources,
  runBrowserImport,
} = require("./main/web-views/browser-profile-import");
const {
  contextMenuRequestedX,
  clampContextMenuPanel,
  cascadeMenuGeometry,
} = require("./main/windows/menu-geometry");
const {
  createRecoveryState,
  createRecoveryCoordinator,
  recordWorkerCommandExit,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
  workerVersionAction,
} = require("./main/worker/worker-recovery-state");
const { installLockPath, installInProgress } = require("./main/self-update/install-lock");
const { validateTransferPayload } = require("./main/web-views/tab-transfer-validation");
const {
  STATE_FILE_NAME,
  loadPersistedState,
  attachWindowStatePersistence,
  browserWindowOptionsForPlan,
  applyRestoredChrome,
} = require("./main/windows/window-state");
const {
  createMainWindowGate,
  registerSingleMainWindow,
} = require("./main/windows/window-lifecycle");
const sessionWindows = require("./main/windows/session-windows");
const themeChrome = require("./main/windows/theme-chrome");
const {
  browserWindowChromeOptions,
  applyNativeTitleBarChrome,
  registerNativeTitleBarIpc,
} = require("./main/windows/window-chrome");

const INSTANCE = loadInstance(process.resourcesPath, app.isPackaged);
// How the shell loader started this process (apps/desktop/shell/loader.js).
// Null in embedded mode, where this file starts itself.
let shellContext = null;
if (INSTANCE.development) {
  // Its own name and userData: window state, the single-instance lock and
  // Chromium storage stay apart from the stable App's.
  app.setName(INSTANCE.name);
  app.setPath("userData", path.join(app.getPath("appData"), INSTANCE.name));
}
// 单实例：worker 单端口。端口由 worker 在本账户第一次启动时选定并记在
// <state>/web-port（通常 18100），App 只读这些文件，见
// main/worker/web-port.js 和 docs/reference/design/cli/single-port.html。
const WEB_PORT_FILES = {
  instance: INSTANCE,
  stateDir: webPort.stateDirectory(),
};
const WEB_PORT_CHOICE = webPort.resolveWebPort(WEB_PORT_FILES);
const WEB_PORT = String(WEB_PORT_CHOICE.port);
const START_URL =
  process.env.OPENPROGRAM_DESKTOP_URL || `http://127.0.0.1:${WEB_PORT}/chat`;
const UI_ORIGIN = new URL(START_URL).origin;
const selfUpdateReopen = createSelfUpdateReopen({ argv: process.argv, origin: UI_ORIGIN });
const HEALTH_URL = new URL("/healthz", START_URL).toString();
const WORKER_COMMAND = "openprogram worker start";
// A worker boot takes a few seconds; probing every second shows the UI
// about as soon as it is up instead of up to three seconds later.
const RECOVERY_INTERVAL_MS = 1_000;
const TRANSFER_TIMEOUT_MS = 15_000;
const DESTINATION_UNDO_TIMEOUT_MS = 2_000;
const COMMIT_RECONCILE_INITIAL_MS = 100;
const COMMIT_RECONCILE_MAX_MS = 5_000;
// Bounded retries for a clean (unambiguous) committed-decision write failure
// before abandoning the commit and taking the pre-commit rollback path.
const COMMIT_DECISION_RETRY_LIMIT = 4;
// As VS Code does: a check ~30 s after start, then every hour, a failed one
// included; channel.json is a tiny static file.
const UPDATE_INITIAL_DELAY_MS = 30_000;
const UPDATE_CHECK_INTERVAL_MS = 3600_000;

let desktopUpdates = null;

let menuReadyVersion = null;

function broadcastUpdateState(state) {
  // The menu offers the restart while a prepared version waits.
  if ((state.readyVersion || null) !== menuReadyVersion) {
    menuReadyVersion = state.readyVersion || null;
    if (app.isReady()) buildMenu();
  }
  for (const win of BrowserWindow.getAllWindows()) {
    try {
      if (!win.isDestroyed() && !win.webContents.isDestroyed()) {
        win.webContents.send("updates:state", state);
      }
    } catch (_error) {
      // Window teardown must not abort an update check or download.
    }
  }
}

function startAutomaticUpdateChecks() {
  if (!app.isPackaged) return;
  const run = () => {
    if (desktopUpdates?.getState().automaticChecks) void desktopUpdates.check();
  };
  setTimeout(() => {
    run();
    setInterval(run, UPDATE_CHECK_INTERVAL_MS);
  }, UPDATE_INITIAL_DELAY_MS);
}

/** The release this App runs: a sealed version's own, otherwise the App's. */
function runningReleaseVersion() {
  if (!shellContext?.versionId) return app.getVersion();
  const recorded = shellContext.runningRelease?.();
  if (recorded) return recorded;
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8")).version;
  } catch {
    return app.getVersion();
  }
}

function updateFileFilters() {
  if (process.platform === "win32") return [{ name: "Windows Installer", extensions: ["exe"] }];
  if (process.platform === "darwin") return [{ name: "macOS Disk Image", extensions: ["dmg"] }];
  return process.env.APPIMAGE
    ? [{ name: "AppImage", extensions: ["AppImage"] }]
    : [{ name: "Debian Package", extensions: ["deb"] }];
}

/**
 * A sealed version updates through its own Python, which holds the fetcher:
 * ``--prepare`` downloads, builds and tries the update and stops before the
 * switch; without it a prepared update is switched to.
 */
function runVersionStoreUpdate(extraArgs = []) {
  const launch = shellContext.resolveWorker();
  const env = { ...process.env, ...webPort.workerPortEnv(WEB_PORT_CHOICE), ...launch.env };
  // The worker lists this App among the version store's users, so the last
  // uninstaller knows whether the store is still needed; the updater also
  // stages a newer App shell beside this App, which it names with its version.
  if (app.isPackaged) {
    env.OPENPROGRAM_APP_PATH = path.dirname(process.execPath);
    env.OPENPROGRAM_SHELL_VERSION = app.getVersion();
  }
  delete env.PYTHONHOME;
  if (!launch.env.PYTHONPATH) delete env.PYTHONPATH;
  return new Promise((resolve, reject) => {
    let output = "";
    const child = spawn(launch.authCommand, [...launch.authArgs, "upgrade", ...extraArgs, "--json"], {
      stdio: ["ignore", "pipe", "ignore"],
      env,
      windowsHide: process.platform === "win32",
    });
    child.stdout.on("data", (data) => { output += data; });
    child.on("error", reject);
    child.on("close", (code) => resolve(parseUpgradeOutcome(output, code)));
  });
}

/** How a packaged App (macOS, Windows, an AppImage) swaps in the App shell the updater staged, or null. */
function desktopShellSwap(appImage) {
  if (!app.isPackaged || !shellContext?.runtimeRoot) return null;
  return shellSwap({
    runtimeRoot: shellContext.runtimeRoot,
    execPath: process.execPath,
    appImage,
    appVersion: app.getVersion(),
    isNewer: (version) => compareVersions(version, app.getVersion()) > 0,
    quit: () => setTimeout(() => app.quit(), 300),
  });
}

function initializeDesktopUpdates() {
  const appImage = process.platform === "linux" ? process.env.APPIMAGE : "";
  const versionStore = Boolean(shellContext?.versionId);
  let windowsLine = null;
  const readLine = () => {
    windowsLine ||= readWindowsUpdateLine();
    return windowsLine;
  };
  desktopUpdates = new DesktopUpdateService({
    currentVersion: runningReleaseVersion(),
    arch: process.arch,
    platform: process.platform,
    statePath: path.join(app.getPath("userData"), "update-state.json"),
    fetchImpl: desktopUpdateFetch,
    linuxFormat: appImage ? "appimage" : "deb",
    windowsLine: readLine,
    versionStore,
    readCurrentVersion: versionStore ? runningReleaseVersion : null,
    applyVersionUpdate: versionStore ? () => runVersionStoreUpdate() : null,
    prepareVersionUpdate: versionStore ? () => runVersionStoreUpdate(["--prepare"]) : null,
    // After the switch the new version's own App code runs from the next start.
    relaunchApp: () => setTimeout(() => {
      app.relaunch(shellContext?.relaunchOptions?.());
      app.exit(0);
    }, 300),
    readRunningBuild: versionStore
      ? () => readRunningBuild(shellContext.runtimeRoot, shellContext.versionId, shellContext.shellCompat || null)
      : null,
    shellSwap: versionStore ? desktopShellSwap(appImage) : null,
    chooseSavePath: async (name) => {
      const result = await dialog.showSaveDialog({
        title: "Download OpenProgram Update",
        defaultPath: path.join(app.getPath("downloads"), name),
        filters: updateFileFilters(),
      });
      return result.canceled ? null : result.filePath;
    },
    verifyArtifact: async (filePath) => verifyWindowsAuthenticode(filePath, {
      line: process.platform === "win32" ? await readLine() : null,
    }),
    openPath: async (filePath) => {
      if (filePath.endsWith(".AppImage")) fs.chmodSync(filePath, 0o755);
      return shell.openPath(filePath);
    },
    emit: broadcastUpdateState,
  });
  startAutomaticUpdateChecks();
  // After a swap to a new App shell, the prepared version is switched to now.
  void desktopUpdates.resumeAfterShellSwap().then(() => desktopUpdates.prepareInBackground());
}

function registerOwnerAuthIpc() {
  ipcMain.handle("owner-auth:refresh", (event) => {
    const senderUrl = event?.senderFrame?.url || event?.sender?.getURL?.() || "";
    try {
      if (new URL(senderUrl).origin !== UI_ORIGIN) return null;
    } catch (_error) {
      return null;
    }
    const startUrl = resolveWorkerStartUrl();
    return startUrl ? ownerAuthTokenFromStartUrl(startUrl) : null;
  });
}

function registerUpdateIpc() {
  ipcMain.handle("updates:get-state", () => desktopUpdates?.getState() || null);
  ipcMain.handle("updates:check", async () => {
    return await desktopUpdates?.check() || null;
  });
  ipcMain.handle("updates:set-automatic-checks", (_event, enabled) => {
    return desktopUpdates?.setAutomaticChecks(enabled) || null;
  });
  ipcMain.handle("updates:download", () => desktopUpdates?.download() || null);
  ipcMain.handle("updates:install", () => desktopUpdates?.install() || null);
  ipcMain.handle("updates:open-release", () => {
    const releaseUrl = desktopUpdates?.getState().release?.releaseUrl;
    return releaseUrl ? shell.openExternal(releaseUrl) : null;
  });
}

function appDocumentWindow(sender) {
  return BrowserWindow.getAllWindows().find((win) => !win.isDestroyed() && win.webContents === sender) || null;
}

// Notifications and keep-awake are reported by every app window; the
// service collapses them into one notification and one power hold.
function registerAttentionIpc() {
  const attention = new DesktopAttentionService({
    statePath: path.join(app.getPath("userData"), "attention-prefs.json"),
    createNotification: (options) => new Notification(options),
    listSystemSounds: () => process.platform !== "darwin" ? [] : fs.readdirSync("/System/Library/Sounds")
      .filter((file) => file.endsWith(".aiff")).map((file) => file.slice(0, -".aiff".length)),
    playSound: (name) => execFile("/usr/bin/afplay", [path.join("/System/Library/Sounds", `${name}.aiff`)], () => {}),
    notificationsSupported: () => Notification.isSupported(),
    powerSaveBlocker,
    isAppFocused: () => Boolean(BrowserWindow.getFocusedWindow()),
    openSession: (senderId, sessionId) => {
      const win = BrowserWindow.getAllWindows().find((candidate) =>
        !candidate.isDestroyed() && candidate.webContents.id === senderId,
      ) || BrowserWindow.getAllWindows().find((candidate) => !candidate.isDestroyed());
      if (!win) return;
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
      if (sessionId) win.webContents.send("attention:open-session", sessionId);
    },
    emit: (prefs) => {
      for (const win of BrowserWindow.getAllWindows()) {
        if (!win.isDestroyed() && !win.webContents.isDestroyed()) win.webContents.send("attention:prefs", prefs);
      }
    },
  });
  const tracked = new Set();
  const track = (sender) => {
    if (tracked.has(sender.id)) return;
    tracked.add(sender.id);
    const id = sender.id;
    sender.once("destroyed", () => {
      tracked.delete(id);
      attention.forgetSender(id);
    });
  };
  ipcMain.handle("attention:get-prefs", () => attention.getPrefs());
  ipcMain.handle("attention:sounds", () => attention.getSounds());
  ipcMain.handle("attention:preview-sound", (event, sound) => {
    if (!appDocumentWindow(event.sender)) return false;
    return attention.previewSound(sound);
  });
  ipcMain.handle("attention:set-prefs", (event, patch) => {
    if (!appDocumentWindow(event.sender)) return attention.getPrefs();
    return attention.setPrefs(patch);
  });
  ipcMain.on("attention:notify", (event, request) => {
    if (!appDocumentWindow(event.sender)) return;
    attention.notify(event.sender.id, request);
  });
  ipcMain.on("attention:busy", (event, busy) => {
    if (!appDocumentWindow(event.sender)) return;
    track(event.sender);
    attention.reportBusy(event.sender.id, Boolean(busy));
  });
}

// agent 接管内置浏览器的数据面通道：后端 browser 工具（engine=auto/app）经
// CDP attach 这里的可见 web tab。Electron 默认只绑 127.0.0.1，不对外暴露；
// 9222 留给后端 sidecar Chrome，互不冲突。必须在 app ready 之前设置。
app.commandLine.appendSwitch("remote-debugging-port", String(INSTANCE.debugPort));

let currentChrome = themeChrome.chromeForTheme("beige-dark");

function errorPageUrl(phase = "waiting") {
  return themeChrome.buildErrorPageUrl(currentChrome, WORKER_COMMAND, phase, {
    port: WEB_PORT,
    holder: portHeldBy,
    setting: WEB_PORT_CHOICE.source,
    chinese: /^zh/i.test(app.getLocale?.() || ""),
  });
}

function isErrorPageUrl(url) {
  return themeChrome.isErrorPageUrl(url);
}

/**
 * Whether an installer (`install-app.sh`, the local refresh) is replacing
 * this App right now. It quits the App and stops the worker first, so an
 * App opened meanwhile finds no backend: it then waits for the installer
 * instead of starting a worker from the half-installed runtime.
 */
function appInstallInProgress() {
  if (shellContext?.switchingInProgress?.()) return true;
  // The stable installers' lock says nothing about the development runtime.
  if (!app.isPackaged || INSTANCE.development) return false;
  return installInProgress(installLockPath(process.execPath));
}

/** Show the waiting page for `phase` unless the window already shows it. */
function showWaitPage(ctx, phase) {
  if (ctx.win.isDestroyed()) return;
  if (themeChrome.waitPagePhase(ctx.win.webContents.getURL?.()) === phase) return;
  void ctx.win.loadURL(errorPageUrl(phase)).catch(() => {});
}

function themePrefsPath() {
  return path.join(app.getPath("userData"), themeChrome.PREFS_FILE_NAME);
}

/** The OS appearance; the App's pages read it through the preload (theme.systemDark). */
function systemPrefersDark() {
  try {
    return require("electron").nativeTheme.shouldUseDarkColors !== false;
  } catch {
    /* older Electron or tests without nativeTheme */
    return true;
  }
}

function resolveStartupChrome(source = "startup") {
  const systemDark = systemPrefersDark();
  const resolved = themeChrome.loadResolvedChrome({
    userDataPath: app.getPath("userData"),
    systemDark,
  });
  themeChrome.recordThemeEvent(app.getPath("userData"), { source, ...resolved, previousTheme: null, systemDark });
  currentChrome = resolved.chrome;
  return resolved;
}

function applyWindowChrome(payload = {}) {
  const theme = themeChrome.isThemeId(payload.theme) ? payload.theme : null;
  const accentColor = themeChrome.normalizeHex(payload.accentColor);
  const fromTheme = theme ? themeChrome.chromeForTheme(theme, accentColor) : currentChrome;
  const background = themeChrome.colorToHex(payload.backgroundColor) || fromTheme.bg;
  currentChrome = { ...fromTheme, bg: background };
  if (accentColor) currentChrome.link = accentColor;
  // App windows only: the agent cursor and other overlays are transparent
  // windows, and a background colour would paint them as an opaque tile.
  for (const { win } of windows.values()) {
    try {
      if (win.isDestroyed()) continue;
      win.setBackgroundColor(currentChrome.bg);
      applyNativeTitleBarChrome(win, process.platform, currentChrome);
      if (isErrorPageUrl(win.webContents.getURL?.())) {
        void win.loadURL(errorPageUrl()).catch(() => {});
      }
    } catch (_error) {
      /* Window teardown must not abort a theme update. */
    }
  }
  const style = themeChrome.THEME_STYLES.includes(payload.style)
    || payload.style === "custom"
    ? themeChrome.coerceThemeStyle(payload.style)
    : null;
  const mode = themeChrome.THEME_MODES.includes(payload.mode) ? payload.mode : null;
  if (theme && style && mode) {
    try {
      themeChrome.writePrefsFile(themePrefsPath(), { style, mode, theme, accent: accentColor });
    } catch (_error) {
      /* Cache write is best-effort; Chromium localStorage remains the store. */
    }
  }
}

const recoveryCoordinator = createRecoveryCoordinator();

// ---------------------------------------------------------------- worker boot

function probe(url, timeoutMs) {
  return new Promise((resolve) => {
    const mod = url.startsWith("https:") ? https : http;
    const req = mod.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 300);
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", () => resolve(false));
  });
}

// A sealed version's worker starts through the launcher, so its version guard
// counts the attempt. An attempt that dies is retried by the recovery cycle
// until the guard moves the pointer back or the recovery page takes over.
async function spawnManagedWorker(action) {
  let launch;
  try {
    launch = shellContext.resolveWorker();
    if (action === "restart") await shellContext.stopWorker();
  } catch (error) {
    recoveryCoordinator.workerSpawned = false;
    console.error(`[desktop] worker start failed: ${error.message}`);
    void shellContext.openRecovery("failed");
    return;
  }
  const env = { ...process.env, ...webPort.workerPortEnv(WEB_PORT_CHOICE), ...launch.env };
  // The worker lists this App among the version store's users, so the last
  // uninstaller knows whether the store is still needed.
  if (app.isPackaged) env.OPENPROGRAM_APP_PATH = path.dirname(process.execPath);
  delete env.PYTHONHOME;
  if (!launch.env.PYTHONPATH) delete env.PYTHONPATH;
  const log = fs.openSync(launch.logFile, "a");
  const child = spawn(launch.command, launch.args, {
    detached: true,
    stdio: ["ignore", log, log],
    env,
    windowsHide: process.platform === "win32",
  });
  fs.closeSync(log);
  child.on("error", (error) => {
    recoveryCoordinator.workerSpawned = false;
    console.error(`[desktop] worker start failed: ${error.message}`);
  });
  child.on("exit", (code) => {
    recoveryCoordinator.workerSpawned = false;
    if (code === 0) return;
    recoveryCoordinator.startRejected = true;
    recoveryCoordinator.restartIssued = false;
    recoveryCoordinator.unreachableProbes = 0;
  });
  child.unref();
}

function spawnWorker(action = "start") {
  if (shellContext) {
    void spawnManagedWorker(action);
    return;
  }
  const env = { ...process.env, ...webPort.workerPortEnv(WEB_PORT_CHOICE) };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  let launch;
  if (INSTANCE.development) {
    launch = resolveInstanceWorker(INSTANCE);
    Object.assign(env, launch.env);
  } else if (app.isPackaged) {
    env.OPENPROGRAM_IMMUTABLE_RUNTIME = "1";
    launch = resolvePackagedWorker(process.resourcesPath, app.getVersion());
    Object.assign(env, launch.env);
  } else {
    launch = { command: "openprogram", args: ["worker", "start"] };
  }
  if (action === "restart" || action === "stop") {
    launch = { ...launch, args: [...launch.args.slice(0, -1), action] };
  }
  const child = spawn(launch.command, launch.args, {
    detached: true,
    stdio: "ignore",
    env,
    // A packaged background worker must never allocate a visible console or
    // open the user's default Windows Terminal in front of the app.
    windowsHide: process.platform === "win32",
  });
  child.on("error", (error) => {
    recoveryCoordinator.workerSpawned = false;
    console.error(`[desktop] worker start failed: ${error.message}`);
  });
  child.on("exit", (code) => {
    recordWorkerCommandExit(recoveryCoordinator, action, code);
  });
  child.unref();
}

// Who holds this App's port when it is not this account's worker, as
// `web auth-url` named it; null while the port is free or ours. Such a
// listener is never given this account's token.
let portHeldBy = null;
function notePortHeld(holder) {
  portHeldBy = holder || "another program or account";
}

function resolveWorkerStartUrl() {
  portHeldBy = null;
  return authenticatedStartUrl();
}

// The worker moved this account's port because another program or account
// held it: the App's addresses are fixed for its run, so it starts again on
// the new port. Once per run.
let relaunchingForPort = false;
function followMovedWebPort() {
  if (relaunchingForPort) return true;
  const moved = webPort.movedWebPort(WEB_PORT_CHOICE, WEB_PORT_FILES);
  if (moved === null) return false;
  relaunchingForPort = true;
  console.log(`[desktop] the worker moved from port ${WEB_PORT} to ${moved}: reopening on it`);
  // An AppImage relaunches from its file, not its mount, which goes away.
  app.relaunch(shellContext?.relaunchOptions?.());
  app.quit();
  return true;
}

// While a port the user set is held by someone else, nothing changes until
// they quit it; ask a little less often than the one-second startup probe.
const PORT_HELD_INTERVAL_MS = 5_000;

function authenticatedStartUrl() {
  if (shellContext) {
    let launch;
    try {
      launch = shellContext.resolveWorker();
    } catch {
      return null;
    }
    return resolveAuthenticatedStartUrl(START_URL, { ...process.env, ...launch.env }, [
      { command: launch.authCommand, args: launch.authArgs },
    ], notePortHeld);
  }
  if (!app.isPackaged) return resolveAuthenticatedStartUrl(START_URL, process.env, undefined, notePortHeld);
  const launch = INSTANCE.development
    ? resolveInstanceWorker(INSTANCE)
    : resolvePackagedWorker(process.resourcesPath, app.getVersion());
  return resolveAuthenticatedStartUrl(START_URL, { ...process.env, ...launch.env }, [
    {
      command: launch.authCommand || launch.command,
      args: ["-I", "-B", "-m", "openprogram"],
    },
  ], notePortHeld);
}

function readLocalJson(url, headers = {}, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const request = http.get(url, { headers, timeout: timeoutMs }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => {
        try {
          resolve(response.statusCode === 200 ? JSON.parse(body) : null);
        } catch {
          resolve(null);
        }
      });
    });
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", () => resolve(null));
  });
}

/** The version the worker on this App's port runs; older workers answer only with a token. */
async function runningWorkerVersion(authenticatedUrl) {
  const health = await readLocalJson(HEALTH_URL);
  if (typeof health?.version === "string") return health.version;
  const token = ownerAuthTokenFromStartUrl(authenticatedUrl);
  if (!token) return null;
  const system = await readLocalJson(new URL("/api/system/version", START_URL).toString(), {
    Authorization: `Bearer ${token}`,
  });
  return typeof system?.currentVersion === "string" ? system.currentVersion : null;
}

/** The version a worker on this port should run, or null when any may. */
function expectedWorkerVersion() {
  if (INSTANCE.development || !app.isPackaged || process.env.OPENPROGRAM_DESKTOP_URL) return null;
  if (shellContext) return shellContext.versionId && !shellContext.switchingInProgress?.() ? runningReleaseVersion() : null;
  return app.getVersion();
}

// While a worker from another version is being replaced, its health answer is
// not the one the windows are waiting for.
const WORKER_VERSION_RESTART_MS = 60_000;
let workerVersionRestart = null;

async function restartWorkerFromOtherVersion(authenticatedUrl) {
  const expected = expectedWorkerVersion();
  if (!expected || recoveryCoordinator.workerVersionChecked) return false;
  const running = await runningWorkerVersion(authenticatedUrl);
  if (workerVersionAction(recoveryCoordinator, running, expected) !== "restart") return false;
  console.log(`[desktop] the running worker is ${running}, this App is ${expected}: restarting it`);
  workerVersionRestart = { expected, authenticatedUrl, until: Date.now() + WORKER_VERSION_RESTART_MS };
  recoveryCoordinator.workerSpawned = true;
  spawnWorker("restart");
  return true;
}

async function workerVersionSettled() {
  if (!workerVersionRestart) return true;
  const { expected, authenticatedUrl, until } = workerVersionRestart;
  if (Date.now() < until && await runningWorkerVersion(authenticatedUrl) !== expected) return false;
  workerVersionRestart = null;
  return true;
}

async function resolveStartUrl() {
  if (followMovedWebPort()) return errorPageUrl();
  let workerWasReachable = false;
  for (let i = 0; i < 3; i++) {
    const healthy = await probe(HEALTH_URL, 1000);
    if (!healthy && !await webPort.portListening(WEB_PORT)) continue;
    const authenticated = resolveWorkerStartUrl();
    if (authenticated) {
      workerWasReachable = true;
      recoveryCoordinator.workerSpawned = false;
      if (await restartWorkerFromOtherVersion(authenticated)) return errorPageUrl();
      return authenticated;
    }
    if (portHeldBy !== null) {
      // Not this account's worker. A port the user set stays theirs to free;
      // a chosen one moves when this account's own worker starts.
      if (webPort.portIsFixed(WEB_PORT_CHOICE)) return errorPageUrl("port");
      break;
    }
    if (healthy) {
      workerWasReachable = true;
      recoveryCoordinator.workerSpawned = false;
    }
  }
  if (!workerWasReachable && appInstallInProgress()) return errorPageUrl("updating");
  if (!workerWasReachable && !recoveryCoordinator.workerSpawned) {
    recoveryCoordinator.workerSpawned = true;
    spawnWorker();
  }
  return errorPageUrl();
}

// The shell's recovery page takes over when the version the launcher fell back
// to does not come up either. Offered once per outage, so closing it sticks.
let unreachableSince = null;
let recoveryOffered = false;
function watchManagedRecovery(reachable, installing) {
  if (!shellContext?.recoveryDue) return;
  if (reachable || installing) {
    unreachableSince = null;
    recoveryOffered = false;
    return;
  }
  unreachableSince ??= Date.now();
  if (!recoveryOffered && shellContext.recoveryDue(Date.now() - unreachableSince)) {
    recoveryOffered = true;
    void shellContext.openRecovery("failed");
  }
}

function stopWindowRecovery(ctx) {
  const state = ctx.recovery;
  if (state.timer !== null) clearInterval(state.timer);
  state.active = false;
  state.probeInFlight = false;
  state.timer = null;
}

async function runWindowRecoveryProbe(ctx) {
  const state = ctx.recovery;
  if (ctx.win.isDestroyed() || !beginRecoveryProbe(state, Date.now())) return;
  if (followMovedWebPort()) return;
  const healthy = await probe(HEALTH_URL, 1000) && await workerVersionSettled();
  const listening = healthy || await webPort.portListening(WEB_PORT);
  // A port nobody listens on any more is no longer held by anyone.
  if (!listening) portHeldBy = null;
  const authenticated = listening ? resolveWorkerStartUrl() : null;
  const heldByOther = !authenticated && portHeldBy !== null;
  if (heldByOther && webPort.portIsFixed(WEB_PORT_CHOICE)) {
    // Starting a worker cannot help: it would refuse the held port too. Say
    // who holds it, and keep checking so the UI loads once it is freed.
    state.probeInFlight = false;
    state.nextProbeAt = Date.now() + PORT_HELD_INTERVAL_MS;
    watchManagedRecovery(true, false);
    showWaitPage(ctx, "port");
    return;
  }
  // Another account's worker or another program answering on a port this
  // account chose is not reachable for it: its own worker starts and moves.
  const reachable = healthy && !heldByOther;
  const installing = !reachable && appInstallInProgress();
  watchManagedRecovery(reachable, installing);
  const action = finishRecoveryProbe(
    state,
    recoveryCoordinator,
    reachable,
    !!authenticated,
    Date.now(),
    RECOVERY_INTERVAL_MS,
    installing,
  );
  if (action === "installing") {
    showWaitPage(ctx, "updating");
  } else if (!reachable && !ctx.win.isDestroyed() && isErrorPageUrl(ctx.win.webContents.getURL?.())) {
    // The installer is done (or was never there): back to the plain wait.
    showWaitPage(ctx, "waiting");
  }
  if (action === "spawn" || action === "restart") {
    spawnWorker(action === "restart" ? "restart" : "start");
  } else if (action === "load" && !ctx.win.isDestroyed()) {
    if (state.timer !== null) clearInterval(state.timer);
    state.timer = null;
    const startUrl = await selfUpdateReopen.resolveStartUrl(ctx, authenticated);
    if (!ctx.win.isDestroyed()) void ctx.win.loadURL(startUrl).catch(() => {});
  }
}

function startWindowRecovery(ctx, showErrorPage = true) {
  if (ctx.win.isDestroyed()) return;
  if (!ctx.recovery.active) {
    startRecoveryCycle(ctx.recovery);
    ctx.recovery.timer = setInterval(
      () => { void runWindowRecoveryProbe(ctx); },
      RECOVERY_INTERVAL_MS,
    );
  }
  if (showErrorPage) showWaitPage(ctx, appInstallInProgress() ? "updating" : "waiting");
  void runWindowRecoveryProbe(ctx);
}

function recoverErroredWindows() {
  for (const ctx of windows.values()) {
    if (
      ctx.recovery.active ||
      isErrorPageUrl(ctx.win.webContents.getURL?.())
    ) {
      startWindowRecovery(ctx);
    }
  }
}

// ------------------------------------------------------------- window state

const stateFile = () => path.join(app.getPath("userData"), STATE_FILE_NAME);

function currentDisplays() {
  try {
    return require("electron").screen.getAllDisplays();
  } catch (_e) {
    return [];
  }
}

function currentPrimaryDisplay() {
  try {
    return require("electron").screen.getPrimaryDisplay();
  } catch (_e) {
    return null;
  }
}

function loadWindowState() {
  return loadPersistedState(stateFile(), currentDisplays(), {
    primary: currentPrimaryDisplay(),
  });
}

// ----------------------------------------------------------------- web tabs

function makeWindowContext(id, win) {
  return {
    id,
    win,
    views: new Map(),
    visibleViewIds: new Set(),
    pendingTransferToken: null,
    recovery: createRecoveryState(),
  };
}

const windows = new Map();
const contextsByBrowserWindowId = new Map();
let lastFocusedWindowId = null;
// Cross-window drop cue: the id of the window the drag cursor currently hovers
// (a mergeable OpenProgram window that is NOT the drag source). That window
// shows an "add tab here" affordance while it holds this slot. Enter/leave are
// pushed from the existing window-at-cursor poll so no second loop is needed.
let currentHoverTargetId = null;

/** Point the cross-window hover cue at `id` (or null to clear). Sends
 *  hover-leave to the previously highlighted window and hover-enter to the new
 *  one, so at most one destination window is ever highlighted. */
function setTransferHoverTarget(id) {
  if (id === currentHoverTargetId) return;
  const prev = currentHoverTargetId ? windows.get(currentHoverTargetId) : null;
  if (prev && !prev.win.isDestroyed()) {
    prev.win.webContents.send("tab-transfer:hover-leave");
  }
  currentHoverTargetId = id;
  const next = id ? windows.get(id) : null;
  if (next && !next.win.isDestroyed()) {
    next.win.webContents.send("tab-transfer:hover-enter");
  }
}

const transferDecisionFile = () =>
  path.join(app.getPath("userData"), "tab-transfers.json");

const browsingHistoryFile = () =>
  path.join(app.getPath("userData"), "browsing-history.json");
const downloadsFile = () => path.join(app.getPath("userData"), "downloads.json");
const downloads = new Map();
const activeDownloads = new Map();
let activeBrowserImport = null;
const DOWNLOAD_STATES = new Set(["progressing", "completed", "cancelled", "interrupted"]);

const {
  downloadsRoot,
  pathInside,
  pathInsideOrEqual,
  allowedDownloadPath,
  validDownloadEntry,
  publicDownloadEntry,
  loadDownloads,
  saveDownloads,
  downloadEntry,
  broadcastDownload,
  downloadReservationKey,
  uniqueDownloadPath,
  registerDownloads,
} = require("./main/web-views/downloads").createDownloads({
  DOWNLOAD_STATES,
  activeDownloads,
  app,
  crypto,
  downloads,
  downloadsFile,
  fs,
  path,
  process,
  session,
  windows,
});

// History is best-effort: a failed write must never break navigation.
function safeRecordVisit(visit) {
  try {
    recordVisit(browsingHistoryFile(), visit);
  } catch (_error) {
    /* history is not worth crashing a navigation over */
  }
}

const {
  reparentRecords,
  restoreRecords,
  makeTransferCoordinator,
} = require("./main/web-views/transfers").createTransfers({
  COMMIT_DECISION_RETRY_LIMIT,
  COMMIT_RECONCILE_INITIAL_MS,
  COMMIT_RECONCILE_MAX_MS,
  DESTINATION_UNDO_TIMEOUT_MS,
  TRANSFER_TIMEOUT_MS,
  ackTransferDecision,
  centerHiddenWindowOnCursor: (...args) => centerHiddenWindowOnCursor(...args),
  clearActionCue: (...args) => clearActionCue(...args),
  clearTimeout,
  closeActionCueWindow: (...args) => closeActionCueWindow(...args),
  closeControlOverlay: (...args) => closeControlOverlay(...args),
  createWindow: (...args) => createWindow(...args),
  crypto,
  loadTransferDecisions,
  putTransferDecision,
  requestPipZoomRestore: (...args) => requestPipZoomRestore(...args),
  saveTransferDecisionsAtomic,
  setTimeout,
  showWindowSmoothly: (...args) => showWindowSmoothly(...args),
  transferDecisionFile,
  validateTransferPayload,
  windows,
});

const tabTransfers = makeTransferCoordinator();
// Other applications' windows inside tabs; made once the app is ready.
let embeddedWindows = null;
let screenshots = null;
let appQuitting = false;

function contextForSender(event) {
  const win = event?.sender
    ? BrowserWindow.fromWebContents(event.sender)
    : null;
  const ctx = win ? contextsByBrowserWindowId.get(win.id) : null;
  return ctx && !ctx.win.isDestroyed() ? ctx : null;
}

function focusedContext() {
  const focused = BrowserWindow.getFocusedWindow();
  const direct = focused ? contextsByBrowserWindowId.get(focused.id) : null;
  if (direct && !direct.win.isDestroyed()) {
    lastFocusedWindowId = direct.id;
    return direct;
  }
  if (focused) return null;
  const recent = lastFocusedWindowId
    ? windows.get(lastFocusedWindowId)
    : null;
  if (recent && !recent.win.isDestroyed()) return recent;
  if (recent) lastFocusedWindowId = null;
  return null;
}

function ownerOf(record) {
  const ctx = record ? windows.get(record.ownerId) : null;
  return ctx
    && !ctx.win.isDestroyed()
    && ctx.views.get(record.id) === record
    ? ctx
    : null;
}

function recordFor(ctx, id) {
  if (tabTransfers.isLocked(id)) return null;
  const record = ctx?.views.get(id);
  return record && record.ownerId === ctx.id ? record : null;
}

const debuggerHolds = new Map();
const ACTION_CUE_MS = 2800;

function acquireDebugger(webContents) {
  const client = webContents?.debugger;
  if (!client || webContents.isDestroyed?.()) return null;
  let hold = debuggerHolds.get(client);
  if (!hold) {
    let attachedHere = false;
    try {
      if (!client.isAttached()) {
        client.attach("1.3");
        attachedHere = true;
      }
    } catch {
      return null;
    }
    hold = { count: 0, attachedHere };
    debuggerHolds.set(client, hold);
  }
  hold.count += 1;
  return client;
}

function releaseDebugger(webContents) {
  const client = webContents?.debugger;
  if (!client) return;
  const hold = debuggerHolds.get(client);
  if (!hold) return;
  hold.count -= 1;
  if (hold.count > 0) return;
  debuggerHolds.delete(client);
  if (!hold.attachedHere) return;
  try {
    if (client.isAttached()) client.detach();
  } catch {
    /* session may already be gone */
  }
}

async function withDebugger(webContents, fn) {
  const client = acquireDebugger(webContents);
  if (!client) return null;
  try {
    return await fn(client);
  } catch {
    return null;
  } finally {
    releaseDebugger(webContents);
  }
}

const {
  viewZoomFactor,
  cssViewportSize,
  finiteNumber,
  pointInViewport,
  viewportMatchesMarker,
  actionCueResourceId,
  isStaleActionMarker,
  rememberActionCueIdentity,
  boundsDiffer,
  actionCueStillCurrent,
  overlayUiOrigin,
  stopCueMotion,
  hideActionCueWindow,
  closeActionCueWindow,
  cueWindowContentOrigin,
  sendPendingCue,
  ensureActionCueWindow,
  bindHostOverlayRelayout,
  layoutActionCue,
  placeActionCueWindow,
  setControlGlow,
  cleanupCue,
  clearActionCue,
  showActionView,
} = require("./main/windows/action-cues").createActionCues({
  ACTION_CUE_MS,
  BrowserWindow,
  START_URL,
  WEB_PORT,
  clearTimeout,
  layoutControlOverlay: (...args) => layoutControlOverlay(...args),
  nativeTheme,
  ownerOf: (...args) => ownerOf(...args),
  path,
  recordFor: (...args) => recordFor(...args),
  setTimeout,
});

// The glow, badge and agent cursor over a desktop app an Agent controls.
const agentControl = require("./main/worker/agent-control").createAgentControl({
  BrowserWindow,
  nativeTheme,
  overlayUiOrigin,
  path,
  setTimeout,
  clearTimeout,
});

const {
  sendState,
  forwardFindResult,
  handleWebTabShortcut,
  isWebUrl,
  isTabUrl,
  sendWebTabPopup,
  showWebTabContextMenu,
  loadView,
  ensureView,
  navigateView,
  pipLayoutZoom,
  rememberUserZoom,
  setPipZoom,
  requestPipZoomRestore,
  restorePendingPipZoom,
  findView,
  stopFindView,
  pickElementInView,
  pickRegionInView,
  cancelPickInView,
  captureView,
  zoomView,
  printPdfDefaultName,
  printView,
} = require("./main/web-views/web-views").createWebViews({
  Buffer,
  Menu,
  WebContentsView,
  app,
  boundsDiffer: (...args) => boundsDiffer(...args),
  clearActionCue: (...args) => clearActionCue(...args),
  hideActionCueWindow: (...args) => hideActionCueWindow(...args),
  layoutActionCue: (...args) => layoutActionCue(...args),
  clipboard,
  crypto,
  dialog,
  fs,
  hideControlOverlay: (...args) => hideControlOverlay(...args),
  layoutControlOverlay: (...args) => layoutControlOverlay(...args),
  ownerOf: (...args) => ownerOf(...args),
  path,
  process,
  recordFor: (...args) => recordFor(...args),
  safeRecordVisit: (...args) => safeRecordVisit(...args),
  withDebugger: (...args) => withDebugger(...args),
  tabTransfers,
});

// Annotate mode runs in the pickers' isolated world (PICKER_WORLD in
// web-views.js), so starting either one ends the other.
const { annotateView } = require("./main/web-views/webtab-annotate").createWebTabAnnotate({
  ownerOf: (...args) => ownerOf(...args),
  recordFor: (...args) => recordFor(...args),
  world: 1207,
});

const {
  normalizedBounds,
  rendererZoomFactor,
  normalizedRendererBounds,
  normalizedRendererMenuOptions,
  syncVisibleViews,
  currentVisibleItems,
  showView,
  hideView,
  devToolsTargetId,
  activateView,
  resolveView,
  inspectView,
  previewView,
  withView,
  runNativeNavigation,
  destroyView,
  clearOwnedViews,
} = require("./main/windows/view-layout").createViewLayout({
  syncWebCorners: (...args) => syncWebCorners(...args),
  clearActionCue: (...args) => clearActionCue(...args),
  closeActionCueWindow: (...args) => closeActionCueWindow(...args),
  closeControlOverlay: (...args) => closeControlOverlay(...args),
  ensureView: (...args) => ensureView(...args),
  isTabUrl: (...args) => isTabUrl(...args),
  navigateView: (...args) => navigateView(...args),
  recordFor: (...args) => recordFor(...args),
  tabTransfers,
  withDebugger: (...args) => withDebugger(...args),
});

const { syncWebCorners, closeWebCorners } = require("./main/windows/web-corners").createWebCorners({ WebContentsView });

// Pages nobody has used for this long close their renderer process and
// come back at the same URL when next shown or used (idle-discard.js).
const PAGE_IDLE_SLEEP_MS = 10 * 60 * 1000;
const idleDiscard = require("./main/web-views/idle-discard").createIdleDiscard({
  now: () => Date.now(),
  idleMs: PAGE_IDLE_SLEEP_MS,
  destroyView: (...args) => destroyView(...args),
  tabTransfers,
});
const idleSweepTimer = setInterval(() => {
  for (const ctx of windows.values()) {
    try {
      idleDiscard.sweep(ctx);
    } catch (_error) {
      /* a Page that will not close stays as it is */
    }
  }
}, 60 * 1000);
idleSweepTimer?.unref?.();

function cleanupWindowContext(ctx) {
  if (
    activeBrowserImport?.ownerId === ctx.id
    && !activeBrowserImport.controller.signal.aborted
  ) {
    activeBrowserImport.controller.abort();
  }
  stopWindowRecovery(ctx);
  closeMainMenu(ctx, true);
  tabTransfers.contextDestroyed(ctx);
  clearOwnedViews(ctx);
  closeWebCorners(ctx);
  ctx.views.clear();
  ctx.visibleViewIds = new Set();
  if (windows.get(ctx.id) === ctx) windows.delete(ctx.id);
  if (contextsByBrowserWindowId.get(ctx.win.id) === ctx) {
    contextsByBrowserWindowId.delete(ctx.win.id);
  }
  if (lastFocusedWindowId === ctx.id) lastFocusedWindowId = null;
  if (![...windows.values()].some((item) => item !== ctx && item.recovery.active)) {
    recoveryCoordinator.workerSpawned = false;
  }
}

// -------------------------------------------------------------- main menu
//
// The ⋮ main menu is its own top-layer WebContentsView loading the app's
// /menu-overlay/main-menu route, added AFTER the web-tab views so it
// covers them (a DOM Radix menu can't, since native views paint above the
// DOM). Singleton per window; closes on outside click (its own blur),
// Esc, window blur/resize, and after a choice.
const MAIN_MENU_WIDTH = 224;
const MAIN_MENU_HEIGHT = 88;
// Extra room around the panel so its drop shadow isn't clipped by the
// view's own edge (the panel itself is smaller than the view).
const MAIN_MENU_GUTTER = 24;
// Generic context-menu overlay (opts.items given): the initial bounds are a
// GUESS (rows are 24px tall, MENU_PANEL adds 6px padding + 1px border per
// side) that only has to survive the first paint — the overlay document
// measures its own panel and sends main-menu:resize with the real pixel size,
// which re-clamps the view against the same window margins. Estimating width
// from label text here would need font metrics the main process doesn't have.
const CONTEXT_MENU_WIDTH = 200;
const CONTEXT_MENU_ROW_HEIGHT = 24;
const CONTEXT_MENU_CHROME = 16;
const MENU_THEME_IDS = themeChrome.THEME_IDS;
const MENU_THEME_ID_SET = new Set(MENU_THEME_IDS);

/** The overlay document measured its own panel — resize the host view to the
 *  real size and re-clamp it against the window edges. Only context menus
 *  (which have a stored anchor) participate; the fixed-size main menu ignores
 *  this. */
const {
  resizeMenuOverlay,
  hasNestedMenuItems,
  menuOverlayUrl,
  closeMainMenu,
  cancelMainMenuClose,
  scheduleMainMenuClose,
  openMainMenu,
  contextForMenuSender,
} = require("./main/windows/menus").createMenus({
  CONTEXT_MENU_CHROME,
  CONTEXT_MENU_ROW_HEIGHT,
  CONTEXT_MENU_WIDTH,
  MAIN_MENU_GUTTER,
  MAIN_MENU_HEIGHT,
  MAIN_MENU_WIDTH,
  MENU_THEME_ID_SET,
  START_URL,
  WEB_PORT,
  WebContentsView,
  cascadeMenuGeometry,
  clampContextMenuPanel,
  clearTimeout,
  contextForSender: (...args) => contextForSender(...args),
  path,
  setTimeout,
  windows,
});

const {
  controlOverlayOwner,
  actionCueOwner,
  hideControlOverlay,
  closeControlOverlay,
  clampControlLayout,
  sendControlOverlayUpdate,
  layoutControlOverlay,
  setControlOverlay,
  handleControlOverlayEvent,
  nativeMenuOwner,
  nativeContextMenus,
} = require("./main/windows/control-overlays").createControlOverlays({
  Menu,
  UI_ORIGIN,
  WebContentsView,
  bindHostOverlayRelayout: (...args) => bindHostOverlayRelayout(...args),
  contextForSender: (...args) => contextForSender(...args),
  finiteNumber: (...args) => finiteNumber(...args),
  overlayUiOrigin: (...args) => overlayUiOrigin(...args),
  ownerOf: (...args) => ownerOf(...args),
  path,
  recordFor: (...args) => recordFor(...args),
  rendererZoomFactor: (...args) => rendererZoomFactor(...args),
  require,
  windows,
});

function registerWebTabIpc() {
  require("./main/web-views/clipboard").registerClipboardIpc(ipcMain, clipboard, nativeMenuOwner);
  ipcMain.handle("native-menu:popup", (event, opts) => {
    const ctx = nativeMenuOwner(event);
    if (!ctx) throw new Error("Unauthorized menu sender");
    return nativeContextMenus.popup(ctx.win, event.sender, opts, rendererZoomFactor(event));
  });
  ipcMain.on("native-menu:close", (event, requestId) => {
    if (nativeMenuOwner(event) && typeof requestId === "string") nativeContextMenus.close(event.sender, requestId);
  });
  ipcMain.on("main-menu:open", (event, opts) => {
    const ctx = contextForSender(event);
    if (ctx) {
      openMainMenu(
        ctx,
        normalizedRendererMenuOptions(event, opts),
        rendererZoomFactor(event),
      );
    }
  });
  ipcMain.on("main-menu:close", (event) => {
    const ctx = contextForMenuSender(event);
    if (ctx) closeMainMenu(ctx);
  });
  ipcMain.on("main-menu:schedule-close", (event, delay) => {
    const ctx = contextForMenuSender(event);
    if (ctx) scheduleMainMenuClose(ctx, delay);
  });
  ipcMain.on("main-menu:cancel-close", (event) => {
    const ctx = contextForMenuSender(event);
    if (ctx) cancelMainMenuClose(ctx);
  });
  ipcMain.on("main-menu:resize", (event, size) => {
    const ctx = contextForMenuSender(event);
    if (ctx) resizeMenuOverlay(ctx, normalizedRendererBounds(event, size));
  });
  ipcMain.on("main-menu:update-items", (event, items) => {
    const ctx = nativeMenuOwner(event);
    const view = ctx?.mainMenuView;
    if (view && !view.webContents.isDestroyed() && Array.isArray(items)) {
      view.webContents.send("main-menu:update", { items });
    }
  });
  ipcMain.on("main-menu:choose", (event, id, options) => {
    const ctx = contextForMenuSender(event);
    if (!ctx) return;
    ctx.win.webContents.send("main-menu:action", id);
    if (options?.keepOpen !== true) closeMainMenu(ctx);
  });
  ipcMain.on("webtab:ensure", (event, id, url) => {
    const ctx = contextForSender(event);
    // A Page gets its first view only at a URL the view loads (a restored
    // about:blank popup tab gets none until it navigates): a view that never
    // loads has no document and answers no DevTools command, which stalls
    // every client of the App's DevTools endpoint, the Agent's browser tool
    // among them, until it times out.
    if (ctx && (ctx.views.has(id) || ctx.sleepingViews?.has(id) || isTabUrl(url))) ensureView(ctx, id, url);
  });
  ipcMain.on("webtab:navigate", (event, id, url) => {
    const ctx = contextForSender(event);
    if (!ctx) return;
    const pending = navigateView(ctx, id, url);
    if (pending) {
      const owned = recordFor(ctx, id);
      void pending.catch(() => {});
    }
  });
  ipcMain.handle("webtab:activate", (event, id, url, requireVisible) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string"
      ? activateView(
          ctx,
          id,
          typeof url === "string" ? url : "",
          requireVisible === true,
        )
      : null;
  });
  ipcMain.handle("webtab:resolve", (event, id) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? resolveView(ctx, id) : null;
  });
  ipcMain.handle("webtab:inspect", (event, id) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? inspectView(ctx, id) : null;
  });
  ipcMain.handle("webtab:preview", (event, id, allowBackground) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string"
      ? previewView(ctx, id, allowBackground === true)
      : null;
  });
  ipcMain.on("webtab:sync-visible", (event, items) => {
    const ctx = contextForSender(event);
    if (ctx) {
      const normalizedItems = Array.isArray(items)
        ? items.map((item) => ({
            ...item,
            bounds: normalizedRendererBounds(event, item?.bounds),
            corners: item?.corners ? { ...item.corners, zoom: rendererZoomFactor(event) } : null,
          }))
        : items;
      syncVisibleViews(ctx, normalizedItems);
    }
  });
  ipcMain.on("webtab:set-bounds", (event, id, bounds) => {
    const ctx = contextForSender(event);
    if (ctx && bounds) {
      withView(ctx, id, (record) => {
        record.view.setBounds(normalizedRendererBounds(event, bounds));
        syncWebCorners(ctx, currentVisibleItems(ctx).map((item) => ({ ...item, record: recordFor(ctx, item.id) })));
      });
    }
  });
  ipcMain.on("webtab:show", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) showView(ctx, id);
  });
  ipcMain.on("webtab:hide", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) hideView(ctx, id);
  });
  ipcMain.on("webtab:destroy", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) destroyView(ctx, id);
  });
  ipcMain.handle("webtab:destroy-confirmed", (event, id) => {
    const ctx = contextForSender(event);
    return ctx ? destroyView(ctx, id, { strict: true }) : false;
  });
  ipcMain.on("webtab:reload", (event, id, options) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => (options?.hard ? wc.reloadIgnoringCache() : wc.reload()));
  });
  ipcMain.on("webtab:stop", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.stop());
  });
  ipcMain.on("webtab:go-back", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.navigationHistory.goBack());
  });
  ipcMain.on("webtab:go-forward", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.navigationHistory.goForward());
  });
  ipcMain.on("webtab:find", (event, id, query, options) => {
    const ctx = contextForSender(event);
    if (ctx) findView(ctx, id, query, options);
  });
  ipcMain.on("webtab:stop-find", (event, id, action) => {
    const ctx = contextForSender(event);
    if (ctx) stopFindView(ctx, id, action);
  });
  ipcMain.handle("webtab:pick-element", (event, id) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? pickElementInView(ctx, id) : null;
  });
  ipcMain.handle("webtab:pick-region", (event, id) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? pickRegionInView(ctx, id) : null;
  });
  ipcMain.on("webtab:cancel-pick", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx && typeof id === "string") cancelPickInView(ctx, id);
  });
  ipcMain.handle("webtab:annotate", (event, id, action, options) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" && typeof action === "string"
      ? annotateView(ctx, id, action, options) : false;
  });
  ipcMain.handle("webtab:zoom", (event, id, action) => {
    const ctx = contextForSender(event);
    return ctx ? zoomView(ctx, id, action) : null;
  });
  ipcMain.on("webtab:set-pip-zoom", (event, id, width, height) => {
    const ctx = contextForSender(event);
    const zoom = rendererZoomFactor(event);
    if (ctx) setPipZoom(ctx, id, width == null ? null : Math.round(width * zoom),
      height == null ? undefined : Math.round(height * zoom));
  });
  ipcMain.handle("webtab:print", (event, id) => {
    const ctx = contextForSender(event);
    return ctx ? printView(ctx, id) : false;
  });
  ipcMain.handle("webtab:capture", (event, id, mode) => {
    const ctx = contextForSender(event);
    return ctx ? captureView(ctx, id, mode === "presentation" ? mode : undefined) : null;
  });
  ipcMain.handle("webtab:show-action", (event, id, marker) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? showActionView(ctx, id, marker) : false;
  });
  ipcMain.on("webtab:control-glow", (event, id, held) => {
    const ctx = contextForSender(event);
    if (!ctx || typeof id !== "string") return;
    setControlGlow(ctx, id, held === true);
  });
  ipcMain.on("webtab:control-overlay", (event, id, payload) => {
    const ctx = contextForSender(event);
    if (!ctx || typeof id !== "string") return;
    setControlOverlay(ctx, id, payload);
  });
  ipcMain.on("webtab:control-overlay-ready", (event) => {
    const owner = controlOverlayOwner(event);
    if (!owner) return;
    owner.record.controlReady = true;
    sendControlOverlayUpdate(owner.record);
  });
  ipcMain.on("webtab:control-overlay-event", (event, payload) => {
    handleControlOverlayEvent(event, payload);
  });
  ipcMain.on("native:action-cue", (event, cue) => {
    if (!contextForSender(event)) return;
    agentControl.showAction(cue);
  });
  ipcMain.on("native:control", (event, data) => {
    if (!contextForSender(event)) return;
    agentControl.showControl(data);
  });
  ipcMain.on("agent-control:ready", (event) => {
    if (agentControl.markReady(event)) return;
    // The same overlay page over a web page the Agent operates.
    const owner = actionCueOwner(event);
    if (!owner) return;
    owner.record.actionCueReady = true;
    sendPendingCue(owner.record);
  });
  ipcMain.handle("history:list", (_event, options) => {
    try {
      return listHistory(browsingHistoryFile(), options || {});
    } catch (_error) {
      return [];
    }
  });
  ipcMain.handle("history:delete", (_event, url, visitedAt) => {
    try {
      return deleteHistoryEntry(browsingHistoryFile(), url, visitedAt);
    } catch (_error) {
      return false;
    }
  });
  ipcMain.handle("history:clear", () => {
    try {
      return clearHistory(browsingHistoryFile());
    } catch (_error) {
      return false;
    }
  });
  ipcMain.handle("downloads:list", (event, options) => {
    if (!contextForSender(event)) return [];
    const query = String(options?.query || "").trim().toLowerCase();
    return [...downloads.values()]
      .filter((entry) => !query || entry.filename.toLowerCase().includes(query)
        || entry.url.toLowerCase().includes(query))
      .sort((a, b) => b.startedAt - a.startedAt)
      .map(publicDownloadEntry);
  });
  ipcMain.handle("downloads:open", async (event, id) => {
    if (!contextForSender(event)) return false;
    const entry = downloadEntry(String(id || ""));
    if (!entry || entry.state !== "completed" || !allowedDownloadPath(entry.path, true)) return false;
    return (await shell.openPath(entry.path)) === "";
  });
  ipcMain.handle("downloads:show", (event, id) => {
    if (!contextForSender(event)) return false;
    const entry = downloadEntry(String(id || ""));
    if (!entry || !allowedDownloadPath(entry.path, true)) return false;
    shell.showItemInFolder(entry.path);
    return true;
  });
  ipcMain.handle("downloads:cancel", (event, id) => {
    if (!contextForSender(event)) return false;
    const item = activeDownloads.get(String(id || ""));
    if (!item) return false;
    item.cancel();
    return true;
  });
  ipcMain.handle("downloads:clear", (event) => {
    if (!contextForSender(event)) return false;
    const prior = new Map(downloads);
    for (const [id, entry] of downloads) {
      if (!activeDownloads.has(id)) downloads.delete(id);
    }
    try {
      saveDownloads();
    } catch (_error) {
      downloads.clear();
      for (const [id, entry] of prior) downloads.set(id, entry);
      return false;
    }
    broadcastDownload(null);
    return true;
  });

  ipcMain.handle("browser-import:list-sources", (event) => {
    if (!contextForSender(event)) return [];
    try {
      return listBrowserSources();
    } catch (_error) {
      return [];
    }
  });
  ipcMain.handle("browser-import:run", (event, request) => {
    const ctx = contextForSender(event);
    if (!ctx) return { ok: false, error: "unauthorized" };
    const requestId = request?.requestId;
    if (
      typeof requestId !== "string"
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(requestId)
    ) {
      return { ok: false, error: "invalid_request" };
    }
    if (activeBrowserImport) {
      return { ok: false, error: "import_busy" };
    }
    const task = {
      requestId,
      ownerId: ctx.id,
      ownerSender: event.sender,
      controller: new AbortController(),
      promise: null,
    };
    activeBrowserImport = task;
    task.promise = (async () => {
      try {
        const result = await runBrowserImport(request || {}, {
          targetSession: session.fromPartition("persist:webtabs"),
          signal: task.controller.signal,
        });
        const historyMerge = result.history.length
          ? importHistoryEntries(browsingHistoryFile(), result.history)
          : {
              imported: 0,
              total: listHistory(browsingHistoryFile(), { limit: 5000 }).length,
            };
        return {
          ok: true,
          source: result.source,
          history: historyMerge,
          bookmarks: result.bookmarks,
          cookies: result.cookies,
        };
      } catch (error) {
        return { ok: false, error: error?.code || "import_failed" };
      } finally {
        if (activeBrowserImport === task) activeBrowserImport = null;
      }
    })();
    return task.promise;
  });
  ipcMain.handle("browser-import:cancel", (event, requestId) => {
    const ctx = contextForSender(event);
    if (
      !ctx
      || !activeBrowserImport
      || activeBrowserImport.ownerId !== ctx.id
      || activeBrowserImport.ownerSender !== event.sender
      || activeBrowserImport.requestId !== requestId
      || activeBrowserImport.controller.signal.aborted
    ) {
      return false;
    }
    activeBrowserImport.controller.abort();
    return true;
  });
  ipcMain.handle("browser-data:clear", async (event, options) => {
    try {
      if (!contextForSender(event)) return { ok: false };
      const request = options && typeof options === "object" ? options : {};
      if (request.history) clearHistory(browsingHistoryFile());
      if (request.cookies) {
        await session.fromPartition("persist:webtabs").clearStorageData({ storages: ["cookies"] });
        webTabPermissionDecisions?.clear();
      }
      return { ok: true };
    } catch (_error) {
      return { ok: false };
    }
  });

  // One native terminal resource, shared by manual views and Agent bindings.
  // The existing guarded IPC dispatcher continues to enforce UI verification.
  require("./main/terminal/terminal-resource-ipc").registerTerminalResourceIpc({
    ipcMain, app, contextForSender, origin: UI_ORIGIN,
    getOwnerToken: () => ownerAuthTokenFromStartUrl(resolveWorkerStartUrl()),
    spawn: (...args) => require("node-pty").spawn(...args),
    platform: process.platform, env: process.env, fileSystem: fs,
    kill: (pid, signal) => process.kill(pid, signal),
    listProcesses: () => execFileSync("/bin/ps", ["-axo", "pid=,ppid="], { encoding: "utf8" }),
    windowsKill: killWindowsProcessTree, waitForPid: waitForTerminalPid,
    timers: { setTimeout, clearTimeout, setInterval, clearInterval },
  });

  ipcMain.on("desktop:open-external", (_e, url) => {
    try {
      const u = new URL(url);
      if (u.protocol === "http:" || u.protocol === "https:") shell.openExternal(url);
    } catch (_err) {
      /* invalid url, ignore */
    }
  });
  // Renderer closed its last tab → close its window. macOS keeps the app
  // alive with no windows (see window-all-closed), so this never quits.
  ipcMain.on("window:close-self", (event) => {
    BrowserWindow.fromWebContents(event.sender)?.close();
  });
  // Single-tab drag moves the whole window. The renderer sends absolute cursor
  // deltas from drag start; main repositions its frame. Runs in the main
  // process so it isn't starved by the macOS modal drag loop the way a
  // renderer's own frame math would be — and it never fights app-region.
  ipcMain.on("window:move-by", (event, dx, dy) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win || win.isDestroyed()) return;
    const [x, y] = win.getPosition();
    win.setPosition(Math.round(x + dx), Math.round(y + dy));
  });
  // A snapshot of one region of this window's own page, given in CSS pixels.
  // Only a window's main page may ask, and only for itself. It is a small
  // JPEG for the window overview.
  ipcMain.handle("window:capture-region", async (event, rect) => {
    const ctx = contextForSender(event);
    if (!ctx || event.sender !== ctx.win.webContents) return null;
    const values = [rect?.x, rect?.y, rect?.width, rect?.height];
    if (!values.every((value) => Number.isFinite(value)) || rect.width < 1 || rect.height < 1) return null;
    const zoom = event.sender.getZoomFactor() || 1;
    try {
      const image = await event.sender.capturePage({
        x: Math.round(rect.x * zoom),
        y: Math.round(rect.y * zoom),
        width: Math.round(rect.width * zoom),
        height: Math.round(rect.height * zoom),
      });
      if (!image || image.isEmpty()) return null;
      const width = Math.min(image.getSize().width, 720);
      return `data:image/jpeg;base64,${image.resize({ width, quality: "good" }).toJPEG(82).toString("base64")}`;
    } catch (_error) {
      return null;
    }
  });
}

function registerTabTransferIpc() {
  ipcMain.on("tab-transfer:prepare", (event, payload) => {
    const ctx = contextForSender(event);
    event.returnValue = ctx ? tabTransfers.prepare(ctx, payload) : null;
  });
  ipcMain.handle("tab-transfer:inspect", (event, token) => {
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.inspect(ctx, token) : null;
  });
  ipcMain.handle("tab-transfer:accept", (event, token, placement) => {
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.accept(ctx, token, placement) : null;
  });
  ipcMain.handle("tab-transfer:reject", (event, token, reason, duplicateId) => {
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.reject(ctx, token, reason, duplicateId) : null;
  });
  ipcMain.handle("tab-transfer:status", (event, token) => {
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.status(ctx, token) : null;
  });
  ipcMain.handle("tab-transfer:journal-opened", (event, token, role) => {
    const ctx = contextForSender(event);
    return !!ctx && tabTransfers.journalOpened(ctx, token, role);
  });
  ipcMain.handle(
    "tab-transfer:journal-finalized",
    (event, token, role, ownerWindowId) => {
      const ctx = contextForSender(event);
      return !!ctx && tabTransfers.journalFinalized(
        ctx,
        token,
        role,
        ownerWindowId || ctx.id,
      );
    },
  );
  ipcMain.handle("tab-transfer:destination-ready", (event, token, ok) => {
    const ctx = contextForSender(event);
    return !!ctx && tabTransfers.destinationReady(ctx, token, ok);
  });
  ipcMain.handle("tab-transfer:source-removed", (event, token, result) => {
    const ctx = contextForSender(event);
    return !!ctx && tabTransfers.sourceRemoved(ctx, token, result);
  });
  ipcMain.handle("tab-transfer:destination-undone", (event, token, ok) => {
    const ctx = contextForSender(event);
    return !!ctx && tabTransfers.destinationUndone(ctx, token, ok);
  });
  ipcMain.handle("tab-transfer:cancel", (event, token) => {
    setTransferHoverTarget(null); // drag ended — clear any hover highlight
    const ctx = contextForSender(event);
    return !!ctx && tabTransfers.cancel(ctx, token);
  });
  ipcMain.handle("tab-transfer:detach", (event, token) => {
    setTransferHoverTarget(null); // detached into a new window — clear highlight
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.detach(ctx, token) : null;
  });
  ipcMain.handle("tab-transfer:claim-pending", (event, windowId) => {
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.claimPending(ctx, windowId) : null;
  });
  ipcMain.handle("tab-transfer:pending-terminal", (event, windowId) => {
    const ctx = contextForSender(event);
    return ctx ? tabTransfers.pendingTerminal(ctx, windowId) : [];
  });
  // Pointer-driven cross-window drop: read-only hit test for another
  // OpenProgram window under the current cursor position.
  ipcMain.handle("tab-transfer:window-at-cursor", (event) => {
    const ctx = contextForSender(event);
    if (!ctx) return null;
    const { screen } = require("electron");
    const point = screen.getCursorScreenPoint();
    // Resolve the hover target, then push enter/leave cues from this same poll
    // (the renderer already calls this each frame during a detaching drag).
    const hits = [];
    for (const candidate of windows.values()) {
      if (candidate === ctx) continue;
      if (candidate.win.isDestroyed() || !candidate.win.isVisible()) continue;
      // An early tear-off window is visible and sits right under the
      // cursor by construction — it must never be reported as a drop
      // target, or the release would "deliver" the tab back into it.
      if (candidate.pendingTransferToken) continue;
      const bounds = candidate.win.getBounds();
      // Merge targets only the TOP TAB STRIP, not the whole window. Dropping a
      // tab anywhere in the content area must NOT merge (that felt far too
      // eager). The strip band is the traffic-light row height — a tab dropped
      // below it is not a merge.
      const STRIP_BAND_PX = 52;
      if (
        point.x >= bounds.x && point.x < bounds.x + bounds.width
        && point.y >= bounds.y && point.y < bounds.y + STRIP_BAND_PX
      ) {
        hits.push(candidate);
      }
    }
    // Overlapping windows: the topmost window under the cursor wins, never map
    // order (which could pick an occluded window behind the one the user sees).
    // Electron exposes no true global z-order, so approximate: an actually
    // focused window is on top; otherwise the most-recently-focused one
    // (lastFocusedWindowId) is; ties fall back to map order deterministically.
    const rank = (c) =>
      c.win.isFocused() ? 2 : c.id === lastFocusedWindowId ? 1 : 0;
    const hit = hits.reduce(
      (best, c) => (best === null || rank(c) > rank(best) ? c : best),
      null,
    );
    setTransferHoverTarget(hit ? hit.id : null);
    return hit ? hit.id : null;
  });
  // Hand a prepared token to another live window so its renderer stages
  // the incoming transfer (the pointer path has no DOM drop event there).
  ipcMain.handle("tab-transfer:deliver", (event, token, windowId) => {
    const ctx = contextForSender(event);
    const target = windows.get(windowId);
    setTransferHoverTarget(null); // drop committed — never leave a window lit
    if (!ctx || !target || target.win.isDestroyed()) return false;
    target.win.webContents.send("tab-transfer:stage-incoming", { token });
    return true;
  });
}

// --------------------------------------------------------------------- menu

// With sealed versions there is always a way back, even when the backend is
// broken: the shell's recovery page needs nothing from it.
function goBackMenuItem() {
  return { label: "Go back to a previous version", click: () => void shellContext.openRecovery("menu") };
}

/** "Restart to Update" while a downloaded version waits for a restart. */
function restartToUpdateItems() {
  if (!menuReadyVersion) return [];
  return [{
    label: `Restart to Update to ${menuReadyVersion}`,
    click: () => { void desktopUpdates?.install().catch(() => {}); },
  }];
}

function applicationMenu(settingsItem) {
  return {
    label: INSTANCE.name,
    submenu: [
      { role: "about", label: `About ${INSTANCE.name}` },
      ...(shellContext?.openRecovery ? [goBackMenuItem()] : []),
      ...restartToUpdateItems(),
      { type: "separator" },
      settingsItem,
      { type: "separator" },
      { role: "services" },
      { type: "separator" },
      { role: "hide", label: `Hide ${INSTANCE.name}` },
      { role: "hideOthers" },
      { role: "unhide" },
      { type: "separator" },
      { role: "quit", label: `Quit ${INSTANCE.name}` },
    ],
  };
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const send = (channel) => () => {
    const ctx = focusedContext();
    if (ctx) ctx.win.webContents.send(channel);
  };
  // New chat and tab switching reach the app as an `op-tab-shortcut` DOM
  // event (handled by the center tab strip). As menu accelerators they work
  // while a native web page has keyboard focus too.
  const shortcut = (detail) => () => {
    const ctx = focusedContext();
    if (!ctx || ctx.win.isDestroyed()) return;
    const script = `window.dispatchEvent(new CustomEvent("op-tab-shortcut", { detail: ${JSON.stringify(detail)} }))`;
    ctx.win.webContents.executeJavaScript(script).catch(() => {});
  };
  const selectTabItems = Array.from({ length: 9 }, (_, i) => ({
    label: i === 8 ? "Last Tab" : `Tab ${i + 1}`,
    accelerator: `CmdOrCtrl+${i + 1}`,
    click: shortcut({ action: "select-tab", index: i + 1 }),
  }));
  const settingsItem = { label: isMac ? "Settings…" : "Settings", accelerator: "CmdOrCtrl+,", click: shortcut({ action: "open-settings" }) };
  const template = [
    ...(isMac ? [applicationMenu(settingsItem)] : []),
    {
      label: "File",
      submenu: [
        { label: "New Chat", accelerator: "CmdOrCtrl+N", click: shortcut({ action: "new-chat" }) },
        { label: "New Tab", accelerator: "CmdOrCtrl+T", click: send("menu:new-tab") },
        // Cmd+W goes to the renderer (close tab); window close is Cmd+Shift+W.
        { label: "Close Tab", accelerator: "CmdOrCtrl+W", click: send("menu:close-tab") },
        { label: "Reopen Closed Tab", accelerator: "Shift+CmdOrCtrl+T", click: shortcut({ action: "reopen-tab" }) },
        { type: "separator" },
        { role: "close", accelerator: "Shift+CmdOrCtrl+W" },
        // Windows and Linux have no application menu: Settings sits here.
        ...(!isMac ? [{ type: "separator" }, settingsItem, ...restartToUpdateItems()] : []),
        ...(!isMac && shellContext?.openRecovery ? [{ type: "separator" }, goBackMenuItem()] : []),
      ],
    },
    { role: "editMenu" },
    {
      // The View menu's reload roles would reload the App window and drop
      // every web view; the renderer reloads the active page instead.
      label: "View",
      submenu: [
        // The sidebar's bell: the list switches to Activity and back.
        { label: "Activity", accelerator: "Alt+CmdOrCtrl+U", click: shortcut({ action: "toggle-activity" }) },
        { type: "separator" },
        { label: "Reload", accelerator: "CmdOrCtrl+R", click: send("menu:reload") },
        { label: "Force Reload", accelerator: "Shift+CmdOrCtrl+R", click: send("menu:force-reload") },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Tab",
      submenu: [
        { label: "Next Tab", accelerator: "CmdOrCtrl+Shift+]", click: shortcut({ action: "next-tab" }) },
        { label: "Previous Tab", accelerator: "CmdOrCtrl+Shift+[", click: shortcut({ action: "previous-tab" }) },
        { type: "separator" },
        ...selectTabItems,
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// --------------------------------------------------------------------- boot

/** Place a window under the cursor (Chrome drops a torn-off window where
 *  you released it).
 *
 *  `clamp` decides whether the result is confined to the display work area.
 *  It must be true for the INITIAL hidden placement (a window that boots
 *  half off-screen is a bug), and false for every follow frame during the
 *  drag: clamping per frame makes a window dragged toward a screen edge
 *  slide ALONG that edge instead of tracking the cursor. Chrome lets a
 *  dragged window hang off the edge, so we do too — the cursor itself is on
 *  a real display, which keeps the window on a sane one. */
function centerHiddenWindowOnCursor(win, { clamp = true } = {}) {
  if (!win || win.isDestroyed()) return;
  const { screen } = require("electron");
  const point = screen.getCursorScreenPoint();
  const area = screen.getDisplayNearestPoint(point).workArea;
  const { width, height } = win.getBounds();
  // Cursor sits over the grabbed tab, so anchor the window's title strip
  // just under the pointer — the held tab stays under the cursor and the
  // (now modestly-sized) window body opens below, on-screen.
  const rawX = point.x - width / 2;
  const rawY = point.y - 20;
  const x = Math.round(
    clamp
      ? Math.min(Math.max(rawX, area.x), area.x + area.width - width)
      : rawX,
  );
  const y = Math.round(
    clamp
      ? Math.min(Math.max(rawY, area.y), area.y + area.height - height)
      : rawY,
  );
  win.setBounds({ x, y, width, height });
}

/** Show a detached window without the instant pop: start transparent, then
 *  ease opacity to 1 over ~140ms. setOpacity is a no-op on some Linux WMs,
 *  in which case this degrades to today's plain show(). */
function showWindowSmoothly(win) {
  if (!win || win.isDestroyed()) return;
  let reduceMotion = false;
  try {
    reduceMotion = require("electron").nativeTheme.prefersReducedMotion === true;
  } catch {
    /* older Electron without the flag — keep the fade */
  }
  // setOpacity is unreliable on Linux WMs; fall back to a plain show there.
  if (process.platform === "linux" || reduceMotion) {
    win.show();
    return;
  }
  win.setOpacity(0);
  win.show();
  const duration = 140;
  const start = Date.now();
  const step = () => {
    if (win.isDestroyed()) return;
    const t = Math.min((Date.now() - start) / duration, 1);
    win.setOpacity(t);
    if (t < 1) setTimeout(step, 16);
  };
  step();
}

// Launch / dock-click / second-instance share one in-flight main-window
// create. See docs/reference/design/ui/desktop/window-lifecycle.html.
const ensureMainWindow = createMainWindowGate({
  windows,
  createWindow,
});
registerReopenIpc({ ipcMain, windows, recovery: selfUpdateReopen, origin: UI_ORIGIN });
registerUiVerificationIpc({ ipcMain, windows, app, origin: UI_ORIGIN,
  request: selfUpdateReopen.requestVerification, guard: uiVerificationGuard,
  sealedVersion: () => shellContext });

async function createWindow(options = {}) {
  const state = loadWindowState();
  const windowId = options.windowId || "main";
  // A torn-off window is positioned at the drop point (centerHiddenWindowOnCursor
  // in detachUnlatched), so it must NOT inherit the parent's saved (often
  // full-screen) bounds — a 1440×851 window anchored at the cursor spills
  // off-screen and reads as "nothing appeared". Give detached windows a
  // modest, movable size. They stay ephemeral: closing one must not overwrite
  // the main window's persisted chrome / normal bounds.
  const detached = options.detached === true;
  const revealOnReady = !detached && options.show !== false;
  const restored = browserWindowOptionsForPlan(state, { detached });
  const win = new BrowserWindow({
    ...restored,
    // Main stays hidden until chrome is applied and the first frame is
    // painted. Instant show + maximize() is the macOS grow-from-frame
    // animation. Tear-offs still pass show:false and fade in themselves.
    show: detached ? options.show !== false : false,
    backgroundColor: currentChrome.bg,
    ...browserWindowChromeOptions(process.platform, currentChrome),
    webPreferences: {
      preload: path.join(__dirname, "main", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--openprogram-window-id=${windowId}`],
    },
  });
  applyNativeTitleBarChrome(win, process.platform, currentChrome);
  win.webContents.on("did-finish-load", () => {
    applyNativeTitleBarChrome(win, process.platform, currentChrome);
  });
  win.on("leave-full-screen", () => {
    applyNativeTitleBarChrome(win, process.platform, currentChrome);
  });
  if (!detached) {
    attachWindowStatePersistence(win, {
      filePath: stateFile(),
      getDisplays: currentDisplays,
    });
    applyRestoredChrome(win, state);
  }
  const ctx = makeWindowContext(windowId, win);
  if (windowId === "main") win.webContents.once("did-finish-load", () => firstWindowLoaded?.());
  windows.set(windowId, ctx);
  contextsByBrowserWindowId.set(win.id, ctx);
  win.on("focus", () => { lastFocusedWindowId = ctx.id; });
  // The main-menu overlay is anchored to the ⋮ button; a window blur or
  // resize invalidates its position — dismiss it. (Its own view blur
  // handles outside clicks inside the window.)
  win.on("blur", () => closeMainMenu(ctx));
  win.on("resize", () => closeMainMenu(ctx));
  win.on("close", (event) => {
    // Windows embedded in the page go back to the desktop before it closes.
    if (embeddedWindows && embeddedWindows.windowClosing(ctx, event, () => {
      if (appQuitting) app.quit();
      else if (!win.isDestroyed()) win.close();
    })) return;
    tabTransfers.windowClosing(ctx, event);
  });
  win.on("closed", () => cleanupWindowContext(ctx));
  // Tear-offs may be revealed mid-drag before the commit path would
  // have shown them. Main also waits here so the first visible frame
  // is already maximized, not a small rect zooming from the top-left.
  ctx.readyToShow = false;
  win.once("ready-to-show", () => {
    ctx.readyToShow = true;
    if (revealOnReady && !win.isDestroyed()) win.show();
  });
  // External links from the app itself (not web tabs) open in the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (uiVerificationGuard.blocked(win.webContents)) return { action: "deny" };
    try {
      const u = new URL(url);
      if (u.protocol === "http:" || u.protocol === "https:") shell.openExternal(url);
    } catch (_e) {
      /* ignore */
    }
    return { action: "deny" };
  });
  // The app renderer must never leave the local UI origin: the preload
  // bridge is exposed to whatever document runs there. Remote links
  // (docs footer, message content) go to the system browser instead.
  win.webContents.on("will-navigate", (e, url) => {
    if (uiVerificationGuard.blocked(win.webContents)) { e.preventDefault(); return; }
    try {
      const dest = new URL(url);
      if (dest.origin === UI_ORIGIN) return;
      e.preventDefault();
      if (dest.protocol === "http:" || dest.protocol === "https:")
        shell.openExternal(url);
    } catch (_e) {
      e.preventDefault();
    }
  });
  win.webContents.on(
    "did-fail-load",
    (_event, errorCode, _description, _url, isMainFrame) => {
      if (errorCode === -3 || isMainFrame === false) return;
      startWindowRecovery(ctx);
    },
  );
  // Renderer reload (Cmd+R) resets the renderer's view bookkeeping —
  // orphaned WebContentsViews would leak until quit. Start clean.
  win.webContents.on("did-navigate", (_event, url) => {
    clearOwnedViews(ctx);
    selfUpdateReopen.observeNavigation(ctx, url);
  });
  win.webContents.on("did-navigate-in-page", (_event, url, isMainFrame) => {
    if (isMainFrame) selfUpdateReopen.observeNavigation(ctx, url);
  });
  const startUrl = await selfUpdateReopen.resolveStartUrl(ctx, await resolveStartUrl());
  void win.loadURL(startUrl).catch(() => {});
  if (isErrorPageUrl(startUrl)) {
    startWindowRecovery(ctx, false);
  }
  return ctx;
}

// Electron renders file:// files but leaves directories blank. Serve a
// Chrome-style listing for directories; everything else passes through.
function registerFileDirectoryListing() {
  const { protocol, session, net } = require("electron");
  const url = require("url");
  const passthrough = (request) =>
    net.fetch(request.url, { bypassCustomProtocolHandlers: true });
  const escapeHtml = (s) =>
    s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const formatSize = (bytes) => {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  };
  // webtab 的 BrowserView 走 persist:webtabs 分区，默认 session 的
  // protocol.handle 对它不生效——必须在该分区的 session 上注册。
  // 默认 session 也注册一份，覆盖将来不带分区的视图。
  const handler = (request) => {
    try {
      const dirPath = url.fileURLToPath(request.url);
      if (!fs.statSync(dirPath).isDirectory()) return passthrough(request);
      const entries = fs.readdirSync(dirPath, { withFileTypes: true });
      const byName = (a, b) => a.name.localeCompare(b.name);
      // ponytail: hidden files listed in place, sorted with the rest
      const dirs = entries.filter((e) => e.isDirectory()).sort(byName);
      const files = entries.filter((e) => !e.isDirectory()).sort(byName);
      const row = (entry) => {
        const isDir = entry.isDirectory();
        const href = encodeURI(
          url.pathToFileURL(path.join(dirPath, entry.name)).href + (isDir ? "/" : ""),
        );
        let size = "";
        if (!isDir) {
          try {
            size = formatSize(fs.statSync(path.join(dirPath, entry.name)).size);
          } catch (_e) {
            /* unreadable entry — show without size */
          }
        }
        return `<li><a href="${href}">${escapeHtml(entry.name)}${isDir ? "/" : ""}</a><span class="size">${size}</span></li>`;
      };
      const parent = path.dirname(dirPath);
      const parentRow =
        parent !== dirPath
          ? `<li><a href="${encodeURI(url.pathToFileURL(parent).href + "/")}">..</a><span class="size"></span></li>`
          : "";
      const listingHtml = `<!doctype html>
<meta charset="utf-8">
<title>${escapeHtml(dirPath)}</title>
<style>
${themeChrome.directoryListingCss(currentChrome)}
</style>
<h1>${escapeHtml(dirPath)}</h1>
<ul>${parentRow}${dirs.map(row).join("")}${files.map(row).join("")}</ul>`;
      return new Response(listingHtml, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    } catch (_e) {
      return passthrough(request);
    }
  };
  protocol.handle("file", handler);
  session.fromPartition("persist:webtabs").protocol.handle("file", handler);
}

// Torn-off windows open at quit come back on the next launch, with their
// tabs (each window's tabs persist under its id in the renderer).
const sessionWindowsFile = () => path.join(app.getPath("userData"), sessionWindows.FILE_NAME);
app.on("before-quit", () => {
  appQuitting = true;
  try {
    sessionWindows.recordOpenWindows(sessionWindowsFile(), [...windows.values()]
      .filter((ctx) => ctx.id !== "main" && !ctx.pendingTransferToken && !ctx.win.isDestroyed())
      .map((ctx) => ({ windowId: ctx.id, bounds: ctx.win.getBounds() })));
  } catch (error) {
    console.error(`[desktop] could not record open windows: ${error.message}`);
  }
});

function boundsOnScreen(bounds) {
  return !!bounds && currentDisplays().some(({ workArea: area }) =>
    Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x) >= 100
    && Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y) >= 100);
}

async function reopenSessionWindows() {
  for (const entry of sessionWindows.takeOpenWindows(sessionWindowsFile())) {
    if (windows.has(entry.windowId)) continue;
    try {
      const ctx = await createWindow({ windowId: entry.windowId, detached: true, show: false });
      if (boundsOnScreen(entry.bounds)) ctx.win.setBounds(entry.bounds);
      const reveal = () => { if (!ctx.win.isDestroyed()) ctx.win.showInactive(); };
      if (ctx.readyToShow) reveal(); else ctx.win.once("ready-to-show", reveal);
    } catch (error) {
      console.error(`[desktop] could not reopen window ${entry.windowId}: ${error.message}`);
    }
  }
}

let webTabPermissionDecisions = null;

// The window a web tab's WebContents is shown in (a WebContentsView child).
function windowForWebContents(contents) {
  return BrowserWindow.getAllWindows().find((win) =>
    !win.isDestroyed() && win.contentView.children.some((view) => view.webContents === contents),
  ) || BrowserWindow.getFocusedWindow() || null;
}

function webTabWatched(contents) {
  const win = BrowserWindow.getAllWindows().find((candidate) =>
    !candidate.isDestroyed() && candidate.contentView.children.some((view) => view.webContents === contents));
  const view = win?.contentView.children.find((child) => child.webContents === contents);
  return Boolean(win && view && win.isFocused() && view.getVisible?.() !== false);
}

// Web tabs present themselves as the Chromium they are and ask before a site
// uses a device (main/web-views/webtab-client-hints.js, webtab-permissions.js).
function registerWebTabIdentity() {
  const webTabs = session.fromPartition("persist:webtabs");
  registerClientHints(webTabs, () => readClientHints(webTabs));
  webTabPermissionDecisions = webTabPermissions.registerWebTabPermissions(webTabs, {
    filePath: path.join(app.getPath("userData"), webTabPermissions.FILE_NAME),
    ask: async ({ origin, purpose, webContents }) => {
      const options = {
        type: "question",
        message: `${new URL(origin).host} wants to ${purpose}`,
        detail: origin,
        buttons: ["Allow", "Block"],
        defaultId: 1,
        cancelId: 1,
        noLink: true,
      };
      const parent = webContents ? windowForWebContents(webContents) : null;
      // A page the user is not looking at (an Agent's, or one in a window
      // behind another application) is not answered for them, and its
      // question does not come in front of what they are doing.
      if (webContents && !webTabWatched(webContents)) return null;
      const result = parent
        ? await dialog.showMessageBox(parent, options)
        : await dialog.showMessageBox(options);
      return result.response === 0;
    },
  });
}

const registerMainWindow = () => registerSingleMainWindow({
  app,
  BrowserWindow,
  ensureMainWindow,
  afterFirstWindow: reopenSessionWindows,
  recoverErroredWindows,
  async onReady() {
    resolveStartupChrome();
    registerFileDirectoryListing();
    const webTabUserAgent = useBrowserUserAgent(session.fromPartition("persist:webtabs"));
    // Before any tab loads, so a restored tab does not meet its old challenge.
    await forgetClearanceOnChange(session.fromPartition("persist:webtabs"),
      path.join(app.getPath("userData"), "webtab-user-agent.json"), webTabUserAgent).catch(() => 0);
    registerWebTabIdentity();
    session.fromPartition("persist:webtabs").registerPreloadScript({ type: "frame", id: "webtab-pinch", filePath: path.join(__dirname, "main", "web-views", "webtab-preload.js") });
    registerDownloads();
    registerWebTabIpc();
    registerTabTransferIpc();
    registerOwnerAuthIpc();
    require("./main/files/open-default").registerOpenFileIpc({
      ipcMain, shell, fs, path, platform: process.platform, contextForSender,
    });
    registerUpdateIpc();
    require("./main/windows/pointer-follow").createPointerFollow({
      ipcMain, screen: require("electron").screen, spawn: require("node:child_process").spawn,
    });
    registerAttentionIpc();
    require("./main/windows/recording-bar").createRecordingBar({
      BrowserWindow, ipcMain, path, screen: require("electron").screen,
      uiOrigin: overlayUiOrigin,
    });
    screenshots = require("./main/windows/screenshot").createScreenshot({
      app, BrowserWindow, Menu, ipcMain, clipboard, dialog, path, fs,
      globalShortcut: require("electron").globalShortcut,
      desktopCapturer: require("electron").desktopCapturer,
      screen: require("electron").screen,
      nativeImage: require("electron").nativeImage,
      uiOrigin: overlayUiOrigin,
      fetchJson: async (route) => {
        const token = ownerAuthTokenFromStartUrl(resolveWorkerStartUrl());
        return readLocalJson(new URL(route, START_URL).toString(), token ? { Authorization: `Bearer ${token}` } : {}, 4000);
      },
      // The App window the user was last in gets a screenshot sent to its conversation.
      targetWindow: () => focusedContext()?.win
        || [...windows.values()].find((ctx) => !ctx.win.isDestroyed())?.win || null,
    });
    screenshots.registerShortcut();
    embeddedWindows = require("./main/windows/embedded-windows").createEmbeddedWindows({
      ipcMain, desktopCapturer: require("electron").desktopCapturer, screen: require("electron").screen,
      contextForSender, platform: process.platform, pid: process.pid, setTimeout, clearTimeout,
    });
    require("./main/windows/pane-recording").createPaneRecording({
      ipcMain, session: session.defaultSession, fs, path, homeDir: app.getPath("home"),
      // Only a window's own page records, never an overlay or an embedded site.
      ownsPage: (event) => {
        const ctx = contextForSender(event);
        return Boolean(ctx && event.sender === ctx.win.webContents);
      },
    });
    registerNativeTitleBarIpc({ ipcMain, BrowserWindow, platform: process.platform, getChrome: () => currentChrome });
    ipcMain.on("theme:trace", (event, payload) => {
      // Only the app document can write diagnostics, not embedded websites.
      if (!BrowserWindow.getAllWindows().some((win) => !win.isDestroyed() && win.webContents === event.sender)) return;
      themeChrome.recordThemeEvent(app.getPath("userData"), payload);
    });
    ipcMain.on("theme:set-chrome", (_event, payload) => {
      applyWindowChrome(payload || {});
    });
    ipcMain.on("theme:system-dark", (event) => {
      event.returnValue = systemPrefersDark();
    });
    try {
      let announcedDark = systemPrefersDark();
      require("electron").nativeTheme.on("updated", () => {
        const dark = systemPrefersDark();
        if (dark !== announcedDark) {
          announcedDark = dark;
          // Windows, menus and overlays each resolve "auto" themselves.
          for (const contents of require("electron").webContents.getAllWebContents()) {
            try {
              if (!contents.isDestroyed()) contents.send("theme:system-changed", dark);
            } catch (_error) {
              /* A page being torn down reads the value on its next load. */
            }
          }
        }
        const resolved = resolveStartupChrome("native-system");
        applyWindowChrome({
          theme: resolved.theme,
          style: resolved.style,
          mode: resolved.mode,
          accentColor: resolved.accentColor,
        });
      });
    } catch {
      /* nativeTheme.updated is best-effort */
    }
    // The development App moves with each deploy, never by release updates.
    if (!INSTANCE.development) initializeDesktopUpdates();
    buildMenu();
    createBackgroundTray();
  },
});

// The worker in the menu bar or notification area (main/worker/worker-tray.js).
// "Stop background" stops the worker the way switching versions does, then
// quits, because the App's windows would otherwise start it again.
let workerTray = null;
function createBackgroundTray() {
  if (workerTray) return;
  const electron = require("electron");
  workerTray = require("./main/worker/worker-tray").createWorkerTray({
    Tray: electron.Tray,
    Menu,
    nativeImage: electron.nativeImage,
    iconRoot: path.join(__dirname, "main", "worker", "tray"),
    probe: async () => ({ running: await probe(HEALTH_URL, 1000) }),
    open: () => { void ensureMainWindow(); },
    screenshot: () => { void screenshots?.start(); },
    stop: async () => {
      if (shellContext) await shellContext.stopWorker();
      else spawnWorker("stop");
      app.quit();
    },
  });
}

let firstWindowLoaded = null;
let started = null;

/**
 * The contract with the shell loader. Resolves when this main process is ready
 * and the first window has loaded a page, even if that page is the wait page.
 */
function start(context = null) {
  if (started) return started;
  shellContext = context;
  started = new Promise((resolve) => { firstWindowLoaded = resolve; });
  registerMainWindow();
  return started;
}

if (typeof module !== "undefined") module.exports = { start };
if (!globalThis.__OPENPROGRAM_SHELL_LOADER__) void start();

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
