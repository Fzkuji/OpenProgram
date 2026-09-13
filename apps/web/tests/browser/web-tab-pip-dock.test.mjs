import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".pip-dock-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "dock.mjs");
const boundsCalls = [];
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { WebTabPip } from "./components/center-tabs/web-tab-pip";
    export { WebTabPane } from "./components/center-tabs/web-tab-pane";
    export { useCenterTabs } from "./lib/tabs/center-tabs-store";
    export {
      useWebTabPip,
      pipChatRect,
      pipCoversCenter,
      pipHostMode,
      pipPresentationSize,
      PIP_DEFAULT_WIDTH,
      PIP_DEFAULT_HEIGHT,
      PIP_EXPANDED_HEIGHT,
      PIP_EXPANDED_WIDTH,
      PIP_MIN_WIDTH,
      PIP_MIN_HEIGHT,
      resizePipRect,
      PIP_RESIZE_DIRS,
      getSnapshot,
      setSnapshot,
    } from "./lib/browser/web-tab-pip-store";
    export {
      ingestBrowserResource,
      resetBrowserResources,
      getPreviewPreference,
      selectResourcePreview,
      togglePreviewExpanded,
      hideResourcePreview,
      followCurrentBranch,
    } from "./lib/chat/session-resources";
    export { recordOperationCue, resetBrowserControl, resumeErrorFor } from "./lib/browser/browser-control";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "dock-services", setup(b) {
    b.onResolve({ filter: /desktop-bridge/ }, () => ({ path: "desktop-bridge", namespace: "test-services" }));
    b.onResolve({ filter: /net\/fetch-client/ }, () => ({ path: "fetch-client", namespace: "test-services" }));
    b.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "next-nav", namespace: "test-services" }));
    b.onResolve({ filter: /(?:^|\/|\.)browser-controls$/ }, () => ({ path: "browser-controls", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "browser-controls"
      ? "export function BookmarkBar(){return null} export function BookmarksLibraryButton(){return null} export function BrowserMenu(){return null}"
      : a.path === "fetch-client"
      ? `export async function jsonFetch(url, init) {
            const body = JSON.parse(init.body || "{}");
            globalThis.controlPosts = globalThis.controlPosts || [];
            globalThis.controlPosts.push({ url: String(url), body });
            if (typeof globalThis.controlReply === "function") return globalThis.controlReply({ url, body });
            return { id: "assoc-1", resource_id: "page-1", control_state: "paused", session_id: "a", conversation_session_id: "a", tab_id: "w:https://page.test/1", kind: "web", title: "Resource test 1", target: "https://page.test/1", status: "open", source: "browser", generation: 1, sequence: 3 };
          }`
      : a.path === "next-nav"
      ? "export const useRouter = () => ({ push() {}, replace() {} }); export const usePathname = () => '/chat';"
      : `
        const bounds = globalThis.webTabBoundsCalls;
        const removed = globalThis.webTabBoundsRemoved;
        export function desktopBridge() {
          return {
            webTab: {
              ensure() {},
              navigate() {},
              goBack() {},
              goForward() {},
              reload() {},
              stop() {},
              openExternal() {},
              setPipZoom() {},
              onState() { return () => {}; },
              onFindResult() { return () => {}; },
              onCommand() { return () => {}; },
              stopFind() {},
              setControlOverlay(id, payload) {
                globalThis.controlOverlays = globalThis.controlOverlays || [];
                globalThis.controlOverlays.push({ id, payload });
              },
              onControlOverlayEvent() { return () => {}; },
              capture: async (id) => (
                typeof globalThis.webTabCapture === "function"
                  ? globalThis.webTabCapture(id)
                  : null
              ),
            },
            openExternal() {},
          };
        }
        export function installDesktopMenuHandlers() {}
        export function destroyStaleWebViews() {}
        export function ensureWebView() {}
        export function registerVisibleWebTabBounds(_bridge, id, next) {
          bounds.push({ id, ...next });
        }
        export function removeVisibleWebTabBounds() { removed.count += 1; }
        export function setWebTabReady() {}
      ` }));
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
let layoutReads = 0;
window.getComputedStyle = (el) => {
  layoutReads += 1;
  return { display: el?.className?.includes?.("center-pane-chat") ? "flex" : "block" };
};
Object.defineProperty(window.HTMLElement.prototype, "offsetParent", {
  configurable: true,
  get() { return this.parentElement; },
});
const resizeObservers = [];
window.ResizeObserver = class {
  constructor(cb) { this.cb = cb; resizeObservers.push(this); }
  observe() {}
  disconnect() {}
  unobserve() {}
};
window.MutationObserver = class {
  observe() {}
  disconnect() {}
};
globalThis.ResizeObserver = window.ResizeObserver;
globalThis.MutationObserver = window.MutationObserver;
function flushObservers() {
  for (const observer of resizeObservers) observer.cb?.();
}
const rafQueue = new Map();
let rafSeq = 0;
window.requestAnimationFrame = (fn) => {
  const id = ++rafSeq;
  rafQueue.set(id, fn);
  return id;
};
window.cancelAnimationFrame = (id) => { rafQueue.delete(id); };
function flushRaf() {
  const fns = [...rafQueue.values()];
  rafQueue.clear();
  for (const fn of fns) fn(0);
}
const capturedPointers = new Map();
window.HTMLElement.prototype.hasPointerCapture = function hasPointerCapture(id) {
  return capturedPointers.get(id) === this;
};
window.HTMLElement.prototype.setPointerCapture = function setPointerCapture(id) {
  capturedPointers.set(id, this);
};
window.HTMLElement.prototype.releasePointerCapture = function releasePointerCapture(id) {
  if (capturedPointers.get(id) !== this) return;
  capturedPointers.delete(id);
  const event = new window.Event("lostpointercapture", { bubbles: true });
  Object.defineProperty(event, "pointerId", { value: id });
  this.dispatchEvent(event);
};
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.HTMLElement.prototype.scrollIntoView = () => {};
if (!globalThis.DOMRect) {
  globalThis.DOMRect = class DOMRect {
    constructor(x = 0, y = 0, width = 0, height = 0) {
      this.x = x; this.y = y; this.width = width; this.height = height;
      this.top = y; this.left = x; this.right = x + width; this.bottom = y + height;
    }
  };
}
globalThis.PointerEvent = window.PointerEvent || window.MouseEvent;
globalThis.webTabBoundsCalls = boundsCalls;
globalThis.webTabBoundsRemoved = { count: 0 };

