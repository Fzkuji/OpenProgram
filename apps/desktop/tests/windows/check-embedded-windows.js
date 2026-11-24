// A tab showing another application's window: the page learns its App
// window's handle and a window's capture source, and a window with
// embedded windows gives them back before it closes.
const assert = require("node:assert/strict");
const { createEmbeddedWindows, nativeHandle, sourceFor } = require("../../main/windows/embedded-windows");

const handlers = {};
const listeners = {};
const timers = [];
const sent = [];
const mainFrame = { processId: 3, routingId: 1 };
const hostSent = [];
const contents = {
  id: 11, mainFrame, getZoomFactor: () => 1.25,
  send: (channel, value) => (channel === "embedded-window:host-changed" ? hostSent.push(value) : sent.push(channel)),
};
const winEvents = {};
const handle = Buffer.alloc(8);
handle.writeBigUInt64LE(0x1234n, 0);
const win = {
  webContents: contents,
  isDestroyed: () => false,
  getBounds: () => ({ x: 0, y: 0, width: 800, height: 600 }),
  getContentBounds: () => ({ x: 10, y: 20, width: 800, height: 560 }),
  getNativeWindowHandle: () => handle,
  on: (name, fn) => { (winEvents[name] = winEvents[name] || []).push(fn); },
};
const ctx = { win };
const own = { sender: contents, senderFrame: mainFrame };
const foreign = { sender: { id: 99, mainFrame }, senderFrame: mainFrame };
let sources = [{ id: "window:42:0", name: "Notes" }, { id: "screen:1:0", name: "Screen" }];

function make(platform) {
  return createEmbeddedWindows({
    ipcMain: {
      handle: (channel, fn) => { handlers[channel] = fn; },
      on: (channel, fn) => { listeners[channel] = fn; },
    },
    desktopCapturer: { getSources: async () => sources },
    screen: { getDisplayMatching: () => ({ scaleFactor: 2 }) },
    contextForSender: (event) => (event.sender === contents ? ctx : null),
    platform, pid: 500,
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
  });
}

(async () => {
  assert.equal(nativeHandle(handle), "4660");
  assert.equal(sourceFor(sources, 42).name, "Notes");
  assert.equal(sourceFor(sources, 4), null, "an id that only prefixes another does not match");

  const embedded = make("win32");
  // The host: only the App's own page, with the parent handle and the geometry it needs.
  assert.equal(await handlers["embedded-window:host"](foreign), null);
  assert.deepEqual(await handlers["embedded-window:host"](own), {
    platform: "win32", pid: 500, handle: "4660", zoom: 1.25, scale: 2,
    content: { x: 10, y: 20, width: 800, height: 560 },
  });

  // The App window moving or resizing tells its page, with no timer; asking
  // again does not add a second listener.
  await handlers["embedded-window:host"](own);
  assert.deepEqual(Object.keys(winEvents).sort(), ["move", "resize"]);
  assert.equal(winEvents.move.length, 1);
  winEvents.move[0]();
  winEvents.resize[0]();
  assert.equal(hostSent.length, 2);
  assert.deepEqual(hostSent[0].content, { x: 10, y: 20, width: 800, height: 560 });
  assert.equal(hostSent[0].zoom, 1.25);

  // Capture: the source id of that window, for the App's own page only.
  assert.equal(await handlers["embedded-window:source"](foreign, 42), null);
  assert.equal(await handlers["embedded-window:source"](own, -1), null);
  assert.equal(await handlers["embedded-window:source"](own, 42), "window:42:0");
  assert.equal(await handlers["embedded-window:source"](own, 77), null, "a window with no source");

  // Closing: held while the page gives its windows back, then closed again.
  let closes = 0;
  let prevented = 0;
  const closeEvent = { preventDefault: () => { prevented += 1; } };
  assert.equal(embedded.windowClosing(ctx, closeEvent, () => { closes += 1; }), false, "no embedded windows");
  listeners["embedded-window:count"](own, 2);
  assert.equal(embedded.windowClosing(ctx, closeEvent, () => { closes += 1; }), true);
  assert.deepEqual(sent, ["embedded-window:release"]);
  assert.equal(embedded.windowClosing(ctx, closeEvent, () => { closes += 1; }), true, "a second close waits too");
  assert.equal(prevented, 2);
  listeners["embedded-window:released"](own);
  assert.equal(closes, 1);
  assert.equal(embedded.windowClosing(ctx, closeEvent, () => { closes += 1; }), false, "the close goes through");

  // A page that never answers is closed after the timeout.
  const second = { win: { ...win, webContents: { ...contents, id: 12, send() {} } } };
  listeners["embedded-window:count"]({ sender: contents, senderFrame: mainFrame }, 1);
  const owner = { sender: second.win.webContents, senderFrame: mainFrame };
  // Counts are kept per page; the second page reports through its own sender.
  const ctxFor = (event) => (event.sender === second.win.webContents ? second : null);
  const other = createEmbeddedWindows({
    ipcMain: { handle() {}, on: (channel, fn) => { listeners[`2:${channel}`] = fn; } },
    desktopCapturer: { getSources: async () => [] }, screen: {}, contextForSender: ctxFor,
    platform: "linux", pid: 1, setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout() {},
  });
  second.win.webContents.mainFrame = mainFrame;
  listeners["2:embedded-window:count"](owner, 1);
  let closedLate = false;
  assert.equal(other.windowClosing(second, closeEvent, () => { closedLate = true; }), true);
  timers[timers.length - 1]();
  assert.equal(closedLate, true);
  console.log("embedded windows: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
