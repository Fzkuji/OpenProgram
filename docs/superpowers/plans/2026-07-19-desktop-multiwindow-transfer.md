# Desktop Multi-window Tab Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a desktop center tab move into a new or existing OpenProgram window, merge into a destination strip or compound group, and move back while preserving the same live `WebContentsView` and all renderer-owned tab state.

**Architecture:** Each Electron `BrowserWindow` receives a stable lifetime `windowId` and a `WindowContext` containing only the native web views owned by that renderer. Cross-window movement is a main-process two-phase transaction: the source creates a single-use token, the destination validates and accepts it, main reparents the same `WebContentsView`, the destination inserts and acknowledges, and only then the source removes its tab. Any rejection, timeout, renderer loss, or reparent failure reverses the native move and leaves the source store unchanged.

**Tech Stack:** Electron 37 `BrowserWindow` / `WebContentsView` / IPC, plain JavaScript preload and main process, React 18, Zustand 5, TypeScript, native HTML Drag and Drop, Node `assert`/`vm` check scripts, Chrome DevTools Protocol for live acceptance.

## Global Constraints

- Test the desktop implementation against the development UI on port `18200`; leave stable port `18100` unchanged until final promotion.
- Do not add a drag-and-drop dependency; use native `DragEvent` and `DataTransfer`.
- Do not use `closeTab()` or `finishClose()` for transfer removal. Transfer must not create session-close tombstones, delete drafts or attachments, destroy a moved web view, or insert an NTP fallback.
- A transferred web tab must retain the exact `WebContentsView` object, CDP target id, `performance.timeOrigin`, navigation history, form state, scroll position, login/session state, and SPA memory.
- Every native-view IPC handler must derive the source `WindowContext` from `event.sender` and reject a tab id not owned by that context.
- Menu shortcuts and backend `webtab.command` requests are handled by only the focused OpenProgram window, with the most recently focused live window as the no-focus fallback.
- Transfer tokens are opaque, single-use, explicitly cancellable, and expire after `15_000` ms.
- A destination must acknowledge insertion before source removal. Failure before final source acknowledgement rolls back the destination insertion and native-view ownership.
- A detached destination window remains hidden until its transfer commits. Rollback closes that hidden window.
- Destination duplicate ids and fourth-member group drops are rejected before native reparenting.
- The existing `persist:webtabs` Electron partition and profile-wide `openprogram.bookmarks` key remain unchanged.

## File Structure

- Modify `desktop/main.js`: own all `WindowContext`s, route sender-owned IPC and focused menu commands, manage transfer tokens, reparent native views, create hidden detach windows, and roll back failures.
- Modify `desktop/preload.js`: expose `windowId`, focused-command claiming, and the transfer transaction bridge.
- Modify `desktop/scripts/check-webtab-navigation.js`: behavior-check sender ownership, focused routing, same-object reparenting, single-use/timeout transactions, rollback, detach, and late source IPC.
- Modify `web/lib/desktop-bridge.ts`: type and install the window/transfer bridge, coordinate destination insert/source remove/rollback, and relinquish renderer-local native-view bookkeeping without destroying the moved view.
- Modify `web/lib/state/center-tabs-store.ts`: key persisted tabs/split state by `windowId`, migrate the legacy primary-window payload once, and add transfer-safe insertion/removal plus group placement.
- Modify `web/lib/session-store/index.ts`: import an unsent composer draft into an already-running destination renderer without deleting it from profile storage.
- Modify `web/lib/state/files-shared.ts`: export small snapshot/restore helpers for an in-memory dirty file draft.
- Modify `web/components/center-tabs/center-tab-strip.tsx`: create native drag tokens, resolve strip/group drop placement, detach on an uncancelled outside drop, and expose the keyboard move command.
- Modify `web/components/center-tabs/center-tabs.module.css`: render drag source opacity and transient insertion/merge target states only.
- Modify `web/scripts/check-web-split.mjs`: behavior-check per-window persistence, transfer-safe store actions, group merge rejection, and draft payload restoration.
- Modify `web/scripts/check-center-tabs.mjs`: statically verify native drag/drop and keyboard accessibility wiring.

---

### Task 1: Replace Single-window Globals with Sender-owned `WindowContext`s

**Files:**
- Modify: `desktop/scripts/check-webtab-navigation.js:6-247`
- Modify: `desktop/main.js:2, 128-425`

**Interfaces:**
- Consumes: Electron `BrowserWindow.fromWebContents(event.sender)` and `BrowserWindow.getFocusedWindow()`.
- Produces: `windows: Map<string, WindowContext>`, `contextsByBrowserWindowId: Map<number, WindowContext>`, `contextForSender(event)`, `focusedContext()`, `createWindow(options): Promise<WindowContext>`, and sender-scoped web-tab functions.

- [ ] **Step 1: Write the failing sender-ownership and focused-menu checks**

Append source and VM checks to `desktop/scripts/check-webtab-navigation.js`:

```js
assert.doesNotMatch(source, /let mainWindow = null/);
assert.match(source, /const windows = new Map\(\)/);
assert.match(source, /const contextsByBrowserWindowId = new Map\(\)/);
assert.match(source, /function contextForSender\(event\)/);
assert.match(source, /BrowserWindow\.fromWebContents\(event\.sender\)/);
assert.match(source, /function focusedContext\(\)/);
assert.match(source, /BrowserWindow\.getFocusedWindow\(\)/);

for (const channel of [
  "ensure", "navigate", "activate", "set-bounds", "show", "hide",
  "destroy", "reload", "go-back", "go-forward",
]) {
  const marker = channel === "activate"
    ? `ipcMain.handle("webtab:${channel}"`
    : `ipcMain.on("webtab:${channel}"`;
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `${channel} handler missing`);
  const handler = source.slice(start, source.indexOf("\n  );", start) + 5);
  assert.match(handler, /contextForSender/,
    `${channel} must resolve ownership from event.sender`);
}

const menuStart = source.indexOf("function buildMenu");
const menuEnd = source.indexOf("// --------------------------------------------------------------------- boot");
const menuSource = source.slice(menuStart, menuEnd);
assert.match(menuSource, /focusedContext\(\)/);
assert.doesNotMatch(menuSource, /mainWindow\.webContents\.send/);
```

- [ ] **Step 2: Run the desktop check and confirm the single-window assumptions fail it**

Run: `npm --prefix desktop run check:webtabs`

Expected: FAIL at `assert.doesNotMatch(source, /let mainWindow = null/)`.

- [ ] **Step 3: Add window contexts and move native-view navigation state into transferable records**

Replace the globals at `desktop/main.js:130-133` with:

```js
const { randomUUID } = require("crypto");

const windows = new Map();
const contextsByBrowserWindowId = new Map();
let lastFocusedWindowId = null;

function makeWindowContext(id, win) {
  return { id, win, views: new Map(), visibleViewId: null };
}

function contextForSender(event) {
  const win = BrowserWindow.fromWebContents(event.sender);
  return win ? contextsByBrowserWindowId.get(win.id) || null : null;
}

function focusedContext() {
  const focused = BrowserWindow.getFocusedWindow();
  const direct = focused ? contextsByBrowserWindowId.get(focused.id) : null;
  if (direct) return direct;
  const recent = lastFocusedWindowId ? windows.get(lastFocusedWindowId) : null;
  return recent && !recent.win.isDestroyed() ? recent : null;
}
```

Use a mutable record for each native view so a pending navigation and state-event routing move with the view:

```js
function makeViewRecord(ctx, id) {
  const view = new WebContentsView({
    webPreferences: { partition: "persist:webtabs" },
  });
  return { id, view, ownerId: ctx.id, navigation: null };
}

function ownerOf(record) {
  const ctx = windows.get(record.ownerId);
  return ctx && ctx.views.get(record.id) === record ? ctx : null;
}

function sendState(record, extra) {
  const ctx = ownerOf(record);
  if (!ctx || ctx.win.isDestroyed()) return;
  const wc = record.view.webContents;
  const url = wc.getURL();
  const title = wc.getTitle();
  ctx.win.webContents.send("webtab:state", {
    id: record.id,
    ...(url ? { url } : {}),
    ...(title ? { title } : {}),
    loading: wc.isLoading(),
    canGoBack: wc.navigationHistory.canGoBack(),
    canGoForward: wc.navigationHistory.canGoForward(),
    ...extra,
  });
}
```

Change `loadView`, `ensureView`, `navigateView`, `showView`, `hideView`, `activateView`, `withView`, and `runNativeNavigation` to receive `ctx`; store the pending promise as `record.navigation`:

```js
function loadView(record, url) {
  const pending = record.navigation;
  if (pending && pending.url === url) return pending.promise;
  const wc = record.view.webContents;
  if (!pending && wc.getURL() === url && !wc.isLoading()) {
    return Promise.resolve(record);
  }
  const promise = wc.loadURL(url).then(() => record).finally(() => {
    if (record.navigation?.promise === promise) record.navigation = null;
  });
  record.navigation = { url, promise };
  return promise;
}

function withOwnedView(ctx, id, fn) {
  const record = ctx?.views.get(id);
  if (record) return fn(record);
  return null;
}
```

Refactor every web-tab IPC handler to resolve its context from the sender and then call the scoped function. The destroy handler must be:

```js
ipcMain.on("webtab:destroy", (event, id) => {
  const ctx = contextForSender(event);
  withOwnedView(ctx, id, (record) => {
    if (ctx.visibleViewId === id) ctx.visibleViewId = null;
    ctx.win.contentView.removeChildView(record.view);
    record.view.webContents.close();
    record.navigation = null;
    ctx.views.delete(id);
  });
});
```

Change menu dispatch to:

```js
const send = (channel) => () => {
  const ctx = focusedContext();
  if (ctx) ctx.win.webContents.send(channel);
};
```

Change `createWindow` to construct and register its local context before loading the renderer:

```js
async function createWindow({
  windowId = "main",
  state = loadWindowState(),
  show = true,
  transferToken = null,
} = {}) {
  const win = new BrowserWindow({
    x: state.x,
    y: state.y,
    width: state.width,
    height: state.height,
    show,
    backgroundColor: "#141416",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : undefined,
    trafficLightPosition: { x: 18, y: 13 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--openprogram-window-id=${windowId}`],
    },
  });
  const ctx = makeWindowContext(windowId, win);
  ctx.transferToken = transferToken;
  windows.set(windowId, ctx);
  contextsByBrowserWindowId.set(win.id, ctx);
  win.on("focus", () => { lastFocusedWindowId = windowId; });
  win.on("close", () => {
    if (windowId === "main") saveWindowState(win);
  });
  win.on("closed", () => disposeWindowContext(ctx));
  installWindowNavigationGuards(ctx);
  await win.loadURL(await resolveStartUrl());
  return ctx;
}
```

`disposeWindowContext(ctx)` and renderer-reload cleanup must iterate only `ctx.views`, close records still owned by `ctx`, and remove both registry entries.

- [ ] **Step 4: Run focused desktop checks**

Run: `npm --prefix desktop run check:webtabs`

Expected: `webtab navigation checks passed`.

- [ ] **Step 5: Commit the window-context refactor**

```bash
git add desktop/main.js desktop/scripts/check-webtab-navigation.js
git commit -m "refactor(desktop): scope native views by window"
```

---

### Task 2: Add Single-use Transfer Transactions and Same-object Reparent Rollback

**Files:**
- Modify: `desktop/scripts/check-webtab-navigation.js`
- Modify: `desktop/main.js`

**Interfaces:**
- Consumes: `WindowContext`, `ViewRecord`, `contextForSender(event)`, and `createWindow(options)` from Task 1.
- Produces: `beginTransfer(ctx, payload)`, `inspectTransfer(token)`, `acceptTransfer(targetCtx, token)`, `completeTransfer(targetCtx, token, ok)`, `cancelTransfer(sourceCtx, token)`, `ackSourceRemoval(sourceCtx, token, ok, empty)`, `rollbackTransfer(tx, reason)`, and `detachTransfer(sourceCtx, token)`.

- [ ] **Step 1: Add failing behavior checks for identity, ownership, token use, and rollback**

Add a deterministic fake clock and parent views to `desktop/scripts/check-webtab-navigation.js`:

```js
function fakeParent(name) {
  const children = [];
  return {
    name,
    children,
    addChildView(view) {
      if (this.failAdd) throw new Error("add failed");
      children.push(view);
    },
    removeChildView(view) {
      const i = children.indexOf(view);
      if (i >= 0) children.splice(i, 1);
    },
  };
}

async function checkTransferIdentityAndRollback() {
  const sourceParent = fakeParent("source");
  const targetParent = fakeParent("target");
  const view = {
    visible: true,
    bounds: { x: 2, y: 40, width: 700, height: 500 },
    setVisible(value) { this.visible = value; },
    getVisible() { return this.visible; },
    setBounds(value) { this.bounds = value; },
    getBounds() { return this.bounds; },
    webContents: { close() { throw new Error("transfer closed webContents"); } },
  };
  sourceParent.addChildView(view);
  const record = { id: "w:https://example.com/", view, ownerId: "source", navigation: null };
  const source = { id: "source", win: { contentView: sourceParent }, views: new Map([[record.id, record]]), visibleViewId: record.id };
  const target = { id: "target", win: { contentView: targetParent }, views: new Map(), visibleViewId: null };

  const snapshot = context.reparentRecord(source, target, record);
  assert.strictEqual(target.views.get(record.id).view, view);
  assert.equal(source.views.has(record.id), false);
  assert.equal(record.ownerId, "target");
  assert.equal(view.visible, false);

  context.restoreRecord(source, target, record, snapshot);
  assert.strictEqual(source.views.get(record.id).view, view);
  assert.equal(target.views.has(record.id), false);
  assert.equal(record.ownerId, "source");
  assert.deepEqual(view.bounds, snapshot.bounds);
  assert.equal(view.visible, true);

  targetParent.failAdd = true;
  assert.throws(() => context.reparentRecord(source, target, record), /add failed/);
  assert.strictEqual(source.views.get(record.id), record);
  assert.equal(record.ownerId, "source");
}
```

Extract the two reparent helpers into the existing VM harness before calling the check:

```js
const reparentStart = source.indexOf("function reparentRecord");
const reparentEnd = source.indexOf("\n\nconst TRANSFER_TTL_MS", reparentStart);
assert.ok(reparentStart >= 0 && reparentEnd > reparentStart,
  "reparent helpers not found");