function box(left, top, width, height) {
  return { left, top, right: left + width, bottom: top + height, width, height, x: left, y: top };
}

let stageWidth = 1000;
function stageTrack(el) {
  const stage = el.hasAttribute("data-pip-dock")
    ? el
    : el.parentElement?.hasAttribute("data-pip-dock")
      ? el.parentElement
      : null;
  if (!stage) return null;
  const width = Number.parseFloat(stage.style.getPropertyValue("--web-pip-dock-width")) || 360;
  const height = Number.parseFloat(stage.style.getPropertyValue("--web-pip-dock-height")) || 220;
  return { stage, edge: stage.getAttribute("data-pip-dock"), width, height };
}
HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  layoutReads += 1;
  const track = stageTrack(this);
  if (this.getAttribute("data-web-pip-dock") && track) {
    return track.edge === "bottom"
      ? box(0, 700 - track.height, stageWidth, track.height)
      : box(stageWidth - track.width, 80, track.width, 520);
  }
  if (track && this.parentElement === track.stage && !this.getAttribute("data-web-pip-dock")) {
    return track.edge === "bottom"
      ? box(0, 80, stageWidth, Math.max(160, 520 - track.height))
      : box(0, 80, Math.max(0, stageWidth - track.width), 520);
  }
  if (this.hasAttribute("data-pip-dock")) return box(0, 80, stageWidth, 520);
  if (this.getAttribute("data-pip") === "true") {
    return box(stageWidth - 372, 78, 360, 280);
  }
  return box(0, 0, stageWidth, 700);
};

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  WebTabPip, WebTabPane, useCenterTabs, useWebTabPip,
  ingestBrowserResource, resetBrowserResources, getPreviewPreference, selectResourcePreview,
  togglePreviewExpanded, hideResourcePreview, followCurrentBranch, getSnapshot, setSnapshot,
  pipChatRect, pipCoversCenter, pipHostMode, pipPresentationSize,
  PIP_DEFAULT_WIDTH, PIP_DEFAULT_HEIGHT, PIP_EXPANDED_HEIGHT, PIP_EXPANDED_WIDTH, PIP_MIN_WIDTH,
  PIP_MIN_HEIGHT, resizePipRect, PIP_RESIZE_DIRS,
  recordOperationCue, resetBrowserControl, resumeErrorFor,
} = await import(pathToFileURL(bundle));

test("expand after a stored float rect keeps the collapsed rect", () => {
  const stored = { x: 48, y: 96, width: 400, height: 250 };
  assert.deepEqual(pipPresentationSize(stored, false), { width: 400, height: 250 });
  assert.deepEqual(pipPresentationSize(stored, true), {
    width: PIP_EXPANDED_WIDTH,
    height: PIP_EXPANDED_HEIGHT,
  });
  const chat = pipChatRect(stored, true, { x: 0, y: 0, width: 1100, height: 800 });
  assert.equal(chat.width, PIP_EXPANDED_WIDTH);
  assert.equal(chat.height, PIP_EXPANDED_HEIGHT);
  assert.equal(stored.width, 400);
  assert.equal(PIP_DEFAULT_WIDTH, 300);
  assert.equal(PIP_DEFAULT_HEIGHT, 198.75);
  assert.equal(PIP_EXPANDED_WIDTH, 720);
  assert.equal(PIP_EXPANDED_HEIGHT, 435);
  assert.equal(PIP_MIN_WIDTH, 240);
});

function Shell() {
  const activeId = useCenterTabs((s) => s.activeId);
  const tabs = useCenterTabs((s) => s.tabs);
  const page = tabs.find((tab) => tab.kind === "web");
  return createElement(
    "div",
    { className: "center-body", style: { position: "relative" } },
    page && activeId === page.id
      ? createElement(WebTabPane, { tabId: page.id, url: page.url })
      : null,
    createElement(WebTabPip),
  );
}

function labeledButton(host, label) {
  return [...host.querySelectorAll("button")].find(button =>
    button.getAttribute("aria-label") === label
    || button.getAttribute("title") === label
    || button.textContent === label);
}

function pipChromeButtons(host) {
  const pip = host.querySelector("[data-pip='true']");
  return [...(pip?.firstElementChild?.querySelectorAll("button") || [])];
}

function chromeButton(host, label) {
  return pipChromeButtons(host).find(button =>
    button.getAttribute("aria-label") === label
    || button.getAttribute("title") === label
    || button.textContent === label);
}

function installNativeMenu() {
  const popups = [];
  const closed = [];
  const resolvers = [];
  window.openprogramDesktop = {
    contextMenu: {
      popup(request) {
        popups.push(request);
        return new Promise(resolve => { resolvers.push(resolve); });
      },
      close(id) { closed.push(id); },
    },
  };
  return { popups, closed, resolvers };
}

function clickButton(button, { detail = 0, clientX = 12, clientY = 34 } = {}) {
  const event = new window.Event("click", { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    detail: { value: detail },
    clientX: { value: clientX },
    clientY: { value: clientY },
  });
  button.dispatchEvent(event);
}

