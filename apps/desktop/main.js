// OpenProgram desktop shell. Plain JS, no bundler.
const {
  app,
  BrowserWindow,
  WebContentsView,
  Menu,
  dialog,
  ipcMain,
  powerMonitor,
  session,
  shell,
} = require("electron");
const { execFileSync, spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const { Buffer } = require("buffer");
const { resolveAuthenticatedStartUrl } = require("./worker-start-url");
const { resolvePackagedWorker } = require("./packaged-runtime");
const { DesktopUpdateService, desktopUpdateFetch } = require("./update-service");
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

// 单实例：worker 单端口 18100（详见 docs/reference/design/cli/single-port.md）
const WEB_PORT = process.env.OPENPROGRAM_WEB_PORT || "18100";
const START_URL =
  process.env.OPENPROGRAM_DESKTOP_URL || `http://127.0.0.1:${WEB_PORT}/chat`;
const UI_ORIGIN = new URL(START_URL).origin;
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
    statePath: path.join(app.getPath("userData"), "update-state.json"),
    fetchImpl: desktopUpdateFetch,
    chooseSavePath: async (name) => {
      const result = await dialog.showSaveDialog({
        title: "Download OpenProgram Update",
        defaultPath: path.join(app.getPath("downloads"), name),
        filters: [{ name: "macOS Disk Image", extensions: ["dmg"] }],
      });
      return result.canceled ? null : result.filePath;
    },
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

function resolveStartupChrome() {
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

function spawnWorker() {
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
  const child = spawn(launch.command, launch.args, {
    detached: true,
    stdio: "ignore",
    env,
  });
  child.on("error", (error) => {
    recoveryCoordinator.workerSpawned = false;
    console.error(`[desktop] worker start failed: ${error.message}`);
  });
  child.unref();
}

function resolveWorkerStartUrl() {
  if (!app.isPackaged) return resolveAuthenticatedStartUrl(START_URL);
  const launch = resolvePackagedWorker(process.resourcesPath, app.getVersion());
  return resolveAuthenticatedStartUrl(START_URL, process.env, [
    {
      command: launch.command,
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
  if (action === "spawn") {
    spawnWorker();
  } else if (action === "load" && !ctx.win.isDestroyed()) {
    if (state.timer !== null) clearInterval(state.timer);
    state.timer = null;
    void ctx.win.loadURL(authenticated).catch(() => {});
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

function downloadsRoot() {
  const directory = app.getPath("downloads");
  fs.mkdirSync(directory, { recursive: true });
  return fs.realpathSync(directory);
}

function pathInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".."
    && !path.isAbsolute(relative);
}

function pathInsideOrEqual(root, candidate) {
  return root === candidate || pathInside(root, candidate);
}

function allowedDownloadPath(value, mustExist = false) {
  if (typeof value !== "string" || !value) return false;
  try {
    const requestedRoot = path.resolve(app.getPath("downloads"));
    const root = downloadsRoot();
    const resolved = path.resolve(value);
    if (!pathInside(requestedRoot, resolved) && !pathInside(root, resolved)) return false;
    if (fs.existsSync(resolved)) {
      if (fs.lstatSync(resolved).isSymbolicLink()) return false;
      return pathInside(root, fs.realpathSync(resolved));
    }
    if (mustExist) return false;
    return pathInsideOrEqual(root, fs.realpathSync(path.dirname(resolved)));
  } catch (_error) {
    return false;
  }
}

function validDownloadEntry(entry) {
  return !!entry
    && typeof entry.id === "string" && entry.id.length > 0
    && typeof entry.path === "string"
    && typeof entry.filename === "string" && entry.filename === path.basename(entry.path)
    && typeof entry.url === "string"
    && DOWNLOAD_STATES.has(entry.state)
    && [entry.receivedBytes, entry.totalBytes, entry.startedAt, entry.updatedAt]
      .every((value) => Number.isFinite(value) && value >= 0)
    && allowedDownloadPath(entry.path);
}

function publicDownloadEntry(entry) {
  return { ...entry, active: activeDownloads.has(entry.id) };
}

function loadDownloads() {
  downloads.clear();
  try {
    const parsed = JSON.parse(fs.readFileSync(downloadsFile(), "utf8"));
    for (const entry of Array.isArray(parsed?.entries) ? parsed.entries : []) {
      if (!validDownloadEntry(entry)) continue;
      downloads.set(entry.id, {
        ...entry,
        state: entry.state === "progressing" ? "interrupted" : entry.state,
      });
    }
  } catch (_error) {
    /* first run or malformed best-effort history */
  }
}

function saveDownloads() {
  const file = downloadsFile();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({
    version: 1,
    entries: [...downloads.values()]
      .sort((a, b) => b.startedAt - a.startedAt)
      .slice(0, 1000),
  }));
  fs.renameSync(temporary, file);
}

function downloadEntry(id) {
  const entry = downloads.get(id);
  return entry ? publicDownloadEntry(entry) : null;
}

function broadcastDownload(entry) {
  for (const ctx of windows.values()) {
    if (!ctx.win.isDestroyed()) {
      ctx.win.webContents.send(
        "downloads:changed",
        entry ? publicDownloadEntry(entry) : null,
      );
    }
  }
}

function downloadReservationKey(value) {
  const normalized = path.resolve(value).normalize("NFD");
  return process.platform === "darwin" || process.platform === "win32"
    ? normalized.toUpperCase().normalize("NFD")
    : normalized;
}

function uniqueDownloadPath(filename) {
  const directory = downloadsRoot();
  const safeName = path.basename(filename || "download") || "download";
  const extension = path.extname(safeName);
  const stem = path.basename(safeName, extension);
  let candidate = path.join(directory, safeName);
  const unavailable = (value) => fs.existsSync(value)
    || [...downloads.values()].some(
      (entry) => activeDownloads.has(entry.id)
        && downloadReservationKey(entry.path) === downloadReservationKey(value),
    );
  for (let index = 1; unavailable(candidate); index += 1) {
    candidate = path.join(directory, `${stem} (${index})${extension}`);
  }
  return candidate;
}

function registerDownloads(targetSession = session.fromPartition("persist:webtabs")) {
  loadDownloads();
  targetSession.on("will-download", (_event, item) => {
    const id = crypto.randomUUID();
    const savePath = uniqueDownloadPath(item.getFilename());
    item.setSavePath(savePath);
    const entry = {
      id,
      filename: path.basename(savePath),
      path: savePath,
      url: item.getURL(),
      state: "progressing",
      receivedBytes: 0,
      totalBytes: Math.max(0, item.getTotalBytes()),
      startedAt: Date.now(),
      updatedAt: Date.now(),
    };
    downloads.set(id, entry);
    activeDownloads.set(id, item);
    try { saveDownloads(); } catch (_error) { /* best effort */ }
    broadcastDownload(entry);
    item.on("updated", (_itemEvent, state) => {
      if (!downloads.has(id)) return;
      entry.state = state === "interrupted" ? "interrupted" : "progressing";
      entry.receivedBytes = Math.max(0, item.getReceivedBytes());
      entry.totalBytes = Math.max(0, item.getTotalBytes());
      entry.updatedAt = Date.now();
      broadcastDownload(entry);
    });
    item.once("done", (_itemEvent, state) => {
      entry.state = state;
      entry.receivedBytes = Math.max(0, item.getReceivedBytes());
      entry.totalBytes = Math.max(0, item.getTotalBytes());
      entry.updatedAt = Date.now();
      activeDownloads.delete(id);
      try { saveDownloads(); } catch (_error) { /* best effort */ }
      broadcastDownload(entry);
    });
  });
}

// History is best-effort: a failed write must never break navigation.
function safeRecordVisit(visit) {
  try {
    recordVisit(browsingHistoryFile(), visit);
  } catch (_error) {
    /* history is not worth crashing a navigation over */
  }
}

function reparentRecords(source, target, records) {
  if (!source || !target || source === target || !Array.isArray(records)) {
    throw new TypeError("Invalid native reparent request");
  }
  for (const record of records) {
    if (
      !record
      || record.ownerId !== source.id
      || source.views.get(record.id) !== record
      || target.views.has(record.id)
    ) {
      throw new Error("Native record ownership changed before reparent");
    }
  }

  const snapshots = [];
  snapshots.sourceVisibleViewIds = [...source.visibleViewIds];
  snapshots.targetVisibleViewIds = [...target.visibleViewIds];
  try {
    for (const record of records) {
      const snapshot = {
        record,
        sourceId: source.id,
        bounds: { ...record.view.getBounds() },
        visible: source.visibleViewIds.has(record.id),
      };
      snapshots.push(snapshot);
      source.win.contentView.removeChildView(record.view);
      source.views.delete(record.id);
      source.visibleViewIds.delete(record.id);
      target.win.contentView.addChildView(record.view);
      target.views.set(record.id, record);
      target.visibleViewIds.delete(record.id);
      record.ownerId = target.id;
      record.view.setVisible(false);
    }
    return snapshots;
  } catch (error) {
    restoreRecords(source, target, snapshots);
    throw error;
  }
}

function restoreRecords(source, target, snapshots) {
  if (!source || !target || !Array.isArray(snapshots)) return false;
  for (const snapshot of [...snapshots].reverse()) {
    const { record } = snapshot;
    target.visibleViewIds.delete(record.id);
    if (target.views.get(record.id) === record) target.views.delete(record.id);
    try {
      target.win.contentView.removeChildView(record.view);
    } catch (_error) {
      /* destination may already be destroyed */
    }
    try {
      source.win.contentView.addChildView(record.view);
    } catch (_error) {
      /* a destroyed source has no native surface to restore */
    }
    source.views.set(record.id, record);
    record.ownerId = source.id;
    record.view.setBounds(snapshot.bounds);
    record.view.setVisible(snapshot.visible);
    if (snapshot.visible) source.visibleViewIds.add(record.id);
    else source.visibleViewIds.delete(record.id);
  }
  if (Array.isArray(snapshots.sourceVisibleViewIds)) {
    source.visibleViewIds = new Set(snapshots.sourceVisibleViewIds);
  }
  if (Array.isArray(snapshots.targetVisibleViewIds)) {
    target.visibleViewIds = new Set(snapshots.targetVisibleViewIds);
  }
  return true;
}

function makeTransferCoordinator(options = {}) {
  const windowRegistry = options.windows || windows;
  const decisionPath = options.decisionFilePath || transferDecisionFile;
  const createDetachedWindow = options.createWindow || createWindow;
  const setTimer = options.setTimer || setTimeout;
  const clearTimer = options.clearTimer || clearTimeout;
  const now = options.now || (() => Date.now());
  const makeToken = options.makeToken || (() => crypto.randomUUID());
  const activeTransfers = new Map();
  const lockedRecords = new Map();
  const orphanAssignments = new Map();
  const forcedOrphanRoles = new Set();
  const allowedWindowCloses = new Set();
  let durableDecisions = loadTransferDecisions(
    typeof decisionPath === "function" ? decisionPath() : decisionPath,
  );

  const storePath = () =>
    typeof decisionPath === "function" ? decisionPath() : decisionPath;
  const roleKey = (token, role, windowId) =>
    JSON.stringify([token, role, windowId]);
  const isLive = (ctx) => !!ctx && !ctx.win.isDestroyed();
  const liveContext = (id) => {
    const ctx = windowRegistry.get(id);
    return isLive(ctx) ? ctx : null;
  };
  const send = (ctx, channel, payload) => {
    if (!isLive(ctx)) return false;
    ctx.win.webContents.send(channel, payload);
    return true;
  };
  const refreshDecisions = () => {
    durableDecisions = loadTransferDecisions(storePath());
    return durableDecisions;
  };
  const terminalDecision = (token) =>
    refreshDecisions().decisions[token] || null;

  function deleteDecision(token) {
    const store = refreshDecisions();
    if (!store.decisions[token]) return false;
    delete store.decisions[token];
    saveTransferDecisionsAtomic(storePath(), store);
    durableDecisions = store;
    return true;
  }

  function persistDecision(transaction, status, finalizedRoles = []) {
    return putTransferDecision(storePath(), {
      token: transaction.token,
      status,
      sourceId: transaction.sourceId,
      destinationId: transaction.destinationId,
      sourceEmpty: !!transaction.sourceEmpty,
      discardDestinationState:
        transaction.detachedWindowId === transaction.destinationId,
      requiredRoles: [...transaction.journalRoles.values()],
      finalizedRoles,
      decidedAt: now(),
    });
  }

  function unlock(transaction) {
    for (const id of transaction.lockedRecordIds) {
      if (lockedRecords.get(id) === transaction.token) lockedRecords.delete(id);
    }
    transaction.lockedRecordIds.clear();
  }

  /** Destroy a staged tear-off window (rollback / rejected commit). Takes the
   *  id rather than the transaction so a caller can unlink it FIRST — `closed`
   *  fires synchronously and contextDestroyed must not still see this window as
   *  the transaction's destination. */
  function closeDetached(detachedWindowId) {
    if (!detachedWindowId) return;
    const destination = windowRegistry.get(detachedWindowId);
    if (!isLive(destination)) return;
    destination.pendingTransferToken = null;
    allowedWindowCloses.add(destination.id);
    try {
      destination.win.close();
    } finally {
      allowedWindowCloses.delete(destination.id);
    }
  }

  function clearActive(transaction, { closeHidden = false } = {}) {
    if (transaction.timer !== null) clearTimer(transaction.timer);
    if (transaction.undoTimer !== null) clearTimer(transaction.undoTimer);
    if (transaction.commitRetryTimer !== null) {
      clearTimer(transaction.commitRetryTimer);
    }
    transaction.timer = null;
    transaction.undoTimer = null;
    transaction.commitRetryTimer = null;
    if (activeTransfers.get(transaction.token) === transaction) {
      activeTransfers.delete(transaction.token);
    }
    unlock(transaction);
    const destination = transaction.destinationId
      ? windowRegistry.get(transaction.destinationId)
      : null;
    if (destination?.pendingTransferToken === transaction.token) {
      destination.pendingTransferToken = null;
    }
    if (closeHidden) closeDetached(transaction.detachedWindowId);
  }

  function notifyTerminal(transaction, status) {
    const receipt = {
      token: transaction.token,
      status,
      sourceId: transaction.sourceId,
      destinationId: transaction.destinationId,
    };
    send(liveContext(transaction.destinationId), `tab-transfer:${status}`, receipt);
    send(liveContext(transaction.sourceId), `tab-transfer:${status}`, receipt);
  }

  function assignmentFor(token, role, windowId) {
    return orphanAssignments.get(roleKey(token, role, windowId)) || null;
  }

  function chooseOrphanWorker(decision, ownerWindowId) {
    for (const id of [decision.sourceId, decision.destinationId]) {
      if (id && id !== ownerWindowId) {
        const candidate = liveContext(id);
        if (candidate) return candidate;
      }
    }
    for (const candidate of windowRegistry.values()) {
      if (candidate.id !== ownerWindowId && isLive(candidate)) return candidate;
    }
    return null;
  }

  function assignOrphanedRoles(decision, forced = new Set()) {
    if (!decision) return;
    const finalized = new Set(
      decision.finalizedRoles.map((item) => roleKey(decision.token, item.role, item.windowId)),
    );
    for (const required of decision.requiredRoles) {
      const key = roleKey(decision.token, required.role, required.windowId);
      if (forced.has(key)) forcedOrphanRoles.add(key);
      if (finalized.has(key)) {
        forcedOrphanRoles.delete(key);
        continue;
      }
      if (!forcedOrphanRoles.has(key) && liveContext(required.windowId)) {
        continue;
      }
      let workerId = orphanAssignments.get(key);
      const assignedWorker = liveContext(workerId);
      if (assignedWorker) continue;
      const candidate = chooseOrphanWorker(decision, required.windowId);
      workerId = candidate?.id || null;
      if (workerId) orphanAssignments.set(key, workerId);
      const worker = liveContext(workerId);
      if (worker) {
        send(worker, "tab-transfer:finalize-orphaned", {
          token: decision.token,
          status: decision.status,
          role: required.role,
          windowId: required.windowId,
          orphaned: true,
          ...(decision.discardDestinationState
            && required.role === "destination"
            && required.windowId === decision.destinationId
            ? { discardWindowState: true }
            : {}),
        });
      }
    }
  }

  function expire(token) {
    const transaction = activeTransfers.get(token);
    if (!transaction) return false;
    if (
      transaction.status === "committing"
      && (transaction.commitRetryTimer !== null
        || transaction.commitAttemptsLeft > 0)
    ) {
      // A commit-decision retry is still pending; the timeout must not
      // roll back a transfer whose source already removed its tabs.
      return false;
    }
    if (transaction.status === "prepared" && transaction.journalRoles.size === 0) {
      clearActive(transaction, { closeHidden: true });
      send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
        token,
        reason: "expired",
      });
      return true;
    }
    return beginRollback(transaction, "expired");
  }

  function prepare(ctx, payloadValue) {
    if (!isLive(ctx)) return null;
    let validated;
    try {
      validated = validateTransferPayload(ctx, payloadValue);
    } catch (_error) {
      return null;
    }
    const token = makeToken();
    const transaction = {
      token,
      sourceId: ctx.id,
      destinationId: null,
      inspectedBy: null,
      payload: validated.payload,
      records: validated.records,
      recordSnapshots: [],
      lockedRecordIds: new Set(),
      journalRoles: new Map(),
      status: "prepared",
      timer: null,
      undoTimer: null,
      detachedWindowId: null,
      /** In-flight window boot, so concurrent detach() calls share one. */
      detachPromise: null,
      placement: null,
      sourceEmpty: false,
      commitIndeterminate: false,
      commitRetryTimer: null,
      commitRetryDelay: COMMIT_RECONCILE_INITIAL_MS,
      commitAttemptsLeft: COMMIT_DECISION_RETRY_LIMIT,
      commitWaiter: null,
    };
    transaction.timer = setTimer(() => expire(token), TRANSFER_TIMEOUT_MS);
    activeTransfers.set(token, transaction);
    return token;
  }

  function inspect(ctx, token) {
    const transaction = activeTransfers.get(token);
    if (
      !isLive(ctx)
      || !transaction
      || transaction.status !== "prepared"
      || ctx.id === transaction.sourceId
      || (transaction.detachedWindowId && transaction.detachedWindowId !== ctx.id)
      || (transaction.inspectedBy && transaction.inspectedBy !== ctx.id)
    ) {
      return null;
    }
    transaction.inspectedBy = ctx.id;
    return {
      token,
      status: transaction.status,
      sourceId: transaction.sourceId,
      payload: transaction.payload,
    };
  }

  function journalOpened(ctx, token, role) {
    const transaction = activeTransfers.get(token);
    if (
      !transaction
      || transaction.status === "rolling-back"
      || transaction.status === "committing"
      || transaction.commitIndeterminate
    ) return false;
    const expectedId = role === "source"
      ? transaction.sourceId
      : role === "destination"
        ? transaction.inspectedBy || transaction.destinationId
        : null;
    if (!expectedId || ctx?.id !== expectedId) return false;
    if (role === "destination" && !transaction.destinationId) {
      transaction.destinationId = ctx.id;
    }
    const value = { role, windowId: ctx.id };
    transaction.journalRoles.set(roleKey(token, role, ctx.id), value);
    return true;
  }

  function accept(ctx, token, placement) {
    const transaction = activeTransfers.get(token);
    if (
      !isLive(ctx)
      || !transaction
      || transaction.status !== "prepared"
      || transaction.inspectedBy !== ctx.id
      || (transaction.destinationId && transaction.destinationId !== ctx.id)
    ) {
      return null;
    }
    const source = liveContext(transaction.sourceId);
    if (!source) return null;
    transaction.destinationId = ctx.id;
    for (const record of transaction.records) {
      const ownerToken = lockedRecords.get(record.id);
      if (ownerToken && ownerToken !== token) return null;
    }
    for (const record of transaction.records) {
      lockedRecords.set(record.id, token);
      transaction.lockedRecordIds.add(record.id);
    }
    try {
      transaction.recordSnapshots = reparentRecords(source, ctx, transaction.records);
    } catch (_error) {
      transaction.recordSnapshots = [];
      unlock(transaction);
      return null;
    }
    transaction.placement = placement || { kind: "strip-end" };
    transaction.status = "destination-staged";
    return {
      token,
      status: "destination-staged",
      sourceId: transaction.sourceId,
      destinationId: transaction.destinationId,
      payload: transaction.payload,
      placement: transaction.placement,
      recordIds: transaction.records.map((record) => record.id),
    };
  }

  function reject(ctx, token, reason, duplicateId) {
    const transaction = activeTransfers.get(token);
    if (
      !transaction
      || transaction.status !== "prepared"
      || transaction.inspectedBy !== ctx?.id
      || transaction.journalRoles.size > 0
      || !new Set(["duplicate", "group-full"]).has(reason)
      || (reason === "duplicate" && (typeof duplicateId !== "string" || !duplicateId))
    ) {
      return null;
    }
    const result = {
      reason,
      ...(reason === "duplicate" ? { duplicateId } : {}),
    };
    clearActive(transaction, { closeHidden: true });
    send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
      token,
      ...result,
    });
    return result;
  }

  function destinationReady(ctx, token, ok) {
    const transaction = activeTransfers.get(token);
    if (
      !transaction
      || transaction.destinationId !== ctx?.id
      || transaction.status !== "destination-staged"
    ) {
      return false;
    }
    if (!ok) return beginRollback(transaction, "destination-failed");
    transaction.status = "awaiting-source";
    return send(liveContext(transaction.sourceId), "tab-transfer:remove-source", {
      token,
      payload: transaction.payload,
    });
  }

  function finishCommittedTransfer(transaction, decision) {
    for (const record of transaction.records) {
      if (!Number.isInteger(record.findRequestId)) continue;
      try {
        record.view.webContents.stopFindInPage("clearSelection");
      } catch (_error) {
        /* the page may have closed after the durable commit */
      }
      record.findRequestId = null;
    }
    transaction.status = "committed";
    transaction.commitIndeterminate = false;
    clearActive(transaction);
    notifyTerminal(transaction, "committed");
    const detached = liveContext(transaction.detachedWindowId);
    // Drop-to-place: the torn-off window is created hidden at release and
    // revealed HERE, once the destination renderer has staged the tab, so it
    // never flashes empty first — then fades in at the drop point instead of
    // popping.
    if (detached) showWindowSmoothly(detached.win);
    if (decision.requiredRoles.length === 0) {
      try {
        deleteDecision(transaction.token);
      } catch (_error) {
        /* the durable committed decision remains available for later cleanup */
      }
    } else {
      assignOrphanedRoles(decision);
    }
    const waiter = transaction.commitWaiter;
    transaction.commitWaiter = null;
    waiter?.resolve(true);
    return true;
  }

  function matchesCommittedTransaction(transaction, decision) {
    return decision?.token === transaction.token
      && decision.status === "committed"
      && decision.sourceId === transaction.sourceId
      && decision.destinationId === transaction.destinationId;
  }

  function reconcileIndeterminateCommit(transaction) {
    if (
      !transaction?.commitIndeterminate
      || activeTransfers.get(transaction.token) !== transaction
    ) return false;
    let store;
    try {
      store = loadTransferDecisions(storePath());
    } catch (_error) {
      return false;
    }
    durableDecisions = store;
    const current = store.decisions[transaction.token] || null;
    if (current && !matchesCommittedTransaction(transaction, current)) {
      return false;
    }
    let decision;
    try {
      // A readable committed rename is not enough after its directory fsync
      // failed. Rewriting the same decision establishes a durable boundary.
      // Preserve any acknowledgements a renderer already recorded against
      // the possibly-landed prior write.
      decision = persistDecision(
        transaction,
        "committed",
        current?.finalizedRoles || [],
      );
    } catch (_error) {
      return false;
    }
    return finishCommittedTransfer(transaction, decision);
  }

  function commitWaiter(transaction) {
    if (transaction.commitWaiter) return transaction.commitWaiter.promise;
    let resolve;
    const promise = new Promise((settle) => { resolve = settle; });
    transaction.commitWaiter = { promise, resolve };
    return promise;
  }

  function scheduleIndeterminateCommitRetry(transaction) {
    if (
      !transaction?.commitIndeterminate
      || activeTransfers.get(transaction.token) !== transaction
      || transaction.commitRetryTimer !== null
    ) return false;
    const delay = transaction.commitRetryDelay;
    transaction.commitRetryDelay = Math.min(delay * 2, COMMIT_RECONCILE_MAX_MS);
    transaction.commitRetryTimer = setTimer(() => {
      transaction.commitRetryTimer = null;
      if (!reconcileIndeterminateCommit(transaction)) {
        scheduleIndeterminateCommitRetry(transaction);
      }
    }, delay);
    return true;
  }

  function waitForIndeterminateCommit(transaction) {
    const pending = commitWaiter(transaction);
    if (!reconcileIndeterminateCommit(transaction)) {
      scheduleIndeterminateCommitRetry(transaction);
    }
    return pending;
  }

  function abandonCommitDecision(transaction) {
    transaction.sourceEmpty = false;
    const waiter = transaction.commitWaiter;
    transaction.commitWaiter = null;
    waiter?.resolve(false);
    if (
      !beginRollback(transaction, "commit-decision-failed")
      && activeTransfers.get(transaction.token) === transaction
      && transaction.timer === null
    ) {
      // ponytail: the rolled-back decision write failed too; re-arm the
      // expire timer so rollback keeps retrying instead of stranding.
      transaction.timer = setTimer(
        () => expire(transaction.token),
        TRANSFER_TIMEOUT_MS,
      );
    }
    return false;
  }

  function scheduleCommitDecisionRetry(transaction) {
    if (
      activeTransfers.get(transaction.token) !== transaction
      || transaction.status !== "committing"
      || transaction.commitRetryTimer !== null
    ) return;
    transaction.commitAttemptsLeft -= 1;
    const delay = transaction.commitRetryDelay;
    transaction.commitRetryDelay = Math.min(delay * 2, COMMIT_RECONCILE_MAX_MS);
    transaction.commitRetryTimer = setTimer(() => {
      transaction.commitRetryTimer = null;
      if (
        activeTransfers.get(transaction.token) !== transaction
        || transaction.status !== "committing"
      ) return;
      attemptCommitDecision(transaction);
    }, delay);
  }

  function attemptCommitDecision(transaction) {
    let decision;
    try {
      decision = persistDecision(transaction, "committed");
    } catch (error) {
      if (transaction.timer !== null) clearTimer(transaction.timer);
      transaction.timer = null;
      if (error?.rollbackError) {
        // The committed decision may have reached disk; rollback is no
        // longer an option. Retry reconciliation until it is durable.
        transaction.commitIndeterminate = true;
        transaction.status = "commit-indeterminate";
        return waitForIndeterminateCommit(transaction);
      }
      // Clean failure: the previous valid file is intact and no committed
      // decision landed. Retry the write with backoff before rolling back.
      transaction.status = "committing";
      if (transaction.commitAttemptsLeft > 0) {
        scheduleCommitDecisionRetry(transaction);
        return commitWaiter(transaction);
      }
      return abandonCommitDecision(transaction);
    }
    return finishCommittedTransfer(transaction, decision);
  }

  function sourceRemoved(ctx, token, result) {
    const transaction = activeTransfers.get(token);
    if (!transaction || transaction.sourceId !== ctx?.id) {
      return false;
    }
    if (transaction.commitIndeterminate) {
      return waitForIndeterminateCommit(transaction);
    }
    if (transaction.status === "committing") {
      // An idempotent re-acknowledgement joins the pending commit attempt.
      return commitWaiter(transaction);
    }
    if (transaction.status !== "awaiting-source") return false;
    const normalized = typeof result === "boolean" ? { ok: result } : result;
    if (!normalized?.ok) return beginRollback(transaction, "source-failed");
    transaction.sourceEmpty = !!normalized.sourceEmpty;
    return attemptCommitDecision(transaction);
  }

  function discardTransferredRecords(transaction) {
    const records = new Set([
      ...transaction.records,
      ...transaction.recordSnapshots.map((snapshot) => snapshot.record),
    ]);
    for (const record of records) {
      for (const context of windowRegistry.values()) {
        if (context.views.get(record.id) !== record) continue;
        context.visibleViewIds.delete(record.id);
        context.views.delete(record.id);
        try {
          context.win.contentView.removeChildView(record.view);
        } catch (_error) {
          /* the owning native surface may already be destroyed */
        }
      }
      record.navigation = null;
      record.ownerId = null;
      try {
        record.view.webContents.close();
      } catch (_error) {
        /* the native web contents may already be closed */
      }
    }
  }

  function finalizeRollback(transaction, destinationTimedOut = false) {
    if (
      activeTransfers.get(transaction?.token) !== transaction
      || transaction.status !== "rolling-back"
    ) return false;
    const source = liveContext(transaction.sourceId);
    const destination = windowRegistry.get(transaction.destinationId);
    if (source && destination && transaction.recordSnapshots.length > 0) {
      restoreRecords(source, destination, transaction.recordSnapshots);
    } else if (!source && transaction.records.length > 0) {
      discardTransferredRecords(transaction);
    }
    clearActive(transaction, { closeHidden: true });
    notifyTerminal(transaction, "rolled-back");
    const decision = terminalDecision(transaction.token);
    if (decision?.requiredRoles.length === 0) {
      deleteDecision(transaction.token);
    } else {
      const forced = destinationTimedOut
        ? new Set([roleKey(
          transaction.token,
          "destination",
          transaction.destinationId,
        )])
        : new Set();
      assignOrphanedRoles(decision, forced);
    }
    return true;
  }

  function beginRollback(transaction, reason, destinationGone = false) {
    if (!transaction || transaction.status === "committed") return false;
    if (transaction.commitIndeterminate) {
      scheduleIndeterminateCommitRetry(transaction);
      return true;
    }
    if (transaction.status === "rolling-back") {
      if (destinationGone) return finalizeRollback(transaction);
      return true;
    }
    if (transaction.status === "prepared" && transaction.journalRoles.size === 0) {
      clearActive(transaction, { closeHidden: true });
      send(liveContext(transaction.sourceId), "tab-transfer:rolled-back", {
        token: transaction.token,
        status: "rolled-back",
        reason,
      });
      return true;
    }
    try {
      persistDecision(transaction, "rolled-back");
    } catch (_error) {
      return false;
    }
    transaction.status = "rolling-back";
    if (transaction.timer !== null) clearTimer(transaction.timer);
    transaction.timer = null;
    if (transaction.commitRetryTimer !== null) {
      clearTimer(transaction.commitRetryTimer);
      transaction.commitRetryTimer = null;
    }
    const waiter = transaction.commitWaiter;
    transaction.commitWaiter = null;
    waiter?.resolve(false);
    const destination = liveContext(transaction.destinationId);
    if (!destination || destinationGone) return finalizeRollback(transaction);
    transaction.undoTimer = setTimer(
      () => finalizeRollback(transaction, true),
      DESTINATION_UNDO_TIMEOUT_MS,
    );
    if (!send(destination, "tab-transfer:undo-destination", {
      token: transaction.token,
      reason,
      discardWindowState: transaction.detachedWindowId === destination.id,
    })) {
      if (transaction.undoTimer !== null) clearTimer(transaction.undoTimer);
      transaction.undoTimer = null;
      return finalizeRollback(transaction, true);
    }
    return true;
  }

  function destinationUndone(ctx, token, ok) {
    const transaction = activeTransfers.get(token);
    if (
      !transaction
      || transaction.status !== "rolling-back"
      || transaction.destinationId !== ctx?.id
      || !ok
    ) {
      return false;
    }
    if (transaction.undoTimer !== null) clearTimer(transaction.undoTimer);
    transaction.undoTimer = null;
    return finalizeRollback(transaction);
  }

  function rollbackTransfer(token, reason = "manual") {
    const transaction = activeTransfers.get(token);
    if (!transaction || transaction.status === "committed") return false;
    return beginRollback(transaction, reason);
  }

  function cancel(ctx, token) {
    const transaction = activeTransfers.get(token);
    if (!transaction || transaction.sourceId !== ctx?.id) return false;
    if (transaction.status === "prepared" && transaction.journalRoles.size === 0) {
      clearActive(transaction, { closeHidden: true });
      send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
        token,
        reason: "cancelled",
      });
      return true;
    }
    return beginRollback(transaction, "cancelled");
  }

  function status(ctx, token) {
    const transaction = activeTransfers.get(token);
    if (transaction) {
      if (
        ctx?.id !== transaction.sourceId
        && ctx?.id !== transaction.destinationId
        && ctx?.id !== transaction.inspectedBy
      ) {
        return null;
      }
      return {
        status: transaction.status,
        sourceId: transaction.sourceId,
        destinationId: transaction.destinationId,
      };
    }
    const decision = terminalDecision(token);
    if (!decision) return null;
    const participant = ctx?.id === decision.sourceId || ctx?.id === decision.destinationId;
    const assigned = decision.requiredRoles.some((required) =>
      assignmentFor(token, required.role, required.windowId) === ctx?.id);
    if (!participant && !assigned) return null;
    return {
      status: decision.status,
      sourceId: decision.sourceId,
      destinationId: decision.destinationId,
    };
  }

  function journalFinalized(ctx, token, role, ownerWindowId = ctx?.id) {
    if (!ctx || (role !== "source" && role !== "destination")) return false;
    const decision = terminalDecision(token);
    if (!decision) return false;
    const required = decision.requiredRoles.find(
      (item) => item.role === role && item.windowId === ownerWindowId,
    );
    if (!required) return false;
    const assignedWorker = assignmentFor(token, role, ownerWindowId);
    if (ctx.id !== ownerWindowId && assignedWorker !== ctx.id) return false;
    let result;
    try {
      result = ackTransferDecision(storePath(), token, required);
    } catch (_error) {
      return false;
    }
    if (result.complete) delete durableDecisions.decisions[token];
    else durableDecisions.decisions[token] = result.decision;
    const finalizedKey = roleKey(token, role, ownerWindowId);
    orphanAssignments.delete(finalizedKey);
    forcedOrphanRoles.delete(finalizedKey);
    if (result.decision.sourceEmpty && role === "source") {
      const source = liveContext(result.decision.sourceId);
      if (source) {
        allowedWindowCloses.add(source.id);
        try {
          source.win.close();
        } finally {
          allowedWindowCloses.delete(source.id);
        }
      }
    }
    if (result.complete) {
      for (const key of [...orphanAssignments.keys()]) {
        if (key.startsWith(`[\"${token}\",`)) orphanAssignments.delete(key);
      }
      for (const key of [...forcedOrphanRoles]) {
        if (key.startsWith(`[\"${token}\",`)) forcedOrphanRoles.delete(key);
      }
    }
    return true;
  }

  function pendingTerminal(ctx, windowId) {
    if (!isLive(ctx) || ctx.id !== windowId) return [];
    const pending = [];
    let store;
    try {
      store = refreshDecisions();
    } catch (_error) {
      // A transient store read failure yields no pending work this round;
      // the renderer re-queries on its next recovery pass.
      return pending;
    }
    for (const decision of Object.values(store.decisions)) {
      const finalized = new Set(
        decision.finalizedRoles.map((item) =>
          roleKey(decision.token, item.role, item.windowId)),
      );
      for (const required of decision.requiredRoles) {
        const key = roleKey(decision.token, required.role, required.windowId);
        if (finalized.has(key)) continue;
        let orphaned = false;
        if (required.windowId !== ctx.id) {
          const assigned = orphanAssignments.get(key);
          if (assigned !== ctx.id) {
            if (assigned && liveContext(assigned)) continue;
            if (!forcedOrphanRoles.has(key) && liveContext(required.windowId)) {
              continue;
            }
            orphanAssignments.set(key, ctx.id);
          }
          orphaned = true;
        }
        pending.push({
          token: decision.token,
          status: decision.status,
          sourceId: decision.sourceId,
          destinationId: decision.destinationId,
          role: required.role,
          windowId: required.windowId,
          orphaned,
          ...(decision.discardDestinationState
            && required.role === "destination"
            && required.windowId === decision.destinationId
            ? { discardWindowState: true }
            : {}),
        });
      }
    }
    return pending;
  }

  async function detach(ctx, token) {
    const transaction = activeTransfers.get(token);
    if (
      !transaction
      || transaction.sourceId !== ctx?.id
      || transaction.status !== "prepared"
    ) {
      return null;
    }
    if (transaction.detachedWindowId) return transaction.detachedWindowId;
    // Idempotence has to latch on the in-flight BOOT, not just the finished
    // window: a single leave-the-strip event can call detach() while a
    // release's detach() is still awaiting createWindow. A completed-only
    // guard lets both through and tears off two windows, one of which is
    // instantly orphaned.
    if (transaction.detachPromise) return transaction.detachPromise;
    const booting = detachUnlatched(transaction, token);
    transaction.detachPromise = booting;
    try {
      return await booting;
    } finally {
      transaction.detachPromise = null;
    }
  }

  async function detachUnlatched(transaction, token) {
    const windowId = `window-${makeToken()}`;
    const destination = await createDetachedWindow({ windowId, show: false, detached: true });
    if (activeTransfers.get(token) !== transaction || transaction.status !== "prepared") {
      allowedWindowCloses.add(destination.id);
      try {
        destination.win.close();
      } finally {
        allowedWindowCloses.delete(destination.id);
      }
      return null;
    }
    // Chrome drops the torn-off window where the tab was released, not at
    // the saved window position. Move it while it is still hidden so the
    // reposition is never visible.
    centerHiddenWindowOnCursor(destination.win);
    transaction.detachedWindowId = destination.id;
    transaction.destinationId = destination.id;
    destination.pendingTransferToken = token;
    return destination.id;
  }

  function claimPending(ctx, windowId) {
    if (!isLive(ctx) || ctx.id !== windowId) return null;
    const token = ctx.pendingTransferToken;
    const transaction = token ? activeTransfers.get(token) : null;
    if (
      !transaction
      || transaction.detachedWindowId !== ctx.id
      || transaction.status === "committed"
      || transaction.status === "rolling-back"
    ) {
      return null;
    }
    return token;
  }

  function contextDestroyed(ctx) {
    if (!ctx) return;
    for (const transaction of [...activeTransfers.values()]) {
      if (transaction.destinationId === ctx.id) {
        beginRollback(transaction, "destination-destroyed", true);
      } else if (
        transaction.status === "prepared"
        && transaction.inspectedBy === ctx.id
      ) {
        clearActive(transaction, { closeHidden: true });
        send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
          token: transaction.token,
          reason: "destination-destroyed",
        });
      } else if (transaction.sourceId === ctx.id) {
        if (
          transaction.status === "prepared"
          && transaction.journalRoles.size === 0
        ) {
          clearActive(transaction, { closeHidden: true });
        } else {
          beginRollback(transaction, "source-destroyed");
        }
      }
    }
    const store = refreshDecisions();
    for (const decision of Object.values(store.decisions)) assignOrphanedRoles(decision);
  }

  function windowClosing(ctx, event) {
    if (!ctx || allowedWindowCloses.has(ctx.id)) return false;
    const transaction = [...activeTransfers.values()].find((candidate) =>
      candidate.sourceId === ctx.id || candidate.destinationId === ctx.id);
    if (!transaction) return false;
    event?.preventDefault?.();
    beginRollback(transaction, "window-closing");
    return true;
  }

  return {
    activeTransfers,
    prepare,
    inspect,
    accept,
    reject,
    status,
    journalOpened,
    journalFinalized,
    destinationReady,
    sourceRemoved,
    destinationUndone,
    rollback: rollbackTransfer,
    cancel,
    detach,
    claimPending,
    pendingTerminal,
    contextDestroyed,
    windowClosing,
    isLocked(id) { return lockedRecords.has(id); },
  };
}

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

