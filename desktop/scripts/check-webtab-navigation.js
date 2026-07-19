const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const preloadSource = fs.readFileSync(
  path.join(__dirname, "..", "preload.js"),
  "utf8",
);
const transferUserData = fs.mkdtempSync(
  path.join(os.tmpdir(), "openprogram-webtab-transaction-"),
);

const ipcListeners = new Map();
const ipcHandlers = new Map();
let focusedWindow = null;
const fakeWindows = [];
const browserWindowOptions = [];
let menuTemplate = null;
let nextGeneratedWindowId = 1000;
let generatedNativeViews = 0;
const rendererQueue = [];

function flushRendererQueue() {
  let delivered = 0;
  while (rendererQueue.length > 0) {
    delivered += 1;
    if (delivered > 1000) throw new Error("renderer callback queue did not settle");
    rendererQueue.shift()();
  }
}

function createFakeClock() {
  let now = 0;
  let nextId = 1;
  const pending = new Map();
  const all = new Map();
  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      const timer = { id, callback, delay, dueAt: now + delay };
      pending.set(id, timer);
      all.set(id, timer);
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    advance(ms) {
      now += ms;
      const ready = [...pending.values()]
        .filter((timer) => timer.dueAt <= now)
        .sort((a, b) => a.dueAt - b.dueAt || a.id - b.id);
      for (const timer of ready) {
        if (!pending.delete(timer.id)) continue;
        timer.callback();
      }
    },
    runCleared(id) { all.get(id)?.callback(); },
    pendingIds() { return [...pending.keys()]; },
  };
}

const clock = createFakeClock();

class FakeBrowserWindow {
  constructor(options) {
    browserWindowOptions.push(options);
    return fakeWindow(nextGeneratedWindowId++);
  }
  static fromWebContents(sender) {
    return fakeWindows.find((win) => win.webContents === sender) || null;
  }
  static getFocusedWindow() { return focusedWindow; }
  static getAllWindows() {
    return fakeWindows.filter((win) => !win.isDestroyed());
  }
}

const fakeHttp = {
  get(_url, _options, callback) {
    const request = {
      on() { return request; },
      destroy() {},
    };
    callback({ resume() {} });
    return request;
  },
};

const fakeElectron = {
  app: {
    commandLine: { appendSwitch() {} },
    getPath() { return transferUserData; },
    whenReady() { return { then() {} }; },
    on() {},
    quit() {},
  },
  BrowserWindow: FakeBrowserWindow,
  WebContentsView: class {
    constructor() {
      generatedNativeViews += 1;
      return controlledRecord(`native-view-${generatedNativeViews}`).record.view;
    }
  },
  Menu: {
    buildFromTemplate(template) {
      menuTemplate = template;
      return template;
    },
    setApplicationMenu() {},
  },
  ipcMain: {
    on(channel, handler) { ipcListeners.set(channel, handler); },
    handle(channel, handler) { ipcHandlers.set(channel, handler); },
  },
  shell: { openExternal() {} },
};

const sandbox = {
  Promise,
  Map,
  Set,
  URL,
  console,
  encodeURIComponent,
  setTimeout: clock.setTimeout,
  clearTimeout: clock.clearTimeout,
  process,
  __dirname: path.join(__dirname, ".."),
  __filename: path.join(__dirname, "..", "main.js"),
  require(id) {
    if (id === "electron") return fakeElectron;
    if (id === "http") return fakeHttp;
    if (id === "./tab-transfer-store") return require("../tab-transfer-store.js");
    return require(id);
  },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  `${source}\n;globalThis.__webtabTestHooks = {
    makeWindowContext,
    contextForSender,
    focusedContext,
    syncVisibleViews,
    showView,
    hideView,
    sendState,
    loadView,
    activateView,
    runNativeNavigation,
    registerWebTabIpc,
    buildMenu,
    createWindow,
    cleanupWindowContext,
    ensureView,
    destroyView,
    validateTransferPayload:
      typeof validateTransferPayload === "function" ? validateTransferPayload : undefined,
    reparentRecords:
      typeof reparentRecords === "function" ? reparentRecords : undefined,
    restoreRecords:
      typeof restoreRecords === "function" ? restoreRecords : undefined,
    makeTransferCoordinator:
      typeof makeTransferCoordinator === "function" ? makeTransferCoordinator : undefined,
    registerTabTransferIpc:
      typeof registerTabTransferIpc === "function" ? registerTabTransferIpc : undefined,
    tabTransfers:
      typeof tabTransfers === "object" ? tabTransfers : undefined,
    windows,
    contextsByBrowserWindowId,
  };`,
  sandbox,
  { filename: "desktop/main.js" },
);
const hooks = sandbox.__webtabTestHooks;

function fakeWindow(id) {
  const listeners = new Map();
  const sent = [];
  const added = [];
  const removed = [];
  let addCalls = 0;
  const win = {
    id,
    destroyed: false,
    listeners,
    sent,
    added,
    removed,
    shown: false,
    closeCalls: 0,
    failAddAt: null,
    contentView: {
      addChildView(view) {
        addCalls += 1;
        if (win.failAddAt === addCalls) throw new Error("injected addChildView failure");
        added.push(view);
      },
      removeChildView(view) { removed.push(view); },
    },
    webContents: {
      send(...args) {
        sent.push(args);
        const callback = win.onSend;
        if (callback) rendererQueue.push(() => callback(...args));
      },
      setWindowOpenHandler() {},
      on() {},
    },
    on(event, handler) { listeners.set(event, handler); },
    isDestroyed() { return this.destroyed; },
    show() { this.shown = true; },
    close() {
      this.closeCalls += 1;
      let prevented = false;
      const event = { preventDefault() { prevented = true; } };
      this.listeners.get("close")?.(event);
      if (prevented) return;
      this.destroyed = true;
      this.listeners.get("closed")?.();
    },
    getBounds() { return { x: 0, y: 0, width: 800, height: 600 }; },
    loadURL(url) { this.loadedUrl = url; return Promise.resolve(); },
  };
  fakeWindows.push(win);
  return win;
}

function controlledRecord(id, currentUrl = "", loading = false) {
  const calls = [];
  const controls = [];
  const visibility = [];
  const boundsCalls = [];
  let bounds = { x: 0, y: 0, width: 0, height: 0 };
  let closeCalls = 0;
  let targetCalls = 0;
  const nativeCalls = { reload: 0, back: 0, forward: 0 };
  const webContents = {
    getURL: () => currentUrl,
    getTitle: () => id,
    isLoading: () => loading,
    loadURL(url) {
      calls.push(url);
      loading = true;
      return new Promise((resolve, reject) => {
        controls.push({
          resolve() {
            currentUrl = url;
            loading = false;
            resolve();
          },
          reject,
        });
      });
    },
    getOrCreateDevToolsTargetId() {
      targetCalls += 1;
      return `${id}-target`;
    },
    navigationHistory: {
      canGoBack: () => false,
      canGoForward: () => false,
      goBack() { nativeCalls.back += 1; },
      goForward() { nativeCalls.forward += 1; },
    },
    reload() { nativeCalls.reload += 1; },
    close() { closeCalls += 1; },
    setWindowOpenHandler() {},
    on() {},
  };
  const view = {
    webContents,
    setVisible(value) { visibility.push(value); },
    setBounds(value) {
      bounds = { ...value };
      boundsCalls.push({ ...value });
    },
    getBounds() { return { ...bounds }; },
  };
  return {
    record: { id, view, ownerId: null, navigation: null },
    calls,
    controls,
    visibility,
    boundsCalls,
    closeCallCount: () => closeCalls,
    targetCallCount: () => targetCalls,
    nativeCalls,
    currentBounds: () => ({ ...bounds }),
  };
}

function registerContext(id, win) {
  const ctx = hooks.makeWindowContext(id, win);
  hooks.windows.set(id, ctx);
  hooks.contextsByBrowserWindowId.set(win.id, ctx);
  win.on("close", (event) => hooks.tabTransfers?.windowClosing(ctx, event));
  return ctx;
}

function addRecord(ctx, controlled) {
  controlled.record.ownerId = ctx.id;
  ctx.views.set(controlled.record.id, controlled.record);
  return controlled.record;
}

function checkPreloadWindowIdentity() {
  const sent = [];
  const invoked = [];
  let exposed = null;
  const argv = [
    "electron",
    "--openprogram-window-id=window-from-main",
    "--openprogram-window-id=ignored-duplicate",
  ];
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return require(id);
      return {
        contextBridge: {
          exposeInMainWorld(_name, value) { exposed = value; },
        },
        ipcRenderer: {
          send(...args) { sent.push(args); },
          invoke(...args) { invoked.push(args); return Promise.resolve(null); },
          on() {},
          removeListener() {},
        },
      };
    },
  };
  vm.createContext(preloadSandbox);
  vm.runInContext(preloadSource, preloadSandbox, { filename: "desktop/preload.js" });

  assert.equal(exposed.windowId, "window-from-main");
  argv[1] = "--openprogram-window-id=changed-after-preload";
  assert.equal(
    exposed.windowId,
    "window-from-main",
    "preload must parse the window id once",
  );
  const items = [{
    id: "pane-a",
    bounds: { x: 1, y: 2, width: 300, height: 200 },
  }];
  exposed.webTab.syncVisible(items);
  assert.deepEqual(sent.at(-1), ["webtab:sync-visible", items]);
  assert.deepEqual(invoked, []);
}

function checkPreloadTabTransfer() {
  const sentSync = [];
  const invoked = [];
  const listeners = new Map();
  let exposed = null;
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv: ["electron", "--openprogram-window-id=transfer-window"] },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return require(id);
      return {
        contextBridge: {
          exposeInMainWorld(_name, value) { exposed = value; },
        },
        ipcRenderer: {
          send() {},
          sendSync(...args) { sentSync.push(args); return "token-sync"; },
          invoke(...args) { invoked.push(args); return Promise.resolve(true); },
          on(channel, listener) {
            listeners.set(channel, [...(listeners.get(channel) ?? []), listener]);
          },
          removeListener(channel, listener) {
            listeners.set(
              channel,
              (listeners.get(channel) ?? []).filter((item) => item !== listener),
            );
          },
        },
      };
    },
  };
  vm.createContext(preloadSandbox);
  vm.runInContext(preloadSource, preloadSandbox, { filename: "desktop/preload.js" });

  const transfer = exposed.tabTransfer;
  assert.ok(transfer, "preload must expose tabTransfer");
  const payload = { tabs: [], source: {}, fileDrafts: [], chats: [] };
  assert.equal(transfer.prepare(payload), "token-sync");
  assert.deepEqual(sentSync, [["tab-transfer:prepare", payload]]);

  transfer.inspect("tok");
  transfer.accept("tok", { kind: "strip-end" });
  transfer.reject("tok", "duplicate", "w:dup");
  transfer.status("tok");
  transfer.journalOpened("tok", "destination");
  transfer.journalFinalized("tok", "source", "owner-window");
  transfer.destinationReady("tok", true);
  transfer.sourceRemoved("tok", true, false);
  transfer.destinationUndone("tok", true);
  transfer.cancel("tok");
  transfer.detach("tok");
  transfer.claimPending("transfer-window");
  transfer.pendingTerminal("transfer-window");
  // Objects built inside the preload VM have that realm's prototypes;
  // JSON-normalize before the strict deep comparison.
  assert.deepEqual(JSON.parse(JSON.stringify(invoked)), [
    ["tab-transfer:inspect", "tok"],
    ["tab-transfer:accept", "tok", { kind: "strip-end" }],
    ["tab-transfer:reject", "tok", "duplicate", "w:dup"],
    ["tab-transfer:status", "tok"],
    ["tab-transfer:journal-opened", "tok", "destination"],
    ["tab-transfer:journal-finalized", "tok", "source", "owner-window"],
    ["tab-transfer:destination-ready", "tok", true],
    ["tab-transfer:source-removed", "tok", { ok: true, sourceEmpty: false }],
    ["tab-transfer:destination-undone", "tok", true],
    ["tab-transfer:cancel", "tok"],
    ["tab-transfer:detach", "tok"],
    ["tab-transfer:claim-pending", "transfer-window"],
    ["tab-transfer:pending-terminal", "transfer-window"],
  ]);

  const channelBySubscription = {
    onRemoveSource: "tab-transfer:remove-source",
    onUndoDestination: "tab-transfer:undo-destination",
    onCommitted: "tab-transfer:committed",
    onRejected: "tab-transfer:rejected",
    onRolledBack: "tab-transfer:rolled-back",
    onFinalizeOrphaned: "tab-transfer:finalize-orphaned",
  };
  for (const [method, channel] of Object.entries(channelBySubscription)) {
    const received = [];
    const cleanup = transfer[method]((detail) => received.push(detail));
    const registered = listeners.get(channel) ?? [];
    assert.equal(registered.length, 1, `${method} must subscribe ${channel}`);
    registered[0](null, { token: "tok", channel });
    assert.deepEqual(received, [{ token: "tok", channel }]);
    cleanup();
    assert.deepEqual(
      listeners.get(channel),
      [],
      `${method} cleanup must remove its listener`,
    );
  }
}

