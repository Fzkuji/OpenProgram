// The control overlay over a desktop app an Agent operates: one
// click-through window per conversation laid over the controlled window,
// following its reports; the cursor keeps the step's point relative to the
// window; steps every App window forwards play once; the end of control
// fades the overlay and closes it. The overlay sits in the stack right
// above the controlled window, not above every app.
const assert = require("node:assert/strict");
const { createAgentControl, OVERLAY_TITLE, MARGIN } = require("../main/worker/agent-control");

const windows = [];
class FakeWindow {
  constructor(options) {
    this.options = options;
    this.bounds = null;
    this.visible = false;
    this.destroyed = false;
    this.ignoresMouse = false;
    this.sent = [];
    this.onTop = false;
    this.above = [];
    this.webContents = { send: (channel, payload) => this.sent.push([channel, payload]) };
    windows.push(this);
  }
  setIgnoreMouseEvents(value) { this.ignoresMouse = value; }
  setAlwaysOnTop(value) { this.onTop = value; }
  moveAbove(source) {
    if (FakeWindow.refuse) throw new Error("Invalid media source id");
    this.above.push(source);
  }
  on() {}
  loadURL(url) { this.url = url; return Promise.resolve(); }
  setBounds(bounds) { this.bounds = bounds; }
  showInactive() { this.visible = true; }
  hide() { this.visible = false; }
  isVisible() { return this.visible; }
  destroy() { this.destroyed = true; }
  isDestroyed() { return this.destroyed; }
}

const timers = [];
const control = createAgentControl({
  BrowserWindow: FakeWindow,
  nativeTheme: { prefersReducedMotion: false },
  overlayUiOrigin: () => "http://127.0.0.1:18100",
  path: require("node:path"),
  __dirname,
  setTimeout: (fn, ms) => { const timer = { fn, ms, cleared: false }; timers.push(timer); return timer; },
  clearTimeout: (timer) => { if (timer) timer.cleared = true; },
});

const report = { session_id: "s", window_id: 7, active: true, app_name: "Calculator",
  frame: [100, 200, 400, 300], buttons: [8, 6, 52, 14], occluders: [[450, 250, 200, 100]] };
assert.equal(control.showControl(report), true);
assert.equal(windows.length, 1);
const [win] = windows;
assert.equal(win.options.title, OVERLAY_TITLE, "the worker recognises its own overlay by title");
assert.equal(win.options.focusable, false, "the overlay never takes focus");
assert.equal(win.ignoresMouse, true, "clicks go through to the app");
assert.ok(win.url.endsWith("/menu-overlay/agent-control"));
assert.deepEqual(win.bounds, { x: 100 - MARGIN, y: 200 - MARGIN, width: 400 + 2 * MARGIN, height: 300 + 2 * MARGIN });
assert.equal(win.visible, true);
assert.equal(win.options.alwaysOnTop, undefined, "the overlay does not float over every app");
assert.equal(win.onTop, false);
assert.deepEqual(win.above, ["window:7:0"], "ordered right above the controlled window");

// Nothing is sent before the page is ready; then the whole place.
assert.equal(win.sent.length, 0);
assert.equal(control.markReady({ sender: win.webContents }), true);
let [channel, payload] = win.sent.at(-1);
assert.equal(channel, "agent-control:update");
assert.deepEqual(payload.buttons, [8 + MARGIN, 6 + MARGIN, 52, 14], "buttons are placed inside the margin");
assert.deepEqual(payload.occluders, [[450 - 100 + MARGIN, 250 - 200 + MARGIN, 200, 100]], "covers are in overlay space");

// A step: the cursor goes to its point relative to the window.
const cue = { session_id: "s", x: 300, y: 260, sequence: 1, at: 10, action: "click" };
assert.equal(control.showAction(cue), true);
assert.deepEqual(win.sent.at(-1)[1].cursor, { x: 200, y: 60, screen: null, action: "click", seq: 1 });
// Every App window forwards the same step: it plays once.
assert.equal(control.showAction({ ...cue }), false);
assert.equal(control.showAction({ ...cue, x: Number.NaN, sequence: 2 }), false);

// The window moves: the overlay follows and the cursor stays on its spot.
control.showControl({ ...report, frame: [150, 220, 400, 300] });
assert.deepEqual(win.bounds, { x: 150 - MARGIN, y: 220 - MARGIN, width: 400 + 2 * MARGIN, height: 300 + 2 * MARGIN });
assert.deepEqual(win.sent.at(-1)[1].cursor, { x: 200, y: 60, screen: null, action: "click", seq: 1 });

assert.deepEqual(win.above.at(-1), "window:7:0", "each report orders it again, as the user switches apps");
assert.ok(win.above.length >= 2);

// A window the server will not order against: the overlay floats instead,
// and goes back into the stack once it can.
FakeWindow.refuse = true;
control.showControl({ ...report, frame: [150, 220, 400, 300] });
assert.equal(win.onTop, true);
FakeWindow.refuse = false;
control.showControl({ ...report, frame: [150, 220, 400, 300] });
assert.equal(win.onTop, false);

// Minimized: hidden until it is back.
control.showControl({ ...report, frame: null });
assert.equal(win.visible, false);
control.showControl(report);
assert.equal(win.visible, true);

// Control ends: the page fades, then the window closes.
control.showControl({ ...report, active: false, frame: null });
assert.equal(win.sent.at(-1)[1].ending, true);
timers.filter((timer) => !timer.cleared).at(-1).fn();
assert.equal(win.destroyed, true);

// A step that arrives before the window's place is kept until it is known.
control.showAction({ session_id: "t", x: 520, y: 330, sequence: 1, at: 11, action: "type_text" });
const second = windows.at(-1);
assert.notEqual(second, win);
control.markReady({ sender: second.webContents });
control.showControl({ ...report, session_id: "t", frame: [500, 300, 200, 100] });
assert.deepEqual(second.sent.at(-1)[1].cursor, { x: 20, y: 30, screen: null, action: "type_text", seq: 1 });

// A theme change paints App windows only: a background colour on this
// transparent window would draw the overlay on an opaque tile.
const mainSource = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "main.js"), "utf8");
const start = mainSource.indexOf("function applyWindowChrome");
const chrome = mainSource.slice(start, mainSource.indexOf("\n}\n", start));
assert.ok(chrome.includes("setBackgroundColor") && !chrome.includes("getAllWindows"),
  "applyWindowChrome repaints the App's own windows, not the transparent overlays");

console.log("agent control overlay: placed, followed, deduped, closed and kept transparent");