vm.runInContext(
  `${source.slice(reparentStart, reparentEnd)}
   globalThis.reparentRecord = reparentRecord;
   globalThis.restoreRecord = restoreRecord;`,
  context,
);
```

Add transaction assertions:

```js
assert.match(source, /const TRANSFER_TTL_MS = 15_000/);
assert.match(source, /const transfers = new Map\(\)/);
assert.match(source, /function rollbackTransfer\(tx, reason\)/);
assert.match(source, /tx\.status !== "prepared"/);
assert.match(source, /tx\.status = "accepted"/);
assert.match(source, /tx\.status = "awaiting-source"/);
assert.match(source, /sourceCtx\.win\.webContents\.send\("tab-transfer:source-commit"/);
assert.match(source, /if \(empty\) sourceCtx\.win\.close\(\)/);
```

Add a behavior harness for single use, explicit cancellation, and timeout:

```js
function fakeWindow(parent) {
  return {
    contentView: parent,
    isDestroyed: () => false,
    webContents: { send() {} },
    close() {},
  };
}

const timers = new Map();
let nextTimer = 1;
let nextToken = 1;
const transferContext = vm.createContext({
  Map,
  Error,
  randomUUID: () => `token-${nextToken++}`,
  setTimeout(callback, delay) {
    assert.equal(delay, 15_000);
    const id = nextTimer++;
    timers.set(id, callback);
    return id;
  },
  clearTimeout(id) { timers.delete(id); },
});
const txStart = source.indexOf("const TRANSFER_TTL_MS");
const txEnd = source.indexOf("\n\nfunction registerWebTabIpc", txStart);
assert.ok(txStart >= 0 && txEnd > txStart, "transfer state machine not found");
vm.runInContext(
  `const windows = new Map();
   ${source.slice(reparentStart, reparentEnd)}
   ${source.slice(txStart, txEnd)}
   globalThis.windowsForCheck = windows;
   globalThis.beginTransfer = beginTransfer;
   globalThis.inspectTransfer = inspectTransfer;
   globalThis.acceptTransfer = acceptTransfer;
   globalThis.cancelTransfer = cancelTransfer;`,
  transferContext,
);

const sourceCtx = {
  id: "source", win: fakeWindow(fakeParent("source")),
  views: new Map(), visibleViewId: null,
};
const targetCtx = {
  id: "target", win: fakeWindow(fakeParent("target")),
  views: new Map(), visibleViewId: null,
};
transferContext.windowsForCheck.set(sourceCtx.id, sourceCtx);
transferContext.windowsForCheck.set(targetCtx.id, targetCtx);
const payload = { tab: { id: "s:one", kind: "session", title: "One" } };

const once = transferContext.beginTransfer(sourceCtx, payload);
assert.deepEqual(transferContext.inspectTransfer(once), payload);
assert.deepEqual(transferContext.acceptTransfer(targetCtx, once), payload);
assert.throws(
  () => transferContext.acceptTransfer(targetCtx, once),
  /stale transfer token/,
  "an accepted token cannot be accepted twice",
);

const cancelled = transferContext.beginTransfer(sourceCtx, payload);
assert.equal(transferContext.cancelTransfer(sourceCtx, cancelled), true);
assert.equal(transferContext.inspectTransfer(cancelled), null);
assert.equal(transferContext.cancelTransfer(sourceCtx, cancelled), false);

const expiring = transferContext.beginTransfer(sourceCtx, payload);
const expiry = Array.from(timers.values()).at(-1);
expiry();
assert.equal(transferContext.inspectTransfer(expiring), null);
```

- [ ] **Step 2: Run the check and verify transfer functions are absent**

Run: `npm --prefix desktop run check:webtabs`

Expected: FAIL with `TypeError: context.reparentRecord is not a function` or the first missing transfer-source assertion.

- [ ] **Step 3: Implement reversible same-object reparenting**

Add to `desktop/main.js`:

```js
function reparentRecord(source, target, record) {
  if (source.views.get(record.id) !== record) throw new Error("source does not own view");
  if (target.views.has(record.id)) throw new Error("destination already owns tab id");
  const snapshot = {
    bounds: record.view.getBounds(),
    visible: record.view.getVisible(),
    visibleViewId: source.visibleViewId,
  };
  record.view.setVisible(false);
  source.win.contentView.removeChildView(record.view);
  source.views.delete(record.id);
  if (source.visibleViewId === record.id) source.visibleViewId = null;
  try {
    target.win.contentView.addChildView(record.view);
    target.views.set(record.id, record);
    record.ownerId = target.id;
    return snapshot;
  } catch (error) {
    source.win.contentView.addChildView(record.view);
    source.views.set(record.id, record);
    record.ownerId = source.id;
    source.visibleViewId = snapshot.visibleViewId;
    record.view.setBounds(snapshot.bounds);
    record.view.setVisible(snapshot.visible);
    throw error;
  }
}

function restoreRecord(source, target, record, snapshot) {
  if (target.views.get(record.id) === record) {
    target.win.contentView.removeChildView(record.view);
    target.views.delete(record.id);
    if (target.visibleViewId === record.id) target.visibleViewId = null;
  }
  source.win.contentView.addChildView(record.view);
  source.views.set(record.id, record);
  record.ownerId = source.id;
  source.visibleViewId = snapshot.visibleViewId;
  record.view.setBounds(snapshot.bounds);
  record.view.setVisible(snapshot.visible);
}
```

- [ ] **Step 4: Implement the token state machine and rollback**

Add one registry and fixed expiry:

```js
const TRANSFER_TTL_MS = 15_000;
const transfers = new Map();

function transferToken() {
  return randomUUID();
}

function scheduleTransferExpiry(tx) {
  tx.timer = setTimeout(() => rollbackTransfer(tx, "expired"), TRANSFER_TTL_MS);
}

function finishToken(tx) {
  clearTimeout(tx.timer);
  transfers.delete(tx.token);
}

function beginTransfer(sourceCtx, payload) {
  if (!payload?.tab?.id) throw new Error("transfer payload requires a tab id");
  if (payload.tab.kind === "web" && !sourceCtx.views.has(payload.tab.id)) {
    throw new Error("source does not own web tab");
  }
  const token = transferToken();
  const tx = {
    token,
    sourceId: sourceCtx.id,
    destinationId: null,
    payload,
    record: sourceCtx.views.get(payload.tab.id) || null,
    snapshot: null,
    status: "prepared",
    timer: null,
    detachedWindowId: null,
  };
  transfers.set(token, tx);
  scheduleTransferExpiry(tx);
  return token;
}

function inspectTransfer(token) {
  const tx = transfers.get(token);
  return tx?.status === "prepared" ? tx.payload : null;
}

function cancelTransfer(sourceCtx, token) {
  const tx = transfers.get(token);
  if (!tx || tx.sourceId !== sourceCtx.id || tx.status !== "prepared") return false;
  rollbackTransfer(tx, "cancelled");
  return true;
}

function acceptTransfer(targetCtx, token) {
  const tx = transfers.get(token);
  if (!tx || tx.status !== "prepared") throw new Error("stale transfer token");
  if (tx.sourceId === targetCtx.id) throw new Error("cross-window accept requires another window");
  const sourceCtx = windows.get(tx.sourceId);
  if (!sourceCtx || sourceCtx.win.isDestroyed()) throw new Error("source window unavailable");
  if (tx.record) tx.snapshot = reparentRecord(sourceCtx, targetCtx, tx.record);
  tx.destinationId = targetCtx.id;
  tx.status = "accepted";
  return tx.payload;
}

function rollbackTransfer(tx, reason) {
  if (!transfers.has(tx.token)) return;
  const sourceCtx = windows.get(tx.sourceId);
  const targetCtx = tx.destinationId ? windows.get(tx.destinationId) : null;
  if (tx.record && tx.snapshot && sourceCtx && targetCtx) {
    restoreRecord(sourceCtx, targetCtx, tx.record, tx.snapshot);
  }
  if (targetCtx && !targetCtx.win.isDestroyed()) {
    targetCtx.win.webContents.send("tab-transfer:rollback", tx.token, tx.payload.tab.id, reason);
  }
  if (sourceCtx && !sourceCtx.win.isDestroyed()) {
    sourceCtx.win.webContents.send("tab-transfer:rollback", tx.token, tx.payload.tab.id, reason);
  }
  if (tx.detachedWindowId) {
    const detached = windows.get(tx.detachedWindowId);
    if (detached && !detached.win.isDestroyed()) detached.win.close();
  }
  finishToken(tx);
}
```

Register `tab-transfer:begin`, `inspect`, `accept`, `complete`, and `source-complete`. `complete(ok=true)` must notify the source without consuming the token:

```js
ipcMain.handle("tab-transfer:cancel", (event, token) => {
  const sourceCtx = contextForSender(event);
  return sourceCtx ? cancelTransfer(sourceCtx, token) : false;
});

ipcMain.handle("tab-transfer:complete", (event, token, ok) => {
  const targetCtx = contextForSender(event);
  const tx = transfers.get(token);
  if (!tx || tx.status !== "accepted" || tx.destinationId !== targetCtx?.id) return false;
  if (!ok) {
    rollbackTransfer(tx, "destination rejected insertion");
    return false;
  }
  const sourceCtx = windows.get(tx.sourceId);
  if (!sourceCtx || sourceCtx.win.isDestroyed()) {
    rollbackTransfer(tx, "source window unavailable");
    return false;
  }
  tx.status = "awaiting-source";
  sourceCtx.win.webContents.send("tab-transfer:source-commit", token, tx.payload.tab.id);
  return true;
});

ipcMain.handle("tab-transfer:source-complete", (event, token, ok, empty) => {
  const sourceCtx = contextForSender(event);
  const tx = transfers.get(token);
  if (!tx || tx.status !== "awaiting-source" || tx.sourceId !== sourceCtx?.id) return false;
  if (!ok) {
    rollbackTransfer(tx, "source removal rejected");
    return false;
  }
  const detached = tx.detachedWindowId ? windows.get(tx.detachedWindowId) : null;
  if (detached && !detached.win.isDestroyed()) detached.win.show();
  finishToken(tx);
  if (empty && !sourceCtx.win.isDestroyed()) sourceCtx.win.close();
  return true;
});
```

On either participating window's `closed` event, call `rollbackTransfer` for every non-final transaction referencing that window before deleting the context.

- [ ] **Step 5: Run the desktop checks**

Run: `npm --prefix desktop run check:webtabs`

Expected: `webtab navigation checks passed`, including same-object identity and reverse-parent rollback.

- [ ] **Step 6: Commit the transaction coordinator**

```bash
git add desktop/main.js desktop/scripts/check-webtab-navigation.js
git commit -m "feat(desktop): transact native tab transfers"
```

---

### Task 3: Expose `windowId`, Focus Claiming, and Transaction IPC Through Preload

**Files:**
- Modify: `desktop/scripts/check-webtab-navigation.js`
- Modify: `desktop/preload.js:1-31`
- Modify: `desktop/main.js`
- Modify: `web/lib/desktop-bridge.ts:17-323`
- Modify: `web/scripts/check-web-split.mjs`

**Interfaces:**
- Consumes: Task 2 IPC channels and source-commit/rollback events.
- Produces: `DesktopBridge.windowId`, `claimWebTabCommand(reqId)`, `tabTransfer.begin/inspect/accept/complete/cancel/detach`, `tabTransfer.onSourceCommit`, `tabTransfer.onRollback`, and `tabTransfer.onPending`.

- [ ] **Step 1: Add failing bridge-contract checks**

In `desktop/scripts/check-webtab-navigation.js`, read `preload.js` and assert:

```js
const preload = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");
assert.match(preload, /--openprogram-window-id=/);
assert.match(preload, /windowId,/);
for (const method of ["begin", "inspect", "accept", "complete", "cancel", "detach"]) {
  assert.match(preload, new RegExp(`${method}:.*ipcRenderer\\.invoke\\("tab-transfer:${method}"`));
}
assert.match(preload, /claimWebTabCommand:.*desktop:claim-webtab-command/);
assert.match(preload, /tab-transfer:source-commit/);
assert.match(preload, /tab-transfer:rollback/);
assert.match(preload, /tab-transfer:pending/);
```

In `web/scripts/check-web-split.mjs`, add source assertions for the typed contract and focused command guard:

```js
assert.match(desktopBridgeSource, /readonly windowId: string/);
assert.match(desktopBridgeSource, /claimWebTabCommand\(reqId: string\): Promise<boolean>/);
assert.match(desktopBridgeSource, /interface DesktopTabTransferApi/);
assert.match(desktopBridgeSource, /await bridge\.claimWebTabCommand\(d\.req_id\)/);
```

- [ ] **Step 2: Run both checks and verify the bridge fields are missing**

Run: `npm --prefix desktop run check:webtabs && npm --prefix web run check:web-split`

Expected: desktop FAIL at `--openprogram-window-id` or web FAIL at `readonly windowId`.

- [ ] **Step 3: Expose a synchronous lifetime `windowId` and transfer methods**

At the top of `desktop/preload.js`, parse the argument once:

```js
const idArg = process.argv.find((arg) => arg.startsWith("--openprogram-window-id="));
const windowId = idArg ? idArg.slice("--openprogram-window-id=".length) : "main";
```

Add to the exposed object:

```js
windowId,
claimWebTabCommand: (reqId) =>
  ipcRenderer.invoke("desktop:claim-webtab-command", reqId),
tabTransfer: {
  begin: (payload) => ipcRenderer.invoke("tab-transfer:begin", payload),
  inspect: (token) => ipcRenderer.invoke("tab-transfer:inspect", token),
  accept: (token) => ipcRenderer.invoke("tab-transfer:accept", token),
  complete: (token, ok) => ipcRenderer.invoke("tab-transfer:complete", token, ok),
  cancel: (token) => ipcRenderer.invoke("tab-transfer:cancel", token),
  detach: (token) => ipcRenderer.invoke("tab-transfer:detach", token),
  onSourceCommit: (cb) => {
    const listener = (_event, token, tabId) => cb(token, tabId);
    ipcRenderer.on("tab-transfer:source-commit", listener);
    return () => ipcRenderer.removeListener("tab-transfer:source-commit", listener);
  },
  onRollback: (cb) => {
    const listener = (_event, token, tabId, reason) => cb(token, tabId, reason);
    ipcRenderer.on("tab-transfer:rollback", listener);
    return () => ipcRenderer.removeListener("tab-transfer:rollback", listener);
  },
  onPending: (cb) => {
    const listener = (_event, token) => cb(token);
    ipcRenderer.on("tab-transfer:pending", listener);
    return () => ipcRenderer.removeListener("tab-transfer:pending", listener);
  },
  sourceComplete: (token, ok, empty) =>
    ipcRenderer.invoke("tab-transfer:source-complete", token, ok, empty),
},
```

- [ ] **Step 4: Type the contract and make backend commands atomically focus-owned**

Add these exact types to `web/lib/desktop-bridge.ts`:

```ts
export type TabDropPlacement =
  | { kind: "strip"; index: number }
  | { kind: "group"; groupId: string; memberIndex: number }
  | { kind: "merge"; targetTabId: string };

export interface DesktopTransferPayload {
  tab: CenterTab;
  sourceGroupId?: string;
  fileDraft?: { key: string; value: FileDraft };
  composerDraft?: { key: string; value: string };
}

export interface DesktopTabTransferApi {
  begin(payload: DesktopTransferPayload): Promise<string>;
  inspect(token: string): Promise<DesktopTransferPayload | null>;
  accept(token: string): Promise<DesktopTransferPayload>;
  complete(token: string, ok: boolean): Promise<boolean>;
  cancel(token: string): Promise<boolean>;
  detach(token: string): Promise<boolean>;
  sourceComplete(token: string, ok: boolean, empty: boolean): Promise<boolean>;
  onSourceCommit(cb: (token: string, tabId: string) => void): () => void;
  onRollback(cb: (token: string, tabId: string, reason: string) => void): () => void;
  onPending(cb: (token: string) => void): () => void;
}

export interface DesktopBridge {
  isDesktop: true;
  readonly windowId: string;
  claimWebTabCommand(reqId: string): Promise<boolean>;
  openExternal(url: string): void;
  webTab: DesktopWebTabApi;
  tabTransfer: DesktopTabTransferApi;
}
```

At the start of the `webtab.command` branch in `installDesktopMenuHandlers`, claim the request before mutating the local store:

```ts
void (async () => {
  if (!(await bridge.claimWebTabCommand(d.req_id!))) return;
  await handleFocusedWebTabCommand(bridge, d);
})();
return;
```

Move the existing open/active body into `handleFocusedWebTabCommand` without changing its web-tab activation and result behavior.

In `desktop/main.js`, implement a bounded atomic claim registry:

```js
const claimedWebTabCommands = new Map();

ipcMain.handle("desktop:claim-webtab-command", (event, reqId) => {
  const ctx = contextForSender(event);
  const focused = focusedContext();
  if (!ctx || focused?.id !== ctx.id || typeof reqId !== "string") return false;
  if (claimedWebTabCommands.has(reqId)) return false;
  const timer = setTimeout(() => claimedWebTabCommands.delete(reqId), 5_000);
  claimedWebTabCommands.set(reqId, timer);
  return true;
});
```

- [ ] **Step 5: Run bridge and desktop checks**

Run: `npm --prefix desktop run check:webtabs && npm --prefix web run check:web-split`

Expected: both scripts print their existing `checks passed` messages.

- [ ] **Step 6: Commit the preload and renderer contract**

```bash
git add desktop/main.js desktop/preload.js desktop/scripts/check-webtab-navigation.js web/lib/desktop-bridge.ts web/scripts/check-web-split.mjs
git commit -m "feat(desktop): expose window transfer bridge"
```

---

### Task 4: Add Per-window Persistence and Transfer-safe Store Actions

**Files:**
- Modify: `web/scripts/check-web-split.mjs:17-499`
- Modify: `web/lib/state/center-tabs-store.ts:20-599`
- Modify: `web/lib/session-store/index.ts:77-180, 307-349, 713-739`
- Modify: `web/lib/state/files-shared.ts:140-160`

**Interfaces:**
- Consumes: `window.openprogramDesktop.windowId` and `TabDropPlacement` from Task 3.
- Produces: `CenterTabGroup`, `TransferredTabPayload`, `insertTransferredTab(payload, placement): boolean`, `removeTransferredTab(id): { ok: boolean; empty: boolean }`, `snapshotFileDraft(tab)`, `restoreFileDraft(payload)`, and `importComposerDraft(key, value)`.

- [ ] **Step 1: Add failing behavior checks for key migration, insertion, and non-close removal**

Extend `web/scripts/check-web-split.mjs` with a desktop bridge before importing a query-isolated store:

```js
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };
values.set("centerTabs", JSON.stringify({
  tabs: [{ id: "s:legacy", kind: "session", title: "Legacy", sessionId: "legacy" }],
  activeId: "s:legacy",
}));
const { useCenterTabs: mainTabs, sessionAckIsActive: mainAck } = await import(
  "../lib/state/center-tabs-store.ts?window-main"
);
assert.equal(mainTabs.getState().activeId, "s:legacy");
assert.ok(values.has("centerTabs:main"));
assert.equal(values.has("centerTabs"), false);