async function checkLoadView() {
  const win = fakeWindow(1);
  const ctx = registerContext("load-window", win);

  // A request for the committed URL must replace a different pending URL.
  const competing = controlledRecord("tab-competing", "https://example.com/one");
  addRecord(ctx, competing);
  const first = hooks.loadView(competing.record, "https://example.com/two");
  const second = hooks.loadView(competing.record, "https://example.com/one");
  assert.notStrictEqual(first, second);
  assert.deepEqual(competing.calls, [
    "https://example.com/two",
    "https://example.com/one",
  ]);
  assert.strictEqual(competing.record.navigation.promise, second);

  let secondSettled = false;
  void second.then(() => { secondSettled = true; });
  competing.controls[0].resolve();
  assert.strictEqual(await first, competing.record);
  await Promise.resolve();
  assert.equal(secondSettled, false);
  assert.strictEqual(competing.record.navigation.promise, second);
  competing.controls[1].resolve();
  assert.strictEqual(await second, competing.record);
  assert.equal(competing.record.navigation, null);

  // Repeating the same pending URL shares one native load.
  const duplicate = controlledRecord("tab-duplicate", "https://example.com/one");
  addRecord(ctx, duplicate);
  const original = hooks.loadView(duplicate.record, "https://example.com/two");
  const repeated = hooks.loadView(duplicate.record, "https://example.com/two");
  assert.strictEqual(repeated, original);
  assert.deepEqual(duplicate.calls, ["https://example.com/two"]);
  duplicate.controls[0].resolve();
  assert.strictEqual(await repeated, duplicate.record);

  // Native reload/history invalidates a pending load before the next activation.
  const interrupted = controlledRecord(
    "tab-interrupted",
    "https://example.com/one",
    true,
  );
  addRecord(ctx, interrupted);
  const replaced = hooks.loadView(interrupted.record, "https://example.com/one");
  hooks.runNativeNavigation(ctx, "tab-interrupted", (wc) => wc.reload());
  assert.equal(interrupted.record.navigation, null);
  const replacement = hooks.loadView(
    interrupted.record,
    "https://example.com/one",
  );
  assert.notStrictEqual(replaced, replacement);
  assert.deepEqual(interrupted.calls, [
    "https://example.com/one",
    "https://example.com/one",
  ]);
  interrupted.controls[0].resolve();
  assert.strictEqual(await replaced, interrupted.record);
  assert.strictEqual(interrupted.record.navigation.promise, replacement);
  interrupted.controls[1].resolve();
  assert.strictEqual(await replacement, interrupted.record);

  // A stable committed URL uses the zero-navigation path.
  const stable = controlledRecord("tab-stable", "https://example.com/one");
  addRecord(ctx, stable);
  assert.strictEqual(
    await hooks.loadView(stable.record, "https://example.com/one"),
    stable.record,
  );
  assert.deepEqual(stable.calls, []);
}

async function checkVisibleCollectionAndActivation() {
  const win = fakeWindow(2);
  const ctx = registerContext("visible-window", win);
  const a = controlledRecord("a");
  const b = controlledRecord("b");
  addRecord(ctx, a);
  addRecord(ctx, b);
  const boundsA = { x: 10, y: 20, width: 300, height: 400 };
  const boundsB = { x: 310, y: 20, width: 320, height: 400 };

  assert.equal(
    hooks.syncVisibleViews(ctx, [
      { id: "a", bounds: boundsA },
      { id: "b", bounds: boundsB },
    ]),
    true,
  );
  assert.deepEqual([...ctx.visibleViewIds].sort(), ["a", "b"]);
  assert.equal(a.visibility.at(-1), true);
  assert.equal(b.visibility.at(-1), true);
  assert.deepEqual(a.currentBounds(), boundsA);
  assert.deepEqual(b.currentBounds(), boundsB);
  assert.ok(a.currentBounds().width > 0 && b.currentBounds().width > 0);

  assert.equal(hooks.syncVisibleViews(ctx, [{ id: "b", bounds: boundsB }]), true);
  assert.deepEqual([...ctx.visibleViewIds], ["b"]);
  assert.equal(a.visibility.at(-1), false);
  assert.equal(b.visibility.at(-1), true);

  // Compatibility wrappers operate on a copied collection, not a singleton.
  assert.equal(hooks.showView(ctx, "a"), true);
  assert.deepEqual([...ctx.visibleViewIds].sort(), ["a", "b"]);
  assert.equal(hooks.hideView(ctx, "a"), true);
  assert.deepEqual([...ctx.visibleViewIds], ["b"]);
  assert.equal(b.visibility.at(-1), true);

  // Ownership validation happens before any visibility mutation.
  const foreign = controlledRecord("foreign");
  foreign.record.ownerId = "another-window";
  ctx.views.set("foreign", foreign.record);
  const beforeA = a.visibility.length;
  const beforeB = b.visibility.length;
  assert.equal(
    hooks.syncVisibleViews(ctx, [
      { id: "a", bounds: boundsA },
      { id: "foreign", bounds: boundsB },
    ]),
    false,
  );
  assert.equal(a.visibility.length, beforeA);
  assert.equal(b.visibility.length, beforeB);
  assert.deepEqual([...ctx.visibleViewIds], ["b"]);

  // Hiding during navigation prevents a stale activation target from winning.
  const activation = hooks.activateView(ctx, "a", "https://example.com/a");
  hooks.hideView(ctx, "a");
  a.controls[0].resolve();
  assert.equal(await activation, null);
  assert.equal(a.targetCallCount(), 0);

  const active = hooks.activateView(ctx, "a", "https://example.com/a2");
  a.controls[1].resolve();
  assert.equal(await active, "a-target");
  assert.equal(a.targetCallCount(), 1);

  // A completed navigation cannot return a target after ownership moves.
  const moved = controlledRecord("moved");
  addRecord(ctx, moved);
  const destination = registerContext("visible-destination", fakeWindow(20));
  const movingActivation = hooks.activateView(
    ctx,
    "moved",
    "https://example.com/moved",
  );
  ctx.views.delete("moved");
  moved.record.ownerId = destination.id;
  destination.views.set("moved", moved.record);
  moved.controls[0].resolve();
  assert.equal(await movingActivation, null);
  assert.equal(moved.targetCallCount(), 0);
}

async function checkSenderOwnership() {
  hooks.registerWebTabIpc();
  const winA = fakeWindow(3);
  const winB = fakeWindow(4);
  const ctxA = registerContext("window-a", winA);
  const ctxB = registerContext("window-b", winB);
  const a = controlledRecord("owned-a");
  const b = controlledRecord("owned-b");
  addRecord(ctxA, a);
  addRecord(ctxB, b);
  const initialBounds = { x: 1, y: 2, width: 200, height: 100 };
  hooks.syncVisibleViews(ctxA, [{ id: "owned-a", bounds: initialBounds }]);
  hooks.syncVisibleViews(ctxB, [{ id: "owned-b", bounds: initialBounds }]);

  // State events follow the record's current owner instead of a fixed window.
  hooks.sendState(a.record);
  assert.equal(winA.sent.at(-1)[0], "webtab:state");
  ctxA.views.delete("owned-a");
  a.record.ownerId = ctxB.id;
  ctxB.views.set("owned-a", a.record);
  hooks.sendState(a.record);
  assert.equal(winB.sent.at(-1)[0], "webtab:state");
  ctxB.views.delete("owned-a");
  a.record.ownerId = ctxA.id;
  ctxA.views.set("owned-a", a.record);

  const eventB = { sender: winB.webContents };
  // A stale cross-window reference must make every view mutation a no-op.
  ctxB.views.set("owned-a", a.record);
  ipcListeners.get("webtab:navigate")(
    eventB,
    "owned-a",
    "https://example.com/blocked",
  );
  ipcListeners.get("webtab:reload")(eventB, "owned-a");
  ipcListeners.get("webtab:go-back")(eventB, "owned-a");
  ipcListeners.get("webtab:go-forward")(eventB, "owned-a");
  ipcListeners.get("webtab:set-bounds")(
    eventB,
    "owned-a",
    { x: 9, y: 9, width: 999, height: 999 },
  );
  ipcListeners.get("webtab:hide")(eventB, "owned-a");
  ipcListeners.get("webtab:show")(eventB, "owned-a");
  ipcListeners.get("webtab:sync-visible")(eventB, [
    { id: "owned-a", bounds: initialBounds },
  ]);
  ipcListeners.get("webtab:destroy")(eventB, "owned-a");
  assert.equal(
    await ipcHandlers.get("webtab:activate")(
      eventB,
      "owned-a",
      "https://example.com/blocked",
    ),
    null,
  );
  ctxB.views.delete("owned-a");
  assert.deepEqual(a.calls, []);
  assert.deepEqual(a.nativeCalls, { reload: 0, back: 0, forward: 0 });
  assert.deepEqual(a.currentBounds(), initialBounds);
  assert.equal(a.visibility.at(-1), true);
  assert.equal(a.closeCallCount(), 0);
  assert.equal(ctxA.views.has("owned-a"), true);
  assert.deepEqual([...ctxB.visibleViewIds], ["owned-b"]);

  ipcListeners.get("webtab:destroy")({ sender: winA.webContents }, "owned-a");
  assert.equal(a.closeCallCount(), 1);
  assert.equal(ctxA.views.has("owned-a"), false);
  assert.equal(ctxA.visibleViewIds.has("owned-a"), false);

  ipcListeners.get("webtab:ensure")(eventB, "fresh-b", "");
  assert.equal(ctxB.views.get("fresh-b").ownerId, ctxB.id);
  assert.equal(ctxA.views.has("fresh-b"), false);
}

async function checkFocusedRoutingAndCleanup() {
  const winA = fakeWindow(5);
  const winB = fakeWindow(6);
  const ctxA = registerContext("focus-a", winA);
  const ctxB = registerContext("focus-b", winB);

  focusedWindow = winA;
  assert.strictEqual(hooks.focusedContext(), ctxA);
  focusedWindow = winB;
  assert.strictEqual(hooks.focusedContext(), ctxB);
  focusedWindow = null;
  assert.strictEqual(hooks.focusedContext(), ctxB);

  const unrelated = fakeWindow(60);
  focusedWindow = unrelated;
  assert.strictEqual(hooks.focusedContext(), null);

  hooks.buildMenu();
  const fileMenu = menuTemplate.find((entry) => entry.label === "File");
  const newTab = fileMenu.submenu.find((entry) => entry.label === "New Tab");
  const closeTab = fileMenu.submenu.find((entry) => entry.label === "Close Tab");
  focusedWindow = winA;
  newTab.click();
  focusedWindow = winB;
  closeTab.click();
  focusedWindow = null;
  newTab.click();
  assert.deepEqual(winA.sent, [["menu:new-tab"]]);
  assert.deepEqual(winB.sent, [["menu:close-tab"], ["menu:new-tab"]]);

  winB.destroyed = true;
  const fallback = hooks.focusedContext();
  assert.ok(fallback === ctxA || fallback === null);
  assert.notStrictEqual(fallback, ctxB);

  const ctxC = await hooks.createWindow({ windowId: "cleanup-c" });
  const winC = ctxC.win;
  assert.deepEqual(
    Array.from(browserWindowOptions.at(-1).webPreferences.additionalArguments),
    ["--openprogram-window-id=cleanup-c"],
  );
  const mainCtx = await hooks.createWindow();
  assert.equal(mainCtx.id, "main");
  assert.deepEqual(
    Array.from(browserWindowOptions.at(-1).webPreferences.additionalArguments),
    ["--openprogram-window-id=main"],
  );
  mainCtx.win.listeners.get("closed")();
  const owned = controlledRecord("owned-c");
  const foreign = controlledRecord("foreign-c");
  addRecord(ctxC, owned);
  foreign.record.ownerId = "different-owner";
  ctxC.views.set("foreign-c", foreign.record);
  ctxC.visibleViewIds = new Set(["owned-c", "foreign-c"]);
  winC.listeners.get("closed")();
  assert.equal(owned.closeCallCount(), 1);
  assert.equal(foreign.closeCallCount(), 0);
  assert.equal(ctxC.views.size, 0);
  assert.equal(ctxC.visibleViewIds.size, 0);
  assert.equal(hooks.windows.has("cleanup-c"), false);
  assert.equal(hooks.contextsByBrowserWindowId.has(winC.id), false);
}