function sendState(record, extra) {
  const ctx = ownerOf(record);
  if (!ctx) return;
  const wc = record.view.webContents;
  // 加载初期 URL 未 commit 时 getURL()/getTitle() 返回空串——发出去会把
  // 渲染端 store 里的 url 冲成空，导致面板被卸载（白屏竞态）。空则不发。
  const u = wc.getURL();
  const ti = wc.getTitle();
  ctx.win.webContents.send("webtab:state", {
    id: record.id,
    ...(u ? { url: u } : {}),
    ...(ti ? { title: ti } : {}),
    loading: wc.isLoading(),
    canGoBack: wc.navigationHistory.canGoBack(),
    canGoForward: wc.navigationHistory.canGoForward(),
    ...extra,
  });
}

function forwardFindResult(record, result) {
  if (tabTransfers.isLocked(record.id) || result?.requestId !== record.findRequestId) return false;
  const owner = ownerOf(record);
  if (!owner) return false;
  owner.win.webContents.send("webtab:find-result", {
    id: record.id,
    activeMatchOrdinal: Number(result?.activeMatchOrdinal) || 0,
    matches: Number(result?.matches) || 0,
    finalUpdate: Boolean(result?.finalUpdate),
  });
  return true;
}

function handleWebTabShortcut(record, event, input) {
  if (
    input?.type !== "keyDown"
    || (!input.meta && !input.control)
    || tabTransfers.isLocked(record.id)
  ) return false;
  const key = String(input.key || "").toLowerCase();
  const owner = ownerOf(record);
  if (!owner) return false;
  if (key === "f") {
    event.preventDefault();
    owner.win.webContents.send("webtab:command", { id: record.id, command: "find" });
    owner.win.webContents.focus?.();
  } else if (key === "p") {
    event.preventDefault();
    void printView(owner, record.id);
  } else if (key === "+" || key === "=") {
    event.preventDefault();
    zoomView(owner, record.id, "in");
  } else if (key === "-") {
    event.preventDefault();
    zoomView(owner, record.id, "out");
  } else if (key === "0") {
    event.preventDefault();
    zoomView(owner, record.id, "reset");
  } else {
    return false;
  }
  return true;
}