globalThis.window.openprogramDesktop.windowId = "secondary";
const { useCenterTabs: secondaryTabs } = await import(
  "../lib/state/center-tabs-store.ts?window-secondary"
);
assert.deepEqual(secondaryTabs.getState().tabs, []);

mainTabs.setState({
  tabs: [{
    id: "s:local_transfer", kind: "session", title: "Draft",
    sessionId: "local_transfer", draft: true,
  }],
  activeId: "s:local_transfer",
  groups: [],
  splitWebTabId: null,
});
const removed = mainTabs.getState().removeTransferredTab("s:local_transfer");
assert.deepEqual(removed, { ok: true, empty: true });
assert.deepEqual(mainTabs.getState().tabs, []);
assert.equal(mainTabs.getState().activeId, null);
assert.equal(mainAck("local_transfer"), false,
  "a removed local draft remains inactive without a close tombstone or fallback tab");

mainTabs.setState({
  tabs: [{ id: "s:running", kind: "session", title: "Running", sessionId: "running" }],
  activeId: "s:running",
  groups: [],
});
assert.deepEqual(
  mainTabs.getState().removeTransferredTab("s:running"),
  { ok: true, empty: true },
);
assert.equal(mainAck("running"), false,
  "a late source acknowledgement must not recreate a transferred session");

