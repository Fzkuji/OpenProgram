import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { startWebTabCaptureLoop } from "../lib/state/web-tab-capture-loop.ts";
import { fittedImageRect, mapOperationPoint } from "../lib/state/browser-marker-geometry.ts";

const pipSource = readFileSync(new URL("../components/center-tabs/web-tab-pip.tsx", import.meta.url), "utf8");
const paneSource = readFileSync(new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url), "utf8");

test("read-only PiP never mounts a native view or iframe", () => {
  assert.doesNotMatch(pipSource, /<iframe/);
  assert.doesNotMatch(pipSource, /ensureWebView/);
  assert.doesNotMatch(pipSource, /registerVisibleWebTabBounds/);
  assert.doesNotMatch(pipSource, /setPipZoom/);
  assert.match(pipSource, /webTab\.capture/);
  assert.match(pipSource, /Last frame|unavailable/);
});

test("live tab stays usable and is not replaced by a bound mask", () => {
  assert.doesNotMatch(paneSource, /PipBoundMask/);
  assert.doesNotMatch(paneSource, /Controlled by/);
  assert.doesNotMatch(paneSource, /webBoundMask/);
  assert.doesNotMatch(paneSource, /isHumanYieldEvent/);
  assert.match(paneSource, /onKeyDownCapture=\{handleRendererShortcut\}/);
  assert.doesNotMatch(paneSource, /yieldFromLiveTab\(tabId, \{ type: "keydown"/);
  assert.match(paneSource, /data-state=\{control \? displayedControlState\(control\)/);
  assert.match(paneSource, /data-native-view-occluder="true"/);
  assert.doesNotMatch(paneSource, /\[data-pip\]/);
  assert.doesNotMatch(paneSource, /data-web-pip-dock/);
  assert.doesNotMatch(paneSource, /setPipPageDock|pipDockEdge|createPortal/);
  assert.match(paneSource, /measureWebTabBounds\(el\)/);
  assert.doesNotMatch(paneSource, /remainingNative|subtractRect|largest-leftover/);
});

test("PiP screenshot maps pixel points onto a letterboxed contain fit", () => {
  const css = readFileSync(new URL("../components/center-tabs/center-tabs.module.css", import.meta.url), "utf8");
  assert.match(css, /\.webPipShot \{[\s\S]*?object-fit: contain/);
  assert.match(css, /\.webPip\[data-state="active"\]/);
  assert.match(css, /\.webPane\[data-state="yielding"\]/);
  assert.match(css, /\.webPipChrome \{[\s\S]*?flex-direction: row/);
  assert.match(css, /\.webPipChrome \{[\s\S]*?flex-wrap: nowrap/);
  assert.match(css, /\.webPipChrome \{[\s\S]*?overflow: hidden/);
  assert.match(css, /\.webPipActions \{[\s\S]*?flex-wrap: nowrap/);
  assert.match(css, /\.webPipTitle \{[\s\S]*?min-width: 0/);
  assert.match(css, /\.webPipChrome \.webToolbarBtn \{[\s\S]*?width: 24px/);
  assert.match(css, /\.webPipChrome \.webToolbarBtn svg \{[\s\S]*?width: 14px/);
  assert.match(css, /\.webPipActions \{[\s\S]*?flex-wrap: nowrap/);
  assert.match(css, /\.webPipActions \{[\s\S]*?flex-shrink: 0/);
  assert.match(css, /\.webPip \{[\s\S]*?width: 300px/);
  assert.match(css, /\.webPip \{[\s\S]*?height: 198\.75px/);
  assert.doesNotMatch(css, /linear-gradient\(135deg/);
  assert.match(pipSource, /data-pip-resize=\{dir\}/);
  assert.match(pipSource, /PIP_RESIZE_DIRS/);
  assert.match(css, /\.webPipExpanded \{[\s\S]*?720px/);
  assert.match(css, /\.webPipExpanded \{[\s\S]*?435px/);
  assert.match(css, /\.webPipBody \{[\s\S]*?margin: 0/);
  assert.doesNotMatch(css, /\.webPipBar \{/);
  assert.doesNotMatch(css, /\.webPipBarBtn/);
  assert.match(css, /\.webPipMode \{[\s\S]*?flex: 0 0 auto/);
  assert.doesNotMatch(pipSource, /webPipBarBtn/);
  assert.doesNotMatch(css, /data-pip-dock/);
  assert.doesNotMatch(css, /margin: 0 10px 10px 0/);
  assert.match(pipSource, /Open page/);
  assert.match(pipSource, /Pause Agent to use page/);
  assert.match(pipSource, /<ExternalLink /);
  assert.match(pipSource, /<Pin /);
  assert.doesNotMatch(pipSource, /<Locate /);
  assert.doesNotMatch(pipSource, /"Follow"/);
  assert.doesNotMatch(pipSource, /className=\{styles\.webPipBar\}/);
  assert.doesNotMatch(pipSource, /COMPACT_PIP_WIDTH/);
  assert.doesNotMatch(pipSource, /Use in webpage/);
  assert.doesNotMatch(pipSource, /createPortal/);
  assert.doesNotMatch(css, /\.browserControl\[data-state="active"\] \{\s*box-shadow: inset/);
  assert.doesNotMatch(pipSource, /recordOperationCue/);
  assert.doesNotMatch(pipSource, /left: `\$\{marker\.point\.x\}%`/);
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