function isWebUrl(u) {
  try {
    const p = new URL(u).protocol;
    return p === "http:" || p === "https:";
  } catch {
    return false;
  }
}

// 地址栏导航（Chrome 式）还允许 file://——输入本地路径直接打开本地
// 文件/目录。弹窗（setWindowOpenHandler）仍只放行 web，网页不能把
// 视图带去本地文件。
function isTabUrl(u) {
  try {
    const p = new URL(u).protocol;
    return p === "http:" || p === "https:" || p === "file:";
  } catch {
    return false;
  }
}

function loadView(record, url) {
  const pending = record.navigation;
  if (pending && pending.url === url) return pending.promise;
  const view = record.view;
  if (
    !pending
    && view.webContents.getURL() === url
    && !view.webContents.isLoading()
  ) {
    return Promise.resolve(record);
  }
  const promise = view.webContents
    .loadURL(url)
    .then(() => record)
    .finally(() => {
      if (record.navigation?.promise === promise) {
        record.navigation = null;
      }
    });
  record.navigation = { url, promise };
  return promise;
}

// create-if-missing; loads url only on CREATION. Re-activating a tab
// re-mounts the renderer pane, which calls ensure again — reloading
// here would throw away scroll/form/SPA state and defeat the whole
// persistent-view design. Explicit navigation goes through navigate.
function ensureView(ctx, id, url) {
  if (!ctx || typeof id !== "string" || !id) return null;
  if (tabTransfers.isLocked(id)) return null;
  let record = recordFor(ctx, id);
  if (!record && !ctx.views.has(id)) {
    const view = new WebContentsView({
      webPreferences: { partition: "persist:webtabs" },
    });
    record = { id, view, ownerId: ctx.id, navigation: null, findRequestId: null };
    ctx.views.set(id, record);
    ctx.win.contentView.addChildView(view);
    view.setVisible(false);
    const wc = view.webContents;
    // Native popup windows are disabled. A valid web popup is delegated to
    // this record's renderer window, which creates a distinct Browser tab and
    // leaves the opener Page (and any exact-page agent session) unchanged.
    wc.setWindowOpenHandler(({ url: popupUrl }) => {
      if (isWebUrl(popupUrl)) {
        const owner = ownerOf(record);
        owner?.win.webContents.send("webtab:popup", {
          openerId: id,
          url: popupUrl,
        });
      }
      return { action: "deny" };
    });
    for (const ev of [
      "did-navigate",
      "did-navigate-in-page",
      "page-title-updated",
      "did-start-loading",
      "did-stop-loading",
    ]) {
      wc.on(ev, () => sendState(record));
    }
    // Browsing history. The store folds repeat hits on the head URL into one
    // row, so the title/favicon events that follow a navigation enrich the
    // entry instead of appending duplicates.
    const noteVisit = () => {
      if (wc.isDestroyed()) return;
      safeRecordVisit({
        url: wc.getURL(),
        title: wc.getTitle(),
        faviconUrl: record.faviconUrl || "",
        visitedAt: Date.now(),
      });
    };
    wc.on("did-navigate", noteVisit);
    wc.on("did-navigate-in-page", (_event, _url, isMainFrame) => {
      if (isMainFrame) noteVisit();
    });
    wc.on("page-title-updated", noteVisit);
    wc.on("page-favicon-updated", (_event, favicons) => {
      record.faviconUrl = Array.isArray(favicons) ? favicons[0] || "" : "";
      sendState(record, { faviconUrl: record.faviconUrl });
      noteVisit();
    });
    // 新页面没有 favicon 时不会再触发 page-favicon-updated——导航提交时先清
    // 掉上一页的图标，否则 tab 会一直挂着旧站点的 icon。
    wc.on("did-navigate", () => {
      record.faviconUrl = "";
      sendState(record, { faviconUrl: "" });
    });
    wc.on("found-in-page", (_event, result) => forwardFindResult(record, result));
    wc.on("before-input-event", (event, input) => handleWebTabShortcut(record, event, input));
    if (url && isTabUrl(url)) void loadView(record, url).catch(() => {});
  }
  return record;
}