function eventFor(win) {
  return { sender: win.webContents, returnValue: undefined };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function webTransferPayload(ids, kind = ids.length > 1 ? "group" : "tab") {
  const source = {
    windowId: "spoofed-renderer-window",
    kind,
  };
  if (kind === "group") {
    source.groupId = `group:${ids.join(":")}`;
    source.memberIds = [...ids];
    source.visibleIds = ids.slice(0, 2);
    source.focusedId = ids[0];
  }
  if (kind === "segment") {
    source.groupId = `group:${ids[0]}`;
    source.memberIndex = 0;
    source.memberIds = [ids[0], `${ids[0]}:peer`];
    source.visibleIds = [...source.memberIds];
    source.focusedId = ids[0];
  }
  return {
    tabs: ids.map((id) => ({
      id,
      kind: "web",
      title: `Title ${id}`,
      url: `https://example.com/${id}`,
    })),
    source,
    fileDrafts: [],
    chats: [],
  };
}

function attachControlledRecord(ctx, controlled, bounds, visible = true) {
  const record = addRecord(ctx, controlled);
  ctx.win.contentView.addChildView(record.view);
  record.view.setBounds(bounds);
  record.view.setVisible(visible);
  if (visible) ctx.visibleViewIds.add(record.id);
  return record;
}

function prepareThroughIpc(win, payload) {
  const event = eventFor(win);
  ipcListeners.get("tab-transfer:prepare")(event, payload);
  return event.returnValue;
}

function transferDecisionFile() {
  return path.join(transferUserData, "tab-transfers.json");
}

function loadTransferDecision(token) {
  const { loadTransferDecisions } = require("../tab-transfer-store.js");
  return loadTransferDecisions(transferDecisionFile()).decisions[token] ?? null;
}

function installOneShotRenameFailure() {
  const original = fs.renameSync;
  let failed = false;
  fs.renameSync = function failOnce(...args) {
    if (!failed) {
      failed = true;
      throw new Error("injected durable acknowledgement failure");
    }
    return original.apply(this, args);
  };
  return () => { fs.renameSync = original; };
}

function installCommittedDecisionWriteFailure(token) {
  const originalRename = fs.renameSync;
  let failures = 0;
  fs.renameSync = function failCommittedWrites(...args) {
    let committedWrite = false;
    try {
      const parsed = JSON.parse(fs.readFileSync(args[0], "utf8"));
      committedWrite = parsed?.decisions?.[token]?.status === "committed";
    } catch (_error) {
      /* non-store renames are irrelevant to this injection */
    }
    if (committedWrite) {
      failures += 1;
      throw new Error("injected committed decision write failure");
    }
    return originalRename.apply(this, args);
  };
  return {
    restore() { fs.renameSync = originalRename; },
    failures() { return failures; },
  };
}

function installReadFailureAt(failAt) {
  const original = fs.readFileSync;
  let calls = 0;
  let triggered = false;
  fs.readFileSync = function failReadAt(...args) {
    calls += 1;
    if (calls === failAt) {
      triggered = true;
      throw new Error(`injected read failure at call ${failAt}`);
    }
    return original.apply(this, args);
  };
  return {
    restore() { fs.readFileSync = original; },
    triggered() { return triggered; },
    calls() { return calls; },
  };
}

function installReadFailureWhenDecisionMissing(token) {
  const original = fs.readFileSync;
  const decisionPath = path.resolve(transferDecisionFile());
  let triggered = false;
  fs.readFileSync = function failAfterDurableDelete(...args) {
    const result = original.apply(this, args);
    if (path.resolve(String(args[0])) !== decisionPath) return result;
    try {
      const parsed = JSON.parse(Buffer.isBuffer(result) ? result.toString("utf8") : result);
      if (!parsed?.decisions?.[token]) {
        triggered = true;
        throw new Error("injected read failure after durable decision deletion");
      }
    } catch (error) {
      if (triggered) throw error;
    }
    return result;
  };
  return {
    restore() { fs.readFileSync = original; },
    triggered() { return triggered; },
  };
}

function installAmbiguousCommitFailure(token, { blockReconcileReads = false } = {}) {
  const originalFsync = fs.fsyncSync;
  const originalRename = fs.renameSync;
  const originalRead = fs.readFileSync;
  let fsyncCalls = 0;
  let renameCalls = 0;
  let ambiguous = false;
  let sawCommitted = false;
  let reconcileReadFailures = 0;

  fs.fsyncSync = function failDirectorySync(...args) {
    fsyncCalls += 1;
    if (fsyncCalls === 2) {
      throw new Error("injected directory fsync failure after committed rename");
    }
    return originalFsync.apply(this, args);
  };
  fs.renameSync = function failPriorRestore(...args) {
    renameCalls += 1;
    if (renameCalls === 2) {
      ambiguous = true;
      throw new Error("injected prior-decision restore failure");
    }
    return originalRename.apply(this, args);
  };
  fs.readFileSync = function observeCommitted(...args) {
    if (ambiguous && blockReconcileReads) {
      reconcileReadFailures += 1;
      throw new Error("injected indeterminate commit reconciliation read failure");
    }
    const result = originalRead.apply(this, args);
    if (ambiguous) {
      try {
        const parsed = JSON.parse(Buffer.isBuffer(result) ? result.toString("utf8") : result);
        if (parsed?.decisions?.[token]?.status === "committed") sawCommitted = true;
      } catch (_error) {
        /* non-JSON reads are irrelevant to this injection */
      }
    }
    return result;
  };

  return {
    restore() {
      fs.fsyncSync = originalFsync;
      fs.renameSync = originalRename;
      fs.readFileSync = originalRead;
    },
    releaseReconcileReads() { blockReconcileReads = false; },
    sawCommitted() { return sawCommitted; },
    reconcileReadFailures() { return reconcileReadFailures; },
  };
}

function assertTransferApiRegistered() {
  assert.equal(
    typeof hooks.makeTransferCoordinator,
    "function",
    "main must expose an executable transfer coordinator to the harness",
  );
  assert.equal(typeof hooks.registerTabTransferIpc, "function");
  assert.equal(typeof hooks.validateTransferPayload, "function");
  assert.equal(typeof hooks.reparentRecords, "function");
  assert.equal(typeof hooks.restoreRecords, "function");
  assert.ok(hooks.tabTransfers);

  hooks.registerTabTransferIpc();
  assert.equal(typeof ipcListeners.get("tab-transfer:prepare"), "function");
  for (const channel of [
    "tab-transfer:inspect",
    "tab-transfer:accept",
    "tab-transfer:reject",
    "tab-transfer:status",
    "tab-transfer:journal-opened",
    "tab-transfer:journal-finalized",
    "tab-transfer:destination-ready",
    "tab-transfer:source-removed",
    "tab-transfer:destination-undone",
    "tab-transfer:cancel",
    "tab-transfer:detach",
    "tab-transfer:claim-pending",
    "tab-transfer:pending-terminal",
  ]) {
    assert.equal(typeof ipcHandlers.get(channel), "function", `missing ${channel}`);
  }
}

async function checkTransferPreparationValidationAndAuthorization() {
  const sourceWin = fakeWindow(70);
  const destinationWin = fakeWindow(71);
  const unrelatedWin = fakeWindow(72);
  const sourceCtx = registerContext("transfer-validation-source", sourceWin);
  const destinationCtx = registerContext("transfer-validation-destination", destinationWin);
  registerContext("transfer-validation-unrelated", unrelatedWin);
  const native = controlledRecord("validation-web");
  attachControlledRecord(
    sourceCtx,
    native,
    { x: 10, y: 20, width: 400, height: 300 },
  );

  const token = prepareThroughIpc(sourceWin, webTransferPayload(["validation-web"]));
  assert.equal(typeof token, "string");
  const inspected = await ipcHandlers.get("tab-transfer:inspect")(
    eventFor(destinationWin),
    token,
  );
  assert.equal(inspected.sourceId, sourceCtx.id);
  assert.equal(inspected.payload.source.windowId, sourceCtx.id);
  assert.equal(
    await ipcHandlers.get("tab-transfer:status")(eventFor(unrelatedWin), token),
    null,
  );
  assert.equal(
    await ipcHandlers.get("tab-transfer:reject")(
      eventFor(unrelatedWin),
      token,
      "duplicate",
      "elsewhere",
    ),
    null,
  );
  assert.equal(
    await ipcHandlers.get("tab-transfer:cancel")(eventFor(sourceWin), token),
    true,
  );
  assert.equal(hooks.tabTransfers.status(sourceCtx, token), null);
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, token), false);
  assert.strictEqual(sourceCtx.views.get("validation-web"), native.record);
  assert.equal(native.closeCallCount(), 0);

  const invalidPayloads = [
    { ...webTransferPayload(["a"]), tabs: [] },
    webTransferPayload(["a", "b", "c", "d"]),
    {
      ...webTransferPayload(["a", "b"]),
      tabs: [
        webTransferPayload(["a"]).tabs[0],
        webTransferPayload(["a"]).tabs[0],
      ],
    },
    {
      ...webTransferPayload(["a"]),
      tabs: [{ id: "a", kind: "unknown", title: "A" }],
    },
    {
      ...webTransferPayload(["a"]),
      tabs: [{ id: "x".repeat(4097), kind: "web", title: "A", url: "https://a.test" }],
    },
    {
      ...webTransferPayload(["a"]),
      tabs: [{ id: "a", kind: "web", title: "x".repeat(4097), url: "https://a.test" }],
    },
    {
      ...webTransferPayload(["a"]),
      tabs: [{ id: "a", kind: "web", title: "A", url: `https://a.test/${"x".repeat(16385)}` }],
    },
    {
      ...webTransferPayload(["a"]),
      fileDrafts: [{ key: "draft:a", value: "x".repeat(2 * 1024 * 1024 + 1) }],
    },
    {
      ...webTransferPayload(["a"]),
      chats: [{
        chatKey: "chat:a",
        pendingProjectId: "x".repeat(16 * 1024 + 1),
      }],
    },
    {
      ...webTransferPayload(["a", "b"]),
      source: {
        windowId: "spoofed",
        kind: "group",
        groupId: "bad-group",
        memberIds: ["a", "missing"],
        visibleIds: ["a"],
        focusedId: "a",
      },
    },
  ];
  for (const payload of invalidPayloads) {
    assert.equal(prepareThroughIpc(sourceWin, payload), null);
  }

  const boundaryPayload = webTransferPayload(["draft-boundary"]);
  boundaryPayload.fileDrafts = [{
    key: "draft:boundary",
    value: "x".repeat(2 * 1024 * 1024),
  }];
  boundaryPayload.chats = [{
    chatKey: "chat:boundary",
    composerDraft: "x".repeat(2 * 1024 * 1024),
  }];
  const boundaryToken = prepareThroughIpc(sourceWin, boundaryPayload);
  assert.equal(typeof boundaryToken, "string");
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, boundaryToken), true);

  const manyUnknownFields = Object.fromEntries(
    Array.from({ length: 128 }, (_, index) => [`extra-${index}`, `value-${index}`]),
  );
  const whitelistedPayload = webTransferPayload(["whitelist-payload"]);
  whitelistedPayload.extraRoot = manyUnknownFields;
  whitelistedPayload.tabs[0] = {
    ...whitelistedPayload.tabs[0],
    draft: true,
    dirty: true,
    extraTab: manyUnknownFields,
  };
  whitelistedPayload.source.extraSource = manyUnknownFields;
  whitelistedPayload.fileDrafts = [{
    key: "project:file.txt",
    extraEntry: manyUnknownFields,
    value: {
      draft: "edited",
      baselineContent: "base",
      baselineMtime: 4,
      extraFileDraft: manyUnknownFields,
    },
  }];
  whitelistedPayload.chats = [{
    chatKey: "local_whitelist",
    composerDraft: "draft",
    wasActive: true,
    composerSettings: {
      thinking: "high",
      tools: true,
      webSearch: false,
      fast: false,
      permission_mode: "ask",
      unattended: false,
      extraSettings: manyUnknownFields,
    },
    draftChannelChoice: {
      channel: "chat",
      account_id: "account",
      extraChoice: manyUnknownFields,
    },
    extraChat: manyUnknownFields,
  }];
  const whitelistedToken = prepareThroughIpc(sourceWin, whitelistedPayload);
  assert.equal(typeof whitelistedToken, "string");
  const whitelistedInspect = hooks.tabTransfers.inspect(
    destinationCtx,
    whitelistedToken,
  );
  assert.deepEqual(plain(whitelistedInspect.payload), {
    tabs: [{
      id: "whitelist-payload",
      kind: "web",
      title: "Title whitelist-payload",
      url: "https://example.com/whitelist-payload",
      draft: true,
      dirty: true,
    }],
    source: {
      windowId: sourceCtx.id,
      kind: "tab",
    },
    fileDrafts: [{
      key: "project:file.txt",
      value: {
        draft: "edited",
        baselineContent: "base",
        baselineMtime: 4,
      },
    }],
    chats: [{
      chatKey: "local_whitelist",
      composerDraft: "draft",
      composerSettings: {
        thinking: "high",
        tools: true,
        webSearch: false,
        fast: false,
        permission_mode: "ask",
        unattended: false,
      },
      draftChannelChoice: {
        channel: "chat",
        account_id: "account",
      },
      wasActive: true,
    }],
  });
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, whitelistedToken), true);

  const tooManyFileDrafts = webTransferPayload(["too-many-file-drafts"]);
  tooManyFileDrafts.fileDrafts = Array.from({ length: 4 }, (_, index) => ({
    key: `draft:${index}`,
    value: "value",
  }));
  assert.equal(prepareThroughIpc(sourceWin, tooManyFileDrafts), null);

  const excessiveFileDraftBytes = webTransferPayload(["file-draft-total"]);
  excessiveFileDraftBytes.fileDrafts = Array.from({ length: 3 }, (_, index) => ({
    key: `draft:${index}`,
    value: "x".repeat(2 * 1024 * 1024),
  }));
  assert.equal(prepareThroughIpc(sourceWin, excessiveFileDraftBytes), null);

  const excessiveRawPayload = webTransferPayload(["raw-payload-limit"]);
  excessiveRawPayload.extraRoot = "x".repeat(20 * 1024 * 1024 + 1);
  assert.equal(prepareThroughIpc(sourceWin, excessiveRawPayload), null);

  const foreign = controlledRecord("foreign-native");
  foreign.record.ownerId = destinationCtx.id;
  sourceCtx.views.set("foreign-native", foreign.record);
  assert.equal(
    prepareThroughIpc(sourceWin, webTransferPayload(["foreign-native"])),
    null,
  );
  sourceCtx.views.delete("foreign-native");
}

