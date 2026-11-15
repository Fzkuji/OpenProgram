import test from "node:test";
import assert from "node:assert/strict";
import { startWebTabCaptureLoop } from "../../lib/browser/web-tab/web-tab-capture-loop.ts";
import { fittedImageRect, mapOperationPoint } from "../../lib/browser/control/browser-marker-geometry.ts";

test("PiP screenshot maps pixel points onto a letterboxed contain fit", () => {
  const fitted = fittedImageRect({ width: 200, height: 200 }, { width: 800, height: 400 });
  assert.equal(fitted.x, 0);
  assert.equal(fitted.y, 50);
  assert.equal(fitted.width, 200);
  assert.equal(fitted.height, 100);
  const center = mapOperationPoint({ x: 400, y: 200, width: 800, height: 400 }, fitted);
  assert.deepEqual(center, { left: 100, top: 100 });
  const corner = mapOperationPoint({ x: 0, y: 0, width: 800, height: 400 }, fitted);
  assert.deepEqual(corner, { left: 0, top: 50 });
  assert.equal(mapOperationPoint({ x: 900, y: 10, width: 800, height: 400 }, fitted), null);
  assert.equal(mapOperationPoint({ x: 10, y: 10 }, fitted), null);
});

test("capture loop ignores stale target generation and its own frame updates", async () => {
  const captures = [];
  const frames = [];
  const unavailable = [];
  const timers = [];
  let generation = 1;
  const loop = startWebTabCaptureLoop({
    tabId: "w:a",
    generation,
    isCurrent: () => ({ tabId: "w:a", generation }),
    capture: async (tabId) => {
      captures.push(tabId);
      if (captures.length === 1) throw new Error("capture failed");
      return `data:image/png,${captures.length}`;
    },
    onFrame: (tabId, dataUrl) => {
      frames.push({ tabId, dataUrl });
      generation = 1;
    },
    onUnavailable: (tabId) => unavailable.push(tabId),
    intervalMs: 20,
    schedule: (fn, ms) => {
      const id = { fn, ms };
      timers.push(id);
      return id;
    },
    cancel: (id) => {
      const index = timers.indexOf(id);
      if (index >= 0) timers.splice(index, 1);
    },
  });
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(unavailable, ["w:a"]);
  assert.equal(captures.length, 1);
  timers.shift()?.fn();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(frames.at(-1)?.dataUrl, "data:image/png,2");
  assert.equal(captures.length, 2);
  generation = 2;
  const late = timers.shift();
  loop.stop();
  late?.fn();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(captures.length, 2);
  assert.equal(frames.length, 1);
});

test("capture loop pause drops in-flight frames and resumes without a permanent stop", async () => {
  const captures = [];
  const frames = [];
  const pending = [];
  const timers = [];
  let generation = 1;
  const loop = startWebTabCaptureLoop({
    tabId: "w:a",
    generation,
    isCurrent: () => ({ tabId: "w:a", generation }),
    capture: async (tabId) => {
      captures.push(tabId);
      return await new Promise((resolve) => pending.push(resolve));
    },
    onFrame: (_tabId, dataUrl) => frames.push(dataUrl),
    onUnavailable: () => {},
    intervalMs: 20,
    schedule: (fn) => {
      const id = { fn };
      timers.push(id);
      return id;
    },
    cancel: (id) => {
      const index = timers.indexOf(id);
      if (index >= 0) timers.splice(index, 1);
    },
  });
  await Promise.resolve();
  assert.equal(captures.length, 1);
  loop.pause();
  pending.shift()?.("data:image/png,late");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(frames, []);
  assert.equal(timers.length, 0);
  assert.equal(captures.length, 1);
  loop.resume();
  await Promise.resolve();
  assert.equal(captures.length, 2);
  pending.shift()?.("data:image/png,ok");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(frames, ["data:image/png,ok"]);
  loop.pause();
  const scheduled = timers.shift();
  loop.stop();
  scheduled?.fn();
  pending.shift()?.("data:image/png,after-stop");
  await Promise.resolve();
  await Promise.resolve();
  loop.resume();
  await Promise.resolve();
  assert.equal(captures.length, 2);
  assert.deepEqual(frames, ["data:image/png,ok"]);
});