async function navigateView(ctx, id, url) {
  if (!url || !isTabUrl(url)) return null;
  const record = recordFor(ctx, id) || ensureView(ctx, id, "");
  return record ? loadView(record, url) : null;
}

const WEBTAB_ZOOM_FACTORS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
const PIP_VIRTUAL_WIDTH = 1920;
const PIP_ZOOM_MIN = 0.25;

function pipLayoutZoom(width) {
  // CSS viewport = viewWidth / zoom. CDP/Playwright Input and
  // screenshot(scale="css") already use that CSS space.
  return Math.max(PIP_ZOOM_MIN, Math.min(1, width / PIP_VIRTUAL_WIDTH));
}

function rememberUserZoom(record) {
  if (record.userZoomFactor != null) return;
  try {
    record.userZoomFactor = record.pipLayoutZoom
      ? 1
      : record.view.webContents.getZoomFactor();
  } catch (_error) {
    record.userZoomFactor = 1;
  }
}

function setPipZoom(ctx, id, width) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  const wc = record.view.webContents;
  try {
    if (typeof width === "number" && width > 0) {
      rememberUserZoom(record);
      const factor = pipLayoutZoom(width);
      record.pipLayoutZoom = factor;
      wc.setZoomFactor(factor);
      return true;
    }
    record.pipLayoutZoom = null;
    wc.setZoomFactor(record.userZoomFactor ?? 1);
    return true;
  } catch (_error) {
    return false;
  }
}