function dispatchPointer(target, type, { pointerId = 1, clientX = 0, clientY = 0, button = 0 } = {}) {
  const event = new window.Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    clientX: { value: clientX },
    clientY: { value: clientY },
    button: { value: button },
    buttons: { value: type === "pointerup" || type === "pointercancel" ? 0 : 1 },
  });
  target.dispatchEvent(event);
}

async function withShell(run, { capture } = {}) {
  resetBrowserResources();
  resetBrowserControl();
  globalThis.controlPosts = [];
  globalThis.controlReply = undefined;
  boundsCalls.length = 0;
  stageWidth = 1000;
  layoutReads = 0;
  rafQueue.clear();
  capturedPointers.clear();
  globalThis.webTabCapture = capture;
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const page = {
    id: "w:https://page.test/1",
    kind: "web",
    url: "https://page.test/1",
    title: "Resource test 1",
    agentOpened: true,
    agentSessionId: "a",
  };
  useCenterTabs.setState({
    tabs: [session, page],
    activeId: session.id,
    groups: [],
    splitWebTabId: null,
  });
  ingestBrowserResource({
    id: "assoc-1",
    resource_id: "page-1",
    session_id: "a",
    conversation_session_id: "a",
    tab_id: page.id,
    kind: "web",
    title: "Resource test 1",
    target: page.url,
    status: "open",
    source: "browser",
    control_state: "idle",
    generation: 1,
    sequence: 1,
  }, "a");
  selectResourcePreview("a", null, "assoc-1");
  useWebTabPip.getState().show(page.id, session.id);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(Shell)));
    await act(async () => { flushObservers(); });
    await run({ host, page, session });
  } finally {
    await act(async () => root.unmount());
    host.remove();
    useWebTabPip.getState().end();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
    resetBrowserControl();
    delete window.openprogramDesktop;
    globalThis.webTabCapture = undefined;
  }
}

test("chat PiP chrome is one row with icon actions and no second toolbar", async () => {
  await withShell(async ({ host }) => {
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(pip);
    assert.equal(pip.getAttribute("data-pip-host"), "chat");
    const handles = [...pip.querySelectorAll("[data-pip-resize]")];
    assert.equal(handles.length, 8);
    assert.deepEqual(handles.map((handle) => handle.getAttribute("data-pip-resize")), [...PIP_RESIZE_DIRS]);
    assert.equal(handles.every((handle) => handle.parentElement === pip), true);
    assert.equal(pip.firstElementChild.contains(handles[0]), false);
    assert.equal(pip.children.length, 10);
    const openPage = chromeButton(host, "Open page");
    const pin = chromeButton(host, "Unpin preview");
    const takeover = chromeButton(host, "Pause Agent to use page") || chromeButton(host, "Retry pause");
    const more = labeledButton(host, "More");
    const expand = labeledButton(host, "Expand");
    const hide = labeledButton(host, "Hide");
    assert.ok(openPage && pin && more && expand && hide);
    assert.equal(takeover, undefined);
    assert.equal(labeledButton(host, "Use in webpage"), undefined);
    assert.equal(labeledButton(host, "Follow current branch"), undefined);
    assert.equal(labeledButton(host, "Follow"), undefined);
    assert.equal(labeledButton(host, "Show actions"), undefined);
    const chrome = pip.firstElementChild;
    const actions = chrome?.querySelector("div");
    assert.equal(chrome.contains(openPage), true);
    assert.equal(chrome.contains(pin), true);
    assert.equal(chrome.contains(expand), true);
    assert.equal(chrome.contains(more), true);
    assert.equal(chrome.contains(hide), true);
    assert.equal(actions.contains(openPage) && actions.contains(hide), true);
    const title = pip.querySelector("span");
    const status = pip.querySelector("small");
    assert.equal(title?.textContent, "Resource test 1");
    assert.equal(status?.textContent, "Ready to use · Fixed preview");
    assert.equal(title.title.includes("Fixed preview"), true);
    assert.equal(pin.getAttribute("aria-pressed"), "true");
    assert.equal(pin.getAttribute("title"), "Return to automatic display of the page the Agent is operating");
    assert.equal(pipChromeButtons(host).length, 5);
  });
});

test("Pin toggle stays on the chrome and switches auto and fixed preview", async () => {
  await withShell(async ({ host, session }) => {
    const unpin = chromeButton(host, "Unpin preview");
    assert.ok(unpin);
    assert.equal(unpin.getAttribute("aria-pressed"), "true");
    await act(async () => {
      clickButton(unpin);
    });
    assert.equal(getPreviewPreference("a", null).mode, "follow");
    const pin = chromeButton(host, "Pin preview");
    assert.ok(pin);
    assert.equal(pin.getAttribute("aria-pressed"), "false");
    assert.equal(chromeButton(host, "Unpin preview"), undefined);
    assert.equal(host.querySelector("small")?.textContent, "Ready to use · Auto preview");
    assert.equal(pipChromeButtons(host).length, 5);
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.deepEqual(globalThis.controlPosts, []);
    await act(async () => {
      clickButton(pin);
    });
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-1");
    assert.equal(chromeButton(host, "Unpin preview").getAttribute("aria-pressed"), "true");
    assert.equal(useWebTabPip.getState().tabId, "w:https://page.test/1");
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.deepEqual(globalThis.controlPosts, []);
  });
});

