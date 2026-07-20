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

const transferDecisionFile = () =>
  path.join(app.getPath("userData"), "tab-transfers.json");

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

  function closeDetached(transaction) {
    if (!transaction.detachedWindowId) return;
    const destination = windowRegistry.get(transaction.detachedWindowId);
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
    if (closeHidden) closeDetached(transaction);
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
    if (detached) detached.win.show();
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
    const windowId = `window-${makeToken()}`;
    const destination = await createDetachedWindow({ windowId, show: false });
    if (activeTransfers.get(token) !== transaction || transaction.status !== "prepared") {
      allowedWindowCloses.add(destination.id);
      try {
        destination.win.close();
      } finally {
        allowedWindowCloses.delete(destination.id);
      }
      return null;
    }
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
    if (url && isWebUrl(url)) void loadView(record, url).catch(() => {});
  }
  return record;
}

async function navigateView(ctx, id, url) {
  if (!url || !isWebUrl(url)) return null;
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
    if (!isWebUrl(url)) return null;
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

function registerWebTabIpc() {
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
  ipcMain.on("desktop:open-external", (_e, url) => {
    try {
      const u = new URL(url);
      if (u.protocol === "http:" || u.protocol === "https:") shell.openExternal(url);
    } catch (_err) {
      /* invalid url, ignore */
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
    const ctx = contextForSender(event);
    return !!ctx && tabTransfers.cancel(ctx, token);
  });
  ipcMain.handle("tab-transfer:detach", (event, token) => {
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
    for (const candidate of windows.values()) {
      if (candidate === ctx) continue;
      if (candidate.win.isDestroyed() || !candidate.win.isVisible()) continue;
      const bounds = candidate.win.getBounds();
      if (
        point.x >= bounds.x && point.x < bounds.x + bounds.width
        && point.y >= bounds.y && point.y < bounds.y + bounds.height
      ) {
        // ponytail: first hit, no z-order tiebreak — overlapping windows
        // resolve by map order; add z-order ranking if it ever matters.
        return candidate.id;
      }
    }
    return null;
  });
  // Hand a prepared token to another live window so its renderer stages
  // the incoming transfer (the pointer path has no DOM drop event there).
  ipcMain.handle("tab-transfer:deliver", (event, token, windowId) => {
    const ctx = contextForSender(event);
    const target = windows.get(windowId);
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

async function createWindow(options = {}) {
  const state = loadWindowState();
  const windowId = options.windowId || "main";
  const win = new BrowserWindow({
    x: state.x,
    y: state.y,
    width: state.width,
    height: state.height,
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
  win.on("close", (event) => {
    if (tabTransfers.windowClosing(ctx, event)) return;
    saveWindowState(win);
  });
  win.on("closed", () => cleanupWindowContext(ctx));
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

app.whenReady().then(() => {
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
