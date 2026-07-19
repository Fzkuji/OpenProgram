const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const start = source.indexOf("function loadView");
const end = source.indexOf("\n\n// create-if-missing", start);
assert.ok(start >= 0 && end > start, "loadView source not found");
const nativeStart = source.indexOf("function runNativeNavigation");
const nativeEnd = source.indexOf("\n\nfunction registerWebTabIpc", nativeStart);
assert.ok(
  nativeStart >= 0 && nativeEnd > nativeStart,
  "runNativeNavigation source not found",
);
const popupStart = source.indexOf("wc.setWindowOpenHandler");
const popupEnd = source.indexOf("\n    for (const ev", popupStart);
const popupHandler = source.slice(popupStart, popupEnd);
assert.match(popupHandler, /navigateView\(id, popupUrl\)/);
assert.doesNotMatch(popupHandler, /wc\.loadURL/);
const ipcStart = source.indexOf("function registerWebTabIpc");
const ipcEnd = source.indexOf("\n}\n\n//", ipcStart) + 2;
const ipcHandlers = source.slice(ipcStart, ipcEnd);
for (const channel of ["reload", "go-back", "go-forward"]) {
  const marker = `ipcMain.on("webtab:${channel}"`;
  const handlerStart = ipcHandlers.indexOf(marker);
  assert.ok(handlerStart >= 0, `${channel} handler not found`);
  const nextHandler = ipcHandlers.indexOf("ipcMain.on(", handlerStart + marker.length);
  const handler = ipcHandlers.slice(
    handlerStart,
    nextHandler >= 0 ? nextHandler : ipcHandlers.length,
  );
  assert.match(
    handler,
    /runNativeNavigation/,
    `${channel} must invalidate a replaced loadURL Promise`,
  );
}

const context = vm.createContext({ Promise, Map });
vm.runInContext(
  `const views = new Map();
const viewNavigations = new Map();
${source.slice(start, end)}
${source.slice(nativeStart, nativeEnd)}
globalThis.loadView = loadView;
globalThis.runNativeNavigation = runNativeNavigation;
globalThis.views = views;
globalThis.viewNavigations = viewNavigations;`,
  context,
);

function controlledView(currentUrl, loading = false) {
  const calls = [];
  const controls = [];
  const webContents = {
    getURL: () => currentUrl,
    isLoading: () => loading,
    loadURL(url) {
      calls.push(url);
      loading = true;
      return new Promise((resolve, reject) => controls.push({ resolve, reject }));
    },
  };
  return { view: { webContents }, calls, controls };
}