test("activating the page hides the chat preview and gives the native Page the full area", async () => {
  await withShell(async ({ host, page, session }) => {
    const prefBefore = { ...getPreviewPreference("a", null) };
    const floatRect = { x: 40, y: 90, width: 400, height: 250 };
    useWebTabPip.getState().setRect(floatRect);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => { flushObservers(); });
    assert.equal(pipHostMode(page.id, session.id, useCenterTabs.getState()), null);
    assert.equal(pipCoversCenter(page.id, session.id, useCenterTabs.getState()), false);
    assert.equal(host.querySelector("[data-pip='true']"), null);
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    assert.equal(host.querySelector("[data-pip-dock]"), null);
    const latest = boundsCalls.at(-1);
    assert.equal(latest.id, page.id);
    assert.equal(latest.width, stageWidth);
    assert.equal(useWebTabPip.getState().tabId, page.id);
    assert.equal(useWebTabPip.getState().ownerTabId, session.id);
    assert.equal(useWebTabPip.getState().rect.x, floatRect.x);
    assert.deepEqual(getPreviewPreference("a", null), prefBefore);
  });
});

test("owner chat keeps the preview when the same Page is also visible in a split or group", async () => {
  await withShell(async ({ host, page, session }) => {
    setSnapshot(page.id, "data:image/png,same-page");
    await act(async () => {
      useCenterTabs.setState({
        groups: [{
          id: "g1",
          memberIds: [session.id, page.id],
          visibleIds: [session.id, page.id],
          focusedId: session.id,
        }],
        splitWebTabId: page.id,
        activeId: session.id,
      });
    });
    assert.equal(pipCoversCenter(page.id, session.id, useCenterTabs.getState()), true);
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(pip);
    assert.equal(pip.getAttribute("data-pip-host"), "chat");
    assert.equal(useWebTabPip.getState().tabId, page.id);
    assert.equal(useWebTabPip.getState().ownerTabId, session.id);
    const shot = pip.querySelector("img");
    assert.ok((shot?.getAttribute("src") || shot?.src || "").includes("same-page"));
    assert.equal(useCenterTabs.getState().tabs.filter(tab => tab.id === page.id).length, 1);
    assert.equal(useCenterTabs.getState().tabs.find(tab => tab.id === page.id)?.url, page.url);
  });
});

test("returning to chat restores the preview unless Hide was used", async () => {
  await withShell(async ({ host, page, session }) => {
    const floatRect = { x: 52, y: 110, width: 380, height: 240 };
    useWebTabPip.getState().setRect(floatRect);
    setSnapshot(page.id, "data:image/png,keep-frame");
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => { flushObservers(); });
    assert.equal(host.querySelector("[data-pip='true']"), null);
    const opened = boundsCalls.at(-1);
    assert.equal(opened.width, stageWidth);
    await act(async () => {
      useCenterTabs.getState().setActive(session.id);
    });
    const chatPip = host.querySelector("[data-pip='true']");
    assert.equal(chatPip?.getAttribute("data-pip-host"), "chat");
    assert.equal(useWebTabPip.getState().rect.x, floatRect.x);
    const restored = chatPip?.querySelector("img");
    assert.ok((restored?.getAttribute("src") || restored?.src || "").includes("keep-frame"));

    await act(async () => {
      hideResourcePreview("a", null);
      useWebTabPip.getState().hide();
    });
    assert.equal(host.querySelector("[data-pip='true']"), null);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => {
      useCenterTabs.getState().setActive(session.id);
    });
    assert.equal(host.querySelector("[data-pip='true']"), null);
    assert.equal(useWebTabPip.getState().tabId, null);
    assert.equal(useWebTabPip.getState().backgroundTabId, page.id);
    assert.equal(getPreviewPreference("a", null).hidden, true);
  });
});

test("expand after a stored rect does not write the expanded size into collapse", async () => {
  await withShell(async ({ host, page, session }) => {
    const floatRect = { x: 30, y: 80, width: 410, height: 230 };
    useWebTabPip.getState().setRect(floatRect);
    await act(async () => {
      togglePreviewExpanded("a", null);
    });
    assert.equal(getPreviewPreference("a", null).expanded, true);
    assert.deepEqual(pipPresentationSize(useWebTabPip.getState().rect, true), {
      width: PIP_EXPANDED_WIDTH,
      height: PIP_EXPANDED_HEIGHT,
    });
    assert.equal(useWebTabPip.getState().rect.width, 410);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    assert.equal(useWebTabPip.getState().rect.width, 410);
    await act(async () => {
      useCenterTabs.getState().setActive(session.id);
      togglePreviewExpanded("a", null);
    });
    assert.equal(getPreviewPreference("a", null).expanded, false);
    assert.equal(useWebTabPip.getState().rect.width, 410);
    assert.equal(host.querySelector("[data-pip='true']")?.getAttribute("data-pip-host"), "chat");
  });
});

test("PiP More native history is a reachable submenu, not a disabled parent", async () => {
  const menu = installNativeMenu();
  await withShell(async ({ host }) => {
    await act(async () => {
      recordOperationCue({
        resourceId: "page-1",
        generation: 1,
        operation: {
          id: "op-1",
          action: "click",
          phase: "acknowledged",
          frame_id: "frame-1",
          geometry_revision: 3,
          point: { x: 10, y: 20, width: 100, height: 80 },
        },
      });
    });
    const more = labeledButton(host, "More");
    assert.ok(more);
    await act(async () => clickButton(more, { detail: 1, clientX: 12, clientY: 34 }));
    assert.equal(menu.popups.length, 1);
    assert.equal(menu.popups[0].items.some(item => item.id === "follow" || item.label === "Follow"), false);
    assert.equal(menu.popups[0].items.some(item => item.id === "pin" || /Pin preview|Unpin preview/.test(item.label || "")), false);
    const history = menu.popups[0].items.find(item => item.id === "history");
    assert.ok(history);
    assert.equal(history.disabled, undefined);
    assert.equal(history.label, "Operation history");
    assert.deepEqual(history.children, [
      { id: "op-1", label: "click · acknowledged", disabled: true },
    ]);
    assert.equal(document.querySelector("[data-native-view-occluder]"), null);
    assert.equal(document.querySelector('[role="menu"]'), null);
    assert.equal(host.querySelector("[data-pip='true']")?.getAttribute("data-pip-host"), "chat");
  });
  assert.equal(menu.closed.length, 1);
});

