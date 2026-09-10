import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".control-overlay-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "overlay.mjs");
await build({
  absWorkingDir: webPath,
  stdin: {
    contents: `export { default as OverlayPage } from "./app/menu-overlay/browser-control/page";`,
    resolveDir: webPath,
  },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
});

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  const collapsed = this.getAttribute("data-collapsed") === "true";
  return { width: collapsed ? 36 : 280, height: collapsed ? 36 : 44, top: 0, left: 0, right: collapsed ? 36 : 280, bottom: collapsed ? 36 : 44, x: 0, y: 0 };
};
window.HTMLElement.prototype.hasPointerCapture = () => false;
window.HTMLElement.prototype.setPointerCapture = () => {};
window.HTMLElement.prototype.releasePointerCapture = () => {};

const events = [];
let update = null;
window.openprogramDesktop = {
  browserControlOverlay: {
    ready() {},
    event(payload) { events.push(payload); },
    onUpdate(cb) {
      update = cb;
      return () => {};
    },
  },
};

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { OverlayPage } = await import(pathToFileURL(bundle));

const payload = {
  resourceId: "page-a",
  generation: 1,
  conversationSessionId: "a",
  controlState: "active",
  showActions: true,
  connected: true,
  status: "Active",
  pauseLabel: "I will operate",
  showLabel: "Show actions",
  historyLabel: "Operation history",
  pauseDisabled: false,
  resumeDisabled: true,
  showTakeover: true,
  expandLabel: "Show controls",
  foldLabel: "Fold",
  dragLabel: "Small draggable Agent button. Click to expand. Drag to move.",
};

test("expanded overlay is an intrinsic compact row with icon fold, not a full-width sentence", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(OverlayPage)));
    await act(async () => update({ ...payload, collapsed: true }));
    const collapsed = host.querySelector("[data-browser-control='float']");
    assert.equal(collapsed.getAttribute("data-collapsed"), "true");
    assert.equal(collapsed.style.width, "36px");
    await act(async () => update({ ...payload, collapsed: false }));
    const expanded = host.querySelector("[data-browser-control='float']");
    assert.equal(expanded.getAttribute("data-collapsed"), "false");
    assert.equal(expanded.style.display, "inline-flex");
    assert.equal(expanded.style.width, "max-content");
    const fold = [...expanded.querySelectorAll("button")].find((button) => button.getAttribute("aria-label") === "Fold");
    assert.ok(fold);
    assert.equal(fold.textContent.includes("Agent controls"), false);
    assert.equal(fold.getAttribute("title"), "Fold");
    assert.ok(fold.querySelector("svg"));
    assert.equal(expanded.textContent.includes("Agent controls. Click to fold"), false);
    const layouts = events.filter((item) => item.type === "layout" && item.collapsed === false);
    const first = layouts.length;
    await act(async () => update({ ...payload, collapsed: false, status: "Active" }));
    const after = events.filter((item) => item.type === "layout" && item.collapsed === false).length;
    assert.equal(after, first, "stable payload updates must not repeat layout acks");
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
