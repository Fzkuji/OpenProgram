// OpenProgram desktop shell. Plain JS, no bundler.
const {
  app,
  BrowserWindow,
  clipboard,
  WebContentsView,
  Menu,
  dialog,
  ipcMain: nativeIpcMain,
  powerMonitor,
  session,
  shell,
  nativeTheme,
} = require("electron");
const { execFileSync, spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const { Buffer } = require("buffer");
const { resolveAuthenticatedStartUrl } = require("./worker-start-url");
const { createSelfUpdateReopen, registerReopenIpc } = require("./self-update-reopen");
const { registerUiVerificationIpc } = require("./self-update-ui");
const { createUiVerificationGuard } = require("./self-update-ui-guard");
const uiVerificationGuard = createUiVerificationGuard(nativeIpcMain);
const { ipcMain } = uiVerificationGuard;
const { resolvePackagedWorker } = require("./packaged-runtime");
const { DesktopUpdateService, desktopUpdateFetch } = require("./update-service");
const {
  killWindowsProcessTree,
  resolveTerminalCommand,
  waitForTerminalPid,
} = require("./terminal-command");
const { verifyWindowsAuthenticode } = require("./windows-signature");
const {
  loadTransferDecisions,
  saveTransferDecisionsAtomic,
  putTransferDecision,
  ackTransferDecision,
} = require("./tab-transfer-store");
const {
  recordVisit,
  listHistory,
  importHistoryEntries,
  deleteHistoryEntry,
  clearHistory,
} = require("./browsing-history-store");
const {
  listBrowserSources,
  runBrowserImport,
} = require("./browser-profile-import");
const {
  contextMenuRequestedX,
  clampContextMenuPanel,
  cascadeMenuGeometry,
} = require("./menu-geometry");
const {
  createRecoveryState,
  createRecoveryCoordinator,
  recordWorkerCommandExit,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
} = require("./worker-recovery-state");
const { validateTransferPayload } = require("./tab-transfer-validation");
const {
  STATE_FILE_NAME,
  loadPersistedState,
  attachWindowStatePersistence,
  browserWindowOptionsForPlan,
  applyRestoredChrome,
} = require("./window-state");
const {
  createMainWindowGate,
  registerSingleMainWindow,
} = require("./window-lifecycle");
const themeChrome = require("./theme-chrome");
const {
  browserWindowChromeOptions,
  applyNativeTitleBarChrome,
} = require("./window-chrome");

// 单实例：worker 单端口 18100（详见 docs/reference/design/cli/single-port.md）
const WEB_PORT = process.env.OPENPROGRAM_WEB_PORT || "18100";
const START_URL =
  process.env.OPENPROGRAM_DESKTOP_URL || `http://127.0.0.1:${WEB_PORT}/chat`;
const UI_ORIGIN = new URL(START_URL).origin;
const selfUpdateReopen = createSelfUpdateReopen({ argv: process.argv, origin: UI_ORIGIN });
const HEALTH_URL = new URL("/healthz", START_URL).toString();
const WORKER_COMMAND = "openprogram worker start";
const RECOVERY_INTERVAL_MS = 3_000;
const TRANSFER_TIMEOUT_MS = 15_000;
const DESTINATION_UNDO_TIMEOUT_MS = 2_000;
const COMMIT_RECONCILE_INITIAL_MS = 100;
const COMMIT_RECONCILE_MAX_MS = 5_000;
// Bounded retries for a clean (unambiguous) committed-decision write failure
// before abandoning the commit and taking the pre-commit rollback path.
const COMMIT_DECISION_RETRY_LIMIT = 4;
const UPDATE_INITIAL_DELAY_MS = 30_000;

let desktopUpdates = null;
let updateTimer = null;

function broadcastUpdateState(state) {
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

function scheduleAutomaticUpdateCheck(initial = false) {
  if (updateTimer !== null) clearTimeout(updateTimer);
  updateTimer = null;
  if (!app.isPackaged || !desktopUpdates?.getState().automaticChecks) return;
  const now = Date.now();
  const dueAt = desktopUpdates.automaticCheckDueAt();
  const delay = initial && dueAt === 0
    ? UPDATE_INITIAL_DELAY_MS
    : Math.max(1_000, dueAt - now);
  updateTimer = setTimeout(async () => {
    updateTimer = null;
    try {
      await desktopUpdates.check();
    } finally {
      scheduleAutomaticUpdateCheck();
    }
  }, Math.min(delay, 2_147_000_000));
}

function initializeDesktopUpdates() {
  desktopUpdates = new DesktopUpdateService({
    currentVersion: app.getVersion(),
    arch: process.arch,
    platform: process.platform,
    statePath: path.join(app.getPath("userData"), "update-state.json"),
    fetchImpl: desktopUpdateFetch,
    chooseSavePath: async (name) => {
      const windowsInstaller = process.platform === "win32";
      const result = await dialog.showSaveDialog({
        title: "Download OpenProgram Update",
        defaultPath: path.join(app.getPath("downloads"), name),
        filters: windowsInstaller
          ? [{ name: "Windows Installer", extensions: ["exe"] }]
          : [{ name: "macOS Disk Image", extensions: ["dmg"] }],
      });
      return result.canceled ? null : result.filePath;
    },
    verifyArtifact: (filePath) => verifyWindowsAuthenticode(filePath),
    openPath: (filePath) => shell.openPath(filePath),
    emit: broadcastUpdateState,
  });
  scheduleAutomaticUpdateCheck(true);
  powerMonitor.on("resume", () => scheduleAutomaticUpdateCheck());
}

function registerUpdateIpc() {
  ipcMain.handle("updates:get-state", () => desktopUpdates?.getState() || null);
  ipcMain.handle("updates:check", async () => {
    const state = await desktopUpdates?.check({ force: true }) || null;
    scheduleAutomaticUpdateCheck();
    return state;
  });
  ipcMain.handle("updates:set-automatic-checks", (_event, enabled) => {
    const state = desktopUpdates?.setAutomaticChecks(enabled) || null;
    scheduleAutomaticUpdateCheck();
    return state;
  });
  ipcMain.handle("updates:download", () => desktopUpdates?.download() || null);
  ipcMain.handle("updates:open-release", () => {
    const releaseUrl = desktopUpdates?.getState().release?.releaseUrl;
    return releaseUrl ? shell.openExternal(releaseUrl) : null;
  });
}

// agent 接管内置浏览器的数据面通道：后端 browser 工具（engine=auto/app）经
// CDP attach 这里的可见 web tab。Electron 默认只绑 127.0.0.1，不对外暴露；
// 9222 留给后端 sidecar Chrome，互不冲突。必须在 app ready 之前设置。
app.commandLine.appendSwitch("remote-debugging-port", "9223");

let currentChrome = themeChrome.chromeForTheme("beige-dark");

function errorPageUrl() {
  return themeChrome.buildErrorPageUrl(currentChrome, WORKER_COMMAND);
}

function isErrorPageUrl(url) {
  return themeChrome.isErrorPageUrl(url);
}

function themePrefsPath() {
  return path.join(app.getPath("userData"), themeChrome.PREFS_FILE_NAME);
}

function resolveStartupChrome(source = "startup") {
  let systemDark = true;
  try {
    systemDark = require("electron").nativeTheme.shouldUseDarkColors !== false;
  } catch {
    /* older Electron or tests without nativeTheme */
  }
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
  for (const win of BrowserWindow.getAllWindows()) {
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

function spawnWorker(action = "start") {
  const env = { ...process.env, OPENPROGRAM_WEB_PORT: WEB_PORT };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  let launch;
  if (app.isPackaged) {
    env.OPENPROGRAM_IMMUTABLE_RUNTIME = "1";
    launch = resolvePackagedWorker(process.resourcesPath, app.getVersion());
    Object.assign(env, launch.env);
  } else {
    launch = { command: "openprogram", args: ["worker", "start"] };
  }
  if (action === "restart") {
    launch = { ...launch, args: [...launch.args.slice(0, -1), "restart"] };
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

function resolveWorkerStartUrl() {
  if (!app.isPackaged) return resolveAuthenticatedStartUrl(START_URL);
  const launch = resolvePackagedWorker(process.resourcesPath, app.getVersion());
  return resolveAuthenticatedStartUrl(START_URL, process.env, [
    {
      command: launch.authCommand || launch.command,
      args: ["-I", "-B", "-m", "openprogram"],
    },
  ]);
}

async function resolveStartUrl() {
  let workerWasReachable = false;
  for (let i = 0; i < 3; i++) {
    if (await probe(HEALTH_URL, 1000)) {
      workerWasReachable = true;
      recoveryCoordinator.workerSpawned = false;
      const authenticated = resolveWorkerStartUrl();
      if (authenticated) return authenticated;
    }
  }
  if (!workerWasReachable && !recoveryCoordinator.workerSpawned) {
    recoveryCoordinator.workerSpawned = true;
    spawnWorker();
  }
  return errorPageUrl();
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
  const reachable = await probe(HEALTH_URL, 1000);
  const authenticated = reachable ? resolveWorkerStartUrl() : null;
  const action = finishRecoveryProbe(
    state,
    recoveryCoordinator,
    reachable,
    !!authenticated,
    Date.now(),
    RECOVERY_INTERVAL_MS,
  );
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
  if (showErrorPage && !isErrorPageUrl(ctx.win.webContents.getURL?.())) {
    void ctx.win.loadURL(errorPageUrl()).catch(() => {});
  }
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
} = require("./main/downloads").createDownloads({
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
} = require("./main/transfers").createTransfers({
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
const ACTION_CUE_SIZE = 28;

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
  placeActionCueWindow,
  cleanupCue,
  clearActionCue,
  showActionView,
} = require("./main/action-cues").createActionCues({
  ACTION_CUE_MS,
  ACTION_CUE_SIZE,
  BrowserWindow,
  START_URL,
  WEB_PORT,
  __dirname,
  clearTimeout,
  layoutControlOverlay: (...args) => layoutControlOverlay(...args),
  nativeTheme,
  ownerOf: (...args) => ownerOf(...args),
  path,
  recordFor: (...args) => recordFor(...args),
  setTimeout,
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
  captureView,
  zoomView,
  printPdfDefaultName,
  printView,
} = require("./main/web-views").createWebViews({
  Buffer,
  Menu,
  WebContentsView,
  app,
  boundsDiffer: (...args) => boundsDiffer(...args),
  clearActionCue: (...args) => clearActionCue(...args),
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
  tabTransfers,
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
} = require("./main/view-layout").createViewLayout({
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

function cleanupWindowContext(ctx) {
  if (
    activeBrowserImport?.ownerId === ctx.id
    && !activeBrowserImport.controller.signal.aborted
  ) {
    activeBrowserImport.controller.abort();
  }
  stopWindowRecovery(ctx);
  closeMainMenu(ctx);
  tabTransfers.contextDestroyed(ctx);
  clearOwnedViews(ctx);
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
} = require("./main/menus").createMenus({
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
  __dirname,
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
} = require("./main/control-overlays").createControlOverlays({
  Menu,
  UI_ORIGIN,
  WebContentsView,
  __dirname,
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
  ipcMain.on("main-menu:choose", (event, id) => {
    const ctx = contextForMenuSender(event);
    if (!ctx) return;
    ctx.win.webContents.send("main-menu:action", id);
    closeMainMenu(ctx);
  });
  ipcMain.on("webtab:ensure", (event, id, url) => {
    const ctx = contextForSender(event);
    if (ctx) ensureView(ctx, id, url);
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
  ipcMain.on("webtab:reload", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.reload());
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
  ipcMain.handle("webtab:zoom", (event, id, action) => {
    const ctx = contextForSender(event);
    return ctx ? zoomView(ctx, id, action) : null;
  });
  ipcMain.on("webtab:set-pip-zoom", (event, id, width) => {
    const ctx = contextForSender(event);
    if (ctx) setPipZoom(ctx, id, width);
  });
  ipcMain.handle("webtab:print", (event, id) => {
    const ctx = contextForSender(event);
    return ctx ? printView(ctx, id) : false;
  });
  ipcMain.handle("webtab:capture", (event, id) => {
    const ctx = contextForSender(event);
    return ctx ? captureView(ctx, id) : null;
  });
  ipcMain.handle("webtab:show-action", (event, id, marker) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? showActionView(ctx, id, marker) : false;
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
  ipcMain.on("webtab:action-cue-ready", (event) => {
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
      }
      return { ok: true };
    } catch (_error) {
      return { ok: false };
    }
  });

  // A renderer may choose one of two fixed programs, never an arbitrary
  // executable/argv pair. The PTY remains in the main process and is keyed by
  // sender so another window cannot write to or resize it by guessing the id.
  const terminals = new Map();
  const terminalSenders = new WeakSet();
  const terminalKey = (sender, id) => `${sender.id}:${id}`;
  const terminalContextForSender = (event) => {
    const ctx = contextForSender(event);
    if (!ctx) return null;
    try {
      return new URL(event.sender.getURL()).origin === UI_ORIGIN ? ctx : null;
    } catch (_error) {
      return null;
    }
  };
  const terminalIdFor = (ctx, preset) => `terminal:${ctx.id}:${preset}`;
  const terminalIdAllowed = (ctx, id) =>
    id === terminalIdFor(ctx, "shell") || id === terminalIdFor(ctx, "claude");
  const terminalDescendants = (rootPid) => {
    if (process.platform === "win32" || !Number.isInteger(rootPid)) return [];
    try {
      const children = new Map();
      const rows = execFileSync("/bin/ps", ["-axo", "pid=,ppid="], {
        encoding: "utf8",
      });
      for (const line of rows.split("\n")) {
        const match = line.trim().match(/^(\d+)\s+(\d+)$/);
        if (!match) continue;
        const pid = Number(match[1]);
        const parent = Number(match[2]);
        const list = children.get(parent) ?? [];
        list.push(pid);
        children.set(parent, list);
      }
      const descendants = [];
      const pending = [...(children.get(rootPid) ?? [])];
      while (pending.length > 0) {
        const pid = pending.pop();
        descendants.push(pid);
        pending.push(...(children.get(pid) ?? []));
      }
      return descendants;
    } catch (_error) {
      return [];
    }
  };
  const signalTerminal = (entry, signal) => {
    if (process.platform === "win32" && Number.isInteger(entry.process.pid)) {
      // node-pty closes the ConPTY transport but can leave the hosted shell
      // alive. Kill the exact process tree first; the subsequent node-pty
      // kill releases its native handles and emits the normal exit event.
      killWindowsProcessTree(entry.process.pid);
      try {
        entry.process.kill();
      } catch (_error) {
        /* process already exited */
      }
      return;
    }
    let groupSignaled = false;
    if (process.platform !== "win32" && Number.isInteger(entry.process.pid)) {
      try {
        process.kill(-entry.process.pid, signal);
        groupSignaled = true;
      } catch (_error) {
        /* fall back to node-pty's root process signal */
      }
    }
    for (const pid of entry.descendantPids ?? []) {
      try {
        process.kill(pid, signal);
      } catch (_error) {
        /* descendant already exited */
      }
    }
    if (groupSignaled) return;
    try {
      entry.process.kill(signal);
    } catch (_error) {
      /* process already exited */
    }
  };
  const stopTerminal = (sender, id) => {
    const key = terminalKey(sender, id);
    const entry = terminals.get(key);
    if (!entry) return;
    terminals.delete(key);
    entry.descendantPids = terminalDescendants(entry.process.pid);
    entry.killTimer = setTimeout(() => {
      entry.killTimer = null;
      if (entry.exited) return;
      signalTerminal(entry, "SIGKILL");
    }, 1_000);
    entry.killTimer.unref?.();
    signalTerminal(entry, "SIGTERM");
  };
  const ensureTerminalSenderCleanup = (sender) => {
    if (terminalSenders.has(sender)) return;
    terminalSenders.add(sender);
    sender.once("destroyed", () => {
      const prefix = `${sender.id}:`;
      for (const key of [...terminals.keys()]) {
        if (key.startsWith(prefix)) stopTerminal(sender, key.slice(prefix.length));
      }
      terminalSenders.delete(sender);
    });
  };
  ipcMain.handle("terminal:start", async (event, request) => {
    const ctx = terminalContextForSender(event);
    if (!ctx) {
      return { ok: false, error: "unauthorized_sender" };
    }
    const preset = request?.preset;
    if (preset !== "shell" && preset !== "claude") {
      return { ok: false, error: "invalid_preset" };
    }
    const id = typeof request?.id === "string" && request.id.length <= 128
      ? request.id
      : "";
    if (id !== terminalIdFor(ctx, preset)) {
      return { ok: false, error: "invalid_terminal" };
    }

    let cwd;
    try {
      cwd = typeof request.cwd === "string" && request.cwd
        ? fs.realpathSync(request.cwd)
        : app.getPath("home");
      if (!fs.statSync(cwd).isDirectory()) throw new Error("not a directory");
    } catch (_error) {
      return { ok: false, error: "invalid_cwd" };
    }

    const cols = Number.isInteger(request.cols)
      ? Math.min(500, Math.max(20, request.cols))
      : 80;
    const rows = Number.isInteger(request.rows)
      ? Math.min(200, Math.max(5, request.rows))
      : 24;
    const terminalLaunch = resolveTerminalCommand(preset);
    const key = terminalKey(event.sender, id);
    const existing = terminals.get(key);
    if (existing && existing.preset === preset && existing.cwd === cwd) {
      if (existing.buffer && !event.sender.isDestroyed()) {
        event.sender.send("terminal:data", { id, data: existing.buffer });
      }
      existing.process.resize(cols, rows);
      return { ok: true, reused: true, pid: existing.process.pid };
    }

    stopTerminal(event.sender, id);
    let child;
    try {
      child = require("node-pty").spawn(terminalLaunch.command, terminalLaunch.args, {
        name: "xterm-256color",
        cols,
        rows,
        cwd,
        useConpty: terminalLaunch.useConpty,
        env: {
          ...process.env,
          TERM: "xterm-256color",
          COLORTERM: "truecolor",
        },
      });
    } catch (_error) {
      return { ok: false, error: "terminal_start_failed" };
    }

    const entry = {
      process: child,
      preset,
      cwd,
      buffer: "",
      exited: false,
      killTimer: null,
    };
    terminals.set(key, entry);
    ensureTerminalSenderCleanup(event.sender);
    child.onData((data) => {
      if (terminals.get(key) !== entry || event.sender.isDestroyed()) return;
      entry.buffer = `${entry.buffer}${data}`.slice(-1_000_000);
      event.sender.send("terminal:data", { id, data });
    });
    child.onExit(({ exitCode }) => {
      entry.exited = true;
      if (entry.killTimer !== null) {
        clearTimeout(entry.killTimer);
        entry.killTimer = null;
      }
      if (terminals.get(key) !== entry) return;
      terminals.delete(key);
      if (!event.sender.isDestroyed()) {
        event.sender.send("terminal:data", { id, data: "", done: true, exitCode });
      }
    });
    // node-pty's Windows backend creates ConPTY asynchronously.  Its public
    // ``pid`` is initially 0 and is updated after ready_datapipe; returning
    // immediately made the renderer permanently publish data-process-id="0"
    // even though the shell was running.  Wait only on Windows, where this
    // delayed PID contract exists, and fail loudly if ConPTY never reports a
    // process instead of leaving an unmanageable terminal behind.
    const pid = process.platform === "win32"
      ? await waitForTerminalPid(child)
      : child.pid;
    if (!pid) {
      stopTerminal(event.sender, id);
      return { ok: false, error: "terminal_pid_unavailable" };
    }
    return { ok: true, pid };
  });
  ipcMain.on("terminal:write", (event, id, data) => {
    const ctx = terminalContextForSender(event);
    const terminalId = String(id || "");
    if (!ctx || !terminalIdAllowed(ctx, terminalId)) return;
    const entry = terminals.get(terminalKey(event.sender, terminalId));
    if (!entry || typeof data !== "string" || Buffer.byteLength(data, "utf8") > 65_536) return;
    entry.process.write(data);
  });
  ipcMain.on("terminal:resize", (event, id, cols, rows) => {
    const ctx = terminalContextForSender(event);
    const terminalId = String(id || "");
    if (!ctx || !terminalIdAllowed(ctx, terminalId)) return;
    const entry = terminals.get(terminalKey(event.sender, terminalId));
    if (!entry || !Number.isInteger(cols) || !Number.isInteger(rows)) return;
    entry.process.resize(
      Math.min(500, Math.max(20, cols)),
      Math.min(200, Math.max(5, rows)),
    );
  });
  ipcMain.on("terminal:stop", (event, id) => {
    const ctx = terminalContextForSender(event);
    const terminalId = String(id || "");
    if (!ctx || !terminalIdAllowed(ctx, terminalId)) return;
    stopTerminal(event.sender, terminalId);
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

function buildMenu() {
  const isMac = process.platform === "darwin";
  const send = (channel) => () => {
    const ctx = focusedContext();
    if (ctx) ctx.win.webContents.send(channel);
  };
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [
        { label: "New Tab", accelerator: "CmdOrCtrl+T", click: send("menu:new-tab") },
        // Cmd+W goes to the renderer (close tab); window close is Cmd+Shift+W.
        { label: "Close Tab", accelerator: "CmdOrCtrl+W", click: send("menu:close-tab") },
        { type: "separator" },
        { role: "close", accelerator: "Shift+CmdOrCtrl+W" },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
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
// create. See docs/reference/design/ui/window-lifecycle.md.
const ensureMainWindow = createMainWindowGate({
  windows,
  createWindow,
});
registerReopenIpc({ ipcMain, windows, recovery: selfUpdateReopen, origin: UI_ORIGIN });
registerUiVerificationIpc({ ipcMain, windows, app, origin: UI_ORIGIN,
  request: selfUpdateReopen.requestVerification, guard: uiVerificationGuard });

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
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--openprogram-window-id=${windowId}`],
    },
  });
  applyNativeTitleBarChrome(win, process.platform, currentChrome);
  if (!detached) {
    attachWindowStatePersistence(win, {
      filePath: stateFile(),
      getDisplays: currentDisplays,
    });
    applyRestoredChrome(win, state);
  }
  const ctx = makeWindowContext(windowId, win);
  windows.set(windowId, ctx);
  contextsByBrowserWindowId.set(win.id, ctx);
  win.on("focus", () => { lastFocusedWindowId = ctx.id; });
  // The main-menu overlay is anchored to the ⋮ button; a window blur or
  // resize invalidates its position — dismiss it. (Its own view blur
  // handles outside clicks inside the window.)
  win.on("blur", () => closeMainMenu(ctx));
  win.on("resize", () => closeMainMenu(ctx));
  win.on("close", (event) => {
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

registerSingleMainWindow({
  app,
  BrowserWindow,
  ensureMainWindow,
  recoverErroredWindows,
  async onReady() {
    resolveStartupChrome();
    registerFileDirectoryListing();
    registerDownloads();
    registerWebTabIpc();
    registerTabTransferIpc();
    registerUpdateIpc();
    ipcMain.on("theme:trace", (event, payload) => {
      // Only the app document can write diagnostics, not embedded websites.
      if (!BrowserWindow.getAllWindows().some((win) => !win.isDestroyed() && win.webContents === event.sender)) return;
      themeChrome.recordThemeEvent(app.getPath("userData"), payload);
    });
    ipcMain.on("theme:set-chrome", (_event, payload) => {
      applyWindowChrome(payload || {});
    });
    try {
      require("electron").nativeTheme.on("updated", () => {
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
    initializeDesktopUpdates();
    buildMenu();
  },
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
