import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".pip-open-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "pip.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { WebTabPip } from "./components/center-tabs/web-tab-pip";
    export { useCenterTabs } from "./lib/state/center-tabs-store";
    export { useWebTabPip, getSnapshot, setSnapshot, usePipSnapshots } from "./lib/state/web-tab-pip-store";
    export { topLevelTabs } from "./lib/state/web-page-management";
    export { resetBrowserResources, getPreviewPreference, selectResourcePreview } from "./lib/state/session-resources";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "pip-services", setup(b) {
    b.onResolve({ filter: /desktop-bridge/ }, () => ({ path: "desktop-bridge", namespace: "test-services" }));
    b.onResolve({ filter: /browser-control-bar/ }, () => ({ path: "control-bar", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "control-bar"
      ? "export function BrowserControlBar() { return null; }"
      : "export function desktopBridge() { return null; }" }));
  }}],
});
const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.CustomEvent = window.CustomEvent;
globalThis.HTMLElement = window.HTMLElement;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = {
  store: { agentic_locale: "en" },
  getItem(key) { return this.store[key] ?? null; },
  setItem(key, value) { this.store[key] = String(value); },
  removeItem(key) { delete this.store[key]; },
};
Object.defineProperty(window, "location", { value: { pathname: "/chat" } });
Object.defineProperty(window, "navigator", { value: { language: "en", userAgent: "" } });
window.innerWidth = 1200;
window.innerHeight = 800;
window.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
globalThis.ResizeObserver = window.ResizeObserver;
window.requestAnimationFrame = () => 0;
window.cancelAnimationFrame = () => {};
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  WebTabPip, useCenterTabs, useWebTabPip, getSnapshot, setSnapshot, usePipSnapshots,
  topLevelTabs, resetBrowserResources, getPreviewPreference, selectResourcePreview,
} = await import(pathToFileURL(bundle));

function pageTab(id, sessionId, url, extra = {}) {
  return { id, kind: "web", agentOpened: true, agentSessionId: sessionId, url, title: url, ...extra };
}

function hiddenPages() {
  return [0, 1, 2, 3, 4].map(index =>
    pageTab(`w:hidden-${index}`, "a", `https://hidden.test/${index}`, { title: `Hidden ${index}` }));
}

function toolbarUsePage(host) {
  return [...host.querySelectorAll("button")]
    .find(button => button.getAttribute("aria-label") === "Use in webpage");
}

function fallbackUsePage(host) {
  return [...host.querySelectorAll("button")]
    .find(button => button.textContent === "Use in webpage" && button.getAttribute("aria-label") !== "Use in webpage");
}

async function withPip(run) {
  resetBrowserResources();
  usePipSnapshots.setState({ shots: {} });
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = pageTab("w:pip-page", "a", "https://page.test/1", { title: "Page 1" });
  const hidden = hiddenPages();
  useCenterTabs.setState({
    tabs: [session, page, ...hidden],
    activeId: session.id, groups: [], splitWebTabId: null,
  });
  setSnapshot(page.id, "data:image/png,keep");
  selectResourcePreview("a", null, "assoc-keep");
  const prefBefore = { ...getPreviewPreference("a", null) };
  useWebTabPip.getState().show(page.id, session.id);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(WebTabPip)));
    await run({ host, page, hidden, session, prefBefore });
  } finally {
    await act(async () => root.unmount());
    host.remove();
    useWebTabPip.getState().end();
    usePipSnapshots.setState({ shots: {} });
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
  }
}

function assertRevealedExactPage({ page, hidden, session, prefBefore }) {
  const state = useCenterTabs.getState();
  const pip = useWebTabPip.getState();
  assert.equal(state.activeId, page.id);
  assert.equal(state.tabs.find(tab => tab.id === page.id).url, page.url);
  assert.equal(state.tabs.find(tab => tab.id === page.id).agentSessionId, "a");
  assert.deepEqual(state.tabs.map(tab => tab.id), [session.id, page.id, ...hidden.map(tab => tab.id)]);
  const visible = topLevelTabs(state.tabs, state.groups).map(tab => tab.id);
  assert.ok(visible.includes(page.id), "Use in webpage must add the existing page to the top strip");
  assert.equal(visible.filter(id => id === page.id).length, 1);
  hidden.forEach(tab => assert.ok(!visible.includes(tab.id)));
  assert.ok(visible.includes(session.id));
  assert.equal(pip.tabId, page.id);
  assert.equal(pip.ownerTabId, session.id);
  assert.equal(getSnapshot(page.id), "data:image/png,keep");
  assert.deepEqual(getPreviewPreference("a", null), prefBefore);
}

test("PiP toolbar Use in webpage reveals the exact hidden page as the current top tab", async () => {
  await withPip(async ({ host, page, hidden, session, prefBefore }) => {
    const visibleBefore = topLevelTabs(useCenterTabs.getState().tabs, []).map(tab => tab.id);
    assert.ok(!visibleBefore.includes(page.id));
    hidden.forEach(tab => assert.ok(!visibleBefore.includes(tab.id)));
    const button = toolbarUsePage(host);
    assert.ok(button);
    assert.equal(fallbackUsePage(host) !== button, true);
    await act(async () => button.click());
    assertRevealedExactPage({ page, hidden, session, prefBefore });
  });
});

test("PiP fallback Use in webpage reveals the exact hidden page as the current top tab", async () => {
  await withPip(async ({ host, page, hidden, session, prefBefore }) => {
    const button = fallbackUsePage(host);
    assert.ok(button);
    assert.equal(toolbarUsePage(host) !== button, true);
    await act(async () => button.click());
    assertRevealedExactPage({ page, hidden, session, prefBefore });
  });
});