const web = {
  id: "w:https://example.com/", kind: "web", title: "Example", url: "https://example.com/",
};
assert.equal(secondaryTabs.getState().insertTransferredTab({ tab: web }, { kind: "strip", index: 0 }), true);
assert.equal(secondaryTabs.getState().insertTransferredTab({ tab: web }, { kind: "strip", index: 1 }), false);
assert.equal(secondaryTabs.getState().tabs.length, 1);
```

Add group-capacity checks:

```js
secondaryTabs.setState({
  tabs: [web,
    { id: "s:a", kind: "session", title: "A", sessionId: "a" },
    { id: "s:b", kind: "session", title: "B", sessionId: "b" },
    { id: "s:c", kind: "session", title: "C", sessionId: "c" },
  ],
  groups: [{ id: "g:full", memberIds: [web.id, "s:a", "s:b"], visibleIds: [web.id, "s:a"], focusedId: web.id }],
});
assert.equal(
  secondaryTabs.getState().insertTransferredTab(
    { tab: { id: "s:c", kind: "session", title: "C", sessionId: "c" } },
    { kind: "group", groupId: "g:full", memberIndex: 3 },
  ),
  false,
);
```

- [ ] **Step 2: Run the store check and verify the unkeyed store or missing actions fail**

Run: `npm --prefix web run check:web-split`

Expected: FAIL because `centerTabs:main` is absent or `removeTransferredTab` is undefined.

- [ ] **Step 3: Key persisted state by the desktop window and migrate only the primary once**

Add storage helpers to `center-tabs-store.ts`:

```ts
function desktopWindowId(): string | null {
  if (typeof window === "undefined") return null;
  const desktop = (window as unknown as {
    openprogramDesktop?: { isDesktop?: boolean; windowId?: string };
  }).openprogramDesktop;
  return desktop?.isDesktop && typeof desktop.windowId === "string"
    ? desktop.windowId
    : null;
}

function windowStorageKey(base: string): string {
  const id = desktopWindowId();
  return id ? `${base}:${id}` : base;
}

function readWindowPayload(base: string): string | null {
  const key = windowStorageKey(base);
  let raw = localStorage.getItem(key);
  if (!raw && desktopWindowId() === "main") {
    raw = localStorage.getItem(base);
    if (raw) {
      localStorage.setItem(key, raw);
      localStorage.removeItem(base);
    }
  }
  return raw;
}
```

Use `readWindowPayload(LS_KEY)` / `windowStorageKey(LS_KEY)` and the same pair for `SPLIT_STORAGE_KEY` in all reads and writes. Keep non-desktop browser behavior unkeyed.

- [ ] **Step 4: Add groups and transfer-safe insertion/removal**

Add the state model:

```ts
export interface CenterTabGroup {
  id: string;
  memberIds: string[];
  visibleIds: string[];
  focusedId: string;
}

export type TabDropPlacement =
  | { kind: "strip"; index: number }
  | { kind: "group"; groupId: string; memberIndex: number }
  | { kind: "merge"; targetTabId: string };

