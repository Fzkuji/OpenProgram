"use strict";
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { registerUiVerificationIpc } = require("../self-update-ui");

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
  contents.capturePage = async () => {
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
  registerUiVerificationIpc({ ipcMain: { handle: (name, fn) => handlers.set(name, fn) }, windows, origin,
    // Filesystem/process installation boundary only; capture and IPC are real module calls.
    installation: () => ({ app_path: "/Applications/OpenProgram.app", app_pid: 55, candidate_sha: revision }),
    request: async (id, body) => {
      if (deny) throw new Error("no active verifier request");
      assert.equal(id, nonce);
      if (!body) return contract;
      assert.equal(attached, false, "upload follows debugger cleanup");
      assert.equal(contents.listenerCount("before-input-event"), 0);
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
  for (const failure of ["denied", "route", "overlay", "menu", "debugger", "revision", "expired", "action",
    "malformed", "empty", "output", "input", "renderer", "timeout", "cleanup"]) {
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
    const failed = await handler(event, nonce);
    assert.equal(failed.ok, false, failure);
    assert.equal(typeof failed.reason, "string", failure);
    assert.equal(uploads, previousUploads, `${failure} must not publish evidence`);
    assert.equal(contents.listenerCount("before-input-event"), 0, failure);
    assert.equal(win.listenerCount("resize"), 0, failure);
    if (failure === "debugger") assert.equal(attached, true, "must not detach another debugger");
    Object.assign(contract, saved, { deadline: Date.now() / 1000 + 10 });
    deny = false;
    ctx.visibleViewIds.clear();
    ctx.mainMenuView = null;
    contents.mainFrame.url = `${origin}/s/p1`;
    contents.getOSProcessId = () => 1234;
    contents.capturePage = capture;
    contents.debugger.detach = () => { attached = false; };
    attached = false;
  }
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
    ipcMain: {}, windows, app: {}, UI_ORIGIN: origin, selfUpdateReopen: { requestVerification() {} } });
  assert.equal(registered, true);
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../package.json")));
  assert.ok(packageJson.build.files.includes("self-update-ui.js"));
  console.log("self-update main-window capture checks passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
