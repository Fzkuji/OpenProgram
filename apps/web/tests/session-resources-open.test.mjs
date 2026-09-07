import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".resources-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "panel.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { SessionResourcesPanel } from "./components/session-resources/session-resources-panel";
    export { useCenterTabs } from "./lib/state/center-tabs-store";
    export { useWebTabPip } from "./lib/state/web-tab-pip-store";
    export { topLevelTabs } from "./lib/state/web-page-management";
    export { resetBrowserResources, getPreviewPreference, hideResourcePreview } from "./lib/state/session-resources";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "panel-services", setup(b) {
    b.onResolve({ filter: /^(next\/navigation|@\/lib\/use-session-resources)$/ }, a => ({ path: a.path, namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "next/navigation"
      ? 'export const useRouter = () => ({ push() {} });'
      : `export const useSessionResources = () => globalThis.resourceBackend || { rows: [], currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true };` }));
  }}],
});
const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.CustomEvent = window.CustomEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = {
  store: {},
  getItem(key) { return this.store[key] ?? null; },
  setItem(key, value) { this.store[key] = String(value); },
  removeItem(key) { delete this.store[key]; },
};
Object.defineProperty(window, "location", { value: { pathname: "/chat" } });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  SessionResourcesPanel, useCenterTabs, useWebTabPip, topLevelTabs,
  resetBrowserResources, getPreviewPreference, hideResourcePreview,
} = await import(pathToFileURL(bundle));

function pageTab(id, sessionId, url, extra = {}) {
  return { id, kind: "web", agentOpened: true, agentSessionId: sessionId, url, title: url, ...extra };
}

test("resource click selects a read-only preview without pinning or changing the live tab", async () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = pageTab("w:a", "a", "https://example.org");
  const pinned = pageTab("w:pin", "a", "https://pinned.test", { webPinned: true, title: "Pinned" });
  const manual = { id: "w:manual", kind: "web", title: "Manual", url: "https://manual.test" };
  useCenterTabs.setState({
    tabs: [session, page, pinned, manual, { id: "s:b", kind: "session", sessionId: "b", title: "Chat B" }],
    activeId: session.id, groups: [], splitWebTabId: null,
  });
  useWebTabPip.getState().end();
  globalThis.resourceBackend = {
    rows: [{
      id: "assoc-a", sessionId: "a", scopeSessionId: "a", kind: "web", title: "Example Domain",
      target: page.url, status: "open", source: "browser", sourceId: page.id, resourceId: "page-a",
      tabId: page.id, branchId: "br-a", branchName: "Research", agentName: "Research Agent",
      controlState: "active", generation: 1, sequence: 1,
    }],
    currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true,
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    assert.equal([...host.querySelectorAll("button")].some(b => /pin to top|remove from top/i.test(`${b.title}${b.getAttribute("aria-label") || ""}`)), false);
    assert.match(host.textContent, /Research/);
    const button = [...host.querySelectorAll("button")].find(b => b.title === page.url);
    assert.ok(button);
    await act(async () => button.click());
    const state = useCenterTabs.getState();
    assert.equal(state.activeId, session.id);
    assert.equal(state.tabs.find(t => t.id === page.id).webPinned, undefined);
    assert.ok(topLevelTabs(state.tabs, state.groups).some(t => t.id === pinned.id));
    assert.ok(topLevelTabs(state.tabs, state.groups).some(t => t.id === manual.id));
    assert.ok(!topLevelTabs(state.tabs, state.groups).some(t => t.id === page.id));
    assert.equal(useWebTabPip.getState().tabId, page.id);
    assert.equal(useWebTabPip.getState().ownerTabId, session.id);
    assert.equal(getPreviewPreference("a", "br-a").mode, "manual");
    await act(async () => button.click());
    assert.equal(useCenterTabs.getState().tabs.length, 5);
    assert.equal(useWebTabPip.getState().tabId, page.id);
  } finally {
    await act(async () => root.unmount()); host.remove();
    useWebTabPip.getState().end();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
  }
});

test("hide keeps the page and does not change the live tab identity", () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = pageTab("w:a", "a", "https://example.org");
  useCenterTabs.setState({ tabs: [session, page], activeId: session.id, groups: [], splitWebTabId: null });
  useWebTabPip.getState().show(page.id, session.id);
  hideResourcePreview("a", "br-a");
  useWebTabPip.getState().hide();
  assert.equal(useCenterTabs.getState().tabs.some(t => t.id === page.id), true);
  assert.equal(useCenterTabs.getState().activeId, session.id);
  assert.equal(useWebTabPip.getState().tabId, null);
  assert.equal(useWebTabPip.getState().backgroundTabId, page.id);
  useWebTabPip.getState().end();
  useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
  resetBrowserResources();
});