function hiddenPage(index, title = "127.0.0.1") {
  const url = `http://127.0.0.1/${index}`;
  return {
    id: `w:${url}`,
    kind: "web",
    url,
    title,
    agentOpened: true,
    agentSessionId: "a",
  };
}

function browserAssoc({ id, tabId, title, sequence = 1, target }) {
  return {
    id,
    resource_id: `page-${id}`,
    session_id: "a",
    conversation_session_id: "a",
    execution_id: "exec-a",
    branch_id: "br-a",
    branch_name: "Research",
    agent_name: "Research Agent",
    tab_id: tabId,
    window_id: "main",
    kind: "web",
    title,
    target: target || `http://127.0.0.1/${id}`,
    status: "open",
    source: "browser",
    control_state: "idle",
    generation: 1,
    sequence,
  };
}

function renderedPipTitle(host) {
  return host.querySelector("[data-pip='true'] span")?.textContent;
}

test("floating PiP shows the current matching resource title for never-opened hidden pages", async () => {
  resetBrowserResources();
  boundsCalls.length = 0;
  const session = { id: "s:a", kind: "session", sessionId: "a", title: "Chat A" };
  const first = hiddenPage(1);
  const second = hiddenPage(2);
  const unattributed = hiddenPage("none");
  const untitled = hiddenPage("url-only", "");
  useCenterTabs.setState({
    tabs: [session, first, second, unattributed, untitled],
    activeId: session.id,
    groups: [],
    splitWebTabId: null,
  });
  ingestBrowserResource(browserAssoc({
    id: "assoc-stale",
    tabId: "w:http://other.test/stale",
    title: "Stale other page",
    target: "http://other.test/stale",
  }), "a");
  ingestBrowserResource(browserAssoc({
    id: "assoc-1", tabId: first.id, title: "Resource test 1", target: first.url,
  }), "a");
  ingestBrowserResource(browserAssoc({
    id: "assoc-2", tabId: second.id, title: "Resource test 2", target: second.url,
  }), "a");
  selectResourcePreview("a", null, "assoc-stale");
  useWebTabPip.getState().show(first.id, session.id);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(Shell)));
    await act(async () => { flushObservers(); });
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    assert.equal(host.querySelector("[data-pip='true']")?.getAttribute("data-pip-host"), "chat");
    assert.equal(renderedPipTitle(host), "Resource test 1");

    await act(async () => {
      ingestBrowserResource(browserAssoc({
        id: "assoc-1", tabId: first.id, title: "Resource test 1 renamed",
        target: first.url, sequence: 2,
      }), "a");
    });
    assert.equal(renderedPipTitle(host), "Resource test 1 renamed");

    await act(async () => {
      selectResourcePreview("a", null, "assoc-2");
      useWebTabPip.getState().show(second.id, session.id);
    });
    assert.equal(renderedPipTitle(host), "Resource test 2");

    await act(async () => {
      selectResourcePreview("a", null, "assoc-stale");
    });
    assert.equal(renderedPipTitle(host), "Resource test 2");

    await act(async () => {
      useWebTabPip.getState().show(unattributed.id, session.id);
    });
    assert.equal(renderedPipTitle(host), "127.0.0.1");

    await act(async () => {
      useWebTabPip.getState().show(untitled.id, session.id);
    });
    assert.equal(renderedPipTitle(host), untitled.url);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    useWebTabPip.getState().end();
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
    resetBrowserResources();
  }
});

test("PiP drag caches bounds, previews with transform, and persists only on release", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 40, y: 80, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    assert.ok(pip && chrome);
    dispatchPointer(chrome, "pointerdown", { clientX: 100, clientY: 90 });
    const readsAfterDown = layoutReads;
    const rectAfterDown = { ...useWebTabPip.getState().rect };
    dispatchPointer(chrome, "pointermove", { clientX: 160, clientY: 130 });
    dispatchPointer(chrome, "pointermove", { clientX: 180, clientY: 150 });
    dispatchPointer(chrome, "pointermove", { clientX: 140, clientY: 110 });
    assert.equal(layoutReads, readsAfterDown);
    assert.deepEqual(useWebTabPip.getState().rect, rectAfterDown);
    flushRaf();
    assert.match(pip.style.transform, /translate\(/);
    await act(async () => {
      dispatchPointer(chrome, "pointerup", { clientX: 140, clientY: 110 });
    });
    assert.equal(pip.style.transform, "");
    assert.deepEqual(useWebTabPip.getState().rect, {
      x: origin.x + 40,
      y: origin.y + 20,
      width: origin.width,
      height: origin.height,
    });
  });
});

test("PiP pointer cancel and lost capture clear transform and persist the latest rect", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 48, y: 96, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    dispatchPointer(chrome, "pointerdown", { clientX: 20, clientY: 20 });
    dispatchPointer(chrome, "pointermove", { clientX: 80, clientY: 50 });
    flushRaf();
    assert.match(pip.style.transform, /translate\(/);
    await act(async () => {
      dispatchPointer(chrome, "pointercancel", { clientX: 80, clientY: 50 });
    });
    assert.equal(pip.style.transform, "");
    assert.equal(pip.className.includes("webPipDragging"), false);
    assert.deepEqual(useWebTabPip.getState().rect, {
      x: origin.x + 60,
      y: origin.y + 30,
      width: origin.width,
      height: origin.height,
    });

    await act(async () => { useWebTabPip.getState().setRect(origin); });
    dispatchPointer(chrome, "pointerdown", { pointerId: 7, clientX: 10, clientY: 10 });
    dispatchPointer(chrome, "pointermove", { pointerId: 7, clientX: 30, clientY: 40 });
    await act(async () => {
      chrome.releasePointerCapture(7);
    });
    assert.equal(pip.style.transform, "");
    assert.deepEqual(useWebTabPip.getState().rect, {
      x: origin.x + 20,
      y: origin.y + 30,
      width: origin.width,
      height: origin.height,
    });
  });
});