async function checkSuccessfulTransferAndDurableCommit() {
  const sourceWin = fakeWindow(80);
  const destinationWin = fakeWindow(81);
  const sourceCtx = registerContext("success-source", sourceWin);
  const destinationCtx = registerContext("success-destination", destinationWin);
  const first = controlledRecord("success-a");
  const second = controlledRecord("success-b");
  attachControlledRecord(sourceCtx, first, { x: 1, y: 2, width: 300, height: 400 });
  attachControlledRecord(sourceCtx, second, { x: 301, y: 2, width: 320, height: 400 });

  const successPayload = webTransferPayload(["success-a", "success-b"]);
  successPayload.fileDrafts = [{ key: "draft:success-a", value: "source draft" }];
  successPayload.chats = [{
    chatKey: "success-chat",
    composerDraft: "source composer",
    wasActive: true,
  }];
  const sourceRenderer = {
    centerTabs: new Map(successPayload.tabs.map((tab) => [tab.id, plain(tab)])),
    fileDrafts: new Map(successPayload.fileDrafts.map((draft) => [draft.key, draft.value])),
    session: new Map(successPayload.chats.map((chat) => [chat.chatKey, plain(chat)])),
    bridge: new Set(["success-a", "success-b"]),
    reversible: null,
  };
  const destinationRenderer = {
    centerTabs: new Map(),
    fileDrafts: new Map(),
    session: new Map(),
    bridge: new Set(),
    provisionalTokens: new Set(),
    committedTokens: new Set(),
  };
  const trace = [];
  const token = prepareThroughIpc(
    sourceWin,
    successPayload,
  );
  const expirationTimer = hooks.tabTransfers.activeTransfers.get(token).timer;
  trace.push("destination validation");
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, token));
  assert.equal(hooks.tabTransfers.journalOpened(destinationCtx, token, "destination"), true);
  const accepted = hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" });
  assert.equal(accepted.status, "destination-staged");
  assert.strictEqual(destinationCtx.views.get("success-a"), first.record);
  assert.strictEqual(destinationCtx.views.get("success-b"), second.record);
  assert.equal(first.record.ownerId, destinationCtx.id);
  assert.equal(second.record.ownerId, destinationCtx.id);
  assert.equal(hooks.tabTransfers.inspect(destinationCtx, token), null);
  assert.equal(
    hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }),
    null,
  );
  assert.equal(sourceCtx.views.has("success-a"), false);
  assert.equal(sourceCtx.views.has("success-b"), false);
  assert.equal(hooks.tabTransfers.isLocked("success-a"), true);
  assert.equal(hooks.tabTransfers.isLocked("success-b"), true);
  trace.push("native destination ownership");

  for (const tab of accepted.payload.tabs) {
    destinationRenderer.centerTabs.set(tab.id, plain(tab));
  }
  for (const draft of accepted.payload.fileDrafts) {
    destinationRenderer.fileDrafts.set(draft.key, draft.value);
  }
  for (const chat of accepted.payload.chats) {
    destinationRenderer.session.set(chat.chatKey, plain(chat));
  }
  for (const id of accepted.recordIds) destinationRenderer.bridge.add(id);
  destinationRenderer.provisionalTokens.add(token);
  assert.deepEqual([...destinationRenderer.centerTabs.keys()], ["success-a", "success-b"]);
  assert.deepEqual([...destinationRenderer.fileDrafts], [["draft:success-a", "source draft"]]);
  assert.deepEqual([...destinationRenderer.session.keys()], ["success-chat"]);
  assert.deepEqual([...destinationRenderer.bridge], ["success-a", "success-b"]);
  assert.deepEqual([...destinationRenderer.provisionalTokens], [token]);
  trace.push("destination provisional insertion");
  let sourceRemovedResult = null;
  destinationWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:committed" || item.token !== token) return;
    destinationRenderer.provisionalTokens.delete(token);
    destinationRenderer.committedTokens.add(token);
    assert.deepEqual([...destinationRenderer.centerTabs.keys()], ["success-a", "success-b"]);
    assert.deepEqual(
      [...destinationRenderer.fileDrafts],
      [["draft:success-a", "source draft"]],
    );
    assert.deepEqual([...destinationRenderer.session.keys()], ["success-chat"]);
    assert.deepEqual([...destinationRenderer.bridge], ["success-a", "success-b"]);
    assert.deepEqual([...destinationRenderer.provisionalTokens], []);
    assert.deepEqual([...destinationRenderer.committedTokens], [token]);
    trace.push("main committed");
    assert.equal(hooks.tabTransfers.activeTransfers.has(token), false);
    assert.equal(hooks.tabTransfers.isLocked("success-a"), false);
    assert.equal(hooks.tabTransfers.isLocked("success-b"), false);
    assert.equal(clock.pendingIds().includes(expirationTimer), false);
    assert.equal(hooks.tabTransfers.status(destinationCtx, token).status, "committed");
    assert.ok(loadTransferDecision(token));
    const committedStoreBytes = fs.readFileSync(transferDecisionFile());
    assert.equal(hooks.tabTransfers.rollback(token, "manual-after-commit"), false);
    clock.runCleared(expirationTimer);
    assert.deepEqual(fs.readFileSync(transferDecisionFile()), committedStoreBytes);
    assert.equal(first.record.ownerId, destinationCtx.id);
    assert.equal(second.record.ownerId, destinationCtx.id);
    assert.equal(
      hooks.tabTransfers.journalFinalized(
        destinationCtx,
        token,
        "destination",
      ),
      true,
    );
    trace.push("destination durable finalize");
  };
  sourceWin.onSend = (channel, item) => {
    if (item.token !== token) return;
    if (channel === "tab-transfer:remove-source") {
      sourceRenderer.reversible = {
        centerTabs: [...sourceRenderer.centerTabs],
        fileDrafts: [...sourceRenderer.fileDrafts],
        session: [...sourceRenderer.session],
        bridge: [...sourceRenderer.bridge],
      };
      for (const tab of item.payload.tabs) sourceRenderer.centerTabs.delete(tab.id);
      for (const draft of item.payload.fileDrafts) {
        sourceRenderer.fileDrafts.delete(draft.key);
      }
      for (const chat of item.payload.chats) sourceRenderer.session.delete(chat.chatKey);
      for (const tab of item.payload.tabs) sourceRenderer.bridge.delete(tab.id);
      assert.deepEqual([...sourceRenderer.centerTabs], []);
      assert.deepEqual([...sourceRenderer.fileDrafts], []);
      assert.deepEqual([...sourceRenderer.session], []);
      assert.deepEqual([...sourceRenderer.bridge], []);
      trace.push("source reversible removal");
      assert.equal(hooks.tabTransfers.journalOpened(sourceCtx, token, "source"), true);
      sourceRemovedResult = hooks.tabTransfers.sourceRemoved(
        sourceCtx,
        token,
        { ok: true, sourceEmpty: false },
      );
      return;
    }
    if (channel !== "tab-transfer:committed") return;
    sourceRenderer.reversible = null;
    assert.deepEqual([...sourceRenderer.centerTabs], []);
    assert.deepEqual([...sourceRenderer.fileDrafts], []);
    assert.deepEqual([...sourceRenderer.session], []);
    assert.deepEqual([...sourceRenderer.bridge], []);
    assert.equal(hooks.tabTransfers.status(sourceCtx, token).status, "committed");
    assert.deepEqual(loadTransferDecision(token).finalizedRoles, [
      { role: "destination", windowId: destinationCtx.id },
    ]);
    assert.equal(
      hooks.tabTransfers.journalFinalized(sourceCtx, token, "source"),
      true,
    );
    trace.push("source durable finalize");
  };
  assert.equal(hooks.tabTransfers.destinationReady(destinationCtx, token, true), true);
  trace.push("destination-ready");
  flushRendererQueue();
  assert.equal(sourceRemovedResult, true);
  assert.equal(hooks.tabTransfers.activeTransfers.has(token), false);
  assert.equal(hooks.tabTransfers.isLocked("success-a"), false);
  assert.equal(hooks.tabTransfers.isLocked("success-b"), false);
  assert.equal(clock.pendingIds().includes(expirationTimer), false);
  assert.equal(first.record.ownerId, destinationCtx.id);
  assert.equal(second.record.ownerId, destinationCtx.id);
  assert.equal(hooks.tabTransfers.inspect(destinationCtx, token), null);
  assert.equal(
    hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }),
    null,
  );
  assert.equal(hooks.tabTransfers.destinationReady(destinationCtx, token, true), false);
  assert.equal(
    hooks.tabTransfers.sourceRemoved(
      sourceCtx,
      token,
      { ok: true, sourceEmpty: false },
    ),
    false,
  );
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, token), false);
  assert.equal(hooks.tabTransfers.status(sourceCtx, token), null);
  assert.equal(loadTransferDecision(token), null);
  assert.deepEqual(trace, [
    "destination validation",
    "native destination ownership",
    "destination provisional insertion",
    "destination-ready",
    "source reversible removal",
    "main committed",
    "destination durable finalize",
    "source durable finalize",
  ]);
  assert.equal(first.closeCallCount(), 0);
  assert.equal(second.closeCallCount(), 0);
}

async function stageTransferForRollback(prefix, { ready = true } = {}) {
  const sourceWin = fakeWindow(nextGeneratedWindowId++);
  const destinationWin = fakeWindow(nextGeneratedWindowId++);
  const sourceCtx = registerContext(`${prefix}-source`, sourceWin);
  const destinationCtx = registerContext(`${prefix}-destination`, destinationWin);
  const controlled = controlledRecord(`${prefix}-web`);
  const originalBounds = { x: 17, y: 19, width: 410, height: 330 };
  attachControlledRecord(sourceCtx, controlled, originalBounds);
  const token = prepareThroughIpc(sourceWin, webTransferPayload([`${prefix}-web`]));
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, token));
  assert.equal(hooks.tabTransfers.journalOpened(destinationCtx, token, "destination"), true);
  assert.ok(hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }));
  assert.equal(hooks.tabTransfers.journalOpened(sourceCtx, token, "source"), true);
  if (ready) {
    assert.equal(hooks.tabTransfers.destinationReady(destinationCtx, token, true), true);
  }
  return {
    sourceWin,
    destinationWin,
    sourceCtx,
    destinationCtx,
    controlled,
    originalBounds,
    token,
    timer: hooks.tabTransfers.activeTransfers.get(token).timer,
  };
}

async function checkLockedRecordsRejectOrdinaryIpc() {
  const transaction = await stageTransferForRollback("locked-ipc");
  const { controlled, destinationCtx, destinationWin, sourceCtx, sourceWin, token } =
    transaction;
  const boundsBefore = controlled.currentBounds();
  const visibilityCallsBefore = controlled.visibility.length;
  const generatedBefore = generatedNativeViews;

  ipcListeners.get("webtab:ensure")(
    eventFor(sourceWin),
    "locked-ipc-web",
    "https://example.com/stale-source",
  );
  ipcListeners.get("webtab:navigate")(
    eventFor(sourceWin),
    "locked-ipc-web",
    "https://example.com/stale-source",
  );
  assert.equal(
    await ipcHandlers.get("webtab:activate")(
      eventFor(sourceWin),
      "locked-ipc-web",
      "https://example.com/stale-source",
    ),
    null,
  );
  ipcListeners.get("webtab:set-bounds")(
    eventFor(destinationWin),
    "locked-ipc-web",
    { x: 999, y: 999, width: 999, height: 999 },
  );
  ipcListeners.get("webtab:reload")(eventFor(destinationWin), "locked-ipc-web");
  ipcListeners.get("webtab:go-back")(eventFor(destinationWin), "locked-ipc-web");
  ipcListeners.get("webtab:go-forward")(eventFor(destinationWin), "locked-ipc-web");
  ipcListeners.get("webtab:show")(eventFor(destinationWin), "locked-ipc-web");
  ipcListeners.get("webtab:hide")(eventFor(destinationWin), "locked-ipc-web");
  ipcListeners.get("webtab:sync-visible")(eventFor(destinationWin), [{
    id: "locked-ipc-web",
    bounds: { x: 999, y: 999, width: 999, height: 999 },
  }]);
  ipcListeners.get("webtab:navigate")(
    eventFor(destinationWin),
    "locked-ipc-web",
    "https://example.com/stale-destination",
  );
  ipcListeners.get("webtab:destroy")(eventFor(destinationWin), "locked-ipc-web");
  assert.equal(
    await ipcHandlers.get("webtab:activate")(
      eventFor(destinationWin),
      "locked-ipc-web",
      "https://example.com/stale-destination",
    ),
    null,
  );

  assert.equal(generatedNativeViews, generatedBefore);
  assert.equal(sourceCtx.views.has("locked-ipc-web"), false);
  assert.strictEqual(destinationCtx.views.get("locked-ipc-web"), controlled.record);
  assert.deepEqual(controlled.calls, []);
  assert.deepEqual(controlled.nativeCalls, { reload: 0, back: 0, forward: 0 });
  assert.deepEqual(controlled.currentBounds(), boundsBefore);
  assert.equal(controlled.visibility.length, visibilityCallsBefore);
  assert.equal(controlled.closeCallCount(), 0);

  assert.equal(hooks.tabTransfers.sourceRemoved(sourceCtx, token, { ok: false }), true);
  assert.equal(hooks.tabTransfers.destinationUndone(destinationCtx, token, true), true);
  await finalizeBoth(transaction);
}

async function finalizeBoth(transaction) {
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      transaction.destinationCtx,
      transaction.token,
      "destination",
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      transaction.sourceCtx,
      transaction.token,
      "source",
    ),
    true,
  );
}

