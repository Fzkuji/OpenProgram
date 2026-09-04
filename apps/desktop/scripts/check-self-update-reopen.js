"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Run the production window entry, not Electron or a source substring assertion.
async function checkWindowEntry() {
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const entry = source.slice(source.indexOf("async function createWindow(options"),
    source.indexOf("// Electron renders file://"));
  class Window {
    constructor() {
      this.id = Math.random();
      this.loaded = [];
      this.webContents = { setWindowOpenHandler() {}, on() {}, getURL: () => "" };
    }
    on() {}
    once() {}
    loadURL(url) { this.loaded.push(url); return Promise.resolve(); }
    isDestroyed() { return false; }
  }
  const calls = [];
  const noop = () => {};
  const context = vm.createContext({
    BrowserWindow: Window, __dirname, path, process, URL,
    loadWindowState: () => ({}), browserWindowOptionsForPlan: () => ({}),
    attachWindowStatePersistence: noop, applyRestoredChrome: noop,
    stateFile: noop, currentDisplays: noop, currentChrome: { bg: "#ffffff" },
    makeWindowContext: (id, win) => ({ id, win }), windows: new Map(),
    contextsByBrowserWindowId: new Map(), closeMainMenu: noop,
    tabTransfers: { windowClosing: noop }, cleanupWindowContext: noop,
    clearOwnedViews: noop, startWindowRecovery: noop,
    resolveStartUrl: async () => "http://127.0.0.1:18100/chat#token=auth",
    isErrorPageUrl: () => false, UI_ORIGIN: "http://127.0.0.1:18100",
    selfUpdateReopen: {
      async resolveStartUrl(ctx, url) {
        calls.push(ctx.id);
        return ctx.id === "main" ? "http://127.0.0.1:18100/s/p1#token=auth" : url;
      },
      observeNavigation: noop,
    },
  });
  vm.runInContext(entry, context);
  const main = await context.createWindow();
  assert.equal(main.win.loaded[0], "http://127.0.0.1:18100/s/p1#token=auth",
    "the update-triggered main must use the recovered original session");
  const other = await context.createWindow({ windowId: "detached-1", detached: true });
  assert.equal(other.win.loaded[0], "http://127.0.0.1:18100/chat#token=auth");
  assert.deepEqual(calls, ["main", "detached-1"]);
}

const { createSelfUpdateReopen, launchUpdateId, registerReopenIpc } = require("../self-update-reopen");
const ORIGIN = "http://127.0.0.1:18100";
const AUTH = `${ORIGIN}/chat#token=${"a".repeat(43)}`;
const intent = () => ({ schema: 1, update_id: "su_test", attempt: 1, session_id: "p1",
  reopen_id: "b".repeat(64), launch_kind: "activation", status: "pending", expires_at: Date.now() / 1000 + 60 });
function ctx(id = "main") {
  let url = `${ORIGIN}/s/p1`;
  const frame = { url };
  return {
    id, navigate(value) { url = value; frame.url = value; },
    win: { isDestroyed: () => false, webContents: { getURL: () => url, mainFrame: frame, send() {} } },
  };
}
function response(value, status = 200) { return new Response(JSON.stringify(value), { status }); }
function recovery(fetchImpl, argv = ["OpenProgram", "--openprogram-self-update=su_test"]) {
  return createSelfUpdateReopen({ argv, origin: ORIGIN, fetchImpl });
}

