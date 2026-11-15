import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = scratchDir(webPath, "pip-open-test");
const bundle = join(dir, "pip.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { WebTabPip } from "./components/center-tabs/pip/web-tab-pip";
    export { useCenterTabs } from "./lib/tabs/center-tabs-store";
    export { useWebTabPip, getSnapshot, setSnapshot, usePipSnapshots } from "./lib/browser/web-tab/web-tab-pip-store";
    export { topLevelTabs } from "./lib/browser/web-tab/web-page-management";
    export { resetBrowserResources, getPreviewPreference, selectResourcePreview } from "./lib/chat/resources/session-resources";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "pip-services", setup(b) {
    b.onResolve({ filter: /desktop-bridge/ }, () => ({ path: "desktop-bridge", namespace: "test-services" }));
    b.onResolve({ filter: /browser-control-bar/ }, () => ({ path: "control-bar", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "control-bar"
      ? "export function BrowserControlBar() { return null; } export function ActionCueTravel() { return null; } export function BrowserPageCue() { return null; }"
      : "export function desktopBridge() { return null; } export function installDesktopMenuHandlers() {} export function ensureWebView() {} export function removeVisibleWebTabBounds() {} export function registerVisibleWebTabBounds() {} export function setWebTabReady() {}" }));
  }}],
});
const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.CustomEvent = window.CustomEvent;
// The preview dispatches its own geometry events on its element.
globalThis.Event = window.Event;
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
const resizeObservers = new Set();
window.ResizeObserver = class {
  constructor(callback) { this.callback = callback; resizeObservers.add(this); }
  observe() {} disconnect() { resizeObservers.delete(this); } unobserve() {}
};
globalThis.ResizeObserver = window.ResizeObserver;
window.requestAnimationFrame = () => 0;
window.cancelAnimationFrame = () => {};
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.HTMLElement.prototype.hasPointerCapture = () => false;
window.HTMLElement.prototype.setPointerCapture = () => {};
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.scrollIntoView = () => {};
if (!globalThis.DOMRect) {
  globalThis.DOMRect = class DOMRect {
    constructor(x = 0, y = 0, width = 0, height = 0) {
      this.x = x; this.y = y; this.width = width; this.height = height;
      this.top = y; this.left = x; this.right = x + width; this.bottom = y + height;
    }
  };
}
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

function toolbarOpenPage(host) {
  const pip = host.querySelector("[data-pip='true']");
  const chrome = pip?.firstElementChild;
  return [...(chrome?.querySelectorAll("button") || [])]
    .find(button => button.getAttribute("aria-label") === "Open page" || button.textContent === "Open page");
}

function bodyOpenPage(host) {
  const pip = host.querySelector("[data-pip='true']");
  const chrome = pip?.firstElementChild;
  return [...(pip?.querySelectorAll("button") || [])]
    .find(button =>
      (button.getAttribute("aria-label") === "Open page" || button.textContent === "Open page")
      && !chrome?.contains(button));
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
  assert.ok(visible.includes(page.id), "Open page must add the existing page to the top strip");
  assert.equal(visible.filter(id => id === page.id).length, 1);
  hidden.forEach(tab => assert.ok(!visible.includes(tab.id)));
  assert.ok(visible.includes(session.id));
  assert.equal(pip.tabId, page.id);
  assert.equal(pip.ownerTabId, session.id);
  assert.equal(getSnapshot(page.id), "data:image/png,keep");
  assert.deepEqual(getPreviewPreference("a", null), prefBefore);
}

test("PiP toolbar Open page reveals the exact hidden page as the current top tab", async () => {
  await withPip(async ({ host, page, hidden, session, prefBefore }) => {
    const visibleBefore = topLevelTabs(useCenterTabs.getState().tabs, []).map(tab => tab.id);
    assert.ok(!visibleBefore.includes(page.id));
    hidden.forEach(tab => assert.ok(!visibleBefore.includes(tab.id)));
    const button = toolbarOpenPage(host);
    assert.ok(button);
    assert.equal(bodyOpenPage(host), undefined);
    await act(async () => button.click());
    assertRevealedExactPage({ page, hidden, session, prefBefore });
    assert.equal(host.querySelector("[data-pip='true']"), null, "opening the Page hides the chat preview");
    assert.equal(useWebTabPip.getState().tabId, page.id);
    assert.equal(useWebTabPip.getState().ownerTabId, session.id);
  });
});

