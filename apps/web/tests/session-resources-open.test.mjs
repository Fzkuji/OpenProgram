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
    export { useBrowserControlStore, resetBrowserControl } from "./lib/state/browser-control";
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
document.oninput = null;
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
  useBrowserControlStore, resetBrowserControl,
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
    assert.ok(previewInConversationButton(host, "Example Domain"));
    assert.equal([...host.querySelectorAll("button")].some(b => b.textContent === "↗"), false);
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

function openInTabButton(host, title) {
  return [...host.querySelectorAll("button")]
    .find(button => button.getAttribute("aria-label") === `Open in tab: ${title}`);
}

function previewInConversationButton(host, title) {
  return [...host.querySelectorAll("button")]
    .find(button => button.getAttribute("aria-label") === `Preview in conversation: ${title}`);
}

function keydown(key) {
  const event = new window.Event("keydown", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "key", { value: key });
  return event;
}

function setInput(input, value) {
  input.type ||= "text";
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set
    || Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  if (setter) setter.call(input, value);
  else input.value = value;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
}

test("five agent-opened session pages stay out of the top strip by default", () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const hidden = [0, 1, 2, 3, 4].map(index =>
    pageTab(`w:hidden-${index}`, "a", `https://hidden.test/${index}`, { title: `Hidden ${index}` }));
  const pinned = pageTab("w:pin", "a", "https://pinned.test", { webPinned: true, title: "Pinned" });
  const manual = { id: "w:manual", kind: "web", title: "Manual", url: "https://manual.test" };
  const tabs = [session, ...hidden, pinned, manual];
  useCenterTabs.setState({ tabs, activeId: session.id, groups: [], splitWebTabId: null });
  const visible = topLevelTabs(useCenterTabs.getState().tabs, []).map(tab => tab.id);
  assert.deepEqual(visible, [session.id, pinned.id, manual.id]);
  assert.equal(useCenterTabs.getState().tabs.length, 8);
  useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
  resetBrowserResources();
});

test("Open in tab reveals the exact existing page as the current top tab", async () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = pageTab("w:exclusive-page", "a", "https://page.test/1", { title: "Page 1" });
  const hidden = [0, 1, 2, 3, 4].map(index =>
    pageTab(`w:hidden-${index}`, "a", `https://hidden.test/${index}`, { title: `Hidden ${index}` }));
  const pinned = pageTab("w:pin", "a", "https://pinned.test", { webPinned: true, title: "Pinned" });
  const manual = { id: "w:manual", kind: "web", title: "Manual", url: "https://manual.test" };
  const groupedSession = { id: "s:group", kind: "session", sessionId: "g", title: "Grouped chat" };
  const groupedPage = pageTab("w:grouped", "a", "https://grouped.test", { title: "Grouped page" });
  const groups = [{
    id: "g:split",
    memberIds: [groupedSession.id, groupedPage.id],
    visibleIds: [groupedSession.id, groupedPage.id],
    focusedId: groupedSession.id,
  }];
  const documentIdsBefore = [
    session.id, page.id, ...hidden.map(tab => tab.id), pinned.id, manual.id,
    groupedSession.id, groupedPage.id,
  ];
  useCenterTabs.setState({
    tabs: [session, page, ...hidden, pinned, manual, groupedSession, groupedPage],
    activeId: session.id, groups, splitWebTabId: null,
  });
  useWebTabPip.getState().end();
  globalThis.resourceBackend = {
    rows: [{
      id: "assoc-branch", sessionId: "a", scopeSessionId: "a", kind: "web", title: "Page 1",
      target: page.url, status: "open", source: "browser", sourceId: page.id, resourceId: "page-1",
      tabId: null, branchId: "br-a", branchName: "Research", agentName: "Research Agent",
      controlState: "idle", generation: 1, sequence: 1,
    }],
    currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true,
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    assert.match(host.textContent, /Unassigned/);
    assert.equal([...host.querySelectorAll("button")].some(b => /pin to top|remove from top/i.test(`${b.title}${b.getAttribute("aria-label") || ""}`)), false);
    const visibleBefore = topLevelTabs(useCenterTabs.getState().tabs, groups).map(tab => tab.id);
    assert.ok(!visibleBefore.includes(page.id));
    hidden.forEach(tab => assert.ok(!visibleBefore.includes(tab.id)));
    const button = openInTabButton(host, "Page 1");
    assert.ok(button);
    await act(async () => button.click());
    const state = useCenterTabs.getState();
    assert.deepEqual(state.tabs.map(tab => tab.id), documentIdsBefore);
    const opened = state.tabs.find(tab => tab.id === page.id);
    assert.equal(opened.id, page.id);
    assert.equal(opened.url, page.url);
    assert.equal(opened.agentOpened, true);
    assert.equal(opened.agentSessionId, "a");
    assert.equal(state.activeId, page.id);
    const visible = topLevelTabs(state.tabs, state.groups).map(tab => tab.id);
    assert.ok(visible.includes(page.id), "explicit Open in tab must add the existing page to the top strip");
    assert.equal(visible.filter(id => id === page.id).length, 1);
    hidden.forEach(tab => assert.ok(!visible.includes(tab.id)));
    assert.ok(visible.includes(pinned.id));
    assert.ok(visible.includes(manual.id));
    assert.ok(visible.includes(groupedPage.id));
    assert.equal(state.tabs.find(tab => tab.id === pinned.id).webPinned, true);
    assert.equal(state.tabs.find(tab => tab.id === groupedPage.id).webPinned, undefined);
    assert.deepEqual(state.groups, groups);
    assert.equal(state.splitWebTabId, null);
    await act(async () => button.click());
    const again = useCenterTabs.getState();
    assert.deepEqual(again.tabs.map(tab => tab.id), documentIdsBefore);
    assert.equal(again.activeId, page.id);
    assert.equal(again.tabs.find(tab => tab.id === page.id).url, page.url);
    assert.ok(topLevelTabs(again.tabs, again.groups).some(tab => tab.id === page.id));
  } finally {
    await act(async () => root.unmount()); host.remove();
    useWebTabPip.getState().end();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
  }
});

