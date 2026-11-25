// The screenshot tool, as Snow Shot does it: a shortcut (or the composer's
// + menu, or the tray) freezes every display, the user selects a region or
// a window on it and marks it up, then copies it, saves it, pins it on the
// screen or puts it into the conversation's message box.
//
// The main process captures each display once (desktopCapturer, at the
// display's own pixels) and shows one borderless overlay window per
// display above everything else. The overlay is the page
// /menu-overlay/screenshot, which draws the frozen picture, the selection
// and the marks; it hands back the finished PNG with what to do with it.
// Overlays stay alive hidden after the first capture, so the next one
// appears at once. A pin is a small window of its own
// (/menu-overlay/screenshot-pin) that the main process moves, zooms and
// fades as the page asks.

const SHORTCUT = "CommandOrControl+Shift+2";
const PIN_MIN_SCALE = 0.1;
const PIN_MAX_SCALE = 5;
const PIN_MIN_OPACITY = 0.25;
const DRAG_FRAME_MS = 16;
// The + menu closes before the capture, so it is not in the picture.
const MENU_CLOSE_MS = 180;

/** The window id inside a capture source id ("window:<id>:<n>"). */
function sourceWindowId(mediaSourceId) {
  const parts = String(mediaSourceId || "").split(":");
  const id = Number(parts[1]);
  return parts[0] === "window" && Number.isFinite(id) && id > 0 ? id : 0;
}

/** Windows from the Worker in one display's own coordinates (DIP, origin at its top-left), clipped to it. */
function windowsForDisplay(listing, display, toDip) {
  const out = [];
  const b = display.bounds;
  for (const window of listing?.windows || []) {
    const frame = Array.isArray(window.frame) ? window.frame.map(Number) : [];
    if (frame.length !== 4 || !frame.every(Number.isFinite)) continue;
    const r = toDip({ x: frame[0], y: frame[1], width: frame[2], height: frame[3] });
    const x0 = Math.max(r.x, b.x);
    const y0 = Math.max(r.y, b.y);
    const x1 = Math.min(r.x + r.width, b.x + b.width);
    const y1 = Math.min(r.y + r.height, b.y + b.height);
    if (x1 - x0 < 8 || y1 - y0 < 8) continue;
    out.push({
      title: String(window.title || window.app || "").slice(0, 200),
      app: String(window.app || "").slice(0, 100),
      rect: { x: x0 - b.x, y: y0 - b.y, width: x1 - x0, height: y1 - y0 },
    });
  }
  return out;
}