test("PiP drag suspends capture frames and resumes after release", async () => {
  const pending = [];
  const captures = [];
  await withShell(async ({ host }) => {
    await Promise.resolve();
    const started = captures.length;
    assert.ok(started >= 1);
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    const img = pip.querySelector("img");
    dispatchPointer(chrome, "pointerdown", { clientX: 12, clientY: 12 });
    while (pending.length) pending.shift()?.("data:image/png,during-drag");
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal((img.getAttribute("src") || img.src || "").includes("during-drag"), false);
    assert.equal(captures.length, started);
    await act(async () => {
      dispatchPointer(chrome, "pointerup", { clientX: 12, clientY: 12 });
      await Promise.resolve();
    });
    assert.ok(captures.length > started);
    while (pending.length) pending.shift()?.("data:image/png,after-drag");
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal((img.getAttribute("src") || img.src || "").includes("after-drag"), true);
  }, {
    capture: (id) => {
      captures.push(id);
      return new Promise((resolve) => pending.push(resolve));
    },
  });
});

test("switching the PiP resource during drag discards the half-drag and releases capture", async () => {
  const pending = [];
  const captures = [];
  await withShell(async ({ host, page, session }) => {
    const origin = { x: 40, y: 80, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    await Promise.resolve();
    const started = captures.length;
    assert.ok(started >= 1);
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    dispatchPointer(chrome, "pointerdown", { clientX: 8, clientY: 8 });
    dispatchPointer(chrome, "pointermove", { clientX: 48, clientY: 28 });
    flushRaf();
    assert.match(pip.style.transform, /translate\(/);
    assert.equal(capturedPointers.size, 1);
    const next = {
      id: "w:https://page.test/2",
      kind: "web",
      url: "https://page.test/2",
      title: "Resource test 2",
      agentOpened: true,
      agentSessionId: "a",
    };
    await act(async () => {
      useCenterTabs.setState({
        tabs: [session, page, next],
        activeId: session.id,
        groups: [],
        splitWebTabId: null,
      });
      useWebTabPip.getState().show(next.id, session.id);
    });
    const after = host.querySelector("[data-pip='true']");
    assert.ok(after);
    assert.equal(after.style.transform, "");
    assert.equal(capturedPointers.size, 0);
    assert.deepEqual(useWebTabPip.getState().rect, origin);
    await Promise.resolve();
    assert.ok(captures.length > started);
    pending.at(-1)?.("data:image/png,switched");
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const img = after.querySelector("img");
    assert.equal((img.getAttribute("src") || img.src || "").includes("switched"), true);
  }, {
    capture: (id) => {
      captures.push(id);
      return new Promise((resolve) => pending.push(resolve));
    },
  });
});

test("leaving chat during drag discards the half-drag and releases capture", async () => {
  await withShell(async ({ host, page }) => {
    const origin = { x: 52, y: 90, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    dispatchPointer(chrome, "pointerdown", { clientX: 10, clientY: 10 });
    dispatchPointer(chrome, "pointermove", { clientX: 70, clientY: 40 });
    flushRaf();
    assert.equal(capturedPointers.size, 1);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    assert.equal(host.querySelector("[data-pip='true']"), null);
    assert.equal(capturedPointers.size, 0);
    assert.deepEqual(useWebTabPip.getState().rect, origin);
  });
});

test("resize then resource switch restores origin inline geometry without a store write", async () => {
  await withShell(async ({ host, page, session }) => {
    const origin = { x: 40, y: 80, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    const pip = host.querySelector("[data-pip='true']");
    const handle = pip.querySelector("[data-pip-resize='se']");
    assert.ok(pip && handle);
    dispatchPointer(handle, "pointerdown", { clientX: 440, clientY: 330 });
    dispatchPointer(handle, "pointermove", { clientX: 520, clientY: 390 });
    flushRaf();
    assert.equal(capturedPointers.size, 1);
    const next = {
      id: "w:https://page.test/2",
      kind: "web",
      url: "https://page.test/2",
      title: "Resource test 2",
      agentOpened: true,
      agentSessionId: "a",
    };
    await act(async () => {
      useCenterTabs.setState({
        tabs: [session, page, next],
        activeId: session.id,
        groups: [],
        splitWebTabId: null,
      });
      useWebTabPip.getState().show(next.id, session.id);
    });
    const after = host.querySelector("[data-pip='true']");
    assert.ok(after);
    assert.equal(after.style.transform, "");
    assert.equal(after.style.left, `${origin.x}px`);
    assert.equal(after.style.top, `${origin.y}px`);
    assert.equal(after.style.width, `${origin.width}px`);
    assert.equal(after.style.height, `${origin.height}px`);
    assert.equal(capturedPointers.size, 0);
    assert.deepEqual(useWebTabPip.getState().rect, origin);
    assert.equal(useWebTabPip.getState().expandedSize, null);
  });
});

test("eight root handles keep content ratio and the opposite anchor", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 200, y: 120, width: 400, height: 255 };
    const bounds = { x: 0, y: 0, width: 1000, height: 700 };
    const pip = host.querySelector("[data-pip='true']");
    const handles = [...pip.querySelectorAll("[data-pip-resize]")];
    assert.equal(handles.length, 8);
    assert.equal(handles.every((handle) => handle.parentElement === pip), true);
    assert.equal(pip.querySelector("[data-pip-resize='se']").parentElement, pip);
    const cases = [
      { dir: "se", dx: 80, dy: 45 },
      { dir: "nw", dx: -48, dy: -27 },
      { dir: "e", dx: 64, dy: 20 },
      { dir: "w", dx: -56, dy: 12 },
      { dir: "s", dx: 20, dy: 36 },
      { dir: "n", dx: 16, dy: -40 },
    ];
    for (const { dir, dx, dy } of cases) {
      await act(async () => { useWebTabPip.getState().setRect(origin); });
      const handle = pip.querySelector(`[data-pip-resize='${dir}']`);
      dispatchPointer(handle, "pointerdown", { clientX: 100, clientY: 100 });
      dispatchPointer(handle, "pointermove", { clientX: 100 + dx, clientY: 100 + dy });
      flushRaf();
      const expected = resizePipRect(origin, dx, dy, bounds, PIP_MIN_WIDTH, PIP_MIN_HEIGHT, dir);
      assert.equal(pip.style.left, `${expected.x}px`, dir);
      assert.equal(pip.style.top, `${expected.y}px`, dir);
      assert.equal(pip.style.width, `${expected.width}px`, dir);
      assert.equal(pip.style.height, `${expected.height}px`, dir);
      await act(async () => {
        dispatchPointer(handle, "pointerup", { clientX: 100 + dx, clientY: 100 + dy });
      });
      assert.deepEqual(useWebTabPip.getState().rect, expected);
    }
  });
});

