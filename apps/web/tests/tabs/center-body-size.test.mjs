import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const file = `${new URL(specifier, context.parentURL).href}.ts`;
      if (existsSync(fileURLToPath(file))) return { url: file, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { setCenterBodySize, smallCanvasPanes, useCenterBodySize, watchCenterBodySize } = await import("../../lib/tabs/canvas/center-body-size.ts");
const { CANVAS_FULLSCREEN_EVENT } = await import("../../lib/tabs/canvas/canvas-fullscreen.ts");
const { rowLayout, leaves } = await import("../../lib/tabs/canvas/canvas-layout.ts");
const { isSplitLayoutAvailable } = await import("../../lib/tabs/canvas/split-layout.ts");

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

/** Every frame of a rail sliding open narrows the centre body by a few pixels. */
const slide = (from, to, step) => { const out = []; for (let w = from; from > to ? w >= to : w <= to; w += from > to ? -step : step) out.push(w); return out; };

test("a sliding rail changes the shell's answers only where they cross a threshold", () => {
  const two = rowLayout(["chat", "web"], 0.88);
  const [, right] = leaves(two.root);
  const answers = { split: [], small: [] };
  const unsubscribe = useCenterBodySize.subscribe((size, previous) => {
    const split = isSplitLayoutAvailable(size.width), small = smallCanvasPanes(two.root, size.width, size.height);
    if (split !== isSplitLayoutAvailable(previous.width)) answers.split.push(size.width);
    if (small !== smallCanvasPanes(two.root, previous.width, previous.height)) answers.small.push([size.width, small]);
  });
  try {
    setCenterBodySize(1314, 746);
    answers.split.length = 0; answers.small.length = 0;
    const widths = slide(1314, 760, 6);
    for (const width of widths) setCenterBodySize(width, 746);
    assert.ok(widths.length > 80, "the move has many frames");
    assert.deepEqual(answers.split, [840], "split stops fitting once, on the first frame below its 846px minimum");
    assert.equal(answers.small.length, 1, "the narrow pane becomes too small once");
    assert.equal(answers.small[0][1], right.id);
  } finally { unsubscribe(); }
});

test("an unchanged size is not written again", () => {
  setCenterBodySize(1000, 700);
  let writes = 0;
  const unsubscribe = useCenterBodySize.subscribe(() => { writes += 1; });
  setCenterBodySize(1000, 700);
  setCenterBodySize(1000, 700);
  setCenterBodySize(1001, 700);
  unsubscribe();
  assert.equal(writes, 1);
});

test("the whole canvas too small for content is reported for a zoomed pane", () => {
  const lone = rowLayout(["chat"]);
  assert.equal(smallCanvasPanes(lone.root, 800, 600), "");
  assert.equal(smallCanvasPanes(lone.root, 150, 600).split(" ").includes("viewport"), true);
});

test("the shell and the canvas controls do not hold the body's size in React state", () => {
  const shell = read("components/app-shell.tsx");
  assert.doesNotMatch(shell, /useState\(0\)/, "no width or height state in the shell");
  assert.match(shell, /return node \? watchCenterBodySize\(node\) : undefined;/);
  assert.match(shell, /useCenterBodySize\(\(size\) => isSplitLayoutAvailable\(size\.width\)\)/);
  assert.match(shell, /<CanvasControls layout=\{canvas\} targetId=\{activeId\} \/>/);
  const controls = read("components/center-tabs/canvas/canvas-controls.tsx");
  assert.match(controls, /export function CanvasControls\(\{ layout, targetId \}: \{ layout: CanvasLayout; targetId: string \}\)/);
});

/** A browser for the watcher: its ResizeObserver, frames, timers, and the
 *  attribute full screen sets. Reads of the body's rect are counted. */
function watchFixture(t) {
  const saved = ["window", "document", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  const put = (key, value) => Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const frames = new Map(), timers = new Map(); let id = 0;
  const attributes = new Set();
  let observer = null;
  put("window", Object.assign(new EventTarget(), {
    setTimeout: (fn) => { timers.set(++id, fn); return id; },
    clearTimeout: (timer) => { timers.delete(timer); },
  }));
  put("document", { documentElement: { hasAttribute: (name) => attributes.has(name) } });
  put("requestAnimationFrame", (fn) => { frames.set(++id, fn); return id; });
  put("cancelAnimationFrame", (frame) => { frames.delete(frame); });
  put("ResizeObserver", class { constructor(fn) { observer = { fn, boxes: [] }; } observe(_target, options) { observer.boxes.push(options?.box); } disconnect() { observer = null; } });
  t.after(() => { for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } });
  const reads = { rect: 0 };
  let rect = { width: 0, height: 0 };
  const body = { getBoundingClientRect: () => { reads.rect += 1; return rect; } };
  const run = (queue) => { const callbacks = [...queue.values()]; queue.clear(); callbacks.forEach((fn) => fn()); };
  return {
    body, reads, attributes, frames, timers,
    get observer() { return observer; },
    setRect(next) { rect = next; },
    /** One frame of a move: the observer reports the body's border box. */
    resize(width, height) { observer.fn([{ borderBoxSize: [{ inlineSize: width, blockSize: height }], contentRect: { width, height } }]); },
    runFrames: () => run(frames), runTimers: () => run(timers),
  };
}

test("the body's size comes from its observer's entries: a moving rail queues no frame and reads no layout", (t) => {
  const f = watchFixture(t);
  setCenterBodySize(0, 0);
  const stop = watchCenterBodySize(f.body);
  assert.deepEqual(f.observer.boxes, ["border-box"], "it watches the border box, the size the rect gave");
  for (const width of slide(1314, 1080, 18)) {
    f.resize(width, 746);
    assert.equal(f.frames.size + f.timers.size, 0, "nothing waits for a frame or a timer");
    assert.deepEqual(useCenterBodySize.getState(), { width, height: 746 }, "the size lands in the observer's callback");
  }
  assert.equal(f.reads.rect, 0, "no layout read");
  stop();
  assert.equal(f.observer, null);
});

test("full screen's sizes are skipped, and the body is measured once when full screen ends", (t) => {
  const f = watchFixture(t);
  setCenterBodySize(1200, 700);
  const stop = watchCenterBodySize(f.body);
  f.attributes.add("data-canvas-fullscreen");
  window.dispatchEvent(new Event(CANVAS_FULLSCREEN_EVENT));
  f.resize(1600, 1000);
  f.runFrames(); f.runTimers();
  assert.deepEqual(useCenterBodySize.getState(), { width: 1200, height: 700 }, "lifted out: the size it had");
  assert.equal(f.reads.rect, 0);
  // The glide back has ended; the window was resized meanwhile.
  f.attributes.delete("data-canvas-fullscreen");
  f.setRect({ width: 1100, height: 690 });
  window.dispatchEvent(new Event(CANVAS_FULLSCREEN_EVENT));
  f.runFrames(); f.runTimers();
  assert.equal(f.reads.rect, 1, "measured once, at rest");
  assert.deepEqual(useCenterBodySize.getState(), { width: 1100, height: 690 });
  stop();
  window.dispatchEvent(new Event(CANVAS_FULLSCREEN_EVENT));
  f.runFrames(); f.runTimers();
  assert.equal(f.reads.rect, 1, "stopped: no more measures");
});