export interface TransferredTabPayload {
  tab: CenterTab;
  sourceGroupId?: string;
  fileDraft?: { key: string; value: FileDraft };
  composerDraft?: { key: string; value: string };
}
```

Persist `groups` beside `tabs` and normalize restore by removing missing member ids, rejecting duplicate membership, truncating to three members, and dissolving one-member groups.

Add these exact state signatures:

```ts
groups: CenterTabGroup[];
insertTransferredTab: (
  payload: TransferredTabPayload,
  placement: TabDropPlacement,
) => boolean;
removeTransferredTab: (id: string) => { ok: boolean; empty: boolean };
```

Keep transfer suppression distinct from close tombstones:

```ts
const transferredSessionAckSuppressions = new Set<string>();
```

`removeTransferredTab` adds a transferred non-local session id to this set. An explicit `openSessionTab` removes the id from both `transferredSessionAckSuppressions` and the existing `closedSessionAckTombstones`. `sessionAckIsActive` returns `false` when either set contains the id. This prevents a late source `chat_ack` from recreating the moved tab without representing the move as a user close.

`removeTransferredTab` must be a separate implementation, not a call to `closeTab`:

```ts
removeTransferredTab: (id) => {
  let result = { ok: false, empty: false };
  set((s) => {
    const index = s.tabs.findIndex((tab) => tab.id === id);
    if (index < 0) return {};
    const transferred = s.tabs[index];
    if (
      transferred.kind === "session" &&
      transferred.sessionId &&
      !transferred.sessionId.startsWith("local_")
    ) {
      transferredSessionAckSuppressions.add(transferred.sessionId);
    }
    const tabs = s.tabs.filter((tab) => tab.id !== id);
    const groups = s.groups.flatMap((group) => {
      const memberIds = group.memberIds.filter((memberId) => memberId !== id);
      if (memberIds.length < 2) return [];
      const visibleIds = group.visibleIds.filter((memberId) => memberId !== id).slice(0, 2);
      return [{
        ...group,
        memberIds,
        visibleIds,
        focusedId: memberIds.includes(group.focusedId) ? group.focusedId : memberIds[0],
      }];
    });
    const activeId = s.activeId === id
      ? (tabs[index] ?? tabs[index - 1])?.id ?? null
      : s.activeId;
    const splitWebTabId = s.splitWebTabId === id ? null : s.splitWebTabId;
    persist({ tabs, groups, activeId });
    persistSplit({ tabId: splitWebTabId, ratio: s.splitRatio });
    result = { ok: true, empty: tabs.length === 0 };
    return { tabs, groups, activeId, splitWebTabId };
  });
  return result;
},
```

`insertTransferredTab` must first reject an existing id and a full group, then insert at the clamped strip/member index and persist once. A `merge` placement creates a two-member group around `targetTabId`; a one-member residual group is never persisted.

- [ ] **Step 5: Preserve unsent composer text and dirty file buffers in the acknowledged payload**

In `web/lib/state/files-shared.ts`, add:

```ts
export function snapshotFileDraft(tab: {
  kind: string; projectId?: string; path?: string;
}): { key: string; value: FileDraft } | undefined {
  if (tab.kind !== "file" || !tab.projectId || !tab.path) return undefined;
  const key = fileDraftKey(tab.projectId, tab.path);
  const value = fileDrafts.get(key);
  return value ? { key, value: { ...value } } : undefined;
}

export function restoreFileDraft(snapshot?: { key: string; value: FileDraft }): void {
  if (snapshot) fileDrafts.set(snapshot.key, { ...snapshot.value });
}
```

In `web/lib/session-store/index.ts`, add to the state interface and implementation:

```ts
importComposerDraft: (key: string, value: string) => void;
```

```ts
importComposerDraft: (key, value) =>
  set((state) => {
    const composerDrafts = { ...state.composerDrafts, [key]: value };
    persistComposerDrafts(composerDrafts);
    return state.activeChatKey === key
      ? { composerDrafts, composerInput: value }
      : { composerDrafts };
  }),
```

The source keeps both drafts until source-complete succeeds. The destination restores them immediately before inserting the transferred tab.

- [ ] **Step 6: Run the focused store checks**

Run: `npm --prefix web run check:web-split && npm --prefix web run check:multi-draft`

Expected: `web-split checks passed` and `multi-draft checks passed`.

- [ ] **Step 7: Commit per-window state and transfer actions**

```bash
git add web/lib/state/center-tabs-store.ts web/lib/session-store/index.ts web/lib/state/files-shared.ts web/scripts/check-web-split.mjs
git commit -m "feat(desktop): persist tabs per window"
```

---

### Task 5: Wire Destination Accept, Source Removal, and Rollback in the Renderer

**Files:**
- Modify: `web/lib/desktop-bridge.ts`
- Modify: `web/scripts/check-web-split.mjs`

**Interfaces:**
- Consumes: Task 3 transfer bridge and Task 4 `insertTransferredTab` / `removeTransferredTab` / draft helpers.
- Produces: `buildTransferPayload(tab)`, `acceptTransferToken(token, placement)`, `forgetTransferredWebView(id)`, and idempotent transaction listeners installed by `installDesktopMenuHandlers()`.

- [ ] **Step 1: Add failing source checks for acknowledgement ordering and no close path**

Append to `web/scripts/check-web-split.mjs`:

```js
assert.match(desktopBridgeSource, /function forgetTransferredWebView\(id: string\)/);
assert.match(desktopBridgeSource, /insertTransferredTab\(payload, placement\)/);
const insertTransferredIndex = desktopBridgeSource.indexOf(
  "insertTransferredTab(payload, placement)",
);
const acknowledgeDestinationIndex = desktopBridgeSource.indexOf(
  "bridge.tabTransfer.complete(token, true)",
);
assert.ok(insertTransferredIndex >= 0 && acknowledgeDestinationIndex > insertTransferredIndex,
  "destination insertion must precede acknowledgement");