test("expanded resize persists size without writing collapsed rect", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 48, y: 90, width: 400, height: 255 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    await act(async () => { togglePreviewExpanded("a", null); });
    const pip = host.querySelector("[data-pip='true']");
    const handle = pip.querySelector("[data-pip-resize='se']");
    dispatchPointer(handle, "pointerdown", { clientX: 200, clientY: 200 });
    dispatchPointer(handle, "pointermove", { clientX: 280, clientY: 245 });
    flushRaf();
    await act(async () => {
      dispatchPointer(handle, "pointerup", { clientX: 280, clientY: 245 });
    });
    const expected = resizePipRect(
      { ...origin, width: PIP_EXPANDED_WIDTH, height: PIP_EXPANDED_HEIGHT },
      80,
      45,
      { x: 0, y: 0, width: 1000, height: 700 },
      PIP_MIN_WIDTH,
      PIP_MIN_HEIGHT,
      "se",
    );
    assert.deepEqual(useWebTabPip.getState().rect, {
      x: expected.x,
      y: expected.y,
      width: origin.width,
      height: origin.height,
    });
    assert.deepEqual(useWebTabPip.getState().expandedSize, { width: expected.width, height: expected.height });
    assert.equal(pip.style.width, `${expected.width}px`);
    assert.equal(pip.style.height, `${expected.height}px`);
  });
});

test("expanded NW resize persists x/y and keeps collapsed size through collapse", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 150, y: 150, width: 400, height: 255 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    await act(async () => { togglePreviewExpanded("a", null); });
    const pip = host.querySelector("[data-pip='true']");
    const handle = pip.querySelector("[data-pip-resize='nw']");
    dispatchPointer(handle, "pointerdown", { clientX: 160, clientY: 160 });
    dispatchPointer(handle, "pointermove", { clientX: 120, clientY: 130 });
    flushRaf();
    const inline = {
      x: Number.parseFloat(pip.style.left),
      y: Number.parseFloat(pip.style.top),
      width: Number.parseFloat(pip.style.width),
      height: Number.parseFloat(pip.style.height),
    };
    await act(async () => {
      dispatchPointer(handle, "pointerup", { clientX: 120, clientY: 130 });
    });
    const stored = useWebTabPip.getState();
    assert.equal(stored.rect.x, inline.x);
    assert.equal(stored.rect.y, inline.y);
    assert.equal(stored.rect.width, origin.width);
    assert.equal(stored.rect.height, origin.height);
    assert.deepEqual(stored.expandedSize, { width: inline.width, height: inline.height });
    await act(async () => { togglePreviewExpanded("a", null); });
    assert.equal(getPreviewPreference("a", null).expanded, false);
    const collapsed = host.querySelector("[data-pip='true']");
    assert.equal(collapsed.style.left, `${inline.x}px`);
    assert.equal(collapsed.style.top, `${inline.y}px`);
    assert.equal(collapsed.style.width, `${origin.width}px`);
    assert.equal(collapsed.style.height, `${origin.height}px`);
    await act(async () => { togglePreviewExpanded("a", null); });
    const expanded = host.querySelector("[data-pip='true']");
    assert.equal(expanded.style.left, `${inline.x}px`);
    assert.equal(expanded.style.top, `${inline.y}px`);
    assert.equal(expanded.style.width, `${inline.width}px`);
    assert.equal(expanded.style.height, `${inline.height}px`);
    const chrome = expanded.firstElementChild;
    dispatchPointer(chrome, "pointerdown", { clientX: 80, clientY: 80 });
    dispatchPointer(chrome, "pointermove", { clientX: 92, clientY: 88 });
    flushRaf();
    await act(async () => {
      dispatchPointer(chrome, "pointerup", { clientX: 92, clientY: 88 });
    });
    const moved = useWebTabPip.getState().rect;
    assert.equal(moved.x, inline.x + 12);
    assert.equal(moved.y, inline.y + 8);
    assert.equal(moved.width, origin.width);
    assert.equal(moved.height, origin.height);
    assert.deepEqual(useWebTabPip.getState().expandedSize, { width: inline.width, height: inline.height });
  });
});

