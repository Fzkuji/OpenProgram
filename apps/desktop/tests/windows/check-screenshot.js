// The screenshot tool's main-process half (main/windows/screenshot.js):
// windows from the Worker land in each display's own coordinates, a pin
// zooms about the pointer, and only the overlay pages may read the frozen
// picture or finish a screenshot, whose result goes where it was sent.
const assert = require("node:assert/strict");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const {
  createScreenshot, screenshotName, sourceWindowId, windowsForDisplay, zoomedBounds,
} = require(path.join(__dirname, "..", "..", "main", "windows", "screenshot.js"));

assert.equal(sourceWindowId("window:4242:0"), 4242);
assert.equal(sourceWindowId("screen:1:0"), 0);
assert.equal(sourceWindowId(""), 0);

{
  // Two displays side by side; a window across the seam is cut at it.
  const left = { id: 1, bounds: { x: 0, y: 0, width: 1000, height: 800 } };
  const right = { id: 2, bounds: { x: 1000, y: 0, width: 800, height: 600 } };
  const listing = { pixels: false, windows: [
    { app: "Notes", title: "Todo", frame: [900, 100, 300, 200] },
    { app: "Tiny", title: "", frame: [1995, 10, 20, 20] },
    { app: "Bad", title: "", frame: ["x"] },
  ] };
  const same = (r) => r;
  assert.deepEqual(windowsForDisplay(listing, left, same), [
    { title: "Todo", app: "Notes", rect: { x: 900, y: 100, width: 100, height: 200 } },
  ]);
  assert.deepEqual(windowsForDisplay(listing, right, same).map((w) => w.rect), [{ x: 0, y: 100, width: 200, height: 200 }]);
  // Pixels are turned into the App's coordinates first.
  const half = (r) => ({ x: r.x / 2, y: r.y / 2, width: r.width / 2, height: r.height / 2 });
  assert.deepEqual(windowsForDisplay({ windows: [{ frame: [200, 200, 400, 400] }] }, left, half)[0].rect,
    { x: 100, y: 100, width: 200, height: 200 });
}

{
  // Zooming keeps the point under the pointer where it is.
  const bounds = { x: 100, y: 100, width: 200, height: 100 };
  const next = zoomedBounds(bounds, { width: 200, height: 100 }, 2, { x: 150, y: 125 });
  assert.deepEqual(next, { x: 50, y: 75, width: 400, height: 200 });
  const fx = (150 - next.x) / next.width;
  assert.equal(fx, 0.25);
}

assert.equal(screenshotName(new Date(2026, 10, 24, 9, 5, 7), false), "Screenshot 2026-11-24 09.05.07.png");
assert.equal(screenshotName(new Date(2026, 10, 24, 9, 5, 7), true), "截图 2026-11-24 09.05.07.png");

// ---------------------------------------------------------------- the flow

class FakeContents extends EventEmitter {
  constructor() { super(); this.sent = []; this.destroyed = false; }
  send(channel, payload) { this.sent.push([channel, payload]); }
  isDestroyed() { return this.destroyed; }
}

class FakeWindow extends EventEmitter {
  constructor(options) {
    super();
    FakeWindow.all.push(this);
    this.options = options;
    this.webContents = new FakeContents();
    this.bounds = { x: options.x, y: options.y, width: options.width, height: options.height };
    this.shown = false;
    this.focused = false;
    this.url = null;
  }
  setAlwaysOnTop() {}
  setVisibleOnAllWorkspaces() {}
  setMenuBarVisibility() {}
  loadURL(url) { this.url = url; return Promise.resolve(); }
  isDestroyed() { return false; }
  getMediaSourceId() { return `window:${900 + FakeWindow.all.indexOf(this)}:0`; }
  setBounds(bounds) { this.bounds = bounds; }
  getBounds() { return this.bounds; }
  getPosition() { return [this.bounds.x, this.bounds.y]; }
  setPosition(x, y) { this.bounds = { ...this.bounds, x, y }; }
  show() { this.shown = true; }
  showInactive() { this.shown = true; }
  hide() { this.shown = false; }
  focus() { this.focused = true; }
  isMinimized() { return false; }
  restore() {}
  close() { this.emit("closed"); }
  destroy() { this.emit("closed"); }
  setOpacity(value) { this.opacity = value; }
}
FakeWindow.all = [];

function image(width, height) {
  return {
    isEmpty: () => false,
    getSize: () => ({ width, height }),
    resize: ({ width: w, height: h }) => image(w, h),
    toBitmap: () => Buffer.alloc(width * height * 4, 7),
  };
}

