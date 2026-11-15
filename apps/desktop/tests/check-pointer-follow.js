// A held right or middle button is followed past the window's edge until the
// physical button comes up, and nothing keeps running after that.
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createPointerFollow } = require("../main/windows/pointer-follow");

function setup(platform = "darwin") {
  const handlers = {};
  const spawned = [];
  const cursor = { x: 10, y: 20 };
  createPointerFollow({
    platform,
    ipcMain: { on: (channel, fn) => { handlers[channel] = fn; } },
    screen: { getCursorScreenPoint: () => ({ ...cursor }) },
    spawn: (file, args) => {
      const helper = new EventEmitter();
      helper.file = file;
      helper.script = args.at(-1);
      helper.kill = () => { helper.killed = true; };
      spawned.push(helper);
      return helper;
    },
  });
  const wc = new EventEmitter();
  wc.id = 1;
  wc.sent = [];
  wc.zoom = 1;
  wc.send = (channel, payload) => wc.sent.push([channel, payload]);
  wc.getZoomFactor = () => wc.zoom;
  return { handlers, spawned, cursor, wc, event: { sender: wc } };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 40));

(async () => {
  // The cursor is reported in the page's CSS pixels, once per position.
  let t = setup();
  t.handlers["pointer-follow:start"](t.event, 2);
  assert.equal(t.spawned.length, 1);
  assert.match(t.spawned[0].script, /pressedMouseButtons & 2\b/);
  t.wc.zoom = 2;
  await tick();
  assert.deepEqual(t.wc.sent, [["pointer-follow:cursor", { x: 5, y: 10 }]]);

  // The button came up beyond the window: the page hears it and polling stops.
  t.spawned[0].emit("exit", 0, null);
  assert.deepEqual(t.wc.sent.at(-1), ["pointer-follow:released", undefined]);
  const heard = t.wc.sent.length;
  t.cursor.x = 400;
  await tick();
  assert.equal(t.wc.sent.length, heard);
  assert.equal(t.wc.listenerCount("destroyed"), 0);

  // The page ended the drag itself: the script goes and no release is reported.
  t = setup();
  t.handlers["pointer-follow:start"](t.event, 4);
  t.handlers["pointer-follow:stop"](t.event);
  assert.equal(t.spawned[0].killed, true);
  t.spawned[0].emit("exit", null, "SIGTERM");
  assert.equal(t.wc.sent.some(([channel]) => channel === "pointer-follow:released"), false);

  // A script that failed reports no release it never saw.
  t = setup();
  t.handlers["pointer-follow:start"](t.event, 2);
  t.spawned[0].emit("exit", 1, null);
  assert.equal(t.wc.sent.some(([channel]) => channel === "pointer-follow:released"), false);

  // A page that closes mid-drag takes the script with it.
  t = setup();
  t.handlers["pointer-follow:start"](t.event, 2);
  t.wc.emit("destroyed");
  assert.equal(t.spawned[0].killed, true);

  // Only right and middle on macOS: the left button and other systems already work.
  t = setup();
  t.handlers["pointer-follow:start"](t.event, 1);
  assert.equal(t.spawned.length, 0);
  t = setup("win32");
  t.handlers["pointer-follow:start"](t.event, 2);
  assert.equal(t.spawned.length, 0);

  console.log("pointer follow: held button followed past the window OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