function deferredCapture() {
  const captures = [];
  const pending = [];
  return {
    captures,
    pending,
    capture: async (tabId) => {
      captures.push(tabId);
      return await new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
  };
}

test("pause then resume before an in-flight resolve discards it and starts a fresh capture", async () => {
  const frames = [];
  const unavailable = [];
  const timers = [];
  const { captures, pending, capture } = deferredCapture();
  const loop = startWebTabCaptureLoop({
    tabId: "w:a",
    generation: 1,
    isCurrent: () => ({ tabId: "w:a", generation: 1 }),
    capture,
    onFrame: (_tabId, dataUrl) => frames.push(dataUrl),
    onUnavailable: (tabId) => unavailable.push(tabId),
    intervalMs: 400,
    schedule: (fn) => {
      const id = { fn };
      timers.push(id);
      return id;
    },
    cancel: (id) => {
      const index = timers.indexOf(id);
      if (index >= 0) timers.splice(index, 1);
    },
  });
  await Promise.resolve();
  assert.equal(captures.length, 1);
  loop.pause();
  loop.resume();
  await Promise.resolve();
  assert.equal(captures.length, 1);
  pending.shift()?.resolve("data:image/png,old");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(frames, []);
  assert.deepEqual(unavailable, []);
  assert.equal(captures.length, 2);
  assert.equal(timers.length, 0);
  pending.shift()?.resolve("data:image/png,fresh");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(frames, ["data:image/png,fresh"]);
  assert.equal(timers.length, 1);
  loop.stop();
});

test("pause then resume before an in-flight reject discards it and starts a fresh capture", async () => {
  const frames = [];
  const unavailable = [];
  const timers = [];
  const { captures, pending, capture } = deferredCapture();
  const loop = startWebTabCaptureLoop({
    tabId: "w:a",
    generation: 1,
    isCurrent: () => ({ tabId: "w:a", generation: 1 }),
    capture,
    onFrame: (_tabId, dataUrl) => frames.push(dataUrl),
    onUnavailable: (tabId) => unavailable.push(tabId),
    intervalMs: 400,
    schedule: (fn) => {
      const id = { fn };
      timers.push(id);
      return id;
    },
    cancel: (id) => {
      const index = timers.indexOf(id);
      if (index >= 0) timers.splice(index, 1);
    },
  });
  await Promise.resolve();
  loop.pause();
  loop.resume();
  pending.shift()?.reject(new Error("late"));
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(frames, []);
  assert.deepEqual(unavailable, []);
  assert.equal(captures.length, 2);
  assert.equal(timers.length, 0);
  pending.shift()?.resolve("data:image/png,fresh");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(frames, ["data:image/png,fresh"]);
  loop.stop();
});

test("stop after pause and resume still invalidates the in-flight capture", async () => {
  const frames = [];
  const { captures, pending, capture } = deferredCapture();
  const loop = startWebTabCaptureLoop({
    tabId: "w:a",
    generation: 1,
    isCurrent: () => ({ tabId: "w:a", generation: 1 }),
    capture,
    onFrame: (_tabId, dataUrl) => frames.push(dataUrl),
    onUnavailable: () => {},
    intervalMs: 400,
    schedule: (fn) => fn(),
    cancel: () => {},
  });
  await Promise.resolve();
  loop.pause();
  loop.resume();
  loop.stop();
  pending.shift()?.resolve("data:image/png,old");
  await Promise.resolve();
  await Promise.resolve();
  loop.resume();
  await Promise.resolve();
  assert.equal(captures.length, 1);
  assert.deepEqual(frames, []);
});