assert.match(desktopBridgeSource, /removeTransferredTab\(tabId\)/);
assert.doesNotMatch(
  desktopBridgeSource,
  /onSourceCommit[\s\S]*closeTab\(/,
  "transfer source removal must not use closeTab",
);
assert.match(desktopBridgeSource, /bridge\.tabTransfer\.sourceComplete\(token, result\.ok, result\.empty\)/);
assert.match(desktopBridgeSource, /onRollback[\s\S]*removeTransferredTab/);
```

- [ ] **Step 2: Run the check and confirm the transfer listener is absent**

Run: `npm --prefix web run check:web-split`

Expected: FAIL at `function forgetTransferredWebView`.

- [ ] **Step 3: Build payloads without destructive source cleanup**

Add to `web/lib/desktop-bridge.ts`:

```ts
export function forgetTransferredWebView(id: string): void {
  liveViewIds.delete(id);
  setWebTabReady(id, false);
  webTabReadyWaiters.delete(id);
}

export function buildTransferPayload(tab: CenterTab): DesktopTransferPayload {
  const composerDraft = tab.kind === "session" && tab.sessionId
    ? (() => {
        const value = useSessionStore.getState().composerDrafts[tab.sessionId!];
        return value === undefined ? undefined : { key: tab.sessionId!, value };
      })()
    : undefined;
  return {
    tab,
    fileDraft: snapshotFileDraft(tab),
    composerDraft,
  };
}
```

- [ ] **Step 4: Add idempotent destination accept and transaction listeners**

Add a placement map keyed by token so rollback can remove only a destination insertion created by this transaction:

```ts
const acceptedTransfers = new Map<string, string>();

export async function acceptTransferToken(
  bridge: DesktopBridge,
  token: string,
  placement: TabDropPlacement,
): Promise<boolean> {
  const preview = await bridge.tabTransfer.inspect(token);
  if (!preview) return false;
  const state = useCenterTabs.getState();
  if (state.tabs.some((tab) => tab.id === preview.tab.id)) return false;
  if (placement.kind === "group") {
    const group = state.groups.find((item) => item.id === placement.groupId);
    if (!group || group.memberIds.length >= 3) return false;
  }
  const payload = await bridge.tabTransfer.accept(token);
  restoreFileDraft(payload.fileDraft);
  if (payload.composerDraft) {
    useSessionStore.getState().importComposerDraft(
      payload.composerDraft.key,
      payload.composerDraft.value,
    );
  }
  const inserted = useCenterTabs.getState().insertTransferredTab(payload, placement);
  if (!inserted) {
    await bridge.tabTransfer.complete(token, false);
    return false;
  }
  acceptedTransfers.set(token, payload.tab.id);
  const committed = await bridge.tabTransfer.complete(token, true);
  if (!committed) {
    useCenterTabs.getState().removeTransferredTab(payload.tab.id);
    acceptedTransfers.delete(token);
  }
  return committed;
}
```

Inside `installDesktopMenuHandlers()` install listeners once:

```ts
bridge.tabTransfer.onSourceCommit((token, tabId) => {
  const tab = useCenterTabs.getState().tabs.find((item) => item.id === tabId);
  if (tab?.kind === "web") {
    forgetTransferredWebView(tabId);
  }
  const result = useCenterTabs.getState().removeTransferredTab(tabId);
  if (result.ok && tab?.kind === "file" && tab.projectId && tab.path) {
    fileDrafts.delete(fileDraftKey(tab.projectId, tab.path));
  }
  void bridge.tabTransfer.sourceComplete(token, result.ok, result.empty);
});

bridge.tabTransfer.onRollback((token, tabId) => {
  if (acceptedTransfers.get(token) !== tabId) return;
  useCenterTabs.getState().removeTransferredTab(tabId);
  acceptedTransfers.delete(token);
});

bridge.tabTransfer.onPending((token) => {
  void acceptTransferToken(bridge, token, { kind: "strip", index: 0 });
});
```

Only source-complete removes the source tab. A normal source store subscription may subsequently issue a stale `webtab:destroy`; Task 1 ownership checks make it a no-op, while `forgetTransferredWebView` prevents repeated attempts.

- [ ] **Step 5: Run renderer checks**

Run: `npm --prefix web run check:web-split && npm --prefix web run check:center-tabs`

Expected: both scripts print their `checks passed` messages.

- [ ] **Step 6: Commit renderer transaction coordination**

```bash
git add web/lib/desktop-bridge.ts web/scripts/check-web-split.mjs
git commit -m "feat(desktop): acknowledge renderer tab transfers"
```

---

### Task 6: Add Native Cross-window Drop, Merge Targets, Keyboard Move, and Hidden-window Detach

**Files:**
- Modify: `web/scripts/check-center-tabs.mjs:16-158`
- Modify: `web/components/center-tabs/center-tab-strip.tsx:21-452`
- Modify: `web/components/center-tabs/center-tabs.module.css`
- Modify: `desktop/scripts/check-webtab-navigation.js`
- Modify: `desktop/main.js`

**Interfaces:**
- Consumes: `buildTransferPayload`, `acceptTransferToken`, `DesktopBridge.tabTransfer`, `TabDropPlacement`, and `createWindow({ show, transferToken })`.
- Produces: `application/x-openprogram-tab-transfer` drag tokens, strip/group merge placement, outside-drop detach, `Shift+F10` move commands, and `detachTransfer(sourceCtx, token)`.

- [ ] **Step 1: Add failing static drag and keyboard checks**

Append to `web/scripts/check-center-tabs.mjs`:

```js
assert.match(strip, /const TAB_TRANSFER_MIME = "application\/x-openprogram-tab-transfer"/);
assert.match(strip, /draggable=\{true\}/);
assert.match(strip, /onDragStart=/);
assert.match(strip, /dataTransfer\.setData\(TAB_TRANSFER_MIME, token\)/);
assert.match(strip, /onDragOver=/);
assert.match(strip, /onDrop=/);
assert.match(strip, /acceptTransferToken/);
assert.match(strip, /dropEffect === "none"/);
assert.match(strip, /bridge\.tabTransfer\.detach\(token\)/);
assert.match(strip, /e\.key === "Escape"/);
assert.match(strip, /bridge\.tabTransfer\.cancel\(token\)/);
assert.match(strip, /e\.shiftKey && e\.key === "F10"/);
assert.match(css, /\[data-tab-drag-source="true"\][^{]*\{[^}]*opacity:\s*\.55/s);
assert.match(css, /\[data-tab-drop-target="merge"\]/);
assert.doesNotMatch(strip, /@dnd-kit|react-dnd|framer-motion/);
```

Add detach assertions to the desktop script:

```js
assert.match(source, /ipcMain\.handle\("tab-transfer:detach"/);
assert.match(source, /createWindow\(\{[\s\S]*show: false,[\s\S]*transferToken: token/);
assert.match(source, /tab-transfer:pending/);
assert.match(source, /detached\.win\.show\(\)/);
```

- [ ] **Step 2: Run checks and verify drag/detach wiring is missing**

Run: `npm --prefix web run check:center-tabs && npm --prefix desktop run check:webtabs`

Expected: web FAIL at `TAB_TRANSFER_MIME` or desktop FAIL at `tab-transfer:detach`.

- [ ] **Step 3: Start a token-backed native drag and preserve Escape cancellation**

In `center-tab-strip.tsx`, add:

```ts
const TAB_TRANSFER_MIME = "application/x-openprogram-tab-transfer";
const TAB_TRANSFER_TEXT_PREFIX = "openprogram-tab-transfer:";
```

Track the active token and cancellation in refs:

```ts
const dragRef = useRef<{ token: string; cancelled: boolean } | null>(null);

async function beginTabDrag(e: React.DragEvent, tab: CenterTab) {
  const bridge = desktopBridge();
  if (!bridge) return;
  const token = await bridge.tabTransfer.begin(buildTransferPayload(tab));
  dragRef.current = { token, cancelled: false };
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData(TAB_TRANSFER_MIME, token);
  e.dataTransfer.setData("text/plain", TAB_TRANSFER_TEXT_PREFIX + token);
}

async function cancelTabDrag() {
  const current = dragRef.current;
  if (!current) return;
  current.cancelled = true;
  await desktopBridge()?.tabTransfer.cancel(current.token);
  dragRef.current = null;
}

function transferTokenFrom(e: React.DragEvent): string | null {
  const direct = e.dataTransfer.getData(TAB_TRANSFER_MIME);
  if (direct) return direct;
  const plain = e.dataTransfer.getData("text/plain");
  return plain.startsWith(TAB_TRANSFER_TEXT_PREFIX)
    ? plain.slice(TAB_TRANSFER_TEXT_PREFIX.length)
    : null;
}
```

Set `draggable={Boolean(desktopBridge())}` on the tab/segment wrapper. On `dragstart`, call `beginTabDrag`; on `Escape`, set `cancelled=true`; on `dragend`, detach only when the token still matches, cancellation is false, and `e.dataTransfer.dropEffect === "none"`:

```ts
async function finishTabDrag(e: React.DragEvent) {
  const current = dragRef.current;
  dragRef.current = null;
  if (!current || current.cancelled || e.dataTransfer.dropEffect !== "none") return;
  await desktopBridge()?.tabTransfer.detach(current.token);
}
```

Call `cancelTabDrag()` on `Escape`. After a same-window reorder or same-window group/ungroup succeeds, call `bridge.tabTransfer.cancel(token)` immediately; do not leave a prepared token waiting for expiry.

- [ ] **Step 4: Resolve destination placement and merge only after validation**

Mark DOM targets with `data-tab-id`, `data-group-id`, and `data-member-index`. The strip drop handler uses its nearest target:

```ts
function placementFromDrop(e: React.DragEvent): TabDropPlacement {
  const element = (e.target as HTMLElement).closest<HTMLElement>(
    "[data-group-id], [data-tab-id]",
  );
  const groupId = element?.dataset.groupId;
  if (groupId) {
    return {
      kind: "group",
      groupId,
      memberIndex: Number(element.dataset.memberIndex ?? 0),
    };
  }
  const targetTabId = element?.dataset.tabId;
  if (targetTabId) return { kind: "merge", targetTabId };
  return { kind: "strip", index: useCenterTabs.getState().tabs.length };
}

async function receiveTabDrop(e: React.DragEvent) {
  const token = transferTokenFrom(e);
  const bridge = desktopBridge();
  if (!token || !bridge) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  await acceptTransferToken(bridge, token, placementFromDrop(e));
}
```

The same-window path uses the existing store reorder/group actions directly and cancels its main token; the cross-window path calls `acceptTransferToken`. A group with three members rejects the drop before `accept()`.

Add `Shift+F10` on a focused tab to open the existing lightweight tab context menu with `Move left`, `Move right`, `Add to split`, `Remove from group`, and `Move to new window`. `Move to new window` calls the same `begin()` then `detach()` sequence; `Escape` closes the menu and announces cancellation through one `aria-live="polite"` region.

- [ ] **Step 5: Add transient drag styles without a persistent accent**

Add to `center-tabs.module.css`:

```css
[data-tab-drag-source="true"] {
  opacity: .55;
}

[data-tab-drop-target="insert"] {
  box-shadow: inset 2px 0 0 var(--accent);
}

[data-tab-drop-target="merge"] {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
```

Remove these attributes at `dragleave`, `drop`, `dragend`, and `Escape`. Do not add a static orange line.

- [ ] **Step 6: Create and claim a hidden detached window**

Register `tab-transfer:detach` in `desktop/main.js`:

```js
ipcMain.handle("tab-transfer:detach", async (event, token) => {
  const sourceCtx = contextForSender(event);
  const tx = transfers.get(token);
  if (!sourceCtx || !tx || tx.sourceId !== sourceCtx.id || tx.status !== "prepared") {
    return false;
  }
  const bounds = sourceCtx.win.getBounds();
  const detachedWindowId = randomUUID();
  tx.detachedWindowId = detachedWindowId;
  const detached = await createWindow({
    windowId: detachedWindowId,
    state: { x: bounds.x + 32, y: bounds.y + 32, width: bounds.width, height: bounds.height },
    show: false,
    transferToken: token,
  });
  return true;
});
```

Inside `createWindow`, register the pending-send listener before `loadURL`:

```js
if (transferToken) {
  win.webContents.once("did-finish-load", () => {
    const tx = transfers.get(transferToken);
    if (tx?.status === "prepared" && tx.detachedWindowId === windowId) {
      win.webContents.send("tab-transfer:pending", transferToken);
    }
  });
}
await win.loadURL(await resolveStartUrl());
```

The window is shown only by `source-complete` after destination acknowledgement. Timeout, target rejection, or target close invokes `rollbackTransfer` and closes the hidden window.

- [ ] **Step 7: Run drag, detach, and full web checks**

Run: `npm --prefix web run check:center-tabs && npm --prefix web run check:web-split && npm --prefix desktop run check:webtabs`

Expected: all three scripts print their `checks passed` messages.

- [ ] **Step 8: Commit pointer, keyboard, merge, and detach behavior**

```bash
git add desktop/main.js desktop/scripts/check-webtab-navigation.js web/components/center-tabs/center-tab-strip.tsx web/components/center-tabs/center-tabs.module.css web/scripts/check-center-tabs.mjs
git commit -m "feat(desktop): drag tabs across windows"
```

---

### Task 7: Verify Regression, CDP Identity, State Preservation, and Failure Rollback Live

**Files:**
- Modify only if a check exposes a defect: the exact implementation or check file from Tasks 1-6.

**Interfaces:**
- Consumes: complete implementation from Tasks 1-6 and Electron remote debugging on port `9223`.
- Produces: current command output and live Electron evidence proving every transfer invariant.

- [ ] **Step 1: Run all deterministic checks and production build**

Run:

```bash
npm --prefix desktop run check:webtabs
npm --prefix web run check
npm --prefix web run build
python -m pytest tests/ --ignore=tests/test_provider_cli.py --ignore=tests/integration -q
```

Expected:

```text
webtab navigation checks passed
center-tabs checks passed
bookmark checks passed
web-split checks passed
multi-draft checks passed
provisional-send checks passed
chat-ui checks passed
Compiled successfully
all selected pytest tests pass
```

- [ ] **Step 2: Launch an isolated development desktop profile**

Run the web UI and desktop shell in separate terminals:

```bash
OPENPROGRAM_PROFILE=dev OPENPROGRAM_BACKEND_PORT=18209 OPENPROGRAM_WEB_PORT=18200 openprogram worker start
```

```bash
cd desktop
OPENPROGRAM_WEB_PORT=18200 \
OPENPROGRAM_DESKTOP_URL=http://127.0.0.1:18200/chat \
ELECTRON_EXTRA_LAUNCH_ARGS=--user-data-dir=/tmp/openprogram-multiwindow-acceptance \
npm run dev
```

Expected: one OpenProgram window loads the development UI; stable port `18100` is untouched.

- [ ] **Step 3: Capture pre-transfer native web state through CDP**

Open `https://example.com/?openprogram-transfer-acceptance=1`, put the web tab beside a chat, and use CDP `Runtime.evaluate` on its page target:

```js
(() => {
  document.body.insertAdjacentHTML(
    "beforeend",
    '<input id="op-transfer-value" value="preserved"><div style="height:2400px"></div>',
  );
  document.querySelector("#op-transfer-value").value = "edited before transfer";
  scrollTo(0, 900);
  return {
    timeOrigin: performance.timeOrigin,
    value: document.querySelector("#op-transfer-value").value,
    scrollY,
  };
})()
```

Record the CDP target id, `timeOrigin`, value, and `scrollY`. Expected value is `edited before transfer` and scroll is approximately `900`.

- [ ] **Step 4: Detach, merge into another window, and merge back**

Perform these pointer operations:

1. Drag the web segment outside every OpenProgram window and release.
2. Confirm a new window appears only after the transfer completes.
3. Drag the tab onto an existing tab in the original window to form/enter its compound group.
4. Drag the segment out of that group and back into the detached window.
5. Use `Shift+F10` → `Move to new window` and then merge it back without pointer drag.

Expected after every move:

- exactly one destination tab exists;
- the source tab disappears only after the destination is visible;
- a group never exceeds three members;
- no NTP appears in an emptied source before its window closes;
- no persistent orange top line appears.

- [ ] **Step 5: Prove the same native page survived all moves**

Query the destination web page target again and evaluate:

```js
({
  timeOrigin: performance.timeOrigin,
  value: document.querySelector("#op-transfer-value")?.value,
  scrollY,
  href: location.href,
})
```

Expected:

- CDP target id equals the pre-transfer id;
- `timeOrigin` equals the pre-transfer value exactly;
- input value remains `edited before transfer`;
- `scrollY` remains approximately `900`;
- URL remains `https://example.com/?openprogram-transfer-acceptance=1`;
- back/forward history and login/session cookies remain usable.

- [ ] **Step 6: Verify focused routing and stale source IPC rejection**

Focus window A and issue one backend `webtab.command(op="active")`; then focus window B and repeat.

Expected: each request receives exactly one `webtab_result`, and its `target_id` belongs to the focused window's visible web pane.

During a transfer, force the source renderer to emit late `webtab:hide`, `webtab:set-bounds`, and `webtab:destroy` for the moved id.

Expected: the destination view remains visible, retains the same target id, and its `webContents` is not closed.

- [ ] **Step 7: Verify rollback and hidden-window failure behavior**

Exercise each failure before source removal:

1. Drop onto a destination containing the same tab id.
2. Drop onto a three-member group.
3. Start detach and close the hidden/new destination before acknowledgement.
4. Start a transfer, prevent destination completion, and wait more than 15 seconds.

Expected for every case:

- source tab and source group order remain unchanged;
- the native view returns to its original parent, bounds, and visibility;
- CDP target id and page state remain unchanged;
- no duplicate tab remains in the destination;
- an uncommitted detached window closes;
- reusing the expired/consumed token returns failure and performs no mutation.

- [ ] **Step 8: Verify per-window restore and profile-wide bookmark behavior**

Create distinct tab orders in the main and one detached window, reload both renderers, and reopen Bookmarks in each.

Expected:

- each renderer restores only `centerTabs:<its windowId>` and `openprogram.webSplit:<its windowId>`;
- the legacy unkeyed payload was migrated only to `centerTabs:main` once;
- both windows show the same profile-wide bookmark list and react to `openprogram-bookmarks-changed`;
- split ratio and group membership in one window do not overwrite the other.

- [ ] **Step 9: Commit only concrete fixes exposed by acceptance**

If acceptance required code changes, rerun Steps 1-8 and commit only those exact fixes:

```bash
git add desktop/main.js desktop/preload.js desktop/scripts/check-webtab-navigation.js \
  web/lib/desktop-bridge.ts web/lib/state/center-tabs-store.ts \
  web/lib/session-store/index.ts web/lib/state/files-shared.ts \
  web/components/center-tabs/center-tab-strip.tsx \
  web/components/center-tabs/center-tabs.module.css \
  web/scripts/check-center-tabs.mjs web/scripts/check-web-split.mjs
git commit -m "fix(desktop): close multiwindow acceptance gaps"
```

If no file changed, do not create an empty commit; record the verified HEAD and command output in the task handoff.
