// The agent cursor over a desktop app window is drawn only where that window
// shows: while the point it goes to lies under another app's window, it hides.
import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = scratchDir(webPath, "agent-control-cursor-test");
const bundle = join(dir, "agent-control.mjs");
await build({
  absWorkingDir: webPath,
  stdin: {
    contents: `export { default as ControlPage } from "./app/menu-overlay/agent-control/page";
export { coveredPoint } from "./components/agent-cursor/covered-point";`,
    resolveDir: webPath,
  },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
});

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
window.innerWidth = 564;

let push = null;
window.openprogramDesktop = {
  agentControlOverlay: {
    ready() {},
    onUpdate(cb) { push = cb; return () => {}; },
  },
};

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { ControlPage, coveredPoint } = await import(pathToFileURL(bundle));

test("a point is covered only inside a window in front", () => {
  const occluders = [[100, 50, 200, 100]];
  assert.equal(coveredPoint(occluders, 150, 80), true);
  assert.equal(coveredPoint(occluders, 99, 80), false);
  assert.equal(coveredPoint(occluders, 300, 80), false, "the right edge is outside");
  assert.equal(coveredPoint([], 150, 80), false);
});

test("the cursor hides while its point is under another window and shows where the window shows", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const base = { margin: 32, width: 564, height: 364, appName: "Calculator", buttons: null, reducedMotion: true };
  try {
    await act(async () => root.render(createElement(ControlPage)));
    // The point is at (32 + 40, 32 + 30) in the overlay; nothing covers it.
    await act(async () => push({ ...base, occluders: [[300, 0, 264, 364]], cursor: { x: 40, y: 30, action: "click", seq: 1 } }));
    const cursor = host.querySelector("[data-agent-cursor]").parentElement;
    assert.equal(cursor.style.opacity, "1");
    // The user brings another app's window over that part of the window.
    await act(async () => push({ ...base, occluders: [[0, 0, 200, 200]], cursor: { x: 40, y: 30, action: "click", seq: 1 } }));
    assert.equal(cursor.style.opacity, "0");
    // The next step goes to a part of the window that still shows.
    await act(async () => push({ ...base, occluders: [[0, 0, 200, 200]], cursor: { x: 400, y: 250, action: "click", seq: 2 } }));
    assert.equal(cursor.style.opacity, "1");
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