function findView(ctx, id, query, options) {
  const record = recordFor(ctx, id);
  const needle = typeof query === "string" ? query.slice(0, 512) : "";
  if (!record || !needle) return false;
  try {
    record.findRequestId = record.view.webContents.findInPage(needle, {
      forward: options?.forward !== false,
      findNext: options?.findNext === true,
    });
    return true;
  } catch (_error) {
    return false;
  }
}

function stopFindView(ctx, id, action) {
  const record = recordFor(ctx, id);
  if (!record || !["clearSelection", "keepSelection", "activateSelection"].includes(action)) {
    return false;
  }
  try {
    record.findRequestId = null;
    record.view.webContents.stopFindInPage(action);
    return true;
  } catch (_error) {
    return false;
  }
}

async function captureView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return null;
  try {
    const image = await record.view.webContents.capturePage();
    if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) {
      return null;
    }
    return image.toDataURL();
  } catch (_error) {
    return null;
  }
}

function zoomView(ctx, id, action) {
  const record = recordFor(ctx, id);
  if (!record || !["in", "out", "reset"].includes(action)) return null;
  try {
    const wc = record.view.webContents;
    rememberUserZoom(record);
    const current = record.userZoomFactor ?? wc.getZoomFactor();
    const nearest = WEBTAB_ZOOM_FACTORS.reduce(
      (best, value, index) => Math.abs(value - current) < Math.abs(WEBTAB_ZOOM_FACTORS[best] - current)
        ? index
        : best,
      0,
    );
    const index = action === "reset"
      ? WEBTAB_ZOOM_FACTORS.indexOf(1)
      : Math.max(
          0,
          Math.min(WEBTAB_ZOOM_FACTORS.length - 1, nearest + (action === "in" ? 1 : -1)),
        );
    const factor = WEBTAB_ZOOM_FACTORS[index];
    record.userZoomFactor = factor;
    if (!record.pipLayoutZoom) wc.setZoomFactor(factor);
    return Math.round(factor * 100);
  } catch (_error) {
    return null;
  }
}

