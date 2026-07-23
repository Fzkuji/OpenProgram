// OpenProgram desktop shell. Plain JS, no bundler.
const { app, BrowserWindow, WebContentsView, Menu, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const { Buffer } = require("buffer");
const {
  loadTransferDecisions,
  saveTransferDecisionsAtomic,
  putTransferDecision,
  ackTransferDecision,
} = require("./tab-transfer-store");
const {
  recordVisit,
  listHistory,
  deleteHistoryEntry,
  clearHistory,
} = require("./browsing-history-store");

// 测试期全部走开发版 18200；正式发布改回 18100
const WEB_PORT = process.env.OPENPROGRAM_WEB_PORT || "18200";
const START_URL =
  process.env.OPENPROGRAM_DESKTOP_URL || `http://127.0.0.1:${WEB_PORT}/chat`;
const WORKER_COMMAND = "openprogram worker start";
const TRANSFER_TIMEOUT_MS = 15_000;
const DESTINATION_UNDO_TIMEOUT_MS = 2_000;
const COMMIT_RECONCILE_INITIAL_MS = 100;
const COMMIT_RECONCILE_MAX_MS = 5_000;
// Bounded retries for a clean (unambiguous) committed-decision write failure
// before abandoning the commit and taking the pre-commit rollback path.
const COMMIT_DECISION_RETRY_LIMIT = 4;

// agent 接管内置浏览器的数据面通道：后端 browser 工具（engine=auto/app）经
// CDP attach 这里的可见 web tab。Electron 默认只绑 127.0.0.1，不对外暴露；
// 9222 留给后端 sidecar Chrome，互不冲突。必须在 app ready 之前设置。
app.commandLine.appendSwitch("remote-debugging-port", "9223");

const ERROR_PAGE =
  "data:text/html;charset=utf-8," +
  encodeURIComponent(
    `<body style="background:#141416;color:#ddd;font-family:-apple-system,sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="max-width:32rem;text-align:center">
        <h2>OpenProgram worker is not running</h2>
        <p>Could not reach <code>${START_URL}</code>.</p>
        <p>Start it manually, then relaunch:</p>
        <pre style="background:#222;padding:0.8em;border-radius:6px">${WORKER_COMMAND}</pre>
      </div>
    </body>`
  );

// ---------------------------------------------------------------- worker boot

function probe(url, timeoutMs) {
  return new Promise((resolve) => {
    const mod = url.startsWith("https:") ? https : http;
    const req = mod.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", () => resolve(false));
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function spawnWorker() {
  // ponytail: PATH first, then the known miniconda location; no config for more.
  // 18200 = 开发实例：openprogram 二进制靠 env 切 dev profile（openprogram-dev
  // 只是 shell alias，spawn 不到），否则拉起的是 stable worker、端口对不上。
  const env =
    WEB_PORT === "18200"
      ? {
          ...process.env,
          OPENPROGRAM_PROFILE: "dev",
          OPENPROGRAM_BACKEND_PORT: "18209",
          OPENPROGRAM_WEB_PORT: "18200",
        }
      : process.env;
  const start = (bin, onFail) => {
    const child = spawn(bin, ["worker", "start"], {
      detached: true,
      stdio: "ignore",
      env,
    });
    child.on("error", onFail || (() => {})); // ENOENT arrives async
    child.unref();
  };
  start("openprogram", () => start("/opt/miniconda3/bin/openprogram"));
}

async function resolveStartUrl() {
  for (let i = 0; i < 3; i++) {
    if (await probe(START_URL, 1000)) return START_URL;
  }
  spawnWorker();
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (await probe(START_URL, 1000)) return START_URL;
    await sleep(1000);
  }
  return ERROR_PAGE;
}

// ------------------------------------------------------------- window state

const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

function loadWindowState() {
  try {
    const s = JSON.parse(fs.readFileSync(stateFile(), "utf8"));
    if (Number.isFinite(s.width) && Number.isFinite(s.height)) {
      // x/y must land on a live display — after a monitor-layout change
      // a stale position would reopen the window fully off-screen.
      if (Number.isFinite(s.x) && Number.isFinite(s.y)) {
        const { screen } = require("electron");
        const onScreen = screen.getAllDisplays().some((d) => {
          const a = d.workArea;
          return (
            s.x < a.x + a.width - 40 && s.x + s.width > a.x + 40 &&
            s.y >= a.y - 20 && s.y < a.y + a.height - 40
          );
        });
        if (!onScreen) {
          delete s.x;
          delete s.y;
        }
      }
      return s;
    }
  } catch (_e) {
    /* first run */
  }
  return { width: 1440, height: 900 };
}

function saveWindowState(win) {
  try {
    fs.writeFileSync(stateFile(), JSON.stringify(win.getBounds()));
  } catch (_e) {
    /* non-fatal */
  }
}

// ----------------------------------------------------------------- web tabs

function makeWindowContext(id, win) {
  return {
    id,
    win,
    views: new Map(),
    visibleViewIds: new Set(),
    pendingTransferToken: null,
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

// History is best-effort: a failed write must never break navigation.
function safeRecordVisit(visit) {
  try {
    recordVisit(browsingHistoryFile(), visit);
  } catch (_error) {
    /* history is not worth crashing a navigation over */
  }
}

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function boundedString(value, field, maxBytes, required = false) {
  if (value === undefined || value === null) {
    if (required) throw new TypeError(`${field} is required`);
    return;
  }
  if (
    typeof value !== "string"
    || (required && !value)
    || Buffer.byteLength(value, "utf8") > maxBytes
  ) {
    throw new TypeError(`${field} is invalid or too large`);
  }
}

function serializedBytes(value, field) {
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch (_error) {
    throw new TypeError(`${field} must be serializable`);
  }
  if (encoded === undefined) throw new TypeError(`${field} must be serializable`);
  return Buffer.byteLength(encoded, "utf8");
}

const TRANSFER_PAYLOAD_MAX_BYTES = 20 * 1024 * 1024;
const FILE_DRAFT_MAX_COUNT = 3;
const FILE_DRAFT_MAX_BYTES = 2 * 1024 * 1024;
const FILE_DRAFTS_MAX_TOTAL_BYTES = 6 * 1024 * 1024;

function optionalBoolean(value, field) {
  if (value === undefined) return;
  if (typeof value !== "boolean") throw new TypeError(`${field} must be boolean`);
  return value;
}

function normalizedComposerSettings(value, field) {
  if (value === undefined) return;
  if (!isPlainObject(value)) throw new TypeError(`${field} must be an object`);
  const normalized = {};
  for (const key of ["thinking", "permission_mode"]) {
    boundedString(value[key], `${field}.${key}`, 16 * 1024);
    if (value[key] !== undefined && value[key] !== null) normalized[key] = value[key];
  }
  for (const key of ["tools", "webSearch", "fast", "unattended"]) {
    const item = optionalBoolean(value[key], `${field}.${key}`);
    if (item !== undefined) normalized[key] = item;
  }
  return normalized;
}

function normalizedDraftChannelChoice(value, field) {
  if (value === undefined) return;
  if (!isPlainObject(value)) throw new TypeError(`${field} must be an object`);
  const normalized = {};
  for (const key of ["channel", "account_id"]) {
    boundedString(value[key], `${field}.${key}`, 16 * 1024);
    if (value[key] !== undefined) normalized[key] = value[key];
  }
  return normalized;
}

function uniqueBoundedIds(value, field, { min = 0, max = 3 } = {}) {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    throw new TypeError(`${field} must contain ${min}-${max} ids`);
  }
  const ids = [];
  const seen = new Set();
  for (const id of value) {
    boundedString(id, field, 4 * 1024, true);
    if (seen.has(id)) throw new TypeError(`${field} contains duplicate ids`);
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

function validateTransferPayload(ctx, value) {
  if (!ctx || !isPlainObject(value)) {
    throw new TypeError("Transfer payload must be an object");
  }
  if (serializedBytes(value, "Transfer payload") > TRANSFER_PAYLOAD_MAX_BYTES) {
    throw new TypeError("Transfer payload is too large");
  }
  if (!Array.isArray(value.tabs) || value.tabs.length < 1 || value.tabs.length > 3) {
    throw new TypeError("Transfer payload requires one to three tabs");
  }

  const validKinds = new Set(["session", "file", "web", "ntp"]);
  const tabs = [];
  const ids = [];
  const seen = new Set();
  for (const tab of value.tabs) {
    if (!isPlainObject(tab) || !validKinds.has(tab.kind)) {
      throw new TypeError("Transfer payload contains an invalid tab");
    }
    boundedString(tab.id, "tab.id", 4 * 1024, true);
    boundedString(tab.title, "tab.title", 4 * 1024);
    for (const field of ["url", "path", "projectId", "sessionId"]) {
      boundedString(tab[field], `tab.${field}`, 16 * 1024);
    }
    if (seen.has(tab.id)) throw new TypeError("Transfer tab ids must be unique");
    seen.add(tab.id);
    ids.push(tab.id);
    const normalized = { id: tab.id, kind: tab.kind };
    if (tab.title !== undefined && tab.title !== null) normalized.title = tab.title;
    for (const field of ["url", "path", "projectId", "sessionId"]) {
      if (tab[field] !== undefined && tab[field] !== null) normalized[field] = tab[field];
    }
    for (const field of ["draft", "dirty"]) {
      const item = optionalBoolean(tab[field], `tab.${field}`);
      if (item !== undefined) normalized[field] = item;
    }
    tabs.push(normalized);
  }

  if (!isPlainObject(value.source)) {
    throw new TypeError("Transfer payload requires source metadata");
  }
  const sourceKind = value.source.kind;
  if (!new Set(["tab", "segment", "group"]).has(sourceKind)) {
    throw new TypeError("Transfer source kind is invalid");
  }
  const source = { windowId: ctx.id, kind: sourceKind };
  if (sourceKind === "tab" && ids.length !== 1) {
    throw new TypeError("A normal tab transfer contains exactly one tab");
  }
  if (sourceKind === "segment") {
    if (ids.length !== 1) {
      throw new TypeError("A segment transfer contains exactly one tab");
    }
    boundedString(value.source.groupId, "source.groupId", 4 * 1024, true);
    if (!Number.isInteger(value.source.memberIndex) || value.source.memberIndex < 0) {
      throw new TypeError("Segment memberIndex is invalid");
    }
  }
  if (sourceKind === "group") {
    boundedString(value.source.groupId, "source.groupId", 4 * 1024, true);
  }
  if (sourceKind === "segment" || sourceKind === "group") {
    const memberIds = uniqueBoundedIds(value.source.memberIds, "source.memberIds", {
      min: 2,
      max: 3,
    });
    const visibleIds = uniqueBoundedIds(value.source.visibleIds, "source.visibleIds", {
      min: 1,
      max: 2,
    });
    boundedString(value.source.focusedId, "source.focusedId", 4 * 1024, true);
    if (visibleIds.some((id) => !memberIds.includes(id))) {
      throw new TypeError("Visible group ids must be members");
    }
    if (!visibleIds.includes(value.source.focusedId)) {
      throw new TypeError("Focused group id must be visible");
    }
    if (sourceKind === "group") {
      if (
        memberIds.length !== ids.length
        || memberIds.some((id, index) => id !== ids[index])
      ) {
        throw new TypeError("Group metadata must match transferred tabs");
      }
    } else if (
      value.source.memberIndex >= memberIds.length
      || memberIds[value.source.memberIndex] !== ids[0]
    ) {
      throw new TypeError("Segment metadata must identify the transferred tab");
    }
    source.groupId = value.source.groupId;
    source.memberIds = memberIds;
    source.visibleIds = visibleIds;
    source.focusedId = value.source.focusedId;
    if (sourceKind === "segment") source.memberIndex = value.source.memberIndex;
  }

  const rawFileDrafts = value.fileDrafts ?? [];
  if (!Array.isArray(rawFileDrafts) || rawFileDrafts.length > FILE_DRAFT_MAX_COUNT) {
    throw new TypeError("fileDrafts must contain at most three entries");
  }
  const fileDrafts = [];
  const fileDraftKeys = new Set();
  let fileDraftBytes = 0;
  for (const draft of rawFileDrafts) {
    if (!isPlainObject(draft)) throw new TypeError("Invalid file draft");
    boundedString(draft.key, "fileDraft.key", 16 * 1024, true);
    if (fileDraftKeys.has(draft.key)) throw new TypeError("Duplicate file draft key");
    fileDraftKeys.add(draft.key);
    let normalizedValue;
    if (typeof draft.value === "string") {
      normalizedValue = draft.value;
    } else if (isPlainObject(draft.value)) {
      if (
        typeof draft.value.draft !== "string"
        || typeof draft.value.baselineContent !== "string"
        || !Number.isFinite(draft.value.baselineMtime)
      ) {
        throw new TypeError("Invalid fileDraft.value");
      }
      normalizedValue = {
        draft: draft.value.draft,
        baselineContent: draft.value.baselineContent,
        baselineMtime: draft.value.baselineMtime,
      };
    } else {
      throw new TypeError("Invalid fileDraft.value");
    }
    const draftBytes = typeof normalizedValue === "string"
      ? Buffer.byteLength(normalizedValue, "utf8")
      : serializedBytes(normalizedValue, "fileDraft.value");
    if (draftBytes > FILE_DRAFT_MAX_BYTES) {
      throw new TypeError("fileDraft.value is too large");
    }
    fileDraftBytes += serializedBytes(normalizedValue, "fileDraft.value");
    if (fileDraftBytes > FILE_DRAFTS_MAX_TOTAL_BYTES) {
      throw new TypeError("fileDrafts are too large");
    }
    fileDrafts.push({ key: draft.key, value: normalizedValue });
  }

  const rawChats = value.chats ?? [];
  if (!Array.isArray(rawChats) || rawChats.length > 3) {
    throw new TypeError("chats must be an array with at most three entries");
  }
  const chats = [];
  for (const chat of rawChats) {
    if (!isPlainObject(chat)) throw new TypeError("Invalid chat transfer state");
    boundedString(chat.chatKey, "chat.chatKey", 16 * 1024, true);
    boundedString(chat.pendingProjectId, "chat.pendingProjectId", 16 * 1024);
    for (const field of ["composerDraft", "activeComposerInput"]) {
      boundedString(chat[field], `chat.${field}`, 2 * 1024 * 1024);
    }
    const normalized = { chatKey: chat.chatKey };
    for (const field of ["composerDraft", "activeComposerInput", "pendingProjectId"]) {
      if (chat[field] !== undefined && chat[field] !== null) normalized[field] = chat[field];
    }
    for (const field of ["composerSettings", "activeComposerSettings"]) {
      const item = normalizedComposerSettings(chat[field], `chat.${field}`);
      if (item !== undefined) normalized[field] = item;
    }
    const choice = normalizedDraftChannelChoice(
      chat.draftChannelChoice,
      "chat.draftChannelChoice",
    );
    if (choice !== undefined) normalized.draftChannelChoice = choice;
    const wasActive = optionalBoolean(chat.wasActive, "chat.wasActive");
    if (wasActive !== undefined) normalized.wasActive = wasActive;
    chats.push(normalized);
  }

  const records = [];
  for (const tab of tabs) {
    if (tab.kind !== "web") continue;
    const record = ctx.views.get(tab.id);
    if (!record) continue;
    if (record.ownerId !== ctx.id) {
      throw new TypeError("Native web view is owned by another window");
    }
    records.push(record);
  }

  const payload = { tabs, source, fileDrafts, chats };
  return { payload, records };
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
    record = { id, view, ownerId: ctx.id, navigation: null };
    ctx.views.set(id, record);
    ctx.win.contentView.addChildView(view);
    view.setVisible(false);
    const wc = view.webContents;
    // Popups navigate the same view instead of opening a window.
    // Same http/https gate as every other egress point.
    wc.setWindowOpenHandler(({ url: popupUrl }) => {
      if (isWebUrl(popupUrl)) {
        const owner = ownerOf(record);
        if (owner) void navigateView(owner, id, popupUrl).catch(() => {});
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
    if (url && isTabUrl(url)) void loadView(record, url).catch(() => {});
  }
  return record;
}

async function navigateView(ctx, id, url) {
  if (!url || !isTabUrl(url)) return null;
  const record = recordFor(ctx, id) || ensureView(ctx, id, "");
  return record ? loadView(record, url) : null;
}

function normalizedBounds(bounds) {
  return {
    x: Math.round(Number(bounds?.x)) || 0,
    y: Math.round(Number(bounds?.y)) || 0,
    width: Math.max(0, Math.round(Number(bounds?.width)) || 0),
    height: Math.max(0, Math.round(Number(bounds?.height)) || 0),
  };
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

async function activateView(ctx, id, url) {
  let record;
  if (url) {
    if (!isTabUrl(url)) return null;
    record = recordFor(ctx, id) || ensureView(ctx, id, "");
    if (!record || !showView(ctx, id)) return null;
    record = await navigateView(ctx, id, url);
    if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null;
  } else {
    record = recordFor(ctx, id);
    if (!record || !showView(ctx, id)) return null;
  }
  return record.view.webContents.getOrCreateDevToolsTargetId();
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
}

// -------------------------------------------------------------- main menu
//
// The ⋮ main menu is its own top-layer WebContentsView loading the app's
// /menu-overlay/main-menu route, added AFTER the web-tab views so it
// covers them (a DOM Radix menu can't, since native views paint above the
// DOM). Singleton per window; closes on outside click (its own blur),
// Esc, window blur/resize, and after a choice.
const MAIN_MENU_WIDTH = 224;
const MAIN_MENU_HEIGHT = 220;
// Extra room around the panel so its drop shadow isn't clipped by the
// view's own edge (the panel itself is smaller than the view).
const MAIN_MENU_GUTTER = 24;
// Generic context-menu overlay (opts.items given): panel width default
// matches the DOM .tabMenu (200px); height derives from the row count —
// itemCls rows are 24px, MENU_PANEL adds 6px padding + 1px border each side.
const CONTEXT_MENU_WIDTH = 200;
const CONTEXT_MENU_ROW_HEIGHT = 24;
const CONTEXT_MENU_CHROME = 16;

function menuOverlayUrl(theme, items) {
  let origin = "http://127.0.0.1:" + WEB_PORT;
  try {
    origin = new URL(START_URL).origin;
  } catch (_e) {
    /* keep fallback */
  }
  const q = new URLSearchParams();
  if (theme === "dark" || theme === "light") q.set("theme", theme);
  if (items) q.set("items", JSON.stringify(items));
  return (
    origin
    + (items ? "/menu-overlay/context-menu?" : "/menu-overlay/main-menu?")
    + q.toString()
  );
}

function closeMainMenu(ctx) {
  if (!ctx || !ctx.mainMenuView) return;
  const view = ctx.mainMenuView;
  ctx.mainMenuView = null;
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

function openMainMenu(ctx, opts) {
  if (!ctx || ctx.win.isDestroyed()) return;
  closeMainMenu(ctx);
  const view = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      transparent: true,
      additionalArguments: [`--openprogram-window-id=${ctx.id}`],
    },
  });
  view.setBackgroundColor("#00000000");
  ctx.mainMenuView = view;
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
  let panelW;
  let panelH;
  let panelX;
  let panelY;
  if (items) {
    // Generic context menu: panel top-left at anchor {x, y}, clamped to an
    // 8px margin inside the window (same clamp the DOM tab menu used).
    panelW = Number(opts.width) || CONTEXT_MENU_WIDTH;
    panelH = Number(opts.height)
      || items.length * CONTEXT_MENU_ROW_HEIGHT + CONTEXT_MENU_CHROME;
    panelX = Math.min(
      Math.max(8, Number(anchor.x) || 0),
      Math.max(8, winW - panelW - 8),
    );
    panelY = Math.min(
      Math.max(8, Number(anchor.y) || 0),
      Math.max(8, winH - panelH - 8),
    );
  } else {
    // Main menu: panel right edge sits `rightInset` from the window right,
    // top edge on the strip's bottom divider.
    panelW = MAIN_MENU_WIDTH;
    panelH = MAIN_MENU_HEIGHT;
    const rightInset = Number.isFinite(anchor.rightInset)
      ? anchor.rightInset
      : 8;
    panelX = Math.max(0, winW - rightInset - panelW);
    panelY = Number.isFinite(anchor.top) ? anchor.top : 40;
  }
  const viewW = panelW + MAIN_MENU_GUTTER * 2;
  const viewH = panelH + MAIN_MENU_GUTTER * 2;
  // Panel is inset by GUTTER inside the view (transparent room for the
  // drop shadow) — offset the view accordingly.
  view.setBounds({
    x: Math.round(panelX - MAIN_MENU_GUTTER),
    y: Math.round(panelY - MAIN_MENU_GUTTER),
    width: viewW,
    height: viewH,
  });

  const theme = opts && opts.theme;
  view.webContents
    .loadURL(menuOverlayUrl(theme, items))
    .then(() => {
      if (ctx.mainMenuView === view && !view.webContents.isDestroyed()) {
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
    if (ctx) openMainMenu(ctx, opts || {});
  });
  ipcMain.on("main-menu:close", (event) => {
    const ctx = contextForMenuSender(event);
    if (ctx) closeMainMenu(ctx);
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
  ipcMain.handle("webtab:activate", (event, id, url) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string"
      ? activateView(ctx, id, typeof url === "string" ? url : "")
      : null;
  });
  ipcMain.on("webtab:sync-visible", (event, items) => {
    const ctx = contextForSender(event);
    if (ctx) syncVisibleViews(ctx, items);
  });
  ipcMain.on("webtab:set-bounds", (event, id, bounds) => {
    const ctx = contextForSender(event);
    if (ctx && bounds) {
      withView(ctx, id, (record) => {
        record.view.setBounds(normalizedBounds(bounds));
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
  ipcMain.on("webtab:go-back", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.navigationHistory.goBack());
  });
  ipcMain.on("webtab:go-forward", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.navigationHistory.goForward());
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

async function createWindow(options = {}) {
  const state = loadWindowState();
  const windowId = options.windowId || "main";
  // A torn-off window is positioned at the drop point (centerHiddenWindowOnCursor
  // in detachUnlatched), so it must NOT inherit the parent's saved (often
  // full-screen) bounds — a 1440×851 window anchored at the cursor spills
  // off-screen and reads as "nothing appeared". Give detached windows a
  // modest, movable size.
  const detached = options.detached === true;
  const width = detached ? Math.min(1100, state.width) : state.width;
  const height = detached ? Math.min(720, state.height) : state.height;
  const win = new BrowserWindow({
    x: state.x,
    y: state.y,
    width,
    height,
    show: options.show !== false,
    backgroundColor: "#141416",
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
    if (tabTransfers.windowClosing(ctx, event)) return;
    saveWindowState(win);
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
      if (dest.hostname === "127.0.0.1" || dest.hostname === "localhost") return;
      e.preventDefault();
      if (dest.protocol === "http:" || dest.protocol === "https:")
        shell.openExternal(url);
    } catch (_e) {
      e.preventDefault();
    }
  });
  // Renderer reload (Cmd+R) resets the renderer's view bookkeeping —
  // orphaned WebContentsViews would leak until quit. Start clean.
  win.webContents.on("did-navigate", () => clearOwnedViews(ctx));
  win.loadURL(await resolveStartUrl());
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
  body { font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: #333; background: #fff; }
  h1 { font-size: 15px; font-weight: 600; word-break: break-all; }
  ul { list-style: none; padding: 0; max-width: 720px; }
  li { display: flex; justify-content: space-between; gap: 16px; line-height: 1.9; border-bottom: 1px solid #f0f0f0; }
  a { color: #1a5fb4; text-decoration: none; word-break: break-all; }
  a:hover { text-decoration: underline; }
  .size { color: #999; font-size: 12px; white-space: nowrap; }
  @media (prefers-color-scheme: dark) {
    body { color: #ddd; background: #1e1e1e; }
    li { border-color: #333; }
    a { color: #6ea8e8; }
    .size { color: #777; }
  }
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

app.whenReady().then(() => {
  registerFileDirectoryListing();
  registerWebTabIpc();
  registerTabTransferIpc();
  buildMenu();
  void createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