async function checkLoadView() {
  // URL2 is pending while getURL() still reports URL1. Requesting URL1 must
  // replace the pending navigation instead of treating the stale committed
  // URL as already active.
  const competing = controlledView("https://example.com/one");
  const first = context.loadView(
    "tab-competing",
    competing.view,
    "https://example.com/two",
  );
  const second = context.loadView(
    "tab-competing",
    competing.view,
    "https://example.com/one",
  );
  assert.notStrictEqual(first, second);
  assert.deepEqual(competing.calls, [
    "https://example.com/two",
    "https://example.com/one",
  ]);
  assert.strictEqual(context.viewNavigations.get("tab-competing").promise, second);

  // Finishing the superseded load must neither resolve the replacement nor
  // delete its registry entry.
  let secondSettled = false;
  void second.then(() => { secondSettled = true; });
  competing.controls[0].resolve();
  assert.strictEqual(await first, competing.view);
  await Promise.resolve();
  assert.equal(secondSettled, false);
  assert.strictEqual(context.viewNavigations.get("tab-competing").promise, second);

  competing.controls[1].resolve();
  assert.strictEqual(await second, competing.view);
  assert.equal(context.viewNavigations.has("tab-competing"), false);

  // A repeated request for the same pending URL must share its Promise and
  // issue exactly one native load.
  const duplicate = controlledView("https://example.com/one");
  const original = context.loadView(
    "tab-duplicate",
    duplicate.view,
    "https://example.com/two",
  );
  const repeated = context.loadView(
    "tab-duplicate",
    duplicate.view,
    "https://example.com/two",
  );
  assert.strictEqual(repeated, original);
  assert.deepEqual(duplicate.calls, ["https://example.com/two"]);
  duplicate.controls[0].resolve();
  assert.strictEqual(await repeated, duplicate.view);

  // A native reload/history operation replaces an in-flight loadURL without
  // going through loadView. It must invalidate the same-URL pending Promise,
  // so a following activation creates and waits for a replacement load.
  const interrupted = controlledView("https://example.com/one", true);
  interrupted.view.webContents.reload = () => {};
  context.views.set("tab-interrupted", interrupted.view);
  const replaced = context.loadView(
    "tab-interrupted",
    interrupted.view,
    "https://example.com/one",
  );
  context.runNativeNavigation(
    "tab-interrupted",
    (webContents) => webContents.reload(),
  );
  assert.equal(context.viewNavigations.has("tab-interrupted"), false);
  const replacement = context.loadView(
    "tab-interrupted",
    interrupted.view,
    "https://example.com/one",
  );
  assert.notStrictEqual(replaced, replacement);
  assert.deepEqual(interrupted.calls, [
    "https://example.com/one",
    "https://example.com/one",
  ]);
  interrupted.controls[0].resolve();
  assert.strictEqual(await replaced, interrupted.view);
  assert.strictEqual(
    context.viewNavigations.get("tab-interrupted").promise,
    replacement,
  );
  interrupted.controls[1].resolve();
  assert.strictEqual(await replacement, interrupted.view);

  // reload/back/forward are not registered in viewNavigations. isLoading()
  // is therefore the remaining signal that the committed URL is not stable.
  const externallyLoading = controlledView("https://example.com/one", true);
  const restarted = context.loadView(
    "tab-external",
    externallyLoading.view,
    "https://example.com/one",
  );
  assert.deepEqual(externallyLoading.calls, ["https://example.com/one"]);
  externallyLoading.controls[0].resolve();
  assert.strictEqual(await restarted, externallyLoading.view);

  // A stable committed URL still uses the zero-navigation fast path.
  const stable = controlledView("https://example.com/one", false);
  assert.strictEqual(
    await context.loadView("tab-stable", stable.view, "https://example.com/one"),
    stable.view,
  );
  assert.deepEqual(stable.calls, []);
  assert.equal(context.viewNavigations.size, 0);
}

async function checkActivateView() {
  const showStart = source.indexOf("function showView");
  const showEnd = source.indexOf("\n\nfunction hideView", showStart);
  const activateStart = source.indexOf("async function activateView");
  const activateEnd = source.indexOf("\n\nfunction withView", activateStart);
  assert.ok(showStart >= 0 && showEnd > showStart, "showView source not found");
  assert.ok(
    activateStart >= 0 && activateEnd > activateStart,
    "activateView source not found",
  );

  const activation = vm.createContext({ Promise, Map });
  vm.runInContext(
    `const views = new Map();
let visibleViewId = null;
let navigationResolve = null;
let targetCalls = 0;
function isWebUrl() { return true; }
function createView(id) {
  return {
    setVisible() {},
    webContents: {
      getOrCreateDevToolsTargetId() {
        targetCalls += 1;
        return id + "-target";
      },
    },
  };
}
views.set("tab-a", createView("tab-a"));
views.set("tab-b", createView("tab-b"));
function ensureView(id) { return views.get(id); }
function navigateView(id) {
  return new Promise((resolve) => {
    navigationResolve = () => resolve(views.get(id));
  });
}
${source.slice(showStart, showEnd)}
${source.slice(activateStart, activateEnd)}
globalThis.activateView = activateView;
globalThis.showView = showView;
globalThis.resolveNavigation = () => navigationResolve();
globalThis.targetCallCount = () => targetCalls;`,
    activation,
  );

  const switched = activation.activateView("tab-a", "https://example.com/a");
  assert.equal(activation.targetCallCount(), 0);
  activation.showView("tab-b");
  activation.resolveNavigation();
  assert.equal(await switched, null);
  assert.equal(activation.targetCallCount(), 0);

  const remainsActive = activation.activateView(
    "tab-a",
    "https://example.com/a",
  );
  assert.equal(activation.targetCallCount(), 0);
  activation.resolveNavigation();
  assert.equal(await remainsActive, "tab-a-target");
  assert.equal(activation.targetCallCount(), 1);
}

Promise.all([checkLoadView(), checkActivateView()])
  .then(() => console.log("webtab navigation checks passed"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