async function checkRollbackOrderingAndLateSourceRace() {
  const transaction = await stageTransferForRollback("rollback-order");
  const trace = [];
  const sourceState = {
    tabs: new Set(),
    drafts: new Map(),
    session: new Set(),
  };
  const destinationState = {
    tabs: new Set(["rollback-order-web"]),
    drafts: new Map([["draft:rollback-order-web", "changed"]]),
    ready: new Set(["rollback-order-web"]),
    bounds: new Map([["rollback-order-web", { width: 100 }]]),
    bridge: new Set(["rollback-order-web"]),
  };
  const sourceBridge = new Set();
  const closeBefore = transaction.controlled.closeCallCount();
  transaction.destinationWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:undo-destination" || item.token !== transaction.token) {
      return;
    }
    const active = hooks.tabTransfers.activeTransfers.get(transaction.token);
    assert.notEqual(active.undoTimer, null);
    assert.equal(clock.pendingIds().includes(active.undoTimer), true);
    destinationState.bridge.delete("rollback-order-web");
    destinationState.ready.delete("rollback-order-web");
    destinationState.bounds.delete("rollback-order-web");
    trace.push("forgetTransferredWebView/clear ready+bounds");
    ipcListeners.get("webtab:destroy")(
      eventFor(transaction.destinationWin),
      "rollback-order-web",
    );
    assert.equal(transaction.controlled.closeCallCount(), closeBefore);
    assert.equal(transaction.destinationCtx.views.has("rollback-order-web"), true);
    trace.push("stale store-subscription webtab:destroy rejected");
    destinationState.tabs.delete("rollback-order-web");
    destinationState.drafts.clear();
    trace.push("transient store/session undo");
    trace.push("destination-undone acknowledgement");
    assert.equal(
      hooks.tabTransfers.destinationUndone(
        transaction.destinationCtx,
        transaction.token,
        true,
      ),
      true,
    );
  };
  transaction.sourceWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:rolled-back" || item.token !== transaction.token) return;
    assert.equal(transaction.controlled.record.ownerId, transaction.sourceCtx.id);
    trace.push("native reparent-to-source");
    sourceBridge.add("rollback-order-web");
    trace.push("source bridge restore");
  };
  sourceState.tabs.add("rollback-order-web");
  sourceState.drafts.set("draft:rollback-order-web", "source draft");
  sourceState.session.add("rollback-order-session");
  trace.push("source recovery");
  assert.equal(
    hooks.tabTransfers.sourceRemoved(
      transaction.sourceCtx,
      transaction.token,
      { ok: false, sourceEmpty: false },
    ),
    true,
  );
  const rollbackUndoTimer = hooks.tabTransfers.activeTransfers.get(
    transaction.token,
  ).undoTimer;
  assert.notEqual(rollbackUndoTimer, null);
  assert.equal(clock.pendingIds().includes(rollbackUndoTimer), true);
  assert.equal(transaction.controlled.record.ownerId, transaction.destinationCtx.id);
  flushRendererQueue();
  assert.equal(clock.pendingIds().includes(rollbackUndoTimer), false);
  assert.strictEqual(
    transaction.sourceCtx.views.get("rollback-order-web"),
    transaction.controlled.record,
  );
  assert.equal(transaction.controlled.record.ownerId, transaction.sourceCtx.id);
  assert.deepEqual(transaction.controlled.currentBounds(), transaction.originalBounds);
  assert.equal(transaction.sourceCtx.visibleViewIds.has("rollback-order-web"), true);
  assert.equal(transaction.sourceWin.sent.at(-1)[0], "tab-transfer:rolled-back");
  assert.equal(hooks.tabTransfers.activeTransfers.has(transaction.token), false);
  assert.equal(hooks.tabTransfers.isLocked("rollback-order-web"), false);
  assert.deepEqual([...destinationState.tabs], []);
  assert.deepEqual([...destinationState.drafts], []);
  assert.deepEqual([...destinationState.ready], []);
  assert.deepEqual([...destinationState.bounds], []);
  assert.deepEqual([...destinationState.bridge], []);
  assert.deepEqual([...sourceState.tabs], ["rollback-order-web"]);
  assert.deepEqual([...sourceState.drafts], [["draft:rollback-order-web", "source draft"]]);
  assert.deepEqual([...sourceState.session], ["rollback-order-session"]);
  assert.deepEqual([...sourceBridge], ["rollback-order-web"]);
  assert.deepEqual(trace, [
    "source recovery",
    "forgetTransferredWebView/clear ready+bounds",
    "stale store-subscription webtab:destroy rejected",
    "transient store/session undo",
    "destination-undone acknowledgement",
    "native reparent-to-source",
    "source bridge restore",
  ]);
  await finalizeBoth(transaction);

  const race = await stageTransferForRollback("timeout-race", { ready: false });
  let pendingSourceRemoval = null;
  const sourceRecovery = { restored: false };
  race.sourceWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:remove-source" || item.token !== race.token) return;
    pendingSourceRemoval = () => {
      const accepted = hooks.tabTransfers.sourceRemoved(
        race.sourceCtx,
        race.token,
        { ok: true, sourceEmpty: false },
      );
      if (!accepted) sourceRecovery.restored = true;
      return accepted;
    };
  };
  race.destinationWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:undo-destination" || item.token !== race.token) return;
    assert.equal(
      hooks.tabTransfers.destinationUndone(race.destinationCtx, race.token, true),
      true,
    );
  };
  assert.equal(hooks.tabTransfers.destinationReady(race.destinationCtx, race.token, true), true);
  flushRendererQueue();
  assert.equal(typeof pendingSourceRemoval, "function");
  clock.runCleared(race.timer);
  const raceUndoTimer = hooks.tabTransfers.activeTransfers.get(race.token).undoTimer;
  assert.notEqual(raceUndoTimer, null);
  flushRendererQueue();
  assert.equal(clock.pendingIds().includes(raceUndoTimer), false);
  assert.equal(pendingSourceRemoval(), false);
  assert.equal(sourceRecovery.restored, true);
  assert.equal(race.controlled.record.ownerId, race.sourceCtx.id);
  assert.equal(race.controlled.closeCallCount(), 0);
  await finalizeBoth(race);
}

async function checkDestinationUndoDeadlineDelegatesJournal() {
  const transaction = await stageTransferForRollback("undo-deadline");
  assert.equal(
    hooks.tabTransfers.sourceRemoved(
      transaction.sourceCtx,
      transaction.token,
      { ok: false, sourceEmpty: false },
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      transaction.destinationCtx,
      transaction.token,
      false,
    ),
    false,
  );
  assert.equal(hooks.tabTransfers.activeTransfers.has(transaction.token), true);
  assert.equal(transaction.controlled.record.ownerId, transaction.destinationCtx.id);
  clock.advance(1_999);
  assert.equal(hooks.tabTransfers.activeTransfers.has(transaction.token), true);
  clock.advance(1);

  assert.equal(hooks.tabTransfers.activeTransfers.has(transaction.token), false);
  assert.equal(transaction.controlled.record.ownerId, transaction.sourceCtx.id);
  const delegated = transaction.sourceWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === transaction.token
      && item.role === "destination",
  );
  assert.ok(delegated, "destination undo timeout must delegate its journal role");
  assert.deepEqual(plain(delegated[1]), {
    token: transaction.token,
    status: "rolled-back",
    role: "destination",
    windowId: transaction.destinationCtx.id,
    orphaned: true,
  });
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      transaction.sourceCtx,
      transaction.token,
      "source",
    ),
    true,
  );
  const sentCounts = new Map(fakeWindows.map((win) => [win, win.sent.length]));
  transaction.sourceWin.destroyed = true;
  hooks.tabTransfers.contextDestroyed(transaction.sourceCtx);
  const replacementWin = fakeWindows.find((win) =>
    win.sent.slice(sentCounts.get(win) || 0).some(
      ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
        && item.token === transaction.token
        && item.role === "destination",
    ));
  assert.ok(replacementWin, "a surviving renderer must receive the reassigned role");
  const replacementCtx = hooks.contextsByBrowserWindowId.get(replacementWin.id);
  assert.ok(replacementCtx);
  const reassigned = hooks.tabTransfers.pendingTerminal(
    replacementCtx,
    replacementCtx.id,
  );
  assert.deepEqual(plain(reassigned), [{
    token: transaction.token,
    status: "rolled-back",
    sourceId: transaction.sourceCtx.id,
    destinationId: transaction.destinationCtx.id,
    role: "destination",
    windowId: transaction.destinationCtx.id,
    orphaned: true,
  }]);
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      replacementCtx,
      transaction.token,
      "destination",
      transaction.destinationCtx.id,
    ),
    true,
  );
  assert.equal(loadTransferDecision(transaction.token), null);
}

async function checkAtomicReparentAndMetadataOnlyTransfer() {
  const sourceWin = fakeWindow(90);
  const destinationWin = fakeWindow(91);
  const sourceCtx = registerContext("atomic-source", sourceWin);
  const destinationCtx = registerContext("atomic-destination", destinationWin);
  const first = controlledRecord("atomic-a");
  const second = controlledRecord("atomic-b");
  attachControlledRecord(sourceCtx, first, { x: 1, y: 1, width: 200, height: 200 });
  attachControlledRecord(sourceCtx, second, { x: 201, y: 1, width: 200, height: 200 });
  destinationCtx.visibleViewIds = new Set(["destination-existing", "atomic-a"]);
  const sourceVisibleBefore = [...sourceCtx.visibleViewIds];
  const destinationVisibleBefore = [...destinationCtx.visibleViewIds];
  const token = prepareThroughIpc(sourceWin, webTransferPayload(["atomic-a", "atomic-b"]));
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, token));
  destinationWin.failAddAt = 2;
  assert.equal(hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }), null);
  assert.strictEqual(sourceCtx.views.get("atomic-a"), first.record);
  assert.strictEqual(sourceCtx.views.get("atomic-b"), second.record);
  assert.equal(first.record.ownerId, sourceCtx.id);
  assert.equal(second.record.ownerId, sourceCtx.id);
  assert.equal(destinationCtx.views.has("atomic-a"), false);
  assert.equal(destinationCtx.views.has("atomic-b"), false);
  assert.deepEqual([...sourceCtx.visibleViewIds], sourceVisibleBefore);
  assert.deepEqual([...destinationCtx.visibleViewIds], destinationVisibleBefore);
  assert.equal(first.closeCallCount(), 0);
  assert.equal(second.closeCallCount(), 0);
  assert.equal(hooks.tabTransfers.isLocked("atomic-a"), false);
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, token), true);

  const conflictSourceWin = fakeWindow(94);
  const conflictDestinationAWin = fakeWindow(95);
  const conflictDestinationBWin = fakeWindow(96);
  const conflictSource = registerContext("lock-conflict-source", conflictSourceWin);
  const conflictDestinationA = registerContext(
    "lock-conflict-destination-a",
    conflictDestinationAWin,
  );
  const conflictDestinationB = registerContext(
    "lock-conflict-destination-b",
    conflictDestinationBWin,
  );
  const conflictA = controlledRecord("lock-conflict-a");
  const conflictB = controlledRecord("lock-conflict-b");
  attachControlledRecord(
    conflictSource,
    conflictA,
    { x: 1, y: 1, width: 200, height: 200 },
  );
  attachControlledRecord(
    conflictSource,
    conflictB,
    { x: 201, y: 1, width: 200, height: 200 },
  );
  const conflictTokenA = prepareThroughIpc(
    conflictSourceWin,
    webTransferPayload(["lock-conflict-b"]),
  );
  const conflictTokenB = prepareThroughIpc(
    conflictSourceWin,
    webTransferPayload(["lock-conflict-a", "lock-conflict-b"]),
  );
  assert.ok(hooks.tabTransfers.inspect(conflictDestinationA, conflictTokenA));
  assert.ok(hooks.tabTransfers.inspect(conflictDestinationB, conflictTokenB));
  assert.ok(
    hooks.tabTransfers.accept(
      conflictDestinationA,
      conflictTokenA,
      { kind: "strip-end" },
    ),
  );
  assert.equal(
    hooks.tabTransfers.accept(
      conflictDestinationB,
      conflictTokenB,
      { kind: "strip-end" },
    ),
    null,
  );
  assert.equal(hooks.tabTransfers.isLocked("lock-conflict-a"), false);
  assert.equal(hooks.tabTransfers.isLocked("lock-conflict-b"), true);
  assert.equal(hooks.tabTransfers.cancel(conflictSource, conflictTokenB), true);
  assert.equal(hooks.tabTransfers.cancel(conflictSource, conflictTokenA), true);
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      conflictDestinationA,
      conflictTokenA,
      true,
    ),
    true,
  );
  assert.equal(hooks.tabTransfers.isLocked("lock-conflict-b"), false);
  assert.strictEqual(conflictSource.views.get("lock-conflict-a"), conflictA.record);
  assert.strictEqual(conflictSource.views.get("lock-conflict-b"), conflictB.record);

  const metadataSourceWin = fakeWindow(92);
  const metadataDestinationWin = fakeWindow(93);
  const metadataSource = registerContext("metadata-source", metadataSourceWin);
  const metadataDestination = registerContext("metadata-destination", metadataDestinationWin);
  const metadataToken = prepareThroughIpc(
    metadataSourceWin,
    webTransferPayload(["metadata-only"]),
  );
  assert.ok(hooks.tabTransfers.inspect(metadataDestination, metadataToken));
  assert.equal(
    hooks.tabTransfers.journalOpened(
      metadataDestination,
      metadataToken,
      "destination",
    ),
    true,
  );
  const metadataAccepted = hooks.tabTransfers.accept(
    metadataDestination,
    metadataToken,
    { kind: "strip-end" },
  );
  assert.deepEqual(Array.from(metadataAccepted.recordIds), []);
  assert.equal(
    hooks.tabTransfers.journalOpened(metadataSource, metadataToken, "source"),
    true,
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(metadataDestination, metadataToken, true),
    true,
  );
  assert.equal(
    hooks.tabTransfers.sourceRemoved(
      metadataSource,
      metadataToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  const beforeEnsure = generatedNativeViews;
  const created = hooks.ensureView(
    metadataDestination,
    "metadata-only",
    "https://example.com/metadata-only",
  );
  assert.ok(created);
  assert.strictEqual(
    hooks.ensureView(
      metadataDestination,
      "metadata-only",
      "https://example.com/metadata-only",
    ),
    created,
  );
  assert.equal(generatedNativeViews, beforeEnsure + 1);
  assert.equal(metadataDestination.views.size, 1);
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      metadataDestination,
      metadataToken,
      "destination",
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      metadataSource,
      metadataToken,
      "source",
    ),
    true,
  );
}