function printPdfDefaultName(title) {
  const stem = String(title || "page")
    .normalize("NFC")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .replace(/[.\s]+$/g, "")
    .slice(0, 120);
  return `${stem || "page"}.pdf`;
}

async function printView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  const wc = record.view.webContents;
  let destroyed = false;
  const printResult = await new Promise((resolve) => {
    let settled = false;
    const finish = (success, failureReason = "") => {
      if (settled) return;
      settled = true;
      wc.removeListener("destroyed", onDestroyed);
      resolve({ success: Boolean(success), failureReason: String(failureReason || "") });
    };
    const onDestroyed = () => {
      destroyed = true;
      finish(false);
    };
    wc.once("destroyed", onDestroyed);
    try {
      wc.print(
        { silent: false, printBackground: true },
        finish,
      );
    } catch (_error) {
      finish(false);
    }
  });
  if (printResult.success) return true;
  if (printResult.failureReason === "Print job canceled") return false;
  if (
    destroyed
    || wc.isDestroyed()
    || ctx.win.isDestroyed()
    || recordFor(ctx, id) !== record
  ) return false;
  let stagedPdfPath = "";
  try {
    const selected = await dialog.showSaveDialog(ctx.win, {
      title: "Save page as PDF",
      defaultPath: path.join(app.getPath("downloads"), printPdfDefaultName(wc.getTitle())),
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    if (selected.canceled || !selected.filePath) return false;
    if (wc.isDestroyed() || ctx.win.isDestroyed() || recordFor(ctx, id) !== record) return false;
    const pdf = await wc.printToPDF({ printBackground: true, preferCSSPageSize: true });
    if (!Buffer.isBuffer(pdf)) return false;
    if (wc.isDestroyed() || ctx.win.isDestroyed() || recordFor(ctx, id) !== record) return false;
    stagedPdfPath = path.join(
      path.dirname(selected.filePath),
      `.${path.basename(selected.filePath)}.${process.pid}.${crypto.randomUUID()}.tmp`,
    );
    await fs.promises.writeFile(stagedPdfPath, pdf, { flag: "wx", mode: 0o600 });
    if (wc.isDestroyed() || ctx.win.isDestroyed() || recordFor(ctx, id) !== record) return false;
    await fs.promises.rename(stagedPdfPath, selected.filePath);
    stagedPdfPath = "";
    return true;
  } catch (_error) {
    return false;
  } finally {
    if (stagedPdfPath) {
      try {
        await fs.promises.unlink(stagedPdfPath);
      } catch (_error) {
        // Best effort: the selected destination remains untouched.
      }
    }
  }
}

function normalizedBounds(bounds) {
  return {
    x: Math.round(Number(bounds?.x)) || 0,
    y: Math.round(Number(bounds?.y)) || 0,
    width: Math.max(0, Math.round(Number(bounds?.width)) || 0),
    height: Math.max(0, Math.round(Number(bounds?.height)) || 0),
  };
}

function rendererZoomFactor(event) {
  const senderZoom = Number(event?.sender?.getZoomFactor?.());
  return Number.isFinite(senderZoom) && senderZoom > 0 ? senderZoom : 1;
}

function normalizedRendererBounds(event, bounds) {
  const zoom = rendererZoomFactor(event);
  return normalizedBounds({
    x: Number(bounds?.x) * zoom,
    y: Number(bounds?.y) * zoom,
    width: Number(bounds?.width) * zoom,
    height: Number(bounds?.height) * zoom,
  });
}

function normalizedRendererMenuOptions(event, options) {
  const source = options && typeof options === "object" ? options : {};
  const anchor = source.anchor && typeof source.anchor === "object"
    ? { ...source.anchor }
    : {};
  const zoom = rendererZoomFactor(event);
  for (const key of ["x", "right", "y", "rightInset", "top", "vw", "vh"]) {
    const value = Number(anchor[key]);
    if (Number.isFinite(value)) anchor[key] = value * zoom;
  }
  const normalized = { ...source, anchor };
  for (const key of ["width", "height"]) {
    const value = Number(source[key]);
    if (Number.isFinite(value)) normalized[key] = value * zoom;
  }
  return normalized;
}

function syncVisibleViews(ctx, items) {
  if (!ctx || ctx.win.isDestroyed() || !Array.isArray(items)) return false;
  const desired = new Map();
  for (const item of items) {
    if (!item || typeof item.id !== "string") return false;
    const record = recordFor(ctx, item.id);
    if (!record) return false;
    desired.set(item.id, {
      record,
      bounds: normalizedBounds(item.bounds),
    });
  }

  for (const record of ctx.views.values()) {
    if (record.ownerId === ctx.id && !desired.has(record.id)) {
      record.view.setVisible(false);
    }
  }
  for (const { record, bounds } of desired.values()) {
    record.view.setBounds(bounds);
    record.view.setVisible(true);
  }
  ctx.visibleViewIds = new Set(desired.keys());
  return true;
}

function currentVisibleItems(ctx, excludedId = null) {
  const items = [];
  for (const id of ctx.visibleViewIds) {
    if (id === excludedId) continue;
    const record = recordFor(ctx, id);
    if (!record) continue;
    items.push({ id, bounds: record.view.getBounds() });
  }
  return items;
}

function showView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  const desired = currentVisibleItems(ctx, id);
  desired.push({ id, bounds: record.view.getBounds() });
  return syncVisibleViews(ctx, desired);
}

