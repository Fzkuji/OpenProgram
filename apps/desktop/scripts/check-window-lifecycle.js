const assert = require("node:assert/strict");

const {
  windowIsLive,
  createMainWindowGate,
  focusExistingWindow,
  registerSingleMainWindow,
} = require("../window-lifecycle");

function liveWin(id) {
  let destroyed = false;
  return {
    id,
    isDestroyed: () => destroyed,
    destroy() { destroyed = true; },
    isMinimized: () => false,
    restore() { this.restored = true; },
    show() { this.shown = true; },
    focus() { this.focused = true; },
  };
}

function checkReuseLiveMain() {
  const win = liveWin("a");
  const windows = new Map([["main", { win }]]);
  let creates = 0;
  const ensure = createMainWindowGate({
    windows,
    createWindow: () => {
      creates += 1;
      return { win: liveWin("b") };
    },
  });
  return Promise.resolve(ensure()).then((first) => {
    assert.equal(first.win, win);
    return Promise.resolve(ensure()).then((second) => {
      assert.equal(second.win, win);
      assert.equal(creates, 0, "live main is reused, never created again");
    });
  });
}

function checkShareInFlightCreate() {
  const windows = new Map();
  let creates = 0;
  let resolveCreate;
  const pending = new Promise((resolve) => { resolveCreate = resolve; });
  const ensure = createMainWindowGate({
    windows,
    createWindow: () => {
      creates += 1;
      return pending;
    },
  });
  const a = ensure();
  const b = ensure();
  assert.equal(a, b, "concurrent callers share one promise");
  assert.equal(creates, 1);
  const ctx = { win: liveWin("main") };
  windows.set("main", ctx);
  resolveCreate(ctx);
  return a.then((got) => {
    assert.equal(got, ctx);
    return Promise.resolve(ensure()).then((again) => {
      assert.equal(again, ctx);
      assert.equal(creates, 1);
    });
  });
}

function checkRecreateAfterDestroy() {
  const first = liveWin("old");
  const windows = new Map([["main", { win: first }]]);
  let creates = 0;
  const ensure = createMainWindowGate({
    windows,
    createWindow: () => {
      creates += 1;
      const next = { win: liveWin("new") };
      windows.set("main", next);
      return next;
    },
  });
  first.destroy();
  return Promise.resolve(ensure()).then((got) => {
    assert.equal(creates, 1);
    assert.equal(got.win.id, "new");
  });
}

function checkFocusExisting() {
  const win = liveWin("f");
  win.isMinimized = () => true;
  focusExistingWindow(win);
  assert.equal(win.restored, true);
  assert.equal(win.shown, true);
  assert.equal(win.focused, true);
  focusExistingWindow(null);
  focusExistingWindow({ isDestroyed: () => true });
}

function checkSecondInstanceReusesGate() {
  const listeners = new Map();
  const all = [];
  const app = {
    requestSingleInstanceLock: () => true,
    on(event, fn) { listeners.set(event, fn); },
    whenReady() { return Promise.resolve(); },
    quit() { this.quitCalled = true; },
  };
  const BrowserWindow = {
    getAllWindows: () => all,
    getFocusedWindow: () => all[0] || null,
  };
  let ensures = 0;
  const result = registerSingleMainWindow({
    app,
    BrowserWindow,
    ensureMainWindow: () => { ensures += 1; },
    recoverErroredWindows: () => { throw new Error("should not recover when empty"); },
    onReady: () => {},
  });
  assert.equal(result.primary, true);
  listeners.get("second-instance")();
  assert.equal(ensures, 1);
}

function checkSecondaryInstanceQuits() {
  const app = {
    requestSingleInstanceLock: () => false,
    on() { throw new Error("secondary must not listen"); },
    whenReady() { throw new Error("secondary must not ready"); },
    quit() { this.quitCalled = true; },
  };
  const result = registerSingleMainWindow({
    app,
    BrowserWindow: { getAllWindows: () => [] },
    ensureMainWindow: () => { throw new Error("no window"); },
  });
  assert.equal(result.primary, false);
  assert.equal(app.quitCalled, true);
}

function checkDestroyedIsNotLive() {
  assert.equal(windowIsLive(null), false);
  assert.equal(windowIsLive({}), false);
  const win = liveWin("x");
  assert.equal(windowIsLive(win), true);
  win.destroy();
  assert.equal(windowIsLive(win), false);
}

Promise.resolve()
  .then(checkReuseLiveMain)
  .then(checkShareInFlightCreate)
  .then(checkRecreateAfterDestroy)
  .then(() => { checkFocusExisting(); })
  .then(checkSecondInstanceReusesGate)
  .then(() => { checkSecondaryInstanceQuits(); })
  .then(() => { checkDestroyedIsNotLive(); })
  .then(() => {
    console.log("window lifecycle check passed");
  })
  .catch((err) => {
    console.error(err);
    process.exitCode = 1;
  });
