"use strict";
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createUiVerificationGuard } = require("../self-update-ui-guard");

const native = new EventEmitter();
const handlers = new Map();
native.handle = (name, fn) => handlers.set(name, fn);
let request, beforeHeaders;
const nonce = "a".repeat(64);
const session = { webRequest: { onBeforeRequest: (fn) => { request = fn; },
  onBeforeSendHeaders: (fn) => { beforeHeaders = fn; } } };
const main = { id: 7, session, isDestroyed: () => false };
const other = { id: 8, session, isDestroyed: () => false };
const guard = createUiVerificationGuard(native);
let calls = 0;
guard.ipcMain.on("desktop:open-external", () => { calls++; });
assert.equal(guard.ipcMain.on("chain", function () { assert.equal(this, native); }), guard.ipcMain);
native.emit("chain", { sender: main });
guard.ipcMain.handle("updates:download", () => { calls++; return "downloaded"; });
native.emit("desktop:open-external", { sender: main });
assert.equal(calls, 1);
const release = guard.acquire(main, nonce);
assert.throws(() => guard.acquire(main, nonce), /already guarded/);
native.emit("desktop:open-external", { sender: main });
assert.equal(calls, 1, "active main sender cannot cause native side effects");
assert.throws(() => handlers.get("updates:download")({ sender: main }), /verification/);
native.emit("desktop:open-external", { sender: other });
assert.equal(handlers.get("updates:download")({ sender: other }), "downloaded");
assert.equal(calls, 3, "other windows remain usable");
let result;
beforeHeaders({ webContentsId: 7, requestHeaders: { "x-openprogram-ui-check": "forged" } }, (value) => { result = value; });
assert.deepEqual(result, { requestHeaders: { "X-OpenProgram-UI-Check": nonce } });
request({ webContentsId: 7 }, (value) => { result = value; });
assert.deepEqual(result, { cancel: true });
request({ webContentsId: 8 }, (value) => { result = value; });
assert.deepEqual(result, { cancel: false });
request({ webContentsId: undefined }, (value) => { result = value; });
assert.deepEqual(result, { cancel: false });
release();
assert.equal(handlers.get("updates:download")({ sender: main }), "downloaded");
request({ webContentsId: 7 }, (value) => { result = value; });
assert.deepEqual(result, { cancel: false });
const second = guard.acquire(main, nonce);
release(); // A previous cleanup cannot release a newer capture's lease.
assert.throws(() => handlers.get("updates:download")({ sender: main }), /verification/);
second();
assert.equal(handlers.get("updates:download")({ sender: main }), "downloaded");
assert.throws(() => guard.acquire({ ...main, isDestroyed: () => true }, nonce), /unavailable/);
const failedSession = { webRequest: { onBeforeRequest() { throw new Error("hook denied"); }, onBeforeSendHeaders() {} } };
assert.throws(() => guard.acquire({ ...main, session: failedSession }, nonce), /hook denied/);
assert.equal(handlers.get("updates:download")({ sender: main }), "downloaded", "failed acquisition leaves no lease");
// Exercise the actual main-window popup/navigation handlers: these bypass IPC.
const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
let popup, navigate, external = 0;
main.setWindowOpenHandler = (fn) => { popup = fn; };
main.on = (_name, fn) => { navigate = fn; };
for (const marker of ['win.webContents.setWindowOpenHandler(', 'win.webContents.on("will-navigate",']) {
  const start = source.indexOf(marker);
  assert.ok(start > 0);
  const end = source.indexOf("\n  });", start) + "\n  });".length;
  vm.runInNewContext(source.slice(start, end), { win: { webContents: main }, uiVerificationGuard: guard,
    UI_ORIGIN: "http://127.0.0.1:18100", URL, shell: { openExternal() { external++; } } });
}
const navigationLease = guard.acquire(main, nonce);
assert.equal(popup({ url: "https://example.com" }).action, "deny");
let prevented = false;
navigate({ preventDefault() { prevented = true; } }, "https://example.com");
assert.equal(prevented, true);
assert.equal(external, 0, "native navigation cannot launch external browser while guarded");
navigationLease();
popup({ url: "https://example.com" });
assert.equal(external, 1, "ordinary navigation behavior preserved");
console.log("self-update native side-effect guard checks passed");