test("explicit Preview in conversation keeps the hidden Page identity and adds no top tab", async () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = pageTab("w:preview-hidden", "a", "https://hidden.preview.test/", { title: "Hidden preview" });
  const documentIds = [session.id, page.id];
  useCenterTabs.setState({ tabs: [session, page], activeId: session.id, groups: [], splitWebTabId: null });
  useWebTabPip.getState().end();
  globalThis.resourceBackend = {
    rows: [{
      id: "assoc-preview", sessionId: "a", scopeSessionId: "a", kind: "web", title: "Hidden preview",
      target: page.url, status: "open", source: "browser", sourceId: page.id, resourceId: "page-preview",
      tabId: page.id, branchId: "br-a", branchName: "Research",
      controlState: "idle", generation: 1, sequence: 1,
    }],
    currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true,
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    assert.ok(!topLevelTabs(useCenterTabs.getState().tabs, []).some(tab => tab.id === page.id));
    const button = previewInConversationButton(host, "Hidden preview");
    assert.ok(button);
    await act(async () => button.click());
    const state = useCenterTabs.getState();
    assert.deepEqual(state.tabs.map(tab => tab.id), documentIds);
    assert.equal(state.tabs.find(tab => tab.id === page.id).url, page.url);
    assert.equal(state.activeId, session.id);
    assert.ok(!topLevelTabs(state.tabs, state.groups).some(tab => tab.id === page.id));
    assert.equal(useWebTabPip.getState().tabId, page.id);
    assert.equal(useWebTabPip.getState().ownerTabId, session.id);
    assert.equal(getPreviewPreference("a", "br-a").mode, "manual");
    assert.equal(getPreviewPreference("a", "br-a").targetId, "assoc-preview");
    assert.equal(getPreviewPreference("a", "br-a").hidden, false);
  } finally {
    await act(async () => root.unmount()); host.remove();
    useWebTabPip.getState().end();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
  }
});

