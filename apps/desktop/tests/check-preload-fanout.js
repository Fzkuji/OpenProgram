// Channels many owners subscribe to (every web tab, every bookmark folder)
// keep one Electron listener each, so the renderer never trips Electron's
// 10-listener warning, and every subscriber still hears every event.
const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

const listeners = new Map();
const ipcRenderer = {
  on(channel, listener) { listeners.set(channel, [...(listeners.get(channel) || []), listener]); },
  removeListener(channel, listener) { listeners.set(channel, (listeners.get(channel) || []).filter((l) => l !== listener)); },
  send() {}, invoke: async () => null, sendSync: () => null,
};
const exposed = {};
const electron = { contextBridge: { exposeInMainWorld: (key, api) => { exposed[key] = api; } }, ipcRenderer, webUtils: { getPathForFile: () => "" } };
const load = Module._load;
Module._load = function (request, ...rest) { return request === "electron" ? electron : load.call(this, request, ...rest); };
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
require(path.join(__dirname, "..", "main", "preload.js"));
Module._load = load;

const api = Object.values(exposed).find((value) => value && value.mainMenu);
assert.ok(api, "preload exposes the main menu");
const heard = [];
const offs = Array.from({ length: 15 }, (_, i) => api.mainMenu.onAction((id) => heard.push([i, id])));
assert.equal(listeners.get("main-menu:action").length, 1, "fifteen subscribers share one Electron listener");
listeners.get("main-menu:action")[0]({}, "bookmark:1");
assert.equal(heard.length, 15, "every subscriber hears the action");
assert.deepEqual(heard.map(([i]) => i), [...Array(15).keys()], "in subscription order");
offs.forEach((off) => off());
assert.equal(listeners.get("main-menu:action").length, 0, "the listener goes with the last subscriber");
const off = api.webTab.onState(() => {});
assert.equal(listeners.get("webtab:state").length, 1);
off();
console.log("preload fan-out: shared listeners OK");
