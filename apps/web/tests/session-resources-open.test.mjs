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
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "panel-services", setup(b) {
    b.onResolve({ filter: /^(next\/navigation|@\/lib\/use-session-resources)$/ }, a => ({ path: a.path, namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "next/navigation"
      ? 'export const useRouter = () => ({ push() {} });'
      : 'export const useSessionResources = () => ({ rows: [], unavailable: false, loaded: true });' }));
  }}],
});
const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.CustomEvent = window.CustomEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
Object.defineProperty(window, "location", { value: { pathname: "/chat" } });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { SessionResourcesPanel, useCenterTabs, useWebTabPip, topLevelTabs } = await import(pathToFileURL(bundle));

for (const mode of ["floating", "background", "split", "other-preview"]) {
  test(`Resources opens a separate visible tab from ${mode}`, async () => {
    const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
    const page = { id: "w:a", kind: "web", agentOpened: true, agentSessionId: "a", url: "https://example.org", title: "Example Domain" };
    const other = { id: "w:b", kind: "web", agentOpened: true, agentSessionId: "b", url: "https://example.net", title: "Other page" };
    useCenterTabs.setState({ tabs: [session, page, { id: "s:b", kind: "session", sessionId: "b", title: "Chat B" }, other], activeId: session.id, groups: [], splitWebTabId: null });
    useWebTabPip.getState().end();
    if (mode === "split") useCenterTabs.getState().setSplitWebTab(page.id);
    else useWebTabPip.getState().show(mode === "other-preview" ? other.id : page.id, mode === "other-preview" ? "s:b" : session.id);
    if (mode === "background") useWebTabPip.getState().hide();
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(createElement(SessionResourcesPanel)));
      const button = [...host.querySelectorAll("button")].find(b => b.title === page.url);
      assert.ok(button);
      await act(async () => button.click());
      const state = useCenterTabs.getState();
      assert.equal(state.activeId, page.id);
      assert.ok(topLevelTabs(state.tabs, state.groups).some(t => t.id === page.id), "opened page must have its own visible top tab");
      assert.ok(state.tabs.some(t => t.id === session.id && t.kind === "session"));
      assert.ok(!state.groups.some(g => g.memberIds.includes(page.id)), "page must be independent from chat");
      assert.equal(state.tabs.find(t => t.id === page.id).agentSessionId, "a");
      assert.equal(useWebTabPip.getState().tabId, mode === "other-preview" ? other.id : null);
      assert.equal(useWebTabPip.getState().backgroundTabId, null);
      assert.ok(!host.textContent.includes("Other page"));
      await act(async () => button.click());
      assert.equal(useCenterTabs.getState().tabs.length, 4, "repeated opening reuses the existing browser leaf");
    } finally {
      await act(async () => root.unmount()); host.remove();
      useWebTabPip.getState().end();
      useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    }
  });
}
