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
  assert.match(paneSource, /isHumanYieldEvent/);
  assert.match(paneSource, /onKeyDownCapture=\{handleRendererShortcut\}/);
  assert.doesNotMatch(paneSource, /yieldFromLiveTab\(tabId, \{ type: "keydown"/);
  assert.match(paneSource, /data-state=\{control \? displayedControlState\(control\)/);
});

test("PiP screenshot maps pixel points onto a letterboxed contain fit", () => {
  const css = readFileSync(new URL("../components/center-tabs/center-tabs.module.css", import.meta.url), "utf8");
  assert.match(css, /\.webPipShot \{[\s\S]*?object-fit: contain/);
  assert.match(css, /\.webPip\[data-state="active"\]/);
  assert.match(css, /\.webPane\[data-state="yielding"\]/);
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
