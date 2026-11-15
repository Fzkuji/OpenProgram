import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WEB_ROOT = new URL("../..", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[cm]?[jt]sx?$/.test(specifier) && context.parentURL) {
      const base = new URL(specifier, context.parentURL).href;
      for (const url of [`${base}.ts`, `${base}.tsx`, `${base}/index.ts`]) {
        if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

const sel = await import("../../lib/runtime-bridge/dag/interaction/selection/selection.ts");
const { deletionTargets } = await import("../../lib/runtime-bridge/dag/interaction/selection/node-delete.ts");

test("One mouse, no tools, and no left rail", () => {
  assert.equal(sel.dagTools, undefined);
  assert.equal(sel.registerTool, undefined);
});

test("Full screen grows the session pane over the sidebars, the tab strip and the other panes; both perspectives share it", async () => {
  const fs = await import("../../lib/tabs/canvas/canvas-fullscreen.ts");
  let calls = 0;
  const off = fs.subscribeCanvasFullscreen(() => calls++);
  fs.setCanvasFullscreen(true);
  assert.equal(fs.canvasFullscreen(), true);
  fs.setCanvasFullscreen(true);
  fs.setCanvasFullscreen(false);
  assert.equal(calls, 2);
  off();
  // The shell does not re-render for the body's size while it is lifted out.
  const watcher = readFileSync(new URL("../../lib/tabs/canvas/center-body-size.ts", import.meta.url), "utf8");
  assert.match(watcher, /new ResizeObserver\(\(entries\) => \{\s*if \(canvasCoversWindow\(\)\) return;/);
});

test("A mouse wheel zooms, two fingers pan, a right drag pans, a left drag draws a box", async () => {
  const { wheelKind } = await import("../../lib/runtime-bridge/dag/interaction/view/wheel.ts");
  const ev = (o) => ({ deltaMode: 0, deltaX: 0, deltaY: 0, ctrlKey: false, metaKey: false, ...o });
  // A mouse notch on macOS Chromium: wheelDeltaY 120, deltaY about -4.
  assert.equal(wheelKind(ev({ timeStamp: 1000, deltaY: -4, wheelDeltaY: 120 })), "zoom");
  assert.equal(wheelKind(ev({ timeStamp: 2000, deltaY: 100, wheelDeltaY: -120 })), "zoom");
  // Two fingers: wheelDeltaY is exactly -3 × deltaY, and the inertial
  // tail keeps panning even when a single event looks like a wheel.
  assert.equal(wheelKind(ev({ timeStamp: 3000, deltaY: 7, wheelDeltaY: -21 })), "pan");
  assert.equal(wheelKind(ev({ timeStamp: 3016, deltaY: 1, wheelDeltaY: 120 })), "pan");
  assert.equal(wheelKind(ev({ timeStamp: 4000, deltaX: 3, deltaY: 0 })), "pan");
  assert.equal(wheelKind(ev({ timeStamp: 5000, deltaY: 3, ctrlKey: true })), "pinch");
  // Line-mode deltas only come from a wheel.
  assert.equal(wheelKind(ev({ timeStamp: 6000, deltaMode: 1, deltaY: 3 })), "zoom");
  // A right click that never moved still opens the node's menu.
  const gestures = readFileSync(new URL("../../lib/runtime-bridge/dag/interaction/view/gestures.ts", import.meta.url), "utf8");
  assert.match(gestures, /menuAt && !moved[\s\S]{0,120}new MouseEvent\("contextmenu"/);
});

test("A pan whose release went missing outside the window stops at the next move", async () => {
  const { wireGestures } = await import("../../lib/runtime-bridge/dag/interaction/view/gestures.ts");
  const store = await import("../../lib/runtime-bridge/dag/store/globals.ts");
  const listeners = {};
  const captured = new Set();
  const host = {
    addEventListener: (type, fn) => { listeners[type] = fn; },
    setPointerCapture: (id) => captured.add(id),
    hasPointerCapture: (id) => captured.has(id),
    releasePointerCapture: (id) => captured.delete(id),
    classList: { add() {}, remove() {} },
  };
  wireGestures(host, { zoomAt() {}, dismiss() {}, moved() {} });
  store.setView(0, 0, 1);
  const fire = (type, o) => listeners[type]({ type, pointerId: 7, target: null, ...o });

  fire("pointerdown", { button: 2, buttons: 2, clientX: 100, clientY: 100 });
  fire("pointermove", { button: -1, buttons: 2, clientX: 130, clientY: 100 });
  assert.equal(store._viewTx, 30);

  // The button came up beyond the window's edge; no pointerup ever arrives.
  fire("pointermove", { button: -1, buttons: 0, clientX: 400, clientY: 300 });
  fire("pointermove", { button: -1, buttons: 0, clientX: 500, clientY: 300 });
  assert.equal(store._viewTx, 30);
  assert.equal(store._viewTy, 0);
  assert.equal(host.hasPointerCapture(7), false);
});

test("A pan keeps following the cursor outside the window and stops when the button comes up", async () => {
  const { wireGestures } = await import("../../lib/runtime-bridge/dag/interaction/view/gestures.ts");
  const store = await import("../../lib/runtime-bridge/dag/store/globals.ts");
  const listeners = {};
  const host = {
    addEventListener: (type, fn) => { listeners[type] = fn; },
    setPointerCapture() {}, hasPointerCapture: () => false, releasePointerCapture() {},
    classList: { add() {}, remove() {} },
  };
  let follow = null;
  let stopped = 0;
  wireGestures(host, {
    zoomAt() {}, dismiss() {}, moved() {},
    followHeldButton: (held, onCursor, onReleased) => {
      follow = { held, onCursor, onReleased };
      return () => { stopped += 1; };
    },
  });
  store.setView(0, 0, 1);
  const fire = (type, o) => listeners[type]({ type, pointerId: 8, target: null, ...o });
  const quiet = () => new Promise((resolve) => setTimeout(resolve, 60));

  // A press alone starts nothing: a plain right click stays a plain click.
  fire("pointerdown", { button: 2, buttons: 2, clientX: 100, clientY: 100 });
  assert.equal(follow, null);
  fire("pointermove", { button: -1, buttons: 2, clientX: 120, clientY: 100 });
  assert.equal(follow.held, 2);
  assert.equal(store._viewTx, 20);

  // While the page still hears moves, the reports add nothing.
  follow.onCursor({ x: 500, y: 500 });
  follow.onCursor({ x: 520, y: 500 });
  assert.equal(store._viewTx, 20);

  // Past the edge the page hears nothing, and the reports drive the pan.
  await quiet();
  follow.onCursor({ x: 600, y: 540 });
  assert.equal(store._viewTx, 100);
  assert.equal(store._viewTy, 40);

  // The button comes up out there: the pan is over.
  follow.onReleased();
  assert.equal(stopped, 1);
  follow.onCursor({ x: 900, y: 900 });
  assert.equal(store._viewTx, 100);
});

const box = { left: 10, top: 10, right: 20, bottom: 20 };

test("A marquee selects what it touches, not only what it encloses", () => {
  assert.ok(sel.rectTouchesBox({ left: 18, top: 0, right: 40, bottom: 12 }, box));
  assert.ok(!sel.rectTouchesBox({ left: 21, top: 0, right: 40, bottom: 40 }, box));
});

test("Deleting a spawn square deletes the call that opened it; structure is skipped", () => {
  const graph = [
    { id: "root", display: "root" },
    { id: "u1", role: "user", predecessor: "root" },
    { id: "a1", role: "assistant", predecessor: "u1" },
    { id: "c1", role: "code", caller: "a1" },
    { id: "sp", role: "user", source: "agent_spawn", caller: "c1" },
    { id: "sum", role: "assistant", covers_ids: ["u1"] },
  ];
  const out = deletionTargets(["sp", "root", "sum", "u1"], graph, null);
  assert.deepEqual(out.ids.sort(), ["c1", "u1"]);
  assert.equal(out.skipped, 2);
});

test("A followup reply merged into a glyph goes with it", () => {
  const graph = [
    { id: "a1", role: "assistant" },
    { id: "f1", role: "assistant", source: "job_followup", predecessor: "a1" },
    { id: "u2", role: "user", predecessor: "f1" },
  ];
  const anchorOf = (id) => (id === "f1" ? "a1" : id);
  assert.deepEqual(deletionTargets(["a1"], graph, anchorOf).ids.sort(), ["a1", "f1"]);
});
