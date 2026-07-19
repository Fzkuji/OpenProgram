const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const preloadSource = fs.readFileSync(
  path.join(__dirname, "..", "preload.js"),
  "utf8",
);

const ipcListeners = new Map();
const ipcHandlers = new Map();
let focusedWindow = null;
const fakeWindows = [];
const browserWindowOptions = [];
let menuTemplate = null;
let nextGeneratedWindowId = 1000;

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
    getPath() { return "/tmp/openprogram-webtab-check"; },
    whenReady() { return { then() {} }; },
    on() {},
    quit() {},
  },
  BrowserWindow: FakeBrowserWindow,
  WebContentsView: class {
    constructor() { return controlledRecord("native-view").record.view; }
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
  setTimeout,
  clearTimeout,
  process,
  __dirname: path.join(__dirname, ".."),
  __filename: path.join(__dirname, "..", "main.js"),
  require(id) {
    if (id === "electron") return fakeElectron;
    if (id === "http") return fakeHttp;
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
  const win = {
    id,
    destroyed: false,
    listeners,
    sent,
    added,
    removed,
    contentView: {
      addChildView(view) { added.push(view); },
      removeChildView(view) { removed.push(view); },
    },
    webContents: {
      send(...args) { sent.push(args); },
      setWindowOpenHandler() {},
      on() {},
    },
    on(event, handler) { listeners.set(event, handler); },
    isDestroyed() { return this.destroyed; },
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

assert.doesNotMatch(source, /\blet mainWindow\b/);
assert.doesNotMatch(source, /\bconst views = new Map\(\)/);
assert.doesNotMatch(source, /\bvisibleViewId\b/);
assert.match(source, /ipcMain\.on\("webtab:sync-visible"/);
const menuStart = source.indexOf("function buildMenu");
const menuEnd = source.indexOf("// --------------------------------------------------------------------- boot");
assert.doesNotMatch(source.slice(menuStart, menuEnd), /mainWindow/);

Promise.all([
  checkLoadView(),
  checkVisibleCollectionAndActivation(),
])
  .then(async () => {
    checkPreloadWindowIdentity();
    await checkSenderOwnership();
    await checkFocusedRoutingAndCleanup();
    console.log("webtab navigation checks passed");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
