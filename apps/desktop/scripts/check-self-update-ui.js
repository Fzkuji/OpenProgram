"use strict";
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { registerUiVerificationIpc } = require("../self-update-ui");
const { createUiVerificationGuard } = require("../self-update-ui-guard");

const origin = "http://127.0.0.1:18100";
const nonce = "a".repeat(64);
const revision = "b".repeat(40);

async function main() {
  const handlers = new Map();
  const contents = new EventEmitter();
  contents.mainFrame = { url: `${origin}/s/p1` };
  contents.id = 7;
  contents.isDestroyed = () => false;
  contents.getURL = () => contents.mainFrame.url;
  contents.getOSProcessId = () => 1234;
  let requestGuard;
  contents.session = { webRequest: { onBeforeRequest(fn) { requestGuard = fn; }, onBeforeSendHeaders() {} } };
  const nativeIpc = new EventEmitter();
  nativeIpc.handle = (name, fn) => handlers.set(name, fn);
  const guard = createUiVerificationGuard(nativeIpc);
  let nativeMutations = 0;
  guard.ipcMain.on("test:mutation", () => { nativeMutations++; });
  let attached = false;
  contents.debugger = {
    isAttached: () => attached,
    attach() { attached = true; },
    detach() { attached = false; },
    async sendCommand(command) {
      if (command === "Target.getTargetInfo") return { targetInfo: { targetId: "main-target" } };
      assert.equal(command, "Accessibility.getFullAXTree");
      return { nodes: [{ nodeId: "1", ignored: false, role: { value: "RootWebArea" }, name: { value: "Original session" } }] };
    },
  };
  let captures = 0;
  const attributes = new Map();
  const area = { scrollTop: 500, scrollLeft: 0, scrollHeight: 2000, clientHeight: 600,
    isConnected: true, style: { scrollBehavior: "smooth" }, contains: () => true,
    getBoundingClientRect: () => ({ width: 800 }), hasAttribute: (name) => attributes.has(name),
    getAttribute: (name) => attributes.get(name), setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name) };
  let perspective = "session";
  const viewAttributes = new Map([["data-session-id", "p1"], ["data-tab-id", "s:p1"]]);
  const pane = { contains: () => true, getAttribute: () => perspective };
  const toggle = { disabled: false, closest: () => pane, hasAttribute: (name) => viewAttributes.has(name),
    getAttribute: (name) => name === "aria-pressed" ? String(perspective === "dag") : viewAttributes.get(name),
    setAttribute: (name, value) => viewAttributes.set(name, value), removeAttribute: (name) => viewAttributes.delete(name),
    click: () => { perspective = perspective === "dag" ? "session" : "dag"; } };
  const isolated = vm.createContext({ document: { getElementById: (id) => id === "chatArea" ? area : id === "sessionPerspectiveToggle" ? toggle : {},
      querySelectorAll: () => [area] }, location: { pathname: "/s/p1" }, setTimeout, clearTimeout,
      requestAnimationFrame: (fn) => setImmediate(fn) });
  contents.executeJavaScriptInIsolatedWorld = async (world, scripts, gesture) => {
    assert.equal(world, 18371);
    assert.equal(gesture, false);
    return vm.runInContext(scripts[0].code, isolated);
  };
  contents.capturePage = async () => {
    let result;
    requestGuard({ webContentsId: contents.id }, (value) => { result = value; });
    assert.deepEqual(result, { cancel: true }, "capture has native network isolation");
    nativeIpc.emit("test:mutation", { sender: contents });
    assert.equal(nativeMutations, 0, "capture cannot invoke native mutation");
    if (contract.interaction?.kind === "view") {
      assert.equal(perspective, contract.interaction.target, "capture observes the approved perspective");
    } else if (contract.interaction) {
      assert.equal(area.scrollTop, 100, "screenshot observes approved post-scroll state");
      assert.equal(attributes.get("data-self-update-verification"), nonce);
    }
    captures++;
    return { isEmpty: () => false, getSize: () => ({ width: 800, height: 600 }), toPNG: () => Buffer.from("fixture-png") };
  };
  const win = new EventEmitter();
  Object.assign(win, { id: 1, webContents: contents, isDestroyed: () => false,
    isVisible: () => true, isMinimized: () => false, getBounds: () => ({ x: 0, y: 0, width: 800, height: 600 }) });
  const ctx = { id: "main", win, visibleViewIds: new Set() };
  const windows = new Map([["main", ctx]]);
  let uploaded;
  let uploads = 0;
  let deny = false;
  const contract = { schema: 1, nonce, update_id: "su_test", attempt: 1, session_id: "p1",
    candidate_sha: revision, worker_pid: 99, check_id: "main-view", deadline: Date.now() / 1000 + 10,
    max_output_bytes: 262144, action: "capture" };
  registerUiVerificationIpc({ ipcMain: guard.ipcMain, guard, windows, origin,
    // Filesystem/process installation boundary only; capture and IPC are real module calls.
    installation: () => ({ app_path: "/Applications/OpenProgram.app", app_pid: 55, candidate_sha: revision }),
    request: async (id, body) => {
      if (deny) throw new Error("no active verifier request");
      assert.equal(id, nonce);
      if (!body) return contract;
      assert.equal(attached, false, "upload follows debugger cleanup");
      assert.equal(contents.listenerCount("before-input-event"), 0);
      let result;
      requestGuard({ webContentsId: contents.id }, (value) => { result = value; });
      assert.deepEqual(result, { cancel: false }, "upload follows native guard cleanup");
      uploaded = body;
      uploads++;
      return { ok: true, nonce };
    },
  });
  const handler = handlers.get("self-update:ui-capture");
  assert.equal(typeof handler, "function");
  const event = { sender: contents, senderFrame: contents.mainFrame };
  assert.deepEqual(await handler(event, nonce), { ok: true });
  assert.equal(captures, 1);
  assert.equal(uploaded.identity.target_id, "main-target");
  assert.equal(uploaded.identity.renderer_pid, 1234);
  assert.equal(uploaded.screenshot.mime_type, "image/png");
  assert.equal(uploaded.accessibility.nodes[0].name.value, "Original session");
  assert.equal(uploaded.cleanup_complete, true);
  assert.equal((await handler({ ...event, senderFrame: {} }, nonce)).ok, false);
  assert.equal(captures, 1, "subframe cannot capture");
  const saved = { ...contract };
  const capture = contents.capturePage;
  const acquire = guard.acquire;
  for (const failure of ["denied", "route", "overlay", "menu", "debugger", "revision", "expired", "action",
    "malformed", "empty", "output", "input", "renderer", "timeout", "cleanup", "guard"]) {
    const previousUploads = uploads;
    if (failure === "denied") deny = true;
    if (failure === "route") contents.mainFrame.url = `${origin}/s/other`;
    if (failure === "overlay") ctx.visibleViewIds.add("webtab");
    if (failure === "menu") ctx.mainMenuView = {};
    if (failure === "debugger") attached = true;
    if (failure === "revision") contract.candidate_sha = "c".repeat(40);
    if (failure === "expired") contract.deadline = 0;
    if (failure === "action") contract.action = "click";
    if (failure === "malformed") contract.session_id = undefined;
    if (failure === "empty") contents.capturePage = async () => ({ isEmpty: () => true });
    if (failure === "output") contract.max_output_bytes = 1;
    if (failure === "input") contents.capturePage = async () => { contents.emit("before-input-event", {}); return capture(); };
    if (failure === "renderer") contents.capturePage = async () => { contents.getOSProcessId = () => 5678; return capture(); };
    if (failure === "timeout") {
      contract.deadline = Date.now() / 1000 + .05;
      contents.capturePage = () => new Promise(() => {});
    }
    if (failure === "cleanup") contents.debugger.detach = () => { throw new Error("detach failed"); };
    if (failure === "guard") guard.acquire = () => { throw new Error("guard unavailable"); };
    const failed = await handler(event, nonce);
    assert.equal(failed.ok, false, failure);
    assert.equal(typeof failed.reason, "string", failure);
    assert.equal(uploads, previousUploads, `${failure} must not publish evidence`);
    assert.equal(contents.listenerCount("before-input-event"), 0, failure);
    assert.equal(win.listenerCount("resize"), 0, failure);
    let network;
    requestGuard({ webContentsId: contents.id }, (value) => { network = value; });
    assert.deepEqual(network, { cancel: false }, `${failure} releases native request isolation`);
    if (failure === "debugger") assert.equal(attached, true, "must not detach another debugger");
    Object.assign(contract, saved, { deadline: Date.now() / 1000 + 10 });
    deny = false;
    ctx.visibleViewIds.clear();
    ctx.mainMenuView = null;
    contents.mainFrame.url = `${origin}/s/p1`;
    contents.getOSProcessId = () => 1234;
    contents.capturePage = capture;
    contents.debugger.detach = () => { attached = false; };
    guard.acquire = acquire;
    attached = false;
  }
  contract.interaction = { kind: "scroll", delta_y: -400 };
  assert.deepEqual(await handler(event, nonce), { ok: true });
  assert.equal(uploaded.interaction.before.top, 500);
  assert.equal(uploaded.interaction.after.top, 100);
  assert.equal(uploaded.interaction.restored.top, 500);
  assert.equal(area.scrollTop, 500);
  assert.equal(area.style.scrollBehavior, "smooth");
  assert.equal(attributes.size, 0, "temporary persistence marker removed");
  contents.capturePage = async () => { throw new Error("capture failed after scroll"); };
  assert.equal((await handler(event, nonce)).ok, false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(area.scrollTop, 500, "failed capture restores own scroll");
  assert.equal(attributes.size, 0);
  contents.capturePage = async () => { area.scrollTop = 250; contents.emit("before-input-event", {}); throw new Error("capture aborted"); };
  assert.equal((await handler(event, nonce)).ok, false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(area.scrollTop, 250, "user scroll must not be overwritten by cleanup");
  assert.equal(attributes.size, 0);
  contents.capturePage = capture;
  contract.interaction = { kind: "view", target: "dag" };
  assert.deepEqual(await handler(event, nonce), { ok: true });
  assert.equal(uploaded.interaction.before, "session");
  assert.equal(uploaded.interaction.after, "dag");
  assert.equal(uploaded.interaction.restored, "session");
  assert.equal(perspective, "session");
  assert.equal(viewAttributes.has("data-self-update-view"), false);
  contents.capturePage = async () => { throw new Error("capture failed after switch"); };
  assert.equal((await handler(event, nonce)).ok, false);
  assert.equal(perspective, "session");
  contents.capturePage = async () => { contents.emit("before-input-event", {}); throw new Error("user interrupted view"); };
  assert.equal((await handler(event, nonce)).ok, false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(perspective, "dag", "interruption does not change the user's perspective");
  assert.equal(viewAttributes.has("data-self-update-view"), false);
  perspective = "session";
  contents.capturePage = capture;
  viewAttributes.set("data-tab-id", "s:other");
  viewAttributes.set("data-session-id", "other");
  assert.equal((await handler(event, nonce)).ok, false, "foreign conversation never switches");
  assert.equal(perspective, "session");
  viewAttributes.set("data-tab-id", "s:p1");
  viewAttributes.set("data-session-id", "p1");
  const click = toggle.click;
  toggle.click = () => { if (perspective === "session") click(); };
  assert.equal((await handler(event, nonce)).ok, false, "failed restoration cannot publish success");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(viewAttributes.has("data-self-update-view"), false);
  toggle.click = click;
  perspective = "session";
  delete contract.interaction;
  // Exercise the production preload entry and main registration, not a copied
  // renderer shim. Electron itself is not launched by this component test.
  let bridge;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8"), {
    process: { argv: [] },
    require: () => ({ contextBridge: { exposeInMainWorld: (_name, value) => { bridge = value; } },
      ipcRenderer: { on() {}, invoke: (name, value) => handlers.get(name)(event, value) }, webUtils: {} }),
  });
  assert.deepEqual(await bridge.selfUpdateCapture(nonce), { ok: true });
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const wiring = source.slice(source.indexOf("registerUiVerificationIpc({ ipcMain"), source.indexOf("async function createWindow(options"));
  let registered = false;
  vm.runInNewContext(wiring, { registerUiVerificationIpc: (options) => { registered = options.windows === windows; },
    ipcMain: {}, uiVerificationGuard: guard, windows, app: {}, UI_ORIGIN: origin, selfUpdateReopen: { requestVerification() {} } });
  assert.equal(registered, true);
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../package.json")));
  assert.ok(packageJson.build.files.includes("self-update-ui.js"));
  assert.ok(packageJson.build.files.includes("self-update-ui-guard.js"));
  console.log("self-update main-window capture checks passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