async function checkProtocol() {
  const calls = [];
  const value = intent();
  const main = ctx();
  const state = recovery(async (url, options) => {
    calls.push({ url, options });
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Authorization, `Bearer ${"a".repeat(43)}`);
    assert.ok(options.signal instanceof AbortSignal);
    if (options.method === "POST") {
      assert.deepEqual(JSON.parse(options.body), { session_id: "p1", reopen_id: value.reopen_id });
      return response({ ...value, status: "acknowledged" });
    }
    return response(value);
  });
  assert.equal(await state.resolveStartUrl(ctx("other"), AUTH), AUTH);
  assert.equal(calls.length, 0, "detached windows cannot resolve");
  assert.equal(await state.resolveStartUrl(main, AUTH), `${ORIGIN}/s/p1#token=${"a".repeat(43)}`);
  assert.equal(calls.length, 1);
  assert.equal(await state.resolveStartUrl(main, AUTH), `${ORIGIN}/s/p1#token=${"a".repeat(43)}`);
  assert.equal(calls.length, 1, "crash-before-ACK can re-locate without another GET");
  await state.sessionLoaded(main, "other");
  await state.sessionLoaded(ctx("detached"), "p1");
  main.navigate(`${ORIGIN}/settings/general`);
  await state.sessionLoaded(main, "p1");
  assert.equal(calls.length, 1, "load ACK requires original session and current route");
  main.navigate(`${ORIGIN}/s/p1`);
  await Promise.all([state.sessionLoaded(main, "p1"), state.sessionLoaded(main, "p1")]);
  assert.equal(calls.length, 2, "concurrent ACK shares a single request");
  assert.equal(state.state().status, "acknowledged");
  await state.sessionLoaded(main, "p1");
  assert.equal(calls.length, 2);
  assert.equal(await state.resolveStartUrl(main, AUTH), AUTH, "ordinary reopens after ACK do not force the route");
  assert.doesNotMatch(JSON.stringify(state.state()), /token|reopen_id|authorization/i);

  const manual = recovery(async () => response(intent()));
  await manual.resolveStartUrl(main, AUTH);
  manual.observeNavigation(main, `${ORIGIN}/settings/general`);
  assert.equal(manual.state().status, "manual_navigation");
  assert.equal(manual.state().sessionId, "p1", "keep a safe result notice target");
  assert.equal(await manual.resolveStartUrl(main, AUTH), AUTH);
  await manual.sessionLoaded(main, "p1");
  assert.equal(manual.state().status, "manual_navigation");
}

async function checkFallbacks() {
  assert.equal(launchUpdateId([]), null);
  for (const args of [["--openprogram-self-update=file:///bad"], ["--openprogram-self-update", "su_test"],
    ["--openprogram-self-update=su_test", "--openprogram-self-update=su_other"]]) {
    assert.throws(() => launchUpdateId(args), /launch_argument_invalid/);
  }
  const neverFetch = () => { throw new Error("unexpected fetch"); };
  assert.equal(await recovery(neverFetch, []).resolveStartUrl(ctx(), AUTH), AUTH);
  for (const url of ["https://example.com/#token=" + "a".repeat(43), `${ORIGIN}/chat`,
    "http://127.0.0.1:18200/chat#token=" + "a".repeat(43)]) {
    const item = recovery(neverFetch);
    assert.equal(await item.resolveStartUrl(ctx(), url), url);
    assert.equal(item.state().reason, "owner_auth_unavailable");
  }
  for (const altered of [ { session_id: "../../private" }, { update_id: "su_other" },
    { schema: true }, { expires_at: 1 }, { url: "https://example.com" }, { reopen_id: "x" } ]) {
    const item = recovery(async () => response({ ...intent(), ...altered }));
    assert.equal(await item.resolveStartUrl(ctx(), AUTH), AUTH);
    assert.equal(item.state().reason, "response_invalid");
  }
  for (const [status, body, reason] of [
    [401, {}, "owner_mismatch"], [409, { reason: "session_missing" }, "session_missing"],
    [409, { reason: "PRIVATE_TOKEN" }, "recovery_unavailable"],
  ]) {
    const item = recovery(async () => response(body, status));
    assert.equal(await item.resolveStartUrl(ctx(), AUTH), AUTH);
    assert.equal(item.state().reason, reason);
  }
  const huge = recovery(async () => new Response("x".repeat(8193)));
  assert.equal(await huge.resolveStartUrl(ctx(), AUTH), AUTH);
  assert.equal(huge.state().reason, "response_invalid");
  const offline = recovery(async () => { throw new Error("token=must-not-escape"); });
  assert.equal(await offline.resolveStartUrl(ctx(), AUTH), AUTH);
  assert.equal(offline.state().reason, "recovery_unavailable");
}