function hideView(ctx, id) {
  if (!recordFor(ctx, id)) return false;
  return syncVisibleViews(ctx, currentVisibleItems(ctx, id));
}

async function devToolsTargetId(webContents) {
  const client = webContents?.debugger;
  if (!client) return null;
  let attachedHere = false;
  try {
    if (!client.isAttached()) {
      client.attach("1.3");
      attachedHere = true;
    }
    const result = await client.sendCommand("Target.getTargetInfo");
    const targetId = result?.targetInfo?.targetId;
    return typeof targetId === "string" && targetId ? targetId : null;
  } catch {
    return null;
  } finally {
    if (attachedHere && client.isAttached()) client.detach();
  }
}

async function activateView(ctx, id, url, requireVisible = false) {
  let record;
  if (url) {
    if (!isTabUrl(url)) return null;
    record = recordFor(ctx, id)
      || (!requireVisible ? ensureView(ctx, id, "") : null);
    if (
      !record
      || (requireVisible
        ? !ctx.visibleViewIds.has(id)
        : !showView(ctx, id))
    ) return null;
    record = await navigateView(ctx, id, url);
    if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null;
  } else {
    record = recordFor(ctx, id);
    if (
      !record
      || (requireVisible
        ? !ctx.visibleViewIds.has(id)
        : !showView(ctx, id))
    ) return null;
  }
  const targetId = await devToolsTargetId(record.view.webContents);
  if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null;
  return targetId;
}

async function resolveView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return null;
  const targetId = await devToolsTargetId(record.view.webContents);
  return recordFor(ctx, id) === record ? targetId : null;
}

async function inspectView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return null;
  const contents = record.view.webContents;
  const targetId = await devToolsTargetId(contents);
  if (!targetId || recordFor(ctx, id) !== record) return null;
  return {
    target_id: targetId,
    url: contents.getURL(),
    title: contents.getTitle(),
  };
}

const SURFACE_PREVIEW_SCRIPT = `(() => {
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 0 && rect.height > 0
      && rect.bottom > 0 && rect.right > 0
      && rect.top < innerHeight && rect.left < innerWidth;
  };
  let bodyText = "";
  if (document.body) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && bodyText.length <= 2000) {
      const parent = walker.currentNode.parentElement;
      if (!parent || !visible(parent)) continue;
      const text = (walker.currentNode.textContent || "").replace(/\\s+/g, " ").trim();
      if (text) bodyText += (bodyText ? " " : "") + text;
    }
  }
  const landmarkSelector = [
    "header", "nav", "main", "aside", "footer",
    "[role=banner]", "[role=navigation]", "[role=main]",
    "[role=complementary]", "[role=contentinfo]", "[role=search]",
  ].join(",");
  const visibleLandmarks = Array.from(document.querySelectorAll(landmarkSelector))
    .filter(visible);
  const landmarks = visibleLandmarks
    .slice(0, 12)
    .map((element) => ({
      role: element.getAttribute("role") || element.tagName.toLowerCase(),
      name: (
        element.getAttribute("aria-label") || element.getAttribute("title") || ""
      ).replace(/\\s+/g, " ").trim().slice(0, 160),
    }));
  const interactiveSelector = [
    "a[href]", "button", "input", "textarea", "select", "summary",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]",
    "[role=tab]", "[role=menuitem]", "[contenteditable=true]",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  return {
    visible_text_excerpt: bodyText.slice(0, 2000),
    text_truncated: bodyText.length > 2000,
    aria_landmarks: landmarks,
    landmarks_truncated: visibleLandmarks.length > landmarks.length,
    interactive_count: Array.from(document.querySelectorAll(interactiveSelector))
      .filter(visible).length,
  };
})()`;

async function previewView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record || !ctx.visibleViewIds.has(id)) return null;
  const wc = record.view.webContents;
  try {
    const [preview, targetId] = await Promise.all([
      wc.executeJavaScript(SURFACE_PREVIEW_SCRIPT, true),
      devToolsTargetId(wc),
    ]);
    if (!targetId || recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) {
      return null;
    }
    return {
      tab_id: id,
      target_id: targetId,
      url: wc.getURL(),
      title: wc.getTitle(),
      preview,
    };
  } catch {
    return null;
  }
}

function withView(ctx, id, fn) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  fn(record);
  return true;
}

// reload/navigationHistory calls replace any in-flight loadURL Promise
// without going through loadView. Remove that stale registry entry before
// invoking the native operation, so a following activation cannot reuse a
// Promise Electron is about to reject with ERR_ABORTED.
function runNativeNavigation(ctx, id, navigate) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  record.navigation = null;
  navigate(record.view.webContents);
  return true;
}

function destroyView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  ctx.visibleViewIds.delete(id);
  record.navigation = null;
  ctx.views.delete(id);
  try {
    ctx.win.contentView.removeChildView(record.view);
  } catch (_e) {
    /* already detached */
  }
  try {
    record.view.webContents.close();
  } catch (_e) {
    /* already closed */
  }
  return true;
}

function clearOwnedViews(ctx) {
  for (const record of [...ctx.views.values()]) {
    if (record.ownerId === ctx.id) destroyView(ctx, record.id);
  }
  ctx.visibleViewIds = new Set();
}

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
function resizeMenuOverlay(ctx, size) {
  const view = ctx && ctx.mainMenuView;
  const anchor = ctx && ctx.mainMenuAnchor;
  if (!view || !anchor || view.webContents.isDestroyed()) return;
  const zoom = Number.isFinite(Number(anchor.zoom)) && Number(anchor.zoom) > 0
    ? Number(anchor.zoom)
    : 1;
  const gutter = MAIN_MENU_GUTTER * zoom;
  const panelW = Math.max(1, Math.round(Number(size && size.width) || 0));
  let panelH = Math.max(1, Math.round(Number(size && size.height) || 0));
  panelH = Math.min(panelH, Math.max(1, anchor.winH - 16 * zoom));
  if (!panelW || !panelH) return;
  const { x, y } = clampContextMenuPanel(anchor, panelW, panelH);
  view.setBounds({
    x: Math.round(x - gutter),
    y: Math.round(y - gutter),
    width: Math.round(panelW + gutter * 2),
    height: Math.round(panelH + gutter * 2),
  });
}

function hasNestedMenuItems(items) {
  return Array.isArray(items) && items.some((item) =>
    Array.isArray(item && item.children) && item.children.length > 0,
  );
}

function menuOverlayUrl(theme, items, anchor, width, cascade = false) {
  let origin = "http://127.0.0.1:" + WEB_PORT;
  try {
    origin = new URL(START_URL).origin;
  } catch (_e) {
    /* keep fallback */
  }
  const q = new URLSearchParams();
  if (MENU_THEME_ID_SET.has(theme)) q.set("theme", theme);
  if (items) q.set("items", JSON.stringify(items));
  if (anchor) {
    q.set("x", String(anchor.x));
    q.set("y", String(anchor.y));
  }
  if (cascade) q.set("cascade", "1");
  if (Number.isFinite(width) && width > 0) q.set("width", String(width));
  return (
    origin
    + (items ? "/menu-overlay/context-menu?" : "/menu-overlay/main-menu?")
    + q.toString()
  );
}

function closeMainMenu(ctx) {
  cancelMainMenuClose(ctx);
  if (!ctx || !ctx.mainMenuView) return;
  const view = ctx.mainMenuView;
  ctx.mainMenuView = null;
  ctx.mainMenuAnchor = null;
  ctx.mainMenuCascade = false;
  ctx.mainMenuPendingUpdate = null;
  try {
    if (!ctx.win.isDestroyed()) ctx.win.contentView.removeChildView(view);
  } catch (_e) {
    /* already detached */
  }
  try {
    view.webContents.close();
  } catch (_e) {
    /* already closed */
  }
}

function cancelMainMenuClose(ctx) {
  if (!ctx || !ctx.mainMenuCloseTimer) return;
  clearTimeout(ctx.mainMenuCloseTimer);
  ctx.mainMenuCloseTimer = null;
}

function scheduleMainMenuClose(ctx, delay) {
  if (!ctx || !ctx.mainMenuView) return;
  cancelMainMenuClose(ctx);
  const requestedDelay = Number(delay);
  const closeDelay = Number.isFinite(requestedDelay)
    ? Math.min(500, Math.max(0, requestedDelay))
    : 120;
  ctx.mainMenuCloseTimer = setTimeout(() => {
    ctx.mainMenuCloseTimer = null;
    closeMainMenu(ctx);
  }, closeDelay);
}

