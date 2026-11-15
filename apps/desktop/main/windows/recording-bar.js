// The floating bar of a demonstration recording.
//
// A window asks for the bar when the worker has started a recording. The
// bar is a small frameless window above every other window; the window
// that asked is minimized so the user can work in other apps. When the
// bar reports the result (stopped or discarded) it closes, the asking
// window comes back and hears the result on "recording:finished".

// The bar's own size, and the transparent room around it for the popover
// shadow (the menus' GUTTER); the page centres the bar in the window.
const BAR_WIDTH = 340;
const BAR_HEIGHT = 44;
const GUTTER = 24;
const WIN_WIDTH = BAR_WIDTH + GUTTER * 2;
const WIN_HEIGHT = BAR_HEIGHT + GUTTER * 2;

function createRecordingBar({ BrowserWindow, ipcMain, path, screen, uiOrigin }) {
  let bar = null;
  let owner = null;      // the webContents that opened the bar
  let ownerWindow = null;

  function deliver(result) {
    const target = owner;
    const win = ownerWindow;
    owner = null;
    ownerWindow = null;
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
    }
    if (target && !target.isDestroyed()) target.send("recording:finished", result);
  }

  function close() {
    const win = bar;
    bar = null;
    if (win && !win.isDestroyed()) win.close();
  }

  function open(event, options) {
    const id = String(options?.id || "");
    if (!/^[0-9a-f]{6,32}$/.test(id)) return false;
    if (bar && !bar.isDestroyed()) return false;
    owner = event.sender;
    ownerWindow = BrowserWindow.fromWebContents(event.sender);
    const near = ownerWindow && !ownerWindow.isDestroyed() ? ownerWindow.getBounds() : null;
    const display = near ? screen.getDisplayMatching(near) : screen.getPrimaryDisplay();
    const area = display.workArea;
    // The bar renders at the App's zoom (same origin), so the window grows with it.
    const zoom = Number(event.sender.getZoomFactor?.()) || 1;
    const winWidth = Math.round(WIN_WIDTH * zoom);
    const winHeight = Math.round(WIN_HEIGHT * zoom);
    const query = new URLSearchParams({
      id,
      max: String(Math.max(1, Number(options?.maxSeconds) || 600)),
      mic: options?.mic ? "1" : "0",
      device: String(options?.deviceId || ""),
    });
    const win = new BrowserWindow({
      width: winWidth,
      height: winHeight,
      x: Math.round(area.x + (area.width - winWidth) / 2),
      y: Math.round(area.y + 12 - GUTTER * zoom),
      frame: false,
      transparent: true,
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      hasShadow: false,
      alwaysOnTop: true,
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "..", "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        additionalArguments: [
          "--openprogram-window-id=recording-bar",
          "--openprogram-surface=recording-bar",
        ],
      },
    });
    win.setAlwaysOnTop(true, "floating");
    if (typeof win.setMenuBarVisibility === "function") win.setMenuBarVisibility(false);
    bar = win;
    win.once("ready-to-show", () => {
      if (!win.isDestroyed()) win.showInactive();
      if (ownerWindow && !ownerWindow.isDestroyed()) ownerWindow.minimize();
    });
    win.on("closed", () => {
      if (bar === win) bar = null;
      // Closed without a result (crash, quit): bring the window back anyway.
      if (owner) deliver({ id, summary: null, error: "The recording bar closed." });
    });
    win.loadURL(`${uiOrigin()}/menu-overlay/recording-bar?${query}`).catch(() => {});
    return true;
  }

  ipcMain.handle("recording:open-bar", (event, options) => open(event, options));
  ipcMain.on("recording:finish", (event, result) => {
    if (!bar || bar.isDestroyed() || event.sender !== bar.webContents) return;
    const summary = result && typeof result === "object" ? result : {};
    deliver({
      id: String(summary.id || ""),
      summary: summary.summary && typeof summary.summary === "object" ? summary.summary : null,
      ...(summary.error ? { error: String(summary.error).slice(0, 500) } : {}),
    });
    close();
  });
}

module.exports = { createRecordingBar };