async function checkAckRetryAndIpc() {
  let posts = 0;
  const value = intent();
  const main = ctx();
  const item = recovery(async (_url, options) => {
    if (options.method === "POST" && ++posts === 1) throw new Error("lost response");
    return response({ ...value, status: options.method === "POST" ? "acknowledged" : "pending" });
  });
  await item.resolveStartUrl(main, AUTH);
  const handlers = new Map();
  registerReopenIpc({ ipcMain: { handle: (key, fn) => handlers.set(key, fn) },
    windows: new Map([["main", main]]), recovery: item, origin: ORIGIN });
  const valid = { sender: main.win.webContents, senderFrame: main.win.webContents.mainFrame };
  const loaded = handlers.get("self-update:session-loaded");
  assert.equal(await loaded({ sender: {}, senderFrame: {} }, "p1"), null);
  assert.equal(await loaded({ ...valid, senderFrame: { url: `${ORIGIN}/s/p1` } }, "p1"), null);
  main.navigate("https://example.com/s/p1");
  assert.equal(await loaded(valid, "p1"), null);
  main.navigate(`${ORIGIN}/s/p1`);
  assert.equal(posts, 0, "untrusted IPC cannot cause ACK");
  assert.equal((await loaded(valid, "p1")).status, "acknowledged");
  assert.equal(posts, 2, "lost ACK response is retried with the same identity");
  assert.equal(handlers.get("self-update:reopen-state")(valid).sessionId, "p1");

  let exposed;
  const invoked = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8"), {
    process: { argv: ["--openprogram-window-id=main"] },
    require: () => ({ contextBridge: { exposeInMainWorld: (_name, value) => { exposed = value; } },
      ipcRenderer: { invoke: (...args) => { invoked.push(args); }, on() {} }, webUtils: {} }),
  });
  exposed.selfUpdateReopen.sessionLoaded("p1");
  exposed.selfUpdateReopen.getState();
  assert.deepEqual(invoked, [["self-update:session-loaded", "p1"], ["self-update:reopen-state"]]);
}

async function checkVerificationTransport() {
  const main = ctx();
  const nonce = "c".repeat(64);
  const calls = [];
  const item = recovery(async (url, options) => {
    calls.push([url, options]);
    if (url.includes("desktop-verification")) return response({ ok: true, nonce });
    return response({ ...intent(), status: options.method === "POST" ? "acknowledged" : "pending" });
  });
  const abort = new AbortController();
  await assert.rejects(item.requestVerification(nonce, null, abort.signal), /unavailable/);
  await item.resolveStartUrl(main, AUTH);
  await assert.rejects(item.requestVerification(nonce, null, abort.signal), /unavailable/);
  await item.sessionLoaded(main, "p1");
  await assert.rejects(item.requestVerification("../../other", null, abort.signal), /unavailable/);
  assert.deepEqual(await item.requestVerification(nonce, null, abort.signal), { ok: true, nonce });
  await item.requestVerification(nonce, { screenshot: "private image" }, abort.signal);
  const [url, options] = calls.at(-1);
  assert.equal(url, `${ORIGIN}/api/self-updates/su_test/desktop-verification/${nonce}`);
  assert.equal(options.signal, abort.signal);
  assert.equal(options.redirect, "error");
  assert.equal(options.headers.Authorization, `Bearer ${"a".repeat(43)}`);
  assert.deepEqual(JSON.parse(options.body), { screenshot: "private image" });
  item.observeNavigation(main, `${ORIGIN}/s/other`);
  await assert.rejects(item.requestVerification(nonce, null, abort.signal), /unavailable/);
  assert.equal(JSON.stringify(item.state()).includes("a".repeat(43)), false);
}

checkWindowEntry().then(checkProtocol).then(checkFallbacks).then(checkAckRetryAndIpc).then(checkVerificationTransport)
  .then(() => console.log("self-update reopen checks passed"))
  .catch((error) => { console.error(error); process.exitCode = 1; });