function openMainMenu(ctx, opts, zoom = 1) {
  if (!ctx || ctx.win.isDestroyed()) return;
  cancelMainMenuClose(ctx);
  const menuZoom = Number.isFinite(Number(zoom)) && Number(zoom) > 0
    ? Number(zoom)
    : 1;
  const requestedCascade = Boolean(opts && opts.cascade);
  const requestedItems = Array.isArray(opts && opts.items) ? opts.items : null;
  const requestedAnchor = (opts && opts.anchor) || {};

  // Adjacent bookmark folders share one live overlay. Replacing its data is
  // immediate and preserves already decoded favicons; rebuilding the whole
  // WebContentsView on every mouseenter made each folder look as if its icons
  // were being fetched again.
  if (
    requestedCascade
    && requestedItems
    && ctx.mainMenuCascade
    && ctx.mainMenuView
    && !ctx.mainMenuView.webContents.isDestroyed()
  ) {
    const { width: contentW, height: contentH } = ctx.win.getContentBounds();
    const winW = Number(requestedAnchor.vw) || contentW;
    const winH = Number(requestedAnchor.vh) || contentH;
    const geometry = cascadeMenuGeometry(requestedAnchor, winW, winH, menuZoom);
    const update = {
      items: requestedItems,
      x: geometry.anchor.x,
      y: geometry.anchor.y,
      theme: opts && opts.theme,
      width: Number.isFinite(Number(opts && opts.width))
        ? Number(opts.width) / menuZoom
        : undefined,
    };
    ctx.mainMenuView.setBounds(geometry.bounds);
    if (ctx.mainMenuView.webContents.isLoadingMainFrame()) {
      ctx.mainMenuPendingUpdate = update;
    } else {
      ctx.mainMenuView.webContents.send("main-menu:update", update);
    }
    return;
  }

  closeMainMenu(ctx);
  const gutter = MAIN_MENU_GUTTER * menuZoom;
  const view = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      transparent: true,
      additionalArguments: [`--openprogram-window-id=${ctx.id}`],
    },
  });
  view.webContents.setZoomFactor(menuZoom);
  view.setBackgroundColor("#00000000");
  ctx.mainMenuView = view;
  ctx.mainMenuCascade = requestedCascade;
  ctx.mainMenuPendingUpdate = null;
  ctx.win.contentView.addChildView(view);

  // Anchor: the panel's right edge sits `rightInset` (8px, the tab-strip
  // gutter) from the window's right; its top edge sits on the strip's bottom
  // divider so the menu covers the content below. The view is GUTTER wider
  // and taller than the panel on every side (transparent room for the drop
  // shadow), so the panel is inset by GUTTER inside the view — offset the
  // view accordingly. The renderer measures against its own viewport, so use
  // the viewport width it reports, not getContentBounds (which can disagree).
  const anchor = (opts && opts.anchor) || {};
  const { width: cbW, height: cbH } = ctx.win.getContentBounds();
  const winW = Number(anchor.vw) || cbW;
  const winH = Number(anchor.vh) || cbH;
  const items = Array.isArray(opts && opts.items) ? opts.items : null;
  const nestedItems = hasNestedMenuItems(items);
  const cascadeMenu = Boolean(opts && opts.cascade);
  const overlayWidth = Number.isFinite(Number(opts && opts.width))
    ? Number(opts.width) / menuZoom
    : null;
  let overlayAnchor = null;
  let panelW;
  let panelH;
  let panelX;
  let panelY;
  let cascadeGeometry = null;
  if (items && cascadeMenu) {
    // Bookmark-folder menus need full horizontal room for submenu portals,
    // but must begin below the triggering bar so adjacent folders still
    // receive hover/click events while the menu is open.
    ctx.mainMenuAnchor = null;
    cascadeGeometry = cascadeMenuGeometry(anchor, winW, winH, menuZoom);
    overlayAnchor = cascadeGeometry.anchor;
    panelW = cascadeGeometry.bounds.width;
    panelH = cascadeGeometry.bounds.height;
    panelX = cascadeGeometry.bounds.x;
    panelY = cascadeGeometry.bounds.y;
  } else if (items && nestedItems) {
    // Cascading bookmark folders need the overlay document to cover the
    // content area so submenu portals are not clipped by root-panel bounds.
    ctx.mainMenuAnchor = null;
    overlayAnchor = {
      x: (Number(anchor.x) || 0) / menuZoom,
      y: (Number(anchor.y) || 0) / menuZoom,
    };
    panelW = winW;
    panelH = winH;
    panelX = 0;
    panelY = 0;
  } else if (items) {
    // Generic context menu: panel top-left at anchor {x, y}, clamped to an
    // 8px margin inside the window (same clamp the DOM tab menu used).
    panelW = Number(opts.width) || CONTEXT_MENU_WIDTH * menuZoom;
    panelH = Math.min(
      Number(opts.height)
        || (items.length * CONTEXT_MENU_ROW_HEIGHT + CONTEXT_MENU_CHROME) * menuZoom,
      Math.max(1, winH - 16 * menuZoom),
    );
    // Remember the anchor + viewport so main-menu:resize can re-clamp the
    // view once the overlay reports its measured panel size.
    const align = anchor.align === "end" && Number.isFinite(Number(anchor.right))
      ? "end"
      : "start";
    ctx.mainMenuAnchor = {
      x: Number(anchor.x) || 0,
      right: Number(anchor.right) || 0,
      align,
      y: Number(anchor.y) || 0,
      winW,
      winH,
      zoom: menuZoom,
    };
    const clamped = clampContextMenuPanel(ctx.mainMenuAnchor, panelW, panelH);
    panelX = clamped.x;
    panelY = clamped.y;
  } else {
    ctx.mainMenuAnchor = null;
    // Main menu: panel right edge sits `rightInset` from the window right,
    // top edge on the strip's bottom divider.
    panelW = MAIN_MENU_WIDTH * menuZoom;
    panelH = MAIN_MENU_HEIGHT * menuZoom;
    const rightInset = Number.isFinite(anchor.rightInset)
      ? anchor.rightInset
      : 8 * menuZoom;
    panelX = Math.max(0, winW - rightInset - panelW);
    panelY = Number.isFinite(anchor.top) ? anchor.top : 40 * menuZoom;
  }
  if (cascadeGeometry) {
    view.setBounds(cascadeGeometry.bounds);
  } else if (nestedItems) {
    view.setBounds({ x: 0, y: 0, width: Math.round(winW), height: Math.round(winH) });
  } else {
    const viewW = panelW + gutter * 2;
    const viewH = panelH + gutter * 2;
    // Panel is inset by GUTTER inside the view (transparent room for the
    // drop shadow) — offset the view accordingly.
    view.setBounds({
      x: Math.round(panelX - gutter),
      y: Math.round(panelY - gutter),
      width: Math.round(viewW),
      height: Math.round(viewH),
    });
  }

  const theme = opts && opts.theme;
  view.webContents
    .loadURL(menuOverlayUrl(theme, items, overlayAnchor, overlayWidth, cascadeMenu))
    .then(() => {
      if (ctx.mainMenuView === view && !view.webContents.isDestroyed()) {
        if (ctx.mainMenuPendingUpdate) {
          view.webContents.send("main-menu:update", ctx.mainMenuPendingUpdate);
          ctx.mainMenuPendingUpdate = null;
        }
        view.webContents.focus();
      }
    })
    .catch(() => {});
  // Outside click steals focus from this view → close.
  view.webContents.on("blur", () => {
    if (ctx.mainMenuView === view) closeMainMenu(ctx);
  });
}

// The menu overlay runs in a WebContentsView, whose webContents does NOT
// resolve via BrowserWindow.fromWebContents — find its owning window by
// matching the sender against each context's mainMenuView.
function contextForMenuSender(event) {
  const fromWindow = contextForSender(event);
  if (fromWindow) return fromWindow;
  const sender = event?.sender;
  if (!sender) return null;
  for (const ctx of windows.values()) {
    if (
      ctx.mainMenuView
      && !ctx.win.isDestroyed()
      && ctx.mainMenuView.webContents === sender
    ) {
      return ctx;
    }
  }
  return null;
}

function registerWebTabIpc() {
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
    if (ctx) void navigateView(ctx, id, url).catch(() => {});
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
  ipcMain.handle("webtab:preview", (event, id) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string" ? previewView(ctx, id) : null;
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
  ipcMain.handle("terminal:start", (event, request) => {
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
    const shellPath = process.env.SHELL && path.isAbsolute(process.env.SHELL)
      ? process.env.SHELL
      : "/bin/zsh";
    const args = preset === "claude"
      ? ["-l", "-i", "-c", "exec claude"]
      : ["-l"];
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
      child = require("node-pty").spawn(shellPath, args, {
        name: "xterm-256color",
        cols,
        rows,
        cwd,
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
    return { ok: true, pid: child.pid };
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
  const restored = browserWindowOptionsForPlan(state, { detached });
  const win = new BrowserWindow({
    ...restored,
    show: options.show !== false,
    backgroundColor: currentChrome.bg,
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : undefined,
    // macOS only: center the traffic lights vertically in the 40px tab row.
    trafficLightPosition: { x: 18, y: 13 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--openprogram-window-id=${windowId}`],
    },
  });
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
  // A tear-off window may be revealed mid-drag, long before the
  // commit path would have shown it. Record when the renderer has actually
  // painted so that reveal can wait for it instead of flashing an empty
  // frame. (Windows created shown are unaffected — nothing reads this.)
  ctx.readyToShow = false;
  win.once("ready-to-show", () => { ctx.readyToShow = true; });
  // External links from the app itself (not web tabs) open in the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
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
  win.webContents.on("did-navigate", () => clearOwnedViews(ctx));
  const startUrl = await resolveStartUrl();
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
    ipcMain.on("theme:set-chrome", (_event, payload) => {
      applyWindowChrome(payload || {});
    });
    try {
      require("electron").nativeTheme.on("updated", () => {
        const resolved = resolveStartupChrome();
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
