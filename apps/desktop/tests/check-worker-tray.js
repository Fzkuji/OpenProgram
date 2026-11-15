const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { createWorkerTray, statusLabel, trayMenuTemplate } = require("../main/worker/worker-tray");

// The menu follows the worker's state.
assert.equal(statusLabel({ running: true }), "Background: running");
assert.equal(statusLabel({ running: false }), "Background: not running");
const actions = { open: () => {}, stop: () => {} };
const running = trayMenuTemplate({ running: true }, actions);
assert.deepEqual(running.map((item) => item.label || item.type),
  ["Background: running", "separator", "Open OpenProgram", "Stop background"]);
assert.equal(running[0].enabled, false);
assert.equal(running[3].enabled, true);
assert.equal(trayMenuTemplate({ running: false }, actions)[3].enabled, false);

// No approved icon, no tray.
assert.equal(createWorkerTray({ iconRoot: "/nowhere", exists: () => false }), null);

async function stopsAndRefreshes() {
  const menus = [];
  let stopped = 0;
  let alive = true;
  class Tray {
    setToolTip() {}
    setContextMenu(menu) { menus.push(menu); }
    destroy() { this.destroyed = true; }
  }
  const tray = createWorkerTray({
    Tray,
    Menu: { buildFromTemplate: (template) => template },
    nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
    iconRoot: "/icons",
    exists: () => true,
    probe: async () => ({ running: alive }),
    open: () => {},
    stop: async () => { stopped += 1; alive = false; },
    setInterval: () => 1,
    clearInterval: () => {},
  });
  await tray.refresh();
  assert.equal(menus.at(-1)[0].label, "Background: running");
  await menus.at(-1)[3].click();
  assert.equal(stopped, 1);
  assert.equal(menus.at(-1)[0].label, "Background: not running");
  assert.equal(menus.at(-1)[3].enabled, false);
  tray.destroy();
}

// main.js creates the tray once the App is ready and stops the worker the
// way switching versions does before quitting.
const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
assert.match(main, /buildMenu\(\);\n\s+createBackgroundTray\(\);/);
assert.match(main, /if \(shellContext\) await shellContext\.stopWorker\(\);\n\s+else spawnWorker\("stop"\);\n\s+app\.quit\(\);/);
assert.match(main, /iconRoot: path\.join\(__dirname, "main", "worker", "tray"\)/);

stopsAndRefreshes().then(() => console.log("worker tray: ok"));
