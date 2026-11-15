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

globalThis.document = {
  fullscreenElement: null,
  documentElement: { hasAttribute: () => false },
  addEventListener: () => {},
};
globalThis.window = { dispatchEvent: () => true, addEventListener: () => {} };
const { registerVisibleWebTabBounds, removeVisibleWebTabBounds } = await import("../../lib/desktop/bridge/bridge-state.ts");

// A native page is drawn by the browser process's compositor, which needs
// about a millisecond after the bounds arrive to deliver its frame; the
// renderer's frame reaches the display about half a millisecond after it
// commits, and the display draws as soon as that frame arrives. Bounds sent
// after the commit arrive too late for that draw (traced: the page a
// refresh behind its frame in a quarter of the refreshes while the right
// panel moved), so they leave while the frame is still painting.
test("a frame's bounds leave within the task that measured them, before the frame commits", async () => {
  const synced = [];
  const bridge = { windowId: "w", webTab: { syncVisible: items => synced.push(items) } };
  // One animation frame callback measures the page twice.
  registerVisibleWebTabBounds(bridge, "w:pip", { x: 821, y: 93, width: 280, height: 156 });
  registerVisibleWebTabBounds(bridge, "w:pip", { x: 827, y: 93, width: 280, height: 156 });
  assert.deepEqual(synced, [], "nothing leaves mid-measurement");
  await Promise.resolve();
  assert.equal(synced.length, 1, "sent in a microtask of the measuring task, once");
  assert.deepEqual(synced[0], [{ id: "w:pip", bounds: { x: 827, y: 93, width: 280, height: 156 }, corners: null }]);

  removeVisibleWebTabBounds(bridge, "w:pip");
  await Promise.resolve();
  assert.deepEqual(synced.at(-1), [], "hiding leaves as promptly");
});