test("browser PiP embeds an interactive sandboxed page and keeps Open page on chrome", async () => {
  await withPip(async ({ host, page, hidden, session, prefBefore }) => {
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    const frame = pip.querySelector("iframe");
    assert.ok(frame);
    assert.equal(frame.getAttribute("src"), "https://page.test/1");
    assert.equal(frame.getAttribute("sandbox"), "allow-scripts allow-same-origin allow-forms allow-popups");
    assert.equal(pip.querySelector("img"), null);
    assert.equal(bodyOpenPage(host), undefined);
    const buttons = [...pip.querySelectorAll("button")];
    assert.ok(buttons.length > 0);
    buttons.forEach(button => assert.equal(chrome.contains(button), true));
    const button = toolbarOpenPage(host);
    assert.ok(button);
    assert.equal(chrome.contains(button), true);
    await act(async () => button.click());
    assertRevealedExactPage({ page, hidden, session, prefBefore });
    assert.equal(host.querySelector("[data-pip='true']"), null, "opening the Page hides the chat preview");
  });
});


test("unsized preview follows its chat pane after layout hydration and resize", async () => {
  const parent = document.body;
  let chatWidth = 500;
  let displayed = false;
  const legacy = document.createElement("div");
  legacy.className = "center-pane-chat";
  parent.append(legacy);
  const chat = document.createElement("div");
  chat.dataset.centerTabId = "s:a";
  chat.className = "center-pane-chat";
  parent.append(chat);
  const previousRect = parent.getBoundingClientRect;
  parent.getBoundingClientRect = () => new DOMRect(0, 0, 1000, 800);
  chat.getBoundingClientRect = () => new DOMRect(0, 0, chatWidth, 800);
  const previousStyle = globalThis.getComputedStyle;
  globalThis.getComputedStyle = el => ({ display: el === chat && displayed ? "flex" : "none" });
  Object.defineProperty(window.HTMLElement.prototype, "offsetParent", { configurable: true, get() { return parent; } });
  try {
    await withPip(async ({ host }) => {
      assert.equal(useWebTabPip.getState().rect, null);
      const pip = host.querySelector('[data-pip="true"]');
      displayed = true;
      await act(async () => { for (const ro of [...resizeObservers]) ro.callback(); });
      assert.ok(parseFloat(pip.style.left) + parseFloat(pip.style.width) <= chatWidth);
      chatWidth = 350;
      await act(async () => { for (const ro of [...resizeObservers]) ro.callback(); });
      assert.ok(parseFloat(pip.style.left) + parseFloat(pip.style.width) <= chatWidth);
    });
  } finally {
    delete window.HTMLElement.prototype.offsetParent;
    globalThis.getComputedStyle = previousStyle;
    parent.getBoundingClientRect = previousRect;
    chat.remove();
    legacy.remove();
  }
});

test("window release ends a drag when the native page consumes pointerup", async () => {
  const previousEvent = globalThis.Event;
  globalThis.Event = window.Event;
  const pointer = (type, x, y) => {
    const event = new window.Event(type, { bubbles: true });
    Object.assign(event, { button: 0, buttons: 1, pointerId: 1, clientX: x, clientY: y });
    return event;
  };
  try {
    await withPip(async ({ host }) => {
      const pip = host.querySelector('[data-pip="true"]');
      const chrome = pip.firstElementChild;
      await act(async () => {
        chrome.dispatchEvent(pointer("pointerdown", 900, 100));
        chrome.dispatchEvent(pointer("pointermove", 800, 160));
        window.dispatchEvent(new window.Event("mouseup"));
      });
      const moved = useWebTabPip.getState().rect;
      assert.ok(moved, "release must persist the moved rectangle");
      const handle = pip.querySelector('[data-pip-resize="se"]');
      await act(async () => {
        handle.dispatchEvent(pointer("pointerdown", 800, 400));
        handle.dispatchEvent(pointer("pointermove", 750, 370));
        window.dispatchEvent(new window.Event("mouseup"));
      });
      assert.ok(useWebTabPip.getState().rect.width < moved.width, "next resize must not be blocked by the old drag");
    });
  } finally { globalThis.Event = previousEvent; }
});
