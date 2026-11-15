"use strict";

// The background worker in the menu bar (macOS) or the notification area
// (Windows, Linux), so it is visible after every window is closed. The
// Assistant and the Butler keep running there, which is why closing a window
// or quitting the App does not stop the worker and asks nothing.
//
// The menu is the system's own: a status line, Open OpenProgram, and Stop
// background. Its icon is a single-colour version of the App icon
// (main/worker/tray/trayTemplate.png, packaged with the main process);
// without that file no tray is created, so nothing appears before the icon
// is approved.

const path = require("path");
const fs = require("fs");

const REFRESH_MS = 10_000;

/** The status line for a worker that answers its health probe, or not. */
function statusLabel(state) {
  if (!state || !state.running) return "Background: not running";
  return "Background: running";
}

/** The native menu's template for one worker state. */
function trayMenuTemplate(state, actions) {
  return [
    { label: statusLabel(state), enabled: false },
    { type: "separator" },
    { label: "Open OpenProgram", click: actions.open },
    {
      label: "Stop background",
      enabled: Boolean(state && state.running),
      toolTip: "Stops the background worker and quits OpenProgram",
      click: actions.stop,
    },
  ];
}

function trayIconPath(iconRoot) {
  return path.join(iconRoot, "trayTemplate.png");
}

/**
 * Create the tray when its icon exists. `probe()` resolves to
 * `{running}`; `open()` shows the main window; `stop()` stops the worker.
 * Returns `{refresh, destroy}`, or null without an icon.
 */
function createWorkerTray({ Tray, Menu, nativeImage, iconRoot, probe, open, stop,
  setInterval: every = setInterval, clearInterval: cancel = clearInterval, exists = fs.existsSync }) {
  const icon = trayIconPath(iconRoot);
  if (!exists(icon)) return null;
  const image = nativeImage.createFromPath(icon);
  if (process.platform === "darwin" && image.setTemplateImage) image.setTemplateImage(true);
  const tray = new Tray(image);
  tray.setToolTip("OpenProgram");
  let state = { running: false };
  const render = () => tray.setContextMenu(Menu.buildFromTemplate(trayMenuTemplate(state, {
    open,
    stop: async () => {
      await stop();
      await refresh();
    },
  })));
  async function refresh() {
    try {
      state = await probe();
    } catch {
      state = { running: false };
    }
    render();
    return state;
  }
  render();
  void refresh();
  const timer = every(() => { void refresh(); }, REFRESH_MS);
  return {
    refresh,
    destroy() {
      cancel(timer);
      tray.destroy();
    },
  };
}

module.exports = { REFRESH_MS, createWorkerTray, statusLabel, trayIconPath, trayMenuTemplate };