async function checkRejectCancelExpiryDetachAndClaim() {
  const sourceWin = fakeWindow(100);
  const destinationWin = fakeWindow(101);
  const otherWin = fakeWindow(102);
  const sourceCtx = registerContext("reject-source", sourceWin);
  const destinationCtx = registerContext("reject-destination", destinationWin);
  const otherCtx = registerContext("reject-other", otherWin);
  const record = controlledRecord("reject-web");
  attachControlledRecord(sourceCtx, record, { x: 1, y: 2, width: 300, height: 200 });
  const sourceRenderer = {
    tabs: ["reject-web"],
    drafts: [["draft:reject-web", "source draft"]],
    preparedTokens: new Set(),
  };
  const destinationRenderer = {
    tabs: ["existing-web"],
    drafts: [["draft:existing-web", "destination draft"]],
    activeId: null,
  };
  const rendererStoresBefore = JSON.stringify({
    sourceTabs: sourceRenderer.tabs,
    sourceDrafts: sourceRenderer.drafts,
    destinationTabs: destinationRenderer.tabs,
    destinationDrafts: destinationRenderer.drafts,
  });
  sourceWin.onSend = (channel, item) => {
    if (channel === "tab-transfer:rejected") {
      sourceRenderer.preparedTokens.delete(item.token);
    }
  };

  const duplicateToken = prepareThroughIpc(sourceWin, webTransferPayload(["reject-web"]));
  sourceRenderer.preparedTokens.add(duplicateToken);
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, duplicateToken));
  assert.equal(
    hooks.tabTransfers.reject(otherCtx, duplicateToken, "duplicate", "existing-web"),
    null,
  );
  const duplicateResult = plain(hooks.tabTransfers.reject(
      destinationCtx,
      duplicateToken,
      "duplicate",
      "existing-web",
    ));
  assert.deepEqual(duplicateResult, { reason: "duplicate", duplicateId: "existing-web" });
  destinationRenderer.activeId = duplicateResult.duplicateId;
  assert.equal(destinationRenderer.activeId, "existing-web");
  flushRendererQueue();
  assert.equal(sourceRenderer.preparedTokens.has(duplicateToken), false);
  assert.equal(JSON.stringify({
    sourceTabs: sourceRenderer.tabs,
    sourceDrafts: sourceRenderer.drafts,
    destinationTabs: destinationRenderer.tabs,
    destinationDrafts: destinationRenderer.drafts,
  }), rendererStoresBefore);
  assert.deepEqual(plain(sourceWin.sent.at(-1)), [
    "tab-transfer:rejected",
    { token: duplicateToken, reason: "duplicate", duplicateId: "existing-web" },
  ]);
  assert.strictEqual(sourceCtx.views.get("reject-web"), record.record);
  assert.equal(hooks.tabTransfers.status(sourceCtx, duplicateToken), null);
  assert.equal(hooks.tabTransfers.inspect(destinationCtx, duplicateToken), null);
  assert.equal(
    hooks.tabTransfers.accept(destinationCtx, duplicateToken, { kind: "strip-end" }),
    null,
  );
  assert.equal(
    hooks.tabTransfers.reject(
      destinationCtx,
      duplicateToken,
      "duplicate",
      "existing-web",
    ),
    null,
  );

  const fullToken = prepareThroughIpc(sourceWin, webTransferPayload(["reject-web"]));
  sourceRenderer.preparedTokens.add(fullToken);
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, fullToken));
  assert.deepEqual(
    plain(hooks.tabTransfers.reject(destinationCtx, fullToken, "group-full")),
    { reason: "group-full" },
  );
  flushRendererQueue();
  assert.equal(sourceWin.sent.at(-1)[1].reason, "group-full");
  assert.equal(sourceRenderer.preparedTokens.has(fullToken), false);
  assert.equal(JSON.stringify({
    sourceTabs: sourceRenderer.tabs,
    sourceDrafts: sourceRenderer.drafts,
    destinationTabs: destinationRenderer.tabs,
    destinationDrafts: destinationRenderer.drafts,
  }), rendererStoresBefore);
  assert.equal(hooks.tabTransfers.status(sourceCtx, fullToken), null);

  const cancelToken = prepareThroughIpc(sourceWin, webTransferPayload(["reject-web"]));
  const cancelTimer = hooks.tabTransfers.activeTransfers.get(cancelToken).timer;
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, cancelToken), true);
  clock.runCleared(cancelTimer);
  assert.equal(hooks.tabTransfers.status(sourceCtx, cancelToken), null);

  const expiryToken = prepareThroughIpc(sourceWin, webTransferPayload(["reject-web"]));
  clock.advance(14_999);
  assert.equal(hooks.tabTransfers.status(sourceCtx, expiryToken).status, "prepared");
  clock.advance(1);
  assert.equal(hooks.tabTransfers.status(sourceCtx, expiryToken), null);
  assert.equal(sourceWin.sent.at(-1)[0], "tab-transfer:rejected");
  assert.equal(sourceWin.sent.at(-1)[1].reason, "expired");

  const detachToken = prepareThroughIpc(sourceWin, webTransferPayload(["reject-web"]));
  const detachedWindowId = await hooks.tabTransfers.detach(sourceCtx, detachToken);
  assert.equal(typeof detachedWindowId, "string");
  const detachedCtx = hooks.windows.get(detachedWindowId);
  assert.ok(detachedCtx);
  assert.equal(detachedCtx.win.shown, false);
  assert.deepEqual(detachedCtx.win.sent, []);
  assert.equal(hooks.tabTransfers.claimPending(otherCtx, detachedWindowId), null);
  assert.equal(
    hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId),
    detachToken,
  );
  assert.equal(
    hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId),
    detachToken,
  );
  assert.equal(hooks.tabTransfers.cancel(sourceCtx, detachToken), true);
  assert.equal(detachedCtx.win.closeCalls, 1);
  assert.equal(hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId), null);
}

async function checkInspectedDestinationCloseClearsPreparedToken() {
  const sourceWin = fakeWindow(103);
  const destinationWin = fakeWindow(104);
  const sourceCtx = registerContext("inspect-close-source", sourceWin);
  const destinationCtx = registerContext("inspect-close-destination", destinationWin);
  const controlled = controlledRecord("inspect-close-web");
  attachControlledRecord(
    sourceCtx,
    controlled,
    { x: 2, y: 3, width: 300, height: 200 },
  );
  const token = prepareThroughIpc(sourceWin, webTransferPayload(["inspect-close-web"]));
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, token));
  destinationWin.destroyed = true;
  hooks.cleanupWindowContext(destinationCtx);

  assert.equal(hooks.tabTransfers.activeTransfers.has(token), false);
  assert.equal(hooks.tabTransfers.status(sourceCtx, token), null);
  assert.strictEqual(sourceCtx.views.get("inspect-close-web"), controlled.record);
  assert.equal(controlled.record.ownerId, sourceCtx.id);
  assert.equal(controlled.closeCallCount(), 0);
}

