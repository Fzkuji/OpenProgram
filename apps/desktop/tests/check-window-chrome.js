"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {
  applyNativeTitleBarChrome,
  browserWindowChromeOptions,
  registerNativeTitleBarIpc,
} = require("../main/windows/window-chrome");

const positions = [];
const win = {
  isDestroyed: () => false,
  webContents: { getZoomFactor: () => Math.sqrt(1 / 1.2) },
  setWindowButtonPosition: position => positions.push(position),
};
applyNativeTitleBarChrome(win, "darwin", { text: "#aaa" });
assert.deepEqual(positions, [{ x: 11, y: 10 }],
  "native controls must follow the actual renderer zoom and tab-row center");

// Run the actual preload, and deliver its notifications to the production
// main-process receiver. Menu zoom produces resize without zoom-changed.
const handlers = new Map();
let zoom = 1;
const frame = {};
const sender = { mainFrame: frame, getZoomFactor: () => zoom };
const owner = { ...win, webContents: sender };
registerNativeTitleBarIpc({
  ipcMain: { on: (channel, listener) => handlers.set(channel, listener) },
  BrowserWindow: { fromWebContents: () => owner },
  platform: "darwin", getChrome: () => ({ text: "#aaa" }),
});
const source = fs.readFileSync(require.resolve("../main/preload"), "utf8");
function preload(platform = "darwin", surface = "") {
  const listeners = new Map();
  vm.runInNewContext(source, {
    process: { platform, argv: surface ? [`--openprogram-surface=${surface}`] : [] },
    window: { addEventListener: (event, listener) => listeners.set(event, listener) },
    require: name => {
      assert.equal(name, "electron");
      return {
        contextBridge: { exposeInMainWorld() {} }, webUtils: {},
        ipcRenderer: {
          on() {},
          send: channel => handlers.get(channel)({ sender, senderFrame: frame }),
        },
      };
    },
  });
  return listeners;
}
const resize = preload().get("resize");
assert.equal(typeof resize, "function");
positions.length = 0;
resize();
zoom = 1.25;
resize();
zoom = Math.sqrt(1 / 1.2);
resize();
assert.deepEqual(positions, [{ x: 13, y: 12 }, { x: 18, y: 17 }, { x: 11, y: 10 }]);

positions.length = 0;
const receiver = handlers.get("window:sync-chrome");
receiver({ sender: {}, senderFrame: frame }); // Child view or another window.
receiver({ sender, senderFrame: {} }); // Subframe in the owning renderer.
assert.deepEqual(positions, []);
assert.equal(preload("darwin", "browser-control").has("resize"), false);
assert.equal(preload("win32").has("resize"), false);
assert.equal(preload("linux").has("resize"), false);

for (const invalid of [NaN, 0, -1, Infinity]) {
  zoom = invalid;
  resize();
  assert.deepEqual(positions.pop(), { x: 13, y: 12 });
}
zoom = 0.25;
resize();
assert.deepEqual(positions.pop(), { x: 1, y: 0 });
applyNativeTitleBarChrome({ isDestroyed: () => true }, "darwin", {});
applyNativeTitleBarChrome({ webContents: { isDestroyed: () => true } }, "darwin", {});
assert.deepEqual(positions, []);

assert.deepEqual(browserWindowChromeOptions("linux", { text: "#aaa" }), { autoHideMenuBar: true });
const menuRow = [];
applyNativeTitleBarChrome({
  isDestroyed: () => false,
  setMenuBarVisibility: (visible) => menuRow.push(visible),
}, "linux", { text: "#aaa" });
assert.deepEqual(menuRow, [false], "Linux spends no permanent row on the application menu");

const main = fs.readFileSync(require.resolve("../main"), "utf8");
assert.match(main, /registerNativeTitleBarIpc\(\{ ipcMain, BrowserWindow,/);
for (const event of ["did-finish-load", "leave-full-screen"]) {
  assert.ok(main.includes(`on("${event}", () => {\n    applyNativeTitleBarChrome(win, process.platform, currentChrome);`));
}
const css = fs.readFileSync(require.resolve("../../web/components/center-tabs/strip/tab-strip.module.css"), "utf8");
assert.match(css, /\.strip\s*\{[^}]*height: 40px;/);
console.log("native window chrome: Linux menu row, macOS geometry, preload resize, sender ownership and lifecycle checks passed");