/** A file name for a saved screenshot: "Screenshot 2026-11-24 10.39.20.png". */
function screenshotName(date, chinese) {
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}.${pad(date.getMinutes())}.${pad(date.getSeconds())}`;
  return `${chinese ? "截图" : "Screenshot"} ${stamp}.png`;
}

/** The pin's new bounds after zooming to `scale`, keeping the point under the cursor where it is. */
function zoomedBounds(bounds, size, scale, anchor) {
  const width = Math.max(1, Math.round(size.width * scale));
  const height = Math.max(1, Math.round(size.height * scale));
  const fx = bounds.width ? (anchor.x - bounds.x) / bounds.width : 0.5;
  const fy = bounds.height ? (anchor.y - bounds.y) / bounds.height : 0.5;
  return {
    x: Math.round(anchor.x - fx * width),
    y: Math.round(anchor.y - fy * height),
    width,
    height,
  };
}

function createScreenshot({
  app, BrowserWindow, Menu, ipcMain, globalShortcut, desktopCapturer, screen, clipboard, dialog,
  nativeImage, path, fs, uiOrigin, fetchJson, targetWindow, platform = process.platform,
  setTimeout: later = setTimeout, setInterval: every = setInterval, clearInterval: stopEvery = clearInterval,
}) {
  const overlays = new Map(); // display id -> { win, ready }
  const pins = new Map();     // pin id -> { win, png, size, scale, opacity, drag }
  let session = null;          // { frames: Map(display id -> payload), windows: Promise, started }
  let capturing = false;
  let nextPin = 1;
  const preload = path.join(__dirname, "..", "preload.js");

  const isOverlay = (event) => [...overlays.values()].some((o) => o.win.webContents === event.sender);
  const pinFor = (event) => [...pins.values()].find((p) => p.win.webContents === event.sender) || null;
  const overlayFor = (event) => [...overlays.entries()].find(([, o]) => o.win.webContents === event.sender) || null;

  function dropOverlays() {
    for (const { win } of overlays.values()) if (!win.isDestroyed()) win.destroy();
    overlays.clear();
  }
  // A display added, removed or changed gets a fresh overlay next time.
  for (const name of ["display-added", "display-removed", "display-metrics-changed"]) {
    screen.on(name, () => { if (!session) dropOverlays(); });
  }

  function overlayWindow(display) {
    const existing = overlays.get(display.id);
    if (existing && !existing.win.isDestroyed()) return existing;
    const { x, y, width, height } = display.bounds;
    const win = new BrowserWindow({
      x, y, width, height,
      show: false,
      frame: false,
      transparent: false,
      backgroundColor: "#000000",
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      hasShadow: false,
      enableLargerThanScreen: true,
      roundedCorners: false,
      alwaysOnTop: true,
      // A panel takes the keyboard without bringing the App forward on macOS,
      // so the application the user was in is in front again afterwards.
      ...(platform === "darwin" ? { type: "panel" } : {}),
      webPreferences: {
        preload,
        contextIsolation: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        additionalArguments: ["--openprogram-window-id=screenshot", "--openprogram-surface=screenshot"],
      },
    });
    win.setAlwaysOnTop(true, "screen-saver");
    if (platform === "darwin") win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
    if (typeof win.setMenuBarVisibility === "function") win.setMenuBarVisibility(false);
    const record = { win, ready: false };
    overlays.set(display.id, record);
    win.on("closed", () => { if (overlays.get(display.id) === record) overlays.delete(display.id); });
    win.loadURL(`${uiOrigin()}/menu-overlay/screenshot?display=${encodeURIComponent(String(display.id))}`).catch(() => {});
    return record;
  }

  function toDipFor(listing) {
    if (!listing?.pixels) return (r) => r;
    if (platform === "win32" && typeof screen.screenToDipRect === "function") {
      return (r) => screen.screenToDipRect(null, r);
    }
    return (r) => {
      const scale = screen.getDisplayNearestPoint({ x: r.x, y: r.y }).scaleFactor || 1;
      return { x: r.x / scale, y: r.y / scale, width: r.width / scale, height: r.height / scale };
    };
  }

  async function listWindows(displays) {
    const exclude = [...overlays.values()].map((o) => sourceWindowId(o.win.getMediaSourceId?.())).filter(Boolean);
    try {
      const listing = await fetchJson(`/api/screenshot/windows?exclude=${exclude.join(",")}`);
      const toDip = toDipFor(listing);
      return new Map(displays.map((d) => [d.id, windowsForDisplay(listing, d, toDip)]));
    } catch (_error) {
      return new Map();
    }
  }

  async function capture(displays) {
    const largest = displays.reduce((size, d) => ({
      width: Math.max(size.width, Math.round(d.size.width * d.scaleFactor)),
      height: Math.max(size.height, Math.round(d.size.height * d.scaleFactor)),
    }), { width: 1, height: 1 });
    const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: largest });
    const frames = new Map();
    displays.forEach((display, index) => {
      const source = sources.find((s) => String(s.display_id) === String(display.id))
        || (sources.length === displays.length ? sources[index] : null);
      if (!source || source.thumbnail.isEmpty()) return;
      const width = Math.round(display.size.width * display.scaleFactor);
      const height = Math.round(display.size.height * display.scaleFactor);
      let image = source.thumbnail;
      const got = image.getSize();
      if (got.width !== width || got.height !== height) image = image.resize({ width, height, quality: "best" });
      frames.set(display.id, { image, width, height });
    });
    return frames;
  }

  async function start() {
    if (capturing || session) return false;
    capturing = true;
    try {
      // The App window to send to is the one in front now, before an overlay takes the focus.
      const target = targetWindow();
      const displays = screen.getAllDisplays();
      const cursor = screen.getCursorScreenPoint();
      const frames = await capture(displays);
      if (!frames.size) return false;
      session = { frames, displays, cursor, target, windows: Promise.resolve(new Map()), started: Date.now() };
      const shown = displays.filter((display) => frames.has(display.id)).map(overlayWindow);
      // Listed once the overlays exist, so they are left out of the list.
      session.windows = listWindows(displays);
      for (const overlay of shown) {
        if (overlay.ready && !overlay.win.isDestroyed()) overlay.win.webContents.send("screenshot:begin");
      }
      return true;
    } catch (_error) {
      session = null;
      return false;
    } finally {
      capturing = false;
    }
  }

  function end() {
    session = null;
    for (const { win } of overlays.values()) {
      if (win.isDestroyed()) continue;
      win.hide();
      win.webContents.send("screenshot:end");
    }
  }

  async function save(png) {
    const chinese = String(app.getLocale?.() || "").toLowerCase().startsWith("zh");
    let folder;
    try { folder = app.getPath("pictures"); } catch (_error) { folder = app.getPath("home"); }
    const result = await dialog.showSaveDialog({
      defaultPath: path.join(folder, screenshotName(new Date(), chinese)),
      filters: [{ name: "PNG", extensions: ["png"] }],
    });
    if (result.canceled || !result.filePath) return false;
    await fs.promises.writeFile(result.filePath, png);
    return true;
  }

  function pin(png, rect, display) {
    const image = nativeImage.createFromBuffer(png);
    if (image.isEmpty()) return false;
    const bounds = {
      x: Math.round(display.bounds.x + rect.x),
      y: Math.round(display.bounds.y + rect.y),
      width: Math.max(1, Math.round(rect.width)),
      height: Math.max(1, Math.round(rect.height)),
    };
    const id = nextPin++;
    const win = new BrowserWindow({
      ...bounds,
      show: false,
      frame: false,
      transparent: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      hasShadow: true,
      alwaysOnTop: true,
      enableLargerThanScreen: true,
      ...(platform === "darwin" ? { type: "panel" } : {}),
      webPreferences: {
        preload,
        contextIsolation: true,
        nodeIntegration: false,
        additionalArguments: ["--openprogram-window-id=screenshot-pin", "--openprogram-surface=screenshot-pin"],
      },
    });
    win.setAlwaysOnTop(true, "floating");
    if (platform === "darwin") win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
    if (typeof win.setMenuBarVisibility === "function") win.setMenuBarVisibility(false);
    const record = { win, png, size: { width: bounds.width, height: bounds.height }, scale: 1, opacity: 1, drag: null };
    pins.set(id, record);
    win.once("ready-to-show", () => { if (!win.isDestroyed()) win.showInactive(); });
    win.on("closed", () => {
      if (record.drag) stopEvery(record.drag.timer);
      pins.delete(id);
    });
    win.loadURL(`${uiOrigin()}/menu-overlay/screenshot-pin?id=${id}`).catch(() => {});
    return true;
  }

  function send(png, chosen) {
    const win = chosen && !chosen.isDestroyed() ? chosen : targetWindow();
    if (!win || win.isDestroyed()) return false;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    win.webContents.send("screenshot:attach", { id: `${Date.now()}`, png, name: screenshotName(new Date(), false) });
    return true;
  }

  // -- overlay pages
  ipcMain.handle("screenshot:start", (event) => {
    // From an App page (the + menu): wait for the menu to close first.
    later(() => { void start(); }, MENU_CLOSE_MS);
    return true;
  });

  ipcMain.handle("screenshot:frame", (event) => {
    const entry = overlayFor(event);
    if (!entry) return null;
    entry[1].ready = true;
    const frame = session?.frames.get(entry[0]);
    const display = session?.displays.find((d) => d.id === entry[0]);
    if (!frame || !display) return null;
    const b = display.bounds;
    const cursor = session.cursor;
    const inside = cursor.x >= b.x && cursor.y >= b.y && cursor.x < b.x + b.width && cursor.y < b.y + b.height;
    return {
      started: session.started,
      width: frame.width,
      height: frame.height,
      scale: display.scaleFactor,
      // Raw pixels, BGRA, rows of width * 4 bytes.
      pixels: frame.image.toBitmap(),
      cursor: inside ? { x: cursor.x - b.x, y: cursor.y - b.y } : null,
      platform,
    };
  });

  ipcMain.handle("screenshot:windows", async (event) => {
    const entry = overlayFor(event);
    if (!entry || !session) return [];
    return (await session.windows).get(entry[0]) || [];
  });

  // The page drew the frame: show the overlay, with the keyboard on the display under the pointer.
  ipcMain.on("screenshot:painted", (event, started) => {
    const entry = overlayFor(event);
    if (!entry || !session || session.started !== started) return;
    const { win } = entry[1];
    const display = session.displays.find((d) => d.id === entry[0]);
    win.setBounds(display.bounds);
    const b = display.bounds;
    const cursor = session.cursor;
    if (cursor.x >= b.x && cursor.y >= b.y && cursor.x < b.x + b.width && cursor.y < b.y + b.height) {
      win.show();
      win.focus();
    } else {
      win.showInactive();
    }
  });

  // Another overlay took the selection: the rest stop showing theirs.
  ipcMain.on("screenshot:selecting", (event) => {
    if (!isOverlay(event)) return;
    for (const { win } of overlays.values()) {
      if (!win.isDestroyed() && win.webContents !== event.sender) win.webContents.send("screenshot:release");
    }
  });

  ipcMain.handle("screenshot:finish", async (event, result) => {
    const entry = overlayFor(event);
    if (!entry || !session) return false;
    const display = session.displays.find((d) => d.id === entry[0]);
    const target = session.target;
    end();
    const action = String(result?.action || "cancel");
    const png = result?.png ? Buffer.from(result.png) : null;
    if (action === "cancel" || !png || !png.length) return true;
    if (action === "copy") {
      clipboard.writeImage(nativeImage.createFromBuffer(png));
      return true;
    }
    if (action === "save") return save(png);
    if (action === "pin") {
      const rect = result.rect || {};
      if (![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)) return false;
      return pin(png, rect, display);
    }
    if (action === "send") return send(png, target);
    return false;
  });

  // -- pins
  ipcMain.handle("screenshot:pin-image", (event) => {
    const record = pinFor(event);
    if (!record) return null;
    return { png: record.png, width: record.size.width, height: record.size.height };
  });

  ipcMain.on("screenshot:pin-drag", (event, phase) => {
    const record = pinFor(event);
    if (!record || record.win.isDestroyed()) return;
    if (phase === "start" && !record.drag) {
      const cursor = screen.getCursorScreenPoint();
      const [x, y] = record.win.getPosition();
      const drag = { dx: cursor.x - x, dy: cursor.y - y, timer: null };
      drag.timer = every(() => {
        if (record.win.isDestroyed()) { stopEvery(drag.timer); return; }
        const point = screen.getCursorScreenPoint();
        record.win.setPosition(Math.round(point.x - drag.dx), Math.round(point.y - drag.dy));
      }, DRAG_FRAME_MS);
      record.drag = drag;
    } else if (phase === "end" && record.drag) {
      stopEvery(record.drag.timer);
      record.drag = null;
    }
  });

  ipcMain.on("screenshot:pin-wheel", (event, wheel) => {
    const record = pinFor(event);
    if (!record || record.win.isDestroyed()) return;
    const delta = Number(wheel?.deltaY) || 0;
    if (!delta) return;
    if (wheel?.opacity) {
      record.opacity = Math.min(1, Math.max(PIN_MIN_OPACITY, record.opacity - Math.sign(delta) * 0.05));
      record.win.setOpacity(record.opacity);
      return;
    }
    const scale = Math.min(PIN_MAX_SCALE, Math.max(PIN_MIN_SCALE, record.scale * (delta < 0 ? 1.1 : 1 / 1.1)));
    if (scale === record.scale) return;
    record.scale = scale;
    record.win.setBounds(zoomedBounds(record.win.getBounds(), record.size, scale, screen.getCursorScreenPoint()));
  });

  ipcMain.on("screenshot:pin-close", (event) => {
    const record = pinFor(event);
    if (record && !record.win.isDestroyed()) record.win.close();
  });

  ipcMain.on("screenshot:pin-menu", (event, labels) => {
    const record = pinFor(event);
    if (!record || record.win.isDestroyed()) return;
    const label = (key, fallback) => String(labels?.[key] || fallback).slice(0, 60);
    Menu.buildFromTemplate([
      { label: label("copy", "Copy"), click: () => clipboard.writeImage(nativeImage.createFromBuffer(record.png)) },
      { label: label("save", "Save…"), click: () => { void save(record.png).catch(() => {}); } },
      {
        label: label("actualSize", "Actual size"),
        click: () => {
          record.scale = 1;
          const [x, y] = record.win.getPosition();
          record.win.setBounds({ x, y, ...record.size });
        },
      },
      { type: "separator" },
      { label: label("close", "Close"), click: () => record.win.close() },
    ]).popup({ window: record.win });
  });

  function registerShortcut() {
    try { globalShortcut.register(SHORTCUT, () => { void start(); }); } catch (_error) { /* taken by another app */ }
    app.on("will-quit", () => { try { globalShortcut.unregister(SHORTCUT); } catch (_error) { /* quitting */ } });
  }

  return { start, end, registerShortcut, shortcut: SHORTCUT };
}

module.exports = { SHORTCUT, createScreenshot, screenshotName, sourceWindowId, windowsForDisplay, zoomedBounds };