async function checkPrecommitFailurePathsAndDynamicRoles() {
  const destinationFailure = await stageTransferForRollback(
    "destination-failure",
    { ready: false },
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      false,
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.status(
      destinationFailure.sourceCtx,
      destinationFailure.token,
    ).status,
    "rolling-back",
  );
  assert.equal(loadTransferDecision(destinationFailure.token).status, "rolled-back");
  assert.equal(
    hooks.tabTransfers.accept(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      { kind: "strip-end" },
    ),
    null,
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      true,
    ),
    false,
  );
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      true,
    ),
    true,
  );
  assert.equal(
    destinationFailure.controlled.record.ownerId,
    destinationFailure.sourceCtx.id,
  );
  await finalizeBoth(destinationFailure);

  const cancelled = await stageTransferForRollback("staged-cancel", { ready: false });
  assert.equal(hooks.tabTransfers.cancel(cancelled.sourceCtx, cancelled.token), true);
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      cancelled.destinationCtx,
      cancelled.token,
      true,
    ),
    true,
  );
  assert.equal(cancelled.controlled.record.ownerId, cancelled.sourceCtx.id);
  await finalizeBoth(cancelled);

  // A transient committed-decision write failure retries automatically:
  // first write fails, the retry succeeds, and the transfer commits.
  const decisionRetry = await stageTransferForRollback("decision-write-retry");
  const restoreRename = installOneShotRenameFailure();
  let retrySettled = null;
  let retryReply;
  try {
    retryReply = Promise.resolve(hooks.tabTransfers.sourceRemoved(
      decisionRetry.sourceCtx,
      decisionRetry.token,
      { ok: true, sourceEmpty: false },
    )).then((value) => {
      retrySettled = value;
      return value;
    });
    await Promise.resolve();
    assert.equal(retrySettled, null);
    assert.equal(
      hooks.tabTransfers.status(decisionRetry.sourceCtx, decisionRetry.token).status,
      "committing",
    );
    assert.equal(hooks.tabTransfers.activeTransfers.has(decisionRetry.token), true);
    assert.equal(loadTransferDecision(decisionRetry.token), null);
    // The retry window is not subject to the transfer timeout.
    assert.equal(clock.pendingIds().includes(decisionRetry.timer), false);
    clock.runCleared(decisionRetry.timer);
    assert.equal(hooks.tabTransfers.activeTransfers.has(decisionRetry.token), true);
    assert.equal(
      hooks.tabTransfers.status(decisionRetry.sourceCtx, decisionRetry.token).status,
      "committing",
    );
    // A duplicate source acknowledgement joins the same pending commit.
    const duplicateReply = hooks.tabTransfers.sourceRemoved(
      decisionRetry.sourceCtx,
      decisionRetry.token,
      { ok: true, sourceEmpty: false },
    );
    assert.equal(typeof duplicateReply?.then, "function");
    clock.advance(100);
    await Promise.resolve();
    assert.equal(await retryReply, true);
    assert.equal(await duplicateReply, true);
  } finally {
    restoreRename();
  }
  assert.equal(loadTransferDecision(decisionRetry.token).status, "committed");
  assert.equal(hooks.tabTransfers.activeTransfers.has(decisionRetry.token), false);
  assert.equal(hooks.tabTransfers.isLocked("decision-write-retry-web"), false);
  assert.equal(
    decisionRetry.controlled.record.ownerId,
    decisionRetry.destinationCtx.id,
  );
  for (const win of [decisionRetry.sourceWin, decisionRetry.destinationWin]) {
    assert.ok(win.sent.some(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === decisionRetry.token,
    ));
    assert.equal(win.sent.some(
      ([channel, item]) => (channel === "tab-transfer:rolled-back"
        || channel === "tab-transfer:undo-destination")
        && item.token === decisionRetry.token,
    ), false);
  }
  await finalizeBoth(decisionRetry);

  // A persistently failing committed-decision write exhausts its retries and
  // then takes the ordinary pre-commit rollback path, in plan order.
  const decisionFailure = await stageTransferForRollback("decision-write-failure");
  const exhaustedFault = installCommittedDecisionWriteFailure(decisionFailure.token);
  const exhaustedTrace = [];
  decisionFailure.destinationWin.onSend = (channel, item) => {
    if (item?.token !== decisionFailure.token) return;
    if (channel === "tab-transfer:undo-destination") {
      // The rolled-back decision is durable before the destination undo.
      assert.equal(loadTransferDecision(decisionFailure.token).status, "rolled-back");
      exhaustedTrace.push("undo-destination");
      return;
    }
    if (channel === "tab-transfer:rolled-back") exhaustedTrace.push("rolled-back");
  };
  let exhaustedSettled = null;
  let exhaustedReply;
  try {
    exhaustedReply = Promise.resolve(hooks.tabTransfers.sourceRemoved(
      decisionFailure.sourceCtx,
      decisionFailure.token,
      { ok: true, sourceEmpty: false },
    )).then((value) => {
      exhaustedSettled = value;
      return value;
    });
    await Promise.resolve();
    assert.equal(exhaustedSettled, null);
    assert.equal(
      hooks.tabTransfers.status(decisionFailure.sourceCtx, decisionFailure.token).status,
      "committing",
    );
    assert.equal(clock.pendingIds().includes(decisionFailure.timer), false);
    for (let elapsed = 0; elapsed < 20_000 && exhaustedSettled === null; elapsed += 1_000) {
      clock.advance(1_000);
      await Promise.resolve();
    }
    assert.equal(await exhaustedReply, false);
    assert.ok(exhaustedFault.failures() >= 5);
  } finally {
    exhaustedFault.restore();
  }
  assert.equal(
    hooks.tabTransfers.status(decisionFailure.sourceCtx, decisionFailure.token).status,
    "rolling-back",
  );
  assert.equal(loadTransferDecision(decisionFailure.token).status, "rolled-back");
  flushRendererQueue();
  assert.deepEqual(exhaustedTrace, ["undo-destination"]);
  assert.equal(
    decisionFailure.sourceWin.sent.some(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === decisionFailure.token,
    ),
    false,
  );
  assert.equal(
    decisionFailure.destinationWin.sent.some(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === decisionFailure.token,
    ),
    false,
  );
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      decisionFailure.destinationCtx,
      decisionFailure.token,
      true,
    ),
    true,
  );
  assert.equal(
    decisionFailure.controlled.record.ownerId,
    decisionFailure.sourceCtx.id,
  );
  flushRendererQueue();
  assert.deepEqual(exhaustedTrace, ["undo-destination", "rolled-back"]);
  await finalizeBoth(decisionFailure);

  const ambiguousCommit = await stageTransferForRollback("ambiguous-commit");
  const ambiguousFault = installAmbiguousCommitFailure(ambiguousCommit.token);
  let ambiguousResult;
  try {
    ambiguousResult = await hooks.tabTransfers.sourceRemoved(
      ambiguousCommit.sourceCtx,
      ambiguousCommit.token,
      { ok: true, sourceEmpty: false },
    );
  } finally {
    ambiguousFault.restore();
  }
  assert.equal(ambiguousFault.sawCommitted(), true);
  assert.equal(ambiguousResult, true);
  assert.equal(loadTransferDecision(ambiguousCommit.token).status, "committed");
  assert.equal(ambiguousCommit.controlled.record.ownerId, ambiguousCommit.destinationCtx.id);
  assert.equal(hooks.tabTransfers.activeTransfers.has(ambiguousCommit.token), false);
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      ambiguousCommit.destinationCtx,
      ambiguousCommit.token,
      "destination",
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      ambiguousCommit.sourceCtx,
      ambiguousCommit.token,
      "source",
    ),
    true,
  );

  const unconfirmedCommit = await stageTransferForRollback("unconfirmed-commit");
  const unconfirmedTimer = hooks.tabTransfers.activeTransfers.get(
    unconfirmedCommit.token,
  ).timer;
  const unconfirmedFault = installAmbiguousCommitFailure(
    unconfirmedCommit.token,
    { blockReconcileReads: true },
  );
  let unconfirmedSettled = false;
  let sourceRecovered = false;
  let unconfirmedReply;
  try {
    unconfirmedReply = Promise.resolve(hooks.tabTransfers.sourceRemoved(
      unconfirmedCommit.sourceCtx,
      unconfirmedCommit.token,
      { ok: true, sourceEmpty: false },
    )).then((result) => {
      unconfirmedSettled = true;
      if (!result) sourceRecovered = true;
      return result;
    });
    await Promise.resolve();
    assert.equal(unconfirmedSettled, false);
    assert.equal(sourceRecovered, false);
    assert.equal(
      hooks.tabTransfers.status(
        unconfirmedCommit.destinationCtx,
        unconfirmedCommit.token,
      ).status,
      "commit-indeterminate",
    );
    assert.equal(hooks.tabTransfers.activeTransfers.has(unconfirmedCommit.token), true);
    assert.equal(hooks.tabTransfers.isLocked("unconfirmed-commit-web"), true);
    assert.equal(clock.pendingIds().includes(unconfirmedTimer), false);
    assert.equal(
      unconfirmedCommit.controlled.record.ownerId,
      unconfirmedCommit.destinationCtx.id,
    );
    hooks.tabTransfers.rollback(unconfirmedCommit.token, "manual-during-indeterminate");
    hooks.tabTransfers.cancel(unconfirmedCommit.sourceCtx, unconfirmedCommit.token);
    let closePrevented = false;
    assert.equal(
      hooks.tabTransfers.windowClosing(unconfirmedCommit.sourceCtx, {
        preventDefault() { closePrevented = true; },
      }),
      true,
    );
    assert.equal(closePrevented, true);
    clock.runCleared(unconfirmedTimer);
    for (let elapsed = 0; elapsed < 60_000; elapsed += 1_000) {
      clock.advance(1_000);
      await Promise.resolve();
    }
    assert.ok(unconfirmedFault.reconcileReadFailures() >= 2);
    assert.equal(unconfirmedSettled, false);
    assert.equal(sourceRecovered, false);
    assert.equal(hooks.tabTransfers.activeTransfers.has(unconfirmedCommit.token), true);
    assert.equal(hooks.tabTransfers.isLocked("unconfirmed-commit-web"), true);
    assert.equal(
      unconfirmedCommit.controlled.record.ownerId,
      unconfirmedCommit.destinationCtx.id,
    );
    assert.equal(
      unconfirmedCommit.destinationWin.sent.some(
        ([channel, item]) => channel === "tab-transfer:undo-destination"
          && item.token === unconfirmedCommit.token,
      ),
      false,
    );
    assert.equal(
      [...unconfirmedCommit.sourceWin.sent, ...unconfirmedCommit.destinationWin.sent].some(
        ([channel, item]) => channel === "tab-transfer:rolled-back"
          && item.token === unconfirmedCommit.token,
      ),
      false,
    );
    unconfirmedFault.releaseReconcileReads();
    for (let elapsed = 0; elapsed < 10_000 && !unconfirmedSettled; elapsed += 1_000) {
      clock.advance(1_000);
      await Promise.resolve();
    }
    assert.equal(await unconfirmedReply, true);
  } finally {
    unconfirmedFault.restore();
  }
  assert.equal(sourceRecovered, false);
  assert.equal(loadTransferDecision(unconfirmedCommit.token).status, "committed");
  assert.equal(hooks.tabTransfers.activeTransfers.has(unconfirmedCommit.token), false);
  assert.equal(hooks.tabTransfers.isLocked("unconfirmed-commit-web"), false);
  assert.ok(
    unconfirmedCommit.destinationWin.sent.find(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === unconfirmedCommit.token,
    ),
  );
  assert.ok(
    unconfirmedCommit.sourceWin.sent.find(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === unconfirmedCommit.token,
    ),
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      unconfirmedCommit.destinationCtx,
      unconfirmedCommit.token,
      "destination",
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      unconfirmedCommit.sourceCtx,
      unconfirmedCommit.token,
      "source",
    ),
    true,
  );

  const postCommitReadFailure = await stageTransferForRollback(
    "post-commit-read-failure",
  );
  const postCommitReadFault = installReadFailureAt(3);
  let postCommitResult;
  try {
    postCommitResult = hooks.tabTransfers.sourceRemoved(
      postCommitReadFailure.sourceCtx,
      postCommitReadFailure.token,
      { ok: true, sourceEmpty: false },
    );
    assert.equal(postCommitResult, true);
    assert.throws(
      () => hooks.tabTransfers.status(
        postCommitReadFailure.sourceCtx,
        postCommitReadFailure.token,
      ),
      /injected read failure at call 3/,
    );
  } finally {
    postCommitReadFault.restore();
  }
  assert.equal(postCommitReadFault.triggered(), true);
  assert.equal(postCommitReadFault.calls(), 3);
  assert.equal(
    hooks.tabTransfers.status(
      postCommitReadFailure.sourceCtx,
      postCommitReadFailure.token,
    ).status,
    "committed",
  );
  assert.equal(loadTransferDecision(postCommitReadFailure.token).status, "committed");
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      postCommitReadFailure.destinationCtx,
      postCommitReadFailure.token,
      "destination",
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      postCommitReadFailure.sourceCtx,
      postCommitReadFailure.token,
      "source",
    ),
    true,
  );
  assert.equal(loadTransferDecision(postCommitReadFailure.token), null);

  const destinationClose = await stageTransferForRollback("destination-close");
  destinationClose.destinationWin.close();
  assert.equal(destinationClose.destinationWin.destroyed, false);
  assert.equal(
    hooks.tabTransfers.status(destinationClose.sourceCtx, destinationClose.token).status,
    "rolling-back",
  );
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      destinationClose.destinationCtx,
      destinationClose.token,
      true,
    ),
    true,
  );
  assert.equal(destinationClose.controlled.record.ownerId, destinationClose.sourceCtx.id);
  await finalizeBoth(destinationClose);

  const dynamicSourceWin = fakeWindow(105);
  const dynamicDestinationWin = fakeWindow(106);
  const dynamicSource = registerContext("dynamic-source", dynamicSourceWin);
  const dynamicDestination = registerContext(
    "dynamic-destination",
    dynamicDestinationWin,
  );
  const dynamicToken = prepareThroughIpc(
    dynamicSourceWin,
    webTransferPayload(["dynamic-metadata"]),
  );
  assert.ok(hooks.tabTransfers.inspect(dynamicDestination, dynamicToken));
  assert.equal(
    hooks.tabTransfers.journalOpened(
      dynamicDestination,
      dynamicToken,
      "destination",
    ),
    true,
  );
  assert.ok(
    hooks.tabTransfers.accept(dynamicDestination, dynamicToken, { kind: "strip-end" }),
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(dynamicDestination, dynamicToken, false),
    true,
  );
  assert.deepEqual(loadTransferDecision(dynamicToken).requiredRoles, [
    { role: "destination", windowId: dynamicDestination.id },
  ]);
  assert.equal(
    hooks.tabTransfers.destinationUndone(dynamicDestination, dynamicToken, true),
    true,
  );
  assert.deepEqual(
    plain(hooks.tabTransfers.pendingTerminal(dynamicDestination, dynamicDestination.id)),
    [{
      token: dynamicToken,
      status: "rolled-back",
      sourceId: dynamicSource.id,
      destinationId: dynamicDestination.id,
      role: "destination",
      windowId: dynamicDestination.id,
      orphaned: false,
    }],
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(dynamicSource, dynamicToken, "source"),
    false,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      dynamicDestination,
      dynamicToken,
      "destination",
    ),
    true,
  );
  assert.equal(loadTransferDecision(dynamicToken), null);

  const detachedSourceWin = fakeWindow(107);
  const detachedSource = registerContext("detached-rollback-source", detachedSourceWin);
  const detachedToken = prepareThroughIpc(
    detachedSourceWin,
    webTransferPayload(["detached-rollback-metadata"]),
  );
  const detachedId = await hooks.tabTransfers.detach(detachedSource, detachedToken);
  const detachedDestination = hooks.windows.get(detachedId);
  assert.ok(hooks.tabTransfers.inspect(detachedDestination, detachedToken));
  assert.equal(
    hooks.tabTransfers.journalOpened(
      detachedDestination,
      detachedToken,
      "destination",
    ),
    true,
  );
  assert.ok(
    hooks.tabTransfers.accept(
      detachedDestination,
      detachedToken,
      { kind: "strip-end" },
    ),
  );
  assert.equal(
    hooks.tabTransfers.journalOpened(detachedSource, detachedToken, "source"),
    true,
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(detachedDestination, detachedToken, false),
    true,
  );
  assert.equal(
    hooks.tabTransfers.destinationUndone(detachedDestination, detachedToken, true),
    true,
  );
  assert.equal(detachedDestination.win.closeCalls, 1);
  assert.equal(detachedDestination.win.destroyed, true);
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      detachedSource,
      detachedToken,
      "destination",
      detachedDestination.id,
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(detachedSource, detachedToken, "source"),
    true,
  );
  assert.equal(loadTransferDecision(detachedToken), null);

  const preparedJournalSourceWin = fakeWindow(108);
  const preparedJournalDestinationWin = fakeWindow(109);
  const preparedJournalSource = registerContext(
    "prepared-journal-source",
    preparedJournalSourceWin,
  );
  const preparedJournalDestination = registerContext(
    "prepared-journal-destination",
    preparedJournalDestinationWin,
  );
  const preparedJournalToken = prepareThroughIpc(
    preparedJournalSourceWin,
    webTransferPayload(["prepared-journal-metadata"]),
  );
  assert.ok(
    hooks.tabTransfers.inspect(preparedJournalDestination, preparedJournalToken),
  );
  assert.equal(
    hooks.tabTransfers.journalOpened(
      preparedJournalDestination,
      preparedJournalToken,
      "destination",
    ),
    true,
  );
  preparedJournalDestinationWin.destroyed = true;
  hooks.cleanupWindowContext(preparedJournalDestination);
  const preparedDecision = loadTransferDecision(preparedJournalToken);
  assert.equal(preparedDecision.status, "rolled-back");
  assert.deepEqual(preparedDecision.requiredRoles, [{
    role: "destination",
    windowId: preparedJournalDestination.id,
  }]);
  const preparedDelegation = preparedJournalSourceWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === preparedJournalToken
      && item.role === "destination",
  );
  assert.ok(preparedDelegation);
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      preparedJournalSource,
      preparedJournalToken,
      "destination",
      preparedJournalDestination.id,
    ),
    true,
  );
  assert.equal(loadTransferDecision(preparedJournalToken), null);

  const preparedSourceJournalWin = fakeWindow(119);
  const preparedSourceJournalWorkerWin = fakeWindow(120);
  const preparedSourceJournal = hooks.makeWindowContext(
    "prepared-source-journal-owner",
    preparedSourceJournalWin,
  );
  const preparedSourceJournalWorker = hooks.makeWindowContext(
    "prepared-source-journal-worker",
    preparedSourceJournalWorkerWin,
  );
  const isolatedWindows = new Map([
    [preparedSourceJournal.id, preparedSourceJournal],
    [preparedSourceJournalWorker.id, preparedSourceJournalWorker],
  ]);
  const preparedSourceCoordinator = hooks.makeTransferCoordinator({
    windows: isolatedWindows,
    decisionFilePath: transferDecisionFile,
    createWindow: hooks.createWindow,
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
  });
  const preparedSourceToken = preparedSourceCoordinator.prepare(
    preparedSourceJournal,
    webTransferPayload(["prepared-source-journal-metadata"]),
  );
  assert.equal(
    preparedSourceCoordinator.journalOpened(
      preparedSourceJournal,
      preparedSourceToken,
      "source",
    ),
    true,
  );
  preparedSourceJournalWin.destroyed = true;
  preparedSourceCoordinator.contextDestroyed(preparedSourceJournal);
  const preparedSourceDecision = loadTransferDecision(preparedSourceToken);
  assert.equal(preparedSourceDecision.status, "rolled-back");
  assert.deepEqual(preparedSourceDecision.requiredRoles, [{
    role: "source",
    windowId: preparedSourceJournal.id,
  }]);
  const preparedSourceDelegation = preparedSourceJournalWorkerWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === preparedSourceToken
      && item.role === "source",
  );
  assert.ok(preparedSourceDelegation);
  assert.equal(
    preparedSourceCoordinator.journalFinalized(
      preparedSourceJournalWorker,
      preparedSourceToken,
      "source",
      preparedSourceJournal.id,
    ),
    true,
  );
  assert.equal(loadTransferDecision(preparedSourceToken), null);
}

