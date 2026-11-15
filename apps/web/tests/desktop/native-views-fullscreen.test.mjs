import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../../${specifier.slice(2)}`, import.meta.url).href;
      const file = `${base}.ts`;
      return { url: existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      return { url: existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

// A document with just what the fullscreen handling reads.
const listeners = {};
const attributes = new Set();
globalThis.document = {
  fullscreenElement: null,
  documentElement: { hasAttribute: name => attributes.has(name) },
  addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
};
globalThis.window = {
  dispatchEvent: () => true,
  addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
};
const { registerVisibleWebTabBounds } = await import("../../lib/desktop/bridge/bridge-state.ts");

const flushed = () => new Promise(resolve => queueMicrotask(resolve));

test("native web views hide while something is fullscreen and return after", async () => {
  const synced = [];
  const bridge = { windowId: "w", webTab: { syncVisible: items => synced.push(items.map(item => item.id)) } };
  registerVisibleWebTabBounds(bridge, "w:page", { x: 0, y: 0, width: 300, height: 200 });
  await flushed();
  assert.deepEqual(synced.at(-1), ["w:page"]);

  document.fullscreenElement = {};
  listeners.fullscreenchange.forEach(fn => fn());
  await flushed();
  assert.deepEqual(synced.at(-1), [], "a fullscreen video covers the window, so no native view shows");

  document.fullscreenElement = null;
  listeners.fullscreenchange.forEach(fn => fn());
  await flushed();
  assert.deepEqual(synced.at(-1), ["w:page"], "leaving fullscreen brings the page back");
});

test("native web views hide while the session pane's full screen covers the window", async () => {
  const synced = [];
  const bridge = { windowId: "w", webTab: { syncVisible: items => synced.push(items.map(item => item.id)) } };
  registerVisibleWebTabBounds(bridge, "w:side", { x: 0, y: 0, width: 300, height: 200 });
  await flushed();
  assert.deepEqual(synced.at(-1), ["w:page", "w:side"]);

  attributes.add("data-canvas-fullscreen");
  listeners["op:canvas-fullscreen"].forEach(fn => fn());
  await flushed();
  assert.deepEqual(synced.at(-1), [], "the right sidebar's page would show through the full-screen pane");

  attributes.delete("data-canvas-fullscreen");
  listeners["op:canvas-fullscreen"].forEach(fn => fn());
  await flushed();
  assert.deepEqual(synced.at(-1), ["w:page", "w:side"]);
});
