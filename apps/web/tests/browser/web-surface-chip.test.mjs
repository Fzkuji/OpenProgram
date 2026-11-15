import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

// The composer's web page chip names one page and acts on that page only.
const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = scratchDir(webPath, "surface-chip-test");
const bundle = join(dir, "chip.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { WebSurfaceChip } from "./components/chat/composer/environment-row/chips/web-surface-chip";
    export { useWebTabPip } from "./lib/browser/web-tab/web-tab-pip-store";
    export { useCenterTabs } from "./lib/tabs/center-tabs-store";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "chip-services", setup(b) {
    b.onResolve({ filter: /^@\/lib\/desktop\/desktop-bridge$/ }, () => ({ path: "bridge", namespace: "chip" }));
    b.onResolve({ filter: /^@\/lib\/browser\/browser-resource-projection$/ }, () => ({ path: "projection", namespace: "chip" }));
    b.onResolve({ filter: /^@\/components\/ui\/tooltip$/ }, () => ({ path: "tooltip", namespace: "chip" }));
    b.onResolve({ filter: /^@\/components\/solar-icons$/ }, () => ({ path: "icons", namespace: "chip" }));
    b.onLoad({ filter: /.*/, namespace: "chip" }, a => ({ resolveDir: webPath, contents: {
      bridge: "export const surfaceRefForChat = () => globalThis.chipSurface ?? null;",
      projection: "export const hidePagePreview = () => { globalThis.chipActions.push('hide'); }; export const restorePagePreview = (id, owner) => { globalThis.chipActions.push(['restore', id, owner]); };",
      tooltip: "export const HoverTip = ({ children }) => children; export const TipBody = () => null;",
      icons: "export const SolarIcon = () => null;",
    }[a.path] }));
  }}],
});

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.HTMLElement = window.HTMLElement;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
Object.defineProperty(window, "navigator", { value: { language: "en", userAgent: "" } });

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { WebSurfaceChip, useWebTabPip, useCenterTabs } = await import(pathToFileURL(bundle));

const chat = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
const preview = { id: "w:https://preview.test/", kind: "web", url: "https://preview.test/", title: "Preview page" };
const paired = { id: "w:https://paired.test/", kind: "web", url: "https://paired.test/", title: "Paired page" };

async function renderChip({ surface, pip }) {
  globalThis.chipSurface = surface;
  globalThis.chipActions = [];
  useCenterTabs.setState({ tabs: [chat, preview, paired], activeId: chat.id, groups: [], splitWebTabId: null });
  if (pip === "closed") {
    // Shown over this chat, then closed: the page stays paired with it.
    useWebTabPip.getState().show(preview.id, chat.id);
    useWebTabPip.getState().hide();
  } else useWebTabPip.setState(pip);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(createElement(WebSurfaceChip, { sessionId: "a" })));
  const chip = host.querySelector("[data-web-surface-chip]");
  const done = async () => { await act(async () => root.unmount()); host.remove(); };
  return { chip, done };
}

const floating = { tabId: preview.id, ownerTabId: chat.id, ownerSessionId: "a", backgroundTabId: null, backgroundOwnerTabId: null };

test("in a split the chip names the other pane's page and leaves the preview of another page alone", async () => {
  // It used to read "Paired page" and close the preview of "Preview page".
  const { chip, done } = await renderChip({ surface: { tab_id: paired.id, title: paired.title, url: paired.url }, pip: floating });
  try {
    assert.equal(chip.getAttribute("data-web-surface-chip"), paired.id);
    assert.equal(chip.tagName, "SPAN", "no preview toggle on a chip naming another page");
    assert.equal(chip.getAttribute("aria-label"), "Agent can access: Paired page");
  } finally { await done(); }
});

test("the chip that names the preview's page closes it", async () => {
  const { chip, done } = await renderChip({ surface: { tab_id: preview.id, title: preview.title, url: preview.url }, pip: floating });
  try {
    assert.equal(chip.getAttribute("data-web-surface-chip"), preview.id);
    assert.equal(chip.getAttribute("aria-label"), "Close page preview: Preview page");
    await act(async () => chip.click());
    assert.deepEqual(globalThis.chipActions, ["hide"]);
  } finally { await done(); }
});

test("after the preview is closed the chip names its page and brings it back", async () => {
  const { chip, done } = await renderChip({
    surface: null,
    pip: "closed",
  });
  try {
    assert.equal(chip.getAttribute("data-web-surface-chip"), preview.id);
    assert.equal(chip.getAttribute("aria-label"), "Show page preview: Preview page");
    await act(async () => chip.click());
    assert.deepEqual(globalThis.chipActions, [["restore", preview.id, chat.id]]);
  } finally { await done(); }
});