test("Preview in conversation from an active webpage returns to the owning session", async () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = pageTab("w:live-preview", "a", "https://live.preview.test/", { title: "Live preview", webPinned: true });
  useCenterTabs.setState({ tabs: [session, page], activeId: page.id, groups: [], splitWebTabId: null });
  useWebTabPip.getState().end();
  globalThis.resourceBackend = {
    rows: [{
      id: "assoc-live", sessionId: "a", scopeSessionId: "a", kind: "web", title: "Live preview",
      target: page.url, status: "open", source: "browser", sourceId: page.id, resourceId: "page-live",
      tabId: page.id, branchId: "br-a", branchName: "Research",
      controlState: "idle", generation: 1, sequence: 1,
    }],
    currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true,
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    const before = useCenterTabs.getState();
    assert.equal(before.activeId, page.id);
    assert.ok(topLevelTabs(before.tabs, before.groups).some(tab => tab.id === page.id));
    const button = previewInConversationButton(host, "Live preview");
    assert.ok(button);
    await act(async () => button.click());
    const state = useCenterTabs.getState();
    assert.equal(state.activeId, session.id);
    assert.equal(state.tabs.filter(tab => tab.id === page.id).length, 1);
    assert.equal(state.tabs.find(tab => tab.id === page.id).url, page.url);
    assert.equal(state.tabs.find(tab => tab.id === page.id).webPinned, true);
    assert.equal(useWebTabPip.getState().tabId, page.id);
    assert.equal(useWebTabPip.getState().ownerTabId, session.id);
    assert.equal(getPreviewPreference("a", "br-a").mode, "manual");
    assert.equal(getPreviewPreference("a", "br-a").targetId, "assoc-live");
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

test("current branch group uses a title-adjacent section chevron and keyboard-collapses without hiding other groups", async () => {
  resetBrowserResources();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const currentPage = pageTab("w:a", "a", "https://example.org", { title: "Example Domain" });
  const otherPage = pageTab("w:b", "a", "https://other.test", { title: "Other page" });
  useCenterTabs.setState({
    tabs: [session, currentPage, otherPage],
    activeId: session.id, groups: [], splitWebTabId: null,
  });
  globalThis.resourceBackend = {
    rows: [{
      id: "assoc-a", sessionId: "a", scopeSessionId: "a", kind: "web", title: "Example Domain",
      target: currentPage.url, status: "open", source: "browser", sourceId: currentPage.id, resourceId: "page-a",
      tabId: currentPage.id, branchId: "br-a", branchName: "Research",
      controlState: "idle", generation: 1, sequence: 1,
    }, {
      id: "assoc-b", sessionId: "a", scopeSessionId: "a", kind: "web", title: "Other page",
      target: otherPage.url, status: "open", source: "browser", sourceId: otherPage.id, resourceId: "page-b",
      tabId: otherPage.id, branchId: "br-b", branchName: "Preview live check",
      controlState: "idle", generation: 1, sequence: 1,
    }],
    currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true,
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    const currentBlock = host.querySelector('[data-resource-group="br-a"]');
    const otherBlock = host.querySelector('[data-resource-group="br-b"]');
    const current = currentBlock?.querySelector("[aria-expanded]");
    const other = otherBlock?.querySelector("[aria-expanded]");
    assert.equal(currentBlock.getAttribute("data-current"), "true");
    assert.equal(otherBlock.getAttribute("data-current"), null);
    assert.equal(current.getAttribute("role"), "button");
    assert.equal(current.getAttribute("aria-expanded"), "true");
    const title = current.firstElementChild;
    assert.equal(title?.textContent, "Research");
    const chevron = title.nextElementSibling;
    assert.ok(chevron?.querySelector("svg"), "chevron sits immediately after the title");
    assert.ok(!chevron.textContent.trim());
    assert.equal(currentBlock.querySelector("small")?.textContent, "Current");
    assert.ok(!current.contains(currentBlock.querySelector("small")), "Current trails the section, not the chevron");
    assert.equal(other.getAttribute("aria-expanded"), "true");
    assert.ok(host.querySelector('[title="https://example.org"]'));
    await act(async () => {
      current.focus();
      current.dispatchEvent(keydown("Enter"));
    });
    assert.equal(current.getAttribute("aria-expanded"), "false", "Enter toggles the group once");
    assert.equal(host.querySelector('[title="https://example.org"]'), null);
    assert.equal(other.getAttribute("aria-expanded"), "true");
    assert.ok(host.querySelector('[title="https://other.test"]'));
    const search = host.querySelector('input[aria-label="Search resources"]');
    await act(async () => setInput(search, "Example"));
    assert.equal(current.getAttribute("aria-expanded"), "true");
    assert.ok(host.querySelector('[title="https://example.org"]'));
    await act(async () => setInput(search, ""));
    assert.equal(current.getAttribute("aria-expanded"), "false", "collapse persists after search clears");
    await act(async () => {
      current.focus();
      current.dispatchEvent(keydown(" "));
    });
    assert.equal(current.getAttribute("aria-expanded"), "true", "Space toggles the group once");
    assert.ok(host.querySelector('[title="https://example.org"]'));
    assert.equal(other.getAttribute("aria-expanded"), "true");
  } finally {
    await act(async () => root.unmount()); host.remove();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
  }
});

function mountPanel() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  return { host, root };
}

test("pending close notices use plain pause failure labels without internal counts", async () => {
  resetBrowserResources();
  resetBrowserControl();
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  useCenterTabs.setState({ tabs: [session], activeId: session.id, groups: [], splitWebTabId: null });
  globalThis.resourceBackend = {
    rows: [], currentBranchId: "br-a", currentBranchName: "Research", unavailable: false, loaded: true,
  };
  const pending = {
    tabId: "w:a", resourceId: "page-a", generation: 1,
    associationIds: ["assoc-a", "assoc-b"], executionIds: ["exec-a"],
  };
  const { host, root } = mountPanel();
  try {
    useBrowserControlStore.setState({ pendingCloses: [{ ...pending }] });
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    assert.match(host.textContent, /Waiting for Agent to pause before closing the page…/);
    assert.doesNotMatch(host.textContent, /references|executions|2|1/);

    useBrowserControlStore.setState({ pendingCloses: [{ ...pending, error: "Stop unconfirmed" }] });
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    assert.match(host.textContent, /Could not pause Agent\. The page is still open\./);
    assert.doesNotMatch(host.textContent, /Stop unconfirmed|Unknown/);

    useBrowserControlStore.setState({ pendingCloses: [{ ...pending, error: "Unknown" }] });
    await act(async () => root.render(createElement(SessionResourcesPanel)));
    assert.match(host.textContent, /Could not confirm the page status\. Check the connection and try again\./);
    assert.doesNotMatch(host.textContent, /Stop unconfirmed|Unknown|Could not pause Agent/);
  } finally {
    await act(async () => root.unmount()); host.remove();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserControl();
    resetBrowserResources();
  }
});