async function checkSourceEmptyDurabilityAndRestartAcknowledgements() {
  const sourceWin = fakeWindow(110);
  const destinationWin = fakeWindow(111);
  const sourceCtx = registerContext("empty-source", sourceWin);
  const destinationCtx = registerContext("empty-destination", destinationWin);
  const token = prepareThroughIpc(sourceWin, webTransferPayload(["empty-metadata"]));
  assert.ok(hooks.tabTransfers.inspect(destinationCtx, token));
  assert.equal(hooks.tabTransfers.journalOpened(destinationCtx, token, "destination"), true);
  assert.ok(hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }));
  assert.equal(hooks.tabTransfers.journalOpened(sourceCtx, token, "source"), true);
  assert.equal(hooks.tabTransfers.destinationReady(destinationCtx, token, true), true);
  assert.equal(
    hooks.tabTransfers.sourceRemoved(sourceCtx, token, { ok: true, sourceEmpty: true }),
    true,
  );
  assert.equal(sourceWin.closeCalls, 0);
  assert.equal(
    hooks.tabTransfers.journalFinalized(destinationCtx, token, "destination"),
    true,
  );
  assert.equal(sourceWin.closeCalls, 0);
  const restoreRename = installOneShotRenameFailure();
  try {
    assert.equal(
      hooks.tabTransfers.journalFinalized(sourceCtx, token, "source"),
      false,
    );
  } finally {
    restoreRename();
  }
  assert.equal(sourceWin.closeCalls, 0);
  const deletedDecisionReadFailure = installReadFailureWhenDecisionMissing(token);
  let finalAckResult;
  try {
    finalAckResult = hooks.tabTransfers.journalFinalized(sourceCtx, token, "source");
    assert.equal(finalAckResult, true);
    assert.throws(
      () => loadTransferDecision(token),
      /injected read failure after durable decision deletion/,
    );
  } finally {
    deletedDecisionReadFailure.restore();
  }
  assert.equal(deletedDecisionReadFailure.triggered(), true);
  assert.equal(sourceWin.closeCalls, 1);
  assert.equal(loadTransferDecision(token), null);

  const crashSourceWin = fakeWindow(112);
  const crashDestinationWin = fakeWindow(113);
  const crashSource = registerContext("crash-source", crashSourceWin);
  const crashDestination = registerContext("crash-destination", crashDestinationWin);
  const crashToken = prepareThroughIpc(
    crashSourceWin,
    webTransferPayload(["crash-metadata"]),
  );
  assert.ok(hooks.tabTransfers.inspect(crashDestination, crashToken));
  assert.equal(
    hooks.tabTransfers.journalOpened(crashDestination, crashToken, "destination"),
    true,
  );
  assert.ok(hooks.tabTransfers.accept(crashDestination, crashToken, { kind: "strip-end" }));
  assert.equal(hooks.tabTransfers.journalOpened(crashSource, crashToken, "source"), true);
  assert.equal(hooks.tabTransfers.destinationReady(crashDestination, crashToken, true), true);
  assert.equal(
    hooks.tabTransfers.sourceRemoved(
      crashSource,
      crashToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  assert.equal(hooks.tabTransfers.journalFinalized(crashSource, crashToken, "source"), true);
  assert.deepEqual(loadTransferDecision(crashToken).finalizedRoles, [
    { role: "source", windowId: crashSource.id },
  ]);
  const sourceAckBytes = fs.readFileSync(transferDecisionFile());

  const restarted = hooks.makeTransferCoordinator({
    windows: hooks.windows,
    decisionFilePath: transferDecisionFile,
    createWindow: hooks.createWindow,
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
  });
  assert.equal(restarted.status(crashDestination, crashToken).status, "committed");
  assert.deepEqual(
    plain(restarted.pendingTerminal(crashDestination, crashDestination.id)),
    [{
      token: crashToken,
      status: "committed",
      sourceId: crashSource.id,
      destinationId: crashDestination.id,
      role: "destination",
      windowId: crashDestination.id,
      orphaned: false,
    }],
  );
  assert.equal(restarted.journalFinalized(crashSource, crashToken, "source"), true);
  assert.deepEqual(fs.readFileSync(transferDecisionFile()), sourceAckBytes);
  assert.equal(restarted.journalFinalized(crashDestination, crashToken, "destination"), true);
  assert.equal(restarted.status(crashDestination, crashToken), null);
  assert.equal(loadTransferDecision(crashToken), null);

  const reverseSourceWin = fakeWindow(115);
  const reverseDestinationWin = fakeWindow(116);
  const reverseSource = registerContext("reverse-crash-source", reverseSourceWin);
  const reverseDestination = registerContext(
    "reverse-crash-destination",
    reverseDestinationWin,
  );
  const reverseToken = prepareThroughIpc(
    reverseSourceWin,
    webTransferPayload(["reverse-crash-metadata"]),
  );
  assert.ok(hooks.tabTransfers.inspect(reverseDestination, reverseToken));
  assert.equal(
    hooks.tabTransfers.journalOpened(
      reverseDestination,
      reverseToken,
      "destination",
    ),
    true,
  );
  assert.ok(
    hooks.tabTransfers.accept(
      reverseDestination,
      reverseToken,
      { kind: "strip-end" },
    ),
  );
  assert.equal(
    hooks.tabTransfers.journalOpened(reverseSource, reverseToken, "source"),
    true,
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(reverseDestination, reverseToken, true),
    true,
  );
  assert.equal(
    hooks.tabTransfers.sourceRemoved(
      reverseSource,
      reverseToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      reverseDestination,
      reverseToken,
      "destination",
    ),
    true,
  );
  assert.deepEqual(loadTransferDecision(reverseToken).finalizedRoles, [
    { role: "destination", windowId: reverseDestination.id },
  ]);
  const destinationAckBytes = fs.readFileSync(transferDecisionFile());
  const reverseRestarted = hooks.makeTransferCoordinator({
    windows: hooks.windows,
    decisionFilePath: transferDecisionFile,
    createWindow: hooks.createWindow,
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
  });
  assert.equal(reverseRestarted.status(reverseSource, reverseToken).status, "committed");
  assert.deepEqual(
    plain(reverseRestarted.pendingTerminal(reverseSource, reverseSource.id)),
    [{
      token: reverseToken,
      status: "committed",
      sourceId: reverseSource.id,
      destinationId: reverseDestination.id,
      role: "source",
      windowId: reverseSource.id,
      orphaned: false,
    }],
  );
  assert.equal(
    reverseRestarted.journalFinalized(
      reverseDestination,
      reverseToken,
      "destination",
    ),
    true,
  );
  assert.deepEqual(fs.readFileSync(transferDecisionFile()), destinationAckBytes);
  assert.equal(
    reverseRestarted.journalFinalized(reverseSource, reverseToken, "source"),
    true,
  );
  assert.equal(reverseRestarted.status(reverseSource, reverseToken), null);
  assert.equal(loadTransferDecision(reverseToken), null);
}

async function checkOrphanFinalizationPendingTerminalAndWindowClose() {
  const orphan = await stageTransferForRollback("orphan-destination");
  const orphanedJournals = new Set([
    `${orphan.token}:destination:${orphan.destinationCtx.id}`,
  ]);
  let delegatedDestinationAck = null;
  orphan.sourceWin.onSend = (channel, item) => {
    if (
      channel !== "tab-transfer:finalize-orphaned"
      || item.token !== orphan.token
      || item.role !== "destination"
    ) {
      return;
    }
    orphanedJournals.delete(`${item.token}:${item.role}:${item.windowId}`);
    delegatedDestinationAck = hooks.tabTransfers.journalFinalized(
      orphan.sourceCtx,
      item.token,
      item.role,
      item.windowId,
    );
  };
  orphan.destinationWin.destroyed = true;
  hooks.tabTransfers.contextDestroyed(orphan.destinationCtx);
  flushRendererQueue();
  assert.equal(orphan.controlled.record.ownerId, orphan.sourceCtx.id);
  assert.equal(hooks.tabTransfers.activeTransfers.has(orphan.token), false);
  const orphanEvents = orphan.sourceWin.sent.filter(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === orphan.token,
  );
  assert.equal(orphanEvents.length, 1);
  const [orphanEvent] = orphanEvents;
  assert.ok(orphanEvent);
  assert.deepEqual(plain(orphanEvent[1]), {
    token: orphan.token,
    status: "rolled-back",
    role: "destination",
    windowId: orphan.destinationCtx.id,
    orphaned: true,
  });
  assert.deepEqual([...orphanedJournals], []);
  assert.equal(delegatedDestinationAck, true);
  assert.deepEqual(loadTransferDecision(orphan.token).finalizedRoles, [{
    role: "destination",
    windowId: orphan.destinationCtx.id,
  }]);
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      orphan.sourceCtx,
      orphan.token,
      "source",
    ),
    true,
  );
  assert.equal(loadTransferDecision(orphan.token), null);

  const sourceGoneWin = fakeWindow(117);
  const sourceGoneDestinationWin = fakeWindow(118);
  const sourceGone = registerContext("orphan-source", sourceGoneWin);
  const sourceGoneDestination = registerContext(
    "orphan-source-destination",
    sourceGoneDestinationWin,
  );
  const sourceGoneToken = prepareThroughIpc(
    sourceGoneWin,
    webTransferPayload(["orphan-source-metadata"]),
  );
  assert.ok(hooks.tabTransfers.inspect(sourceGoneDestination, sourceGoneToken));
  assert.equal(
    hooks.tabTransfers.journalOpened(
      sourceGoneDestination,
      sourceGoneToken,
      "destination",
    ),
    true,
  );
  assert.ok(
    hooks.tabTransfers.accept(
      sourceGoneDestination,
      sourceGoneToken,
      { kind: "strip-end" },
    ),
  );
  assert.equal(
    hooks.tabTransfers.journalOpened(sourceGone, sourceGoneToken, "source"),
    true,
  );
  assert.equal(
    hooks.tabTransfers.destinationReady(
      sourceGoneDestination,
      sourceGoneToken,
      true,
    ),
    true,
  );
  sourceGoneWin.destroyed = true;
  hooks.cleanupWindowContext(sourceGone);
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      sourceGoneDestination,
      sourceGoneToken,
      true,
    ),
    true,
  );
  const delegatedSource = sourceGoneDestinationWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === sourceGoneToken
      && item.role === "source",
  );
  assert.ok(delegatedSource);
  assert.deepEqual(plain(delegatedSource[1]), {
    token: sourceGoneToken,
    status: "rolled-back",
    role: "source",
    windowId: sourceGone.id,
    orphaned: true,
  });
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      sourceGoneDestination,
      sourceGoneToken,
      "source",
      sourceGone.id,
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      sourceGoneDestination,
      sourceGoneToken,
      "destination",
    ),
    true,
  );
  assert.equal(loadTransferDecision(sourceGoneToken), null);

  const sourceNativeGone = await stageTransferForRollback("orphan-source-native");
  sourceNativeGone.sourceWin.destroyed = true;
  hooks.cleanupWindowContext(sourceNativeGone.sourceCtx);
  const sourceNativeUndoTimer = hooks.tabTransfers.activeTransfers.get(
    sourceNativeGone.token,
  ).undoTimer;
  assert.notEqual(sourceNativeUndoTimer, null);
  assert.equal(
    hooks.tabTransfers.destinationUndone(
      sourceNativeGone.destinationCtx,
      sourceNativeGone.token,
      true,
    ),
    true,
  );
  assert.equal(
    sourceNativeGone.destinationCtx.views.has("orphan-source-native-web"),
    false,
  );
  assert.equal(
    sourceNativeGone.destinationCtx.visibleViewIds.has("orphan-source-native-web"),
    false,
  );
  assert.equal(sourceNativeGone.controlled.record.ownerId, null);
  assert.equal(sourceNativeGone.controlled.closeCallCount(), 1);
  const sourceNativeRollbackReceipts = sourceNativeGone.destinationWin.sent.filter(
    ([channel, item]) => channel === "tab-transfer:rolled-back"
      && item.token === sourceNativeGone.token,
  ).length;
  clock.runCleared(sourceNativeUndoTimer);
  assert.equal(sourceNativeGone.controlled.closeCallCount(), 1);
  assert.equal(
    sourceNativeGone.destinationWin.sent.filter(
      ([channel, item]) => channel === "tab-transfer:rolled-back"
        && item.token === sourceNativeGone.token,
    ).length,
    sourceNativeRollbackReceipts,
  );
  assert.equal(hooks.tabTransfers.isLocked("orphan-source-native-web"), false);
  assert.equal(
    hooks.tabTransfers.activeTransfers.has(sourceNativeGone.token),
    false,
  );
  assert.equal(loadTransferDecision(sourceNativeGone.token).status, "rolled-back");
  assert.ok(
    sourceNativeGone.destinationWin.sent.find(
      ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
        && item.token === sourceNativeGone.token
        && item.role === "source",
    ),
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      sourceNativeGone.destinationCtx,
      sourceNativeGone.token,
      "source",
      sourceNativeGone.sourceCtx.id,
    ),
    true,
  );
  assert.equal(
    hooks.tabTransfers.journalFinalized(
      sourceNativeGone.destinationCtx,
      sourceNativeGone.token,
      "destination",
    ),
    true,
  );
  assert.equal(loadTransferDecision(sourceNativeGone.token), null);

  const bothGone = await stageTransferForRollback("orphan-both");
  bothGone.sourceWin.destroyed = true;
  bothGone.destinationWin.destroyed = true;
  hooks.tabTransfers.contextDestroyed(bothGone.destinationCtx);
  assert.ok(loadTransferDecision(bothGone.token));
  const restarted = hooks.makeTransferCoordinator({
    windows: hooks.windows,
    decisionFilePath: transferDecisionFile,
    createWindow: hooks.createWindow,
    setTimer: clock.setTimeout,
    clearTimer: clock.clearTimeout,
  });
  const recoveryWin = fakeWindow(114);
  const recoveryCtx = registerContext("orphan-recovery", recoveryWin);
  const terminal = restarted.pendingTerminal(recoveryCtx, recoveryCtx.id);
  assert.deepEqual(
    Array.from(terminal, (item) => [item.role, item.windowId, item.orphaned]).sort(),
    [
      ["destination", bothGone.destinationCtx.id, true],
      ["source", bothGone.sourceCtx.id, true],
    ].sort(),
  );
  assert.equal(restarted.status(recoveryCtx, bothGone.token).status, "rolled-back");
  assert.equal(
    restarted.journalFinalized(
      recoveryCtx,
      bothGone.token,
      terminal[0].role,
      terminal[0].windowId,
    ),
    true,
  );
  assert.ok(loadTransferDecision(bothGone.token));
  assert.equal(
    restarted.journalFinalized(
      recoveryCtx,
      bothGone.token,
      terminal[1].role,
      terminal[1].windowId,
    ),
    true,
  );
  assert.equal(loadTransferDecision(bothGone.token), null);

  const closing = await stageTransferForRollback("source-close");
  closing.sourceWin.close();
  assert.equal(closing.sourceWin.destroyed, false);
  assert.equal(
    hooks.tabTransfers.status(closing.sourceCtx, closing.token).status,
    "rolling-back",
  );
  assert.equal(
    hooks.tabTransfers.destinationUndone(closing.destinationCtx, closing.token, true),
    true,
  );
  assert.equal(closing.controlled.record.ownerId, closing.sourceCtx.id);
  await finalizeBoth(closing);
}

assert.doesNotMatch(source, /\blet mainWindow\b/);
assert.doesNotMatch(source, /\bconst views = new Map\(\)/);
assert.doesNotMatch(source, /\bvisibleViewId\b/);
assert.match(source, /ipcMain\.on\("webtab:sync-visible"/);
assert.match(source, /ipcMain\.on\("tab-transfer:prepare"/);
assert.match(source, /event\.returnValue\s*=/);
assert.match(source, /TRANSFER_TIMEOUT_MS\s*=\s*15_000/);
assert.match(source, /status:\s*"destination-staged"/);
assert.match(source, /transaction\.status\s*=\s*"awaiting-source"/);
assert.match(source, /putTransferDecision/);
const menuStart = source.indexOf("function buildMenu");
const menuEnd = source.indexOf("// --------------------------------------------------------------------- boot");
assert.doesNotMatch(source.slice(menuStart, menuEnd), /mainWindow/);

Promise.all([
  checkLoadView(),
  checkVisibleCollectionAndActivation(),
])
  .then(async () => {
    checkPreloadWindowIdentity();
    checkPreloadTabTransfer();
    await checkSenderOwnership();
    await checkFocusedRoutingAndCleanup();
    assertTransferApiRegistered();
    await checkTransferPreparationValidationAndAuthorization();
    await checkSuccessfulTransferAndDurableCommit();
    await checkLockedRecordsRejectOrdinaryIpc();
    await checkRollbackOrderingAndLateSourceRace();
    await checkDestinationUndoDeadlineDelegatesJournal();
    await checkAtomicReparentAndMetadataOnlyTransfer();
    await checkRejectCancelExpiryDetachAndClaim();
    await checkInspectedDestinationCloseClearsPreparedToken();
    await checkPrecommitFailurePathsAndDynamicRoles();
    await checkSourceEmptyDurabilityAndRestartAcknowledgements();
    await checkOrphanFinalizationPendingTerminalAndWindowClose();
    assert.equal(rendererQueue.length, 0, "all fake renderer deliveries must be flushed");
    console.log("webtab navigation checks passed");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => {
    fs.rmSync(transferUserData, { recursive: true, force: true });
  });