test("expanded move persists position once and keeps collapsed size through collapse", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 48, y: 90, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    await act(async () => { togglePreviewExpanded("a", null); });
    assert.equal(getPreviewPreference("a", null).expanded, true);
    const expandedBefore = useWebTabPip.getState().expandedSize;
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    dispatchPointer(chrome, "pointerdown", { clientX: 80, clientY: 100 });
    const rectDuring = { ...useWebTabPip.getState().rect };
    dispatchPointer(chrome, "pointermove", { clientX: 140, clientY: 130 });
    dispatchPointer(chrome, "pointermove", { clientX: 128, clientY: 124 });
    assert.deepEqual(useWebTabPip.getState().rect, rectDuring);
    assert.equal(useWebTabPip.getState().expandedSize, expandedBefore);
    flushRaf();
    await act(async () => {
      dispatchPointer(chrome, "pointerup", { clientX: 128, clientY: 124 });
    });
    const moved = useWebTabPip.getState().rect;
    assert.equal(moved.x, origin.x + 48);
    assert.equal(moved.y, origin.y + 24);
    assert.equal(moved.width, origin.width);
    assert.equal(moved.height, origin.height);
    assert.equal(useWebTabPip.getState().expandedSize, expandedBefore);
    await act(async () => { togglePreviewExpanded("a", null); });
    await act(async () => { togglePreviewExpanded("a", null); });
    assert.equal(getPreviewPreference("a", null).expanded, true);
    assert.deepEqual(useWebTabPip.getState().rect, moved);
    await act(async () => { togglePreviewExpanded("a", null); });
    assert.equal(getPreviewPreference("a", null).expanded, false);
    assert.deepEqual(useWebTabPip.getState().rect, moved);
    const collapsed = host.querySelector("[data-pip='true']");
    assert.equal(collapsed.style.left, `${moved.x}px`);
    assert.equal(collapsed.style.top, `${moved.y}px`);
    assert.equal(collapsed.style.width, `${origin.width}px`);
    assert.equal(collapsed.style.height, `${origin.height}px`);
  });
});

test("parent resize during drag does not write the store; release clamps to the current box", async () => {
  await withShell(async ({ host }) => {
    const origin = { x: 40, y: 80, width: 400, height: 250 };
    await act(async () => { useWebTabPip.getState().setRect(origin); });
    const pip = host.querySelector("[data-pip='true']");
    const chrome = pip.firstElementChild;
    dispatchPointer(chrome, "pointerdown", { clientX: 20, clientY: 20 });
    const rectAfterDown = { ...useWebTabPip.getState().rect };
    dispatchPointer(chrome, "pointermove", { clientX: 420, clientY: 20 });
    stageWidth = 280;
    resizeObservers.at(-1)?.cb?.();
    dispatchPointer(chrome, "pointermove", { clientX: 460, clientY: 30 });
    assert.deepEqual(useWebTabPip.getState().rect, rectAfterDown);
    flushRaf();
    await act(async () => {
      dispatchPointer(chrome, "pointerup", { clientX: 460, clientY: 30 });
    });
    const finalRect = useWebTabPip.getState().rect;
    assert.equal(pip.style.transform, "");
    assert.ok(finalRect.x >= 0);
    assert.ok(finalRect.y >= 0);
    assert.ok(finalRect.x + finalRect.width <= 280);
    assert.ok(finalRect.y + finalRect.height <= 700);
  });
});

test("unpin follows the latest Agent page; pin holds the current page", async () => {
  await withShell(async ({ host, page, session }) => {
    const second = {
      id: "w:https://page.test/2",
      kind: "web",
      url: "https://page.test/2",
      title: "Resource test 2",
      agentOpened: true,
      agentSessionId: "a",
    };
    await act(async () => {
      useCenterTabs.setState({
        tabs: [session, page, second],
        activeId: session.id,
        groups: [],
        splitWebTabId: null,
      });
      ingestBrowserResource({
        id: "assoc-2",
        resource_id: "page-2",
        session_id: "a",
        conversation_session_id: "a",
        tab_id: second.id,
        kind: "web",
        title: "Resource test 2",
        target: second.url,
        status: "open",
        source: "browser",
        control_state: "active",
        generation: 1,
        sequence: 4,
        last_operation: { id: "op-b", action: "click", phase: "dispatched" },
      }, "a");
      togglePreviewExpanded("a", null);
    });
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    assert.equal(getPreviewPreference("a", null).expanded, true);
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-1");
    assert.equal(useWebTabPip.getState().tabId, page.id);
    await act(async () => {
      clickButton(chromeButton(host, "Unpin preview"));
    });
    assert.equal(getPreviewPreference("a", null).mode, "follow");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-2");
    assert.equal(getPreviewPreference("a", null).expanded, true);
    assert.equal(useWebTabPip.getState().tabId, second.id);
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.deepEqual(globalThis.controlPosts, []);
    await act(async () => {
      clickButton(chromeButton(host, "Pin preview"));
    });
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-2");
    assert.equal(getPreviewPreference("a", null).expanded, true);
    await act(async () => {
      ingestBrowserResource({
        id: "assoc-1",
        resource_id: "page-1",
        session_id: "a",
        conversation_session_id: "a",
        tab_id: page.id,
        kind: "web",
        title: "Resource test 1",
        target: page.url,
        status: "open",
        source: "browser",
        control_state: "active",
        generation: 1,
        sequence: 5,
        last_operation: { id: "op-a", action: "click", phase: "dispatched" },
      }, "a");
    });
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-2");
    assert.equal(useWebTabPip.getState().tabId, second.id);
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.deepEqual(globalThis.controlPosts, []);
  });
});


test("chat PiP does not provide separate task pause controls", async () => {
  await withShell(async ({ host }) => {
    assert.equal(chromeButton(host, "Pause Agent to use page"), undefined);
    assert.equal(chromeButton(host, "Continue Agent"), undefined);
  });
});
