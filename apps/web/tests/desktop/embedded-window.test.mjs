// A tab holding another application's window: where the worker puts the
// window, where a point in the tab lands in it, and how input is batched.
import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

import { scratchDir } from "../scratch-dir.mjs";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = scratchDir(webPath, "embedded-window-test");
const bundle = join(dir, "embedded-window.mjs");
await build({
  absWorkingDir: webPath,
  stdin: {
    contents: `export * from "./lib/desktop/embedded-window/embedded-window";
export { setSocket } from "./lib/runtime-bridge/state";`,
    resolveDir: webPath,
  },
  bundle: true, format: "esm", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
});
const embedded = await import(pathToFileURL(bundle).href);

const mac = { platform: "darwin", pid: 1, handle: "0", zoom: 1.25, scale: 2, content: { x: 100, y: 40, width: 1200, height: 800 } };
const win = { ...mac, platform: "win32", handle: "4660", scale: 1.5 };

test("macOS places the window behind the tab in screen points", () => {
  assert.deepEqual(embedded.hostRect(mac, { left: 200, top: 50, width: 400, height: 300 }), [350, 103, 500, 375]);
});

test("Windows and Linux place the child in device pixels of the client area", () => {
  assert.deepEqual(embedded.hostRect(win, { left: 200, top: 50, width: 400, height: 300 }), [375, 94, 750, 563]);
});

test("a point in the tab is that point of the window, scaled by the page zoom", () => {
  assert.deepEqual(embedded.windowPoint(mac, 40, 10), { x: 50, y: 12.5 });
});

test("modifiers and wheel units", () => {
  assert.deepEqual(embedded.heldModifiers({ shiftKey: true, ctrlKey: false, altKey: true, metaKey: true }), ["shift", "alt", "meta"]);
  assert.deepEqual(embedded.wheelPixels({ deltaX: 0, deltaY: 3, deltaMode: 1 }), { dx: 0, dy: 48 });
  assert.deepEqual(embedded.wheelPixels({ deltaX: 2, deltaY: 1, deltaMode: 2 }, 500), { dx: 1000, dy: 500 });
});

test("input goes out once a frame, moves and wheel turns folded, in order", () => {
  const sent = [];
  const frames = [];
  globalThis.WebSocket = { OPEN: 1 };
  globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  globalThis.cancelAnimationFrame = () => {};
  embedded.setSocket({ readyState: 1, send: (data) => sent.push(JSON.parse(data)) });
  const queue = embedded.inputQueue(42);
  queue.push({ type: "move", x: 1, y: 1, buttons: 0 });
  queue.push({ type: "move", x: 2, y: 2, buttons: 0 });
  queue.push({ type: "down", x: 2, y: 2, button: 0 });
  queue.push({ type: "move", x: 3, y: 3, buttons: 1 });
  queue.push({ type: "wheel", x: 3, y: 3, dx: 0, dy: 5 });
  queue.push({ type: "wheel", x: 3, y: 3, dx: 1, dy: 7 });
  assert.equal(frames.length, 1, "one frame scheduled for the burst");
  frames[0]();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].action, "embedded_window_input");
  assert.equal(sent[0].window_id, 42);
  assert.deepEqual(sent[0].events.map((event) => event.type), ["move", "down", "move", "wheel"]);
  assert.equal(sent[0].events[0].x, 2, "the later move replaces the earlier one");
  assert.deepEqual([sent[0].events[3].dx, sent[0].events[3].dy], [1, 12], "wheel turns add up");
  queue.push({ type: "key", code: "KeyA", down: true });
  queue.dispose();
  assert.equal(sent.length, 2, "what is left goes out when the tab closes");
});

test("changes asked for within a frame run once, in that frame", () => {
  const frames = new Map();
  let next = 0;
  let runs = 0;
  const soon = embedded.frameCoalesced(() => { runs += 1; }, {
    request: (fn) => { frames.set(++next, fn); return next; },
    cancel: (id) => frames.delete(id),
  });
  soon.schedule();
  soon.schedule();
  soon.schedule();
  assert.equal(frames.size, 1);
  frames.get(1)();
  frames.delete(1);
  assert.equal(runs, 1);
  soon.schedule();
  soon.cancel();
  assert.equal(frames.size, 0, "a closed view runs nothing later");
  assert.equal(runs, 1);
});

function followFakes() {
  const handlers = new Map();
  const timers = new Map();
  let id = 0;
  return {
    handlers, timers,
    deps: {
      onChanged: (topic, handler) => { handlers.set(topic, handler); return () => handlers.delete(topic); },
      setInterval: (fn, ms) => { timers.set(++id, { fn, ms }); return id; },
      clearInterval: (timer) => timers.delete(timer),
    },
  };
}

test("a followed window is read again on the Worker's word for it, with no timer", () => {
  const fakes = followFakes();
  let reads = 0;
  const stop = embedded.followWindow(7, true, () => { reads += 1; }, fakes.deps);
  assert.equal(fakes.timers.size, 0);
  const changed = fakes.handlers.get(embedded.WINDOW_CHANGED_TOPIC);
  changed(["8"]);
  assert.equal(reads, 0, "another window's change is not this one's");
  changed(["8", "7"]);
  changed(null);
  assert.equal(reads, 2, "its own change, and a reconnect that may have lost one");
  stop();
  assert.equal(fakes.handlers.size, 0);
});

test("a window nothing announces is looked at on a timer only while the view is open", () => {
  const fakes = followFakes();
  let reads = 0;
  const stop = embedded.followWindow(7, false, () => { reads += 1; }, fakes.deps);
  assert.equal(fakes.handlers.size, 0);
  const [timer] = fakes.timers.values();
  assert.equal(timer.ms, embedded.UNFOLLOWED_LOOK_MS);
  timer.fn();
  assert.equal(reads, 1);
  stop();
  assert.equal(fakes.timers.size, 0);
});