async function flow() {
  const handlers = new Map();
  const listeners = new Map();
  const ipcMain = {
    handle: (channel, fn) => handlers.set(channel, fn),
    on: (channel, fn) => listeners.set(channel, fn),
  };
  const displays = [{ id: 7, bounds: { x: 0, y: 0, width: 100, height: 50 }, size: { width: 100, height: 50 }, scaleFactor: 2 }];
  const clipboardImages = [];
  const target = new FakeWindow({ x: 0, y: 0, width: 10, height: 10 });
  const fetched = [];
  const shots = createScreenshot({
    app: { getLocale: () => "en-US", getPath: () => "/tmp", on() {} },
    BrowserWindow: FakeWindow,
    Menu: { buildFromTemplate: () => ({ popup() {} }) },
    ipcMain,
    globalShortcut: { register: () => true, unregister() {} },
    desktopCapturer: { getSources: async () => [{ display_id: "7", thumbnail: image(200, 100) }] },
    screen: {
      on() {},
      getAllDisplays: () => displays,
      getCursorScreenPoint: () => ({ x: 10, y: 10 }),
      getDisplayNearestPoint: () => displays[0],
    },
    clipboard: { writeImage: (img) => clipboardImages.push(img) },
    dialog: { showSaveDialog: async () => ({ canceled: true }) },
    nativeImage: { createFromBuffer: (buffer) => ({ buffer, isEmpty: () => buffer.length === 0 }) },
    path,
    fs: { promises: { writeFile: async () => {} } },
    uiOrigin: () => "http://127.0.0.1:18200",
    fetchJson: async (route) => { fetched.push(route); return { pixels: false, windows: [{ app: "A", frame: [10, 10, 50, 30] }] }; },
    targetWindow: () => target,
    platform: "darwin",
    setTimeout: (fn) => fn(),
  });

  assert.equal(await shots.start(), true);
  const overlay = FakeWindow.all.find((w) => w.url?.includes("/menu-overlay/screenshot?display=7"));
  assert.ok(overlay, "an overlay opens on the display");
  assert.equal(overlay.options.type, "panel", "it takes the keyboard without bringing the App forward on macOS");
  const fromOverlay = { sender: overlay.webContents };
  const fromPage = { sender: target.webContents };

  assert.equal(await handlers.get("screenshot:frame")(fromPage), null, "an App page cannot read the picture");
  const frame = await handlers.get("screenshot:frame")(fromOverlay);
  assert.equal(frame.width, 200);
  assert.equal(frame.height, 100);
  assert.equal(frame.pixels.length, 200 * 100 * 4);
  assert.deepEqual(frame.cursor, { x: 10, y: 10 });

  const windows = await handlers.get("screenshot:windows")(fromOverlay);
  assert.deepEqual(windows[0].rect, { x: 10, y: 10, width: 50, height: 30 });
  assert.match(fetched[0], /^\/api\/screenshot\/windows\?exclude=\d+$/, "the overlay itself is left out");

  listeners.get("screenshot:painted")(fromOverlay, frame.started);
  assert.equal(overlay.shown, true);
  assert.equal(overlay.focused, true, "the display under the pointer takes the keyboard");

  assert.equal(await handlers.get("screenshot:finish")(fromPage, { action: "copy", png: new Uint8Array([1]) }), false);
  assert.equal(await handlers.get("screenshot:finish")(fromOverlay, { action: "copy", png: new Uint8Array([1, 2]) }), true);
  assert.equal(clipboardImages.length, 1);
  assert.equal(overlay.shown, false, "the overlay hides when done");
  assert.ok(overlay.webContents.sent.some(([channel]) => channel === "screenshot:end"));

  // The next screenshot reuses the overlay and sends the picture to the conversation.
  assert.equal(await shots.start(), true);
  assert.ok(overlay.webContents.sent.some(([channel]) => channel === "screenshot:begin"));
  assert.equal(await handlers.get("screenshot:finish")(fromOverlay, { action: "send", png: new Uint8Array([9]) }), true);
  const attach = target.webContents.sent.find(([channel]) => channel === "screenshot:attach");
  assert.ok(attach, "the App window hears the screenshot");
  assert.deepEqual([...attach[1].png], [9]);
  assert.equal(target.focused, true);

  // A pin opens where the selection was and answers only its own page.
  assert.equal(await shots.start(), true);
  assert.equal(await handlers.get("screenshot:finish")(fromOverlay, {
    action: "pin", png: new Uint8Array([5]), rect: { x: 20, y: 10, width: 30, height: 20 },
  }), true);
  const pin = FakeWindow.all.find((w) => w.url?.includes("/menu-overlay/screenshot-pin"));
  assert.deepEqual(pin.bounds, { x: 20, y: 10, width: 30, height: 20 });
  assert.equal(await handlers.get("screenshot:pin-image")(fromOverlay), null);
  const pinned = await handlers.get("screenshot:pin-image")({ sender: pin.webContents });
  assert.deepEqual([...pinned.png], [5]);
  listeners.get("screenshot:pin-wheel")({ sender: pin.webContents }, { deltaY: 1, opacity: true });
  assert.equal(pin.opacity, 0.95);
}

flow().then(() => console.log("screenshot: ok"), (error) => { console.error(error); process.exit(1); });
