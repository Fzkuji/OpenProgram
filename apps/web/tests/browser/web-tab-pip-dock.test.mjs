import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = scratchDir(webPath, "pip-dock-test");
const bundle = join(dir, "dock.mjs");
const boundsCalls = [];
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { default as ContextMenuPage } from "./app/menu-overlay/context-menu/page";
    export { WebTabPip } from "./components/center-tabs/pip/web-tab-pip";
    export { WebTabPipSurface } from "./components/center-tabs/pip/web-tab-pip-surface";
    export { WebTabPane } from "./components/center-tabs/browser/web-tab-pane";
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
    } from "./lib/browser/web-tab/web-tab-pip-store";
    export {
      ingestBrowserResource,
      resetBrowserResources,
      getPreviewPreference,
      selectResourcePreview,
      togglePreviewExpanded,
      hideResourcePreview,
      followCurrentBranch,
    } from "./lib/chat/resources/session-resources";
    export { recordOperationCue, resetBrowserControl, resumeErrorFor } from "./lib/browser/control/browser-control";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "dock-services", setup(b) {
    // The preview's page surface, counted each time it renders inside the
    // frame (globalThis.surfaceRenders).
    b.onResolve({ filter: /^\.\/web-tab-pip-surface$/ }, a => a.importer.endsWith("web-tab-pip.tsx")
      ? { path: "counted-surface", namespace: "test-services" } : undefined);
    // The frame's animations: finished at once, or recorded and left
    // running while a test collects them (globalThis.pipAnimations).
    b.onResolve({ filter: /^framer-motion$/ }, a => a.importer.endsWith("web-tab-pip.tsx")
      ? { path: "recorded-motion", namespace: "test-services" } : undefined);
    b.onResolve({ filter: /desktop-bridge/ }, () => ({ path: "desktop-bridge", namespace: "test-services" }));
    b.onResolve({ filter: /net\/fetch-client/ }, () => ({ path: "fetch-client", namespace: "test-services" }));
    b.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "next-nav", namespace: "test-services" }));
    b.onResolve({ filter: /(?:^|\/|\.)(?:browser-controls|bookmark-bar)$/ }, () => ({ path: "browser-controls", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ resolveDir: webPath, contents: a.path === "counted-surface"
      ? `import { WebTabPipSurface as Surface } from "./components/center-tabs/pip/web-tab-pip-surface";
         export function WebTabPipSurface(props) {
           globalThis.surfaceRenders = (globalThis.surfaceRenders || 0) + 1;
           return Surface(props);
         }`
      : a.path === "recorded-motion"
      ? `import { animate as run } from "framer-motion";
         export * from "framer-motion";
         export function animate(...args) {
           if (!(args[0] instanceof HTMLElement)) return run(...args);
           if (!globalThis.pipAnimations) return { finished: Promise.resolve(), stop() {} };
           globalThis.pipAnimations.push(args);
           return { finished: new Promise(() => {}), stop() {} };
         }`
      : a.path === "browser-controls"
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
      ? "export const useRouter = () => ({ push() {}, replace() {} }); export const usePathname = () => '/chat'; export const useSearchParams = () => globalThis.menuParams || new URLSearchParams();"
      : `
        const bounds = globalThis.webTabBoundsCalls;
        const removed = globalThis.webTabBoundsRemoved;
        export function buildTransferPayload() { return {}; }
        export function desktopBridge() {
          return {
            mainMenu: window.openprogramDesktop?.mainMenu,
            webTab: {
              syncVisible: globalThis.livePipTest ? () => {} : undefined,
              ensure() {},
              navigate() {},
              goBack() {},
              goForward() {},
              reload() {},
              stop() {},
              openExternal() {},
              setBounds(id, next) { (globalThis.pipFinalBounds ||= []).push({ id, ...next }); },
              setPipZoom(...args) { (globalThis.pipViewportCalls ||= []).push(args); },
              onState() { return () => {}; },
              onFindResult() { return () => {}; },
              onCommand() { return () => {}; },
              stopFind() {},
              setControlOverlay(id, payload) {
                globalThis.controlOverlays = globalThis.controlOverlays || [];
                globalThis.controlOverlays.push({ id, payload });
              },
              onControlOverlayEvent() { return () => {}; },
              capture: async (id, mode) => (
                typeof globalThis.webTabCapture === "function"
                  ? globalThis.webTabCapture(id, mode)
                  : null
              ),
            },
            openExternal() {},
          };
        }
        export function installDesktopMenuHandlers() {}
        export function destroyStaleWebViews() {}
        export function ensureWebView(_bridge, id) { (globalThis.pipEnsures ||= []).push(id); }
        export function registerVisibleWebTabBounds(_bridge, id, next) {
          bounds.push({ id, ...next });
        }
        export function removeVisibleWebTabBounds() { removed.count += 1; }
        export function setFloatingWebTab() {}
        export function setWebTabReady() {}
      ` }));
  }}],
});

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.CustomEvent = window.CustomEvent;
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
  observe() { this.active = true; }
  disconnect() { this.active = false; }
  unobserve() { this.active = false; }
};
const RealMutationObserver = window.MutationObserver;
window.MutationObserver = class {
  observe() {}
  disconnect() {}
};
globalThis.ResizeObserver = window.ResizeObserver;
globalThis.MutationObserver = window.MutationObserver;
function flushObservers() {
  for (const observer of resizeObservers) if (observer.active) observer.cb?.();
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
let stageShift = null;
// Where everything is drawn from: a move of the whole pane in the window
// (something above it went away) that changes no size and no attribute.
const windowShift = { x: 0, y: 0 };
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
  if (this.getAttribute("data-pip-live") === "native") {
    const pip = this.closest('[data-pip="true"]');
    const translate = /translate\(([-.\d]+)px, ([-.\d]+)px\)/.exec(pip.style.transform || "");
    return box((parseFloat(pip.style.left) || 0) + Number(translate?.[1] || 0) + windowShift.x,
      (parseFloat(pip.style.top) || 0) + Number(translate?.[2] || 0) + 30 + windowShift.y,
      (parseFloat(pip.style.width) || 300), (parseFloat(pip.style.height) || 199) - 30);
  }
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
    return box(stageWidth - 372, 86, 360, 280);
  }
  const rect = box(0, 0, stageWidth, 700);
  // The pane narrows once, after it has been read `reads` times: what a
  // commit that lands another pending update (a sidebar's new width) does
  // between the frame's read of its pane and the page's measurement.
  if (stageShift && this.classList?.contains("center-body") && --stageShift.reads === 0) {
    stageWidth = stageShift.width;
    stageShift = null;
  }
  return rect;
};

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  ContextMenuPage, WebTabPip, WebTabPane, WebTabPipSurface, useCenterTabs, useWebTabPip,
  ingestBrowserResource, resetBrowserResources, getPreviewPreference, selectResourcePreview,
  togglePreviewExpanded, hideResourcePreview, setSnapshot,
  pipChatRect, pipCoversCenter, pipHostMode, pipPresentationSize,
  PIP_DEFAULT_WIDTH, PIP_DEFAULT_HEIGHT, PIP_EXPANDED_HEIGHT, PIP_EXPANDED_WIDTH, PIP_MIN_WIDTH,
  PIP_MIN_HEIGHT, resizePipRect,
  recordOperationCue, resetBrowserControl,
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

function installOverlayMenu() {
  const popups = [], closed = [], resolvers = [];
  const actions = new Set(), closures = new Set();
  window.openprogramDesktop = {
    contextMenu: { popup() { return Promise.resolve(null); }, close() {} },
    mainMenu: {
      open(request) {
        popups.push(request);
        resolvers.push(id => {
          const item = request.items.find(item => item.id.endsWith(":" + id));
          for (const cb of actions) cb(item?.id || id);

        });
      },
      updateItems(items) { popups.at(-1).items = items; },
      close() { closed.push(true); for (const cb of closures) cb(); },
      onAction(cb) { actions.add(cb); return () => actions.delete(cb); },
      onClosed(cb) { closures.add(cb); return () => closures.delete(cb); },
    },
  };
  return { popups, closed, resolvers, actions, closures, dismiss() { for (const cb of closures) cb(); } };
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

test("preview chrome offers a close icon and omits pin and expand controls", async () => {
  await withShell(async ({ host, page, session }) => {
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(pip);
    assert.equal(pip.querySelectorAll("[data-pip-resize]").length, 8);
    assert.deepEqual(pipChromeButtons(host).map(button => button.getAttribute("aria-label")),
      ["Open page", "More", "Close preview"]);
    const hide = chromeButton(host, "Close preview");
    assert.equal(hide.textContent, "");
    assert.ok(hide.querySelector("svg"));
    const tabsBefore = useCenterTabs.getState().tabs;
    await act(async () => clickButton(hide));
    assert.equal(host.querySelector("[data-pip='true']"), null);
    assert.equal(getPreviewPreference("a", null).hidden, true);
    assert.deepEqual(useCenterTabs.getState().tabs, tabsBefore);
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.deepEqual(globalThis.controlPosts, []);
    await act(async () => {
      selectResourcePreview("a", null, "assoc-1");
      useWebTabPip.getState().show(page.id, session.id);
    });
    assert.ok(host.querySelector("[data-pip='true']"));
    assert.equal(getPreviewPreference("a", null).hidden, false);
  });
});

async function toggleFollowMenu(host, menu, expectedChecked) {
  if (chromeButton(host, "More").getAttribute("aria-expanded") !== "true") {
    await act(async () => clickButton(chromeButton(host, "More")));
  }
  const item = menu.popups.at(-1).items.find(item => item.id.endsWith(":follow-page"));
  assert.ok(item);
  assert.equal(item.label, "Automatically show the page the Agent is using");
  assert.equal(item.checked, expectedChecked);
  assert.match(item.description, /When enabled.*When disabled/s);
  assert.equal(menu.popups.at(-1).width, 360);
  await act(async () => menu.resolvers.at(-1)("follow-page"));
  assert.equal(chromeButton(host, "More").getAttribute("aria-expanded"), "true");
}

test("More explains follow mode and toggles it without controlling execution", async () => {
  const menu = installOverlayMenu();
  await withShell(async ({ host, session }) => {
    await toggleFollowMenu(host, menu, false);
    assert.equal(getPreviewPreference("a", null).mode, "follow");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-1");
    await toggleFollowMenu(host, menu, true);
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-1");
    assert.equal(menu.popups.length, 1);
    assert.equal(menu.closed.length, 0);
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

test("focusing another pane of the owner's split keeps the owner's preview", async () => {
  await withShell(async ({ host, page, session }) => {
    const other = { id: "s:b", kind: "session", sessionId: "b", title: "Chat B" };
    await act(async () => {
      useCenterTabs.setState((state) => ({
        tabs: [...state.tabs, other],
        groups: [{
          id: "g1",
          memberIds: [session.id, other.id],
          visibleIds: [session.id, other.id],
          focusedId: other.id,
        }],
        activeId: other.id,
      }));
    });
    assert.equal(pipHostMode(page.id, session.id, useCenterTabs.getState()), "chat");
    assert.ok(host.querySelector("[data-pip='true']"));
    // A window without the owner hides it.
    await act(async () => {
      useCenterTabs.setState({ groups: [], activeId: other.id });
    });
    assert.equal(pipHostMode(page.id, session.id, useCenterTabs.getState()), null);
    assert.equal(host.querySelector("[data-pip='true']"), null);
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

test("PiP More custom history is a reachable submenu, not a disabled parent", async () => {
  const menu = installOverlayMenu();
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
    const history = menu.popups[0].items.find(item => item.id.endsWith(":history"));
    assert.ok(history);
    assert.equal(history.disabled, undefined);
    assert.equal(history.label, "Operation history");
    assert.deepEqual(history.children, [
      { id: history.children[0].id, glyph: "clock-circle", label: "click · acknowledged", disabled: true },
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
      useCenterTabs.getState().updateWebTab(second.id, { url: "http://127.0.0.1/moved", title: "Moved page" });
    });
    // The record still names the old URL; the tab's live title wins.
    assert.equal(renderedPipTitle(host), "Moved page");

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
    const bounds = { x: 22, y: 22, width: 956, height: 656 };
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
      { dir: "se", dx: 2000, dy: 2000 },
      { dir: "nw", dx: -2000, dy: -2000 },
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
      { x: 22, y: 22, width: 956, height: 656 },
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
    assert.ok(finalRect.x >= 22);
    assert.ok(finalRect.y >= 22);
    assert.ok(finalRect.x + finalRect.width <= 258);
    assert.ok(finalRect.y + finalRect.height <= 678);
  });
});

test("follow menu selects latest Agent page and unchecked holds current page", async () => {
  const menu = installOverlayMenu();
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
    await toggleFollowMenu(host, menu, false);
    assert.equal(getPreviewPreference("a", null).mode, "follow");
    assert.equal(getPreviewPreference("a", null).targetId, "assoc-2");
    assert.equal(getPreviewPreference("a", null).expanded, true);
    assert.equal(useWebTabPip.getState().tabId, second.id);
    assert.equal(useCenterTabs.getState().activeId, session.id);
    assert.deepEqual(globalThis.controlPosts, []);
    await toggleFollowMenu(host, menu, true);
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


/** Wait out the surface's relayout pause (PIP_RELAYOUT_MS). */
const relayout = () => new Promise(resolve => setTimeout(resolve, 300));

test("live PiP freezes native geometry through continuous resize and commits once on release", async () => {
  globalThis.livePipTest = true;
  globalThis.pipFinalBounds = [];
  const captures = [];
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host, page }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 40, y: 80, width: 400, height: 250 }));
      flushObservers(); flushRaf();
      // Showing at a new size lays the hidden page out there first.
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      captures.length = 0;
      globalThis.pipFinalBounds = [];
      const before = boundsCalls.length;
      const zooms = globalThis.pipViewportCalls.length;
      const removed = globalThis.webTabBoundsRemoved.count;
      const pip = host.querySelector('[data-pip="true"]');
      const handle = pip.querySelector('[data-pip-resize="se"]');
      await act(async () => dispatchPointer(handle, "pointerdown", { clientX: 400, clientY: 250 }));
      for (let i = 1; i <= 60; i++) {
        dispatchPointer(handle, "pointermove", { clientX: 400 + i, clientY: 250 + i });
        flushRaf(); flushObservers(); flushRaf();
      }
      await act(async () => { await Promise.resolve(); });
      assert.equal(boundsCalls.length, before, "held resize must not resize the native renderer");
      assert.equal(globalThis.pipViewportCalls.length, zooms);
      assert.deepEqual(captures, ["presentation"]);
      assert.ok(globalThis.webTabBoundsRemoved.count > removed);
      assert.ok(host.querySelector('[data-pip-gesture-frame]').src.startsWith("data:image/"));
      await act(async () => { dispatchPointer(handle, "pointerup", { clientX: 460, clientY: 310 }); await relayout(); });
      assert.equal(globalThis.pipFinalBounds.length, 1);
      // The gesture's picture, then the new size's first paint and its
      // paint once the page has had time to lay itself out again.
      assert.deepEqual(captures, ["presentation", "presentation", "presentation"]);
      assert.ok(boundsCalls.length > before, "release restores the live page");
      assert.ok(useCenterTabs.getState().tabs.some(tab => tab.id === page.id));
    }, { capture: async (_id, mode) => { captures.push(mode); return "data:image/png;base64,FRAME"; } });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("the still picture goes once the live page is back, and a re-layout at rest shows no stale picture", async () => {
  globalThis.livePipTest = true;
  globalThis.pipFinalBounds = [];
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 40, y: 80, width: 400, height: 250 }));
      flushObservers(); flushRaf();
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const still = host.querySelector('[data-pip-gesture-frame]');
      assert.equal(still.hidden, true, "nothing held: no picture");
      // A drag of the preview holds the page: its picture stands in.
      const chrome = host.querySelector('[data-pip="true"]').firstElementChild;
      await act(async () => {
        dispatchPointer(chrome, "pointerdown", { clientX: 100, clientY: 90 });
        dispatchPointer(chrome, "pointermove", { clientX: 120, clientY: 90 });
        await relayout();
      });
      assert.equal(still.hidden, false, "held: the picture stands in for the page");
      const shown = boundsCalls.length;
      await act(async () => { dispatchPointer(chrome, "pointerup", { clientX: 120, clientY: 90 }); await relayout(); });
      assert.ok(boundsCalls.length > shown, "released: the live page is back");
      assert.equal(still.hidden, true, "and the picture is gone from under it");
      // At rest, a new size: the page goes down to lay out there.
      const removed = globalThis.webTabBoundsRemoved.count;
      await act(async () => useWebTabPip.getState().setRect({ x: 40, y: 80, width: 460, height: 300 }));
      await act(async () => { flushObservers(); flushRaf(); });
      assert.ok(globalThis.webTabBoundsRemoved.count > removed, "the page is down while it lays out");
      assert.equal(still.hidden, true, "no stale picture, stretched to the new size, shows meanwhile");
      await act(async () => { await relayout(); });
      assert.equal(still.hidden, true);
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("a held picture is drawn as the page is, with no bar of the preview's background beside it", async () => {
  // Drawn as the live page: the page's fixed layout width scaled to the
  // frame's width, from the top, inside a body rounded at the top like the
  // native page (rounded on all corners by the desktop App), so a hold
  // changes no corner. Contained, it left bars of the preview's background
  // whenever a snap or resize changed the frame's shape.
  const css = readFileSync(join(webPath, "components/center-tabs/browser/browser.module.css"), "utf8");
  const rule = css.match(/\.webPipGestureFrame \{([^}]*)\}/)[1];
  assert.match(rule, /width: 100%/);
  assert.match(rule, /height: auto/);
  assert.doesNotMatch(rule, /object-fit|border-radius/);
  const surface = readFileSync(join(webPath, "components/center-tabs/pip/web-tab-pip-surface.tsx"), "utf8");
  assert.match(surface, /data-pip-live="native"/);
  assert.match(surface.match(/<div ref=\{bodyRef\} className="([^"]*)" data-pip-live="native"/)[1], /\boverflow-hidden\b.*\brounded-t-lg\b/);
  // Held, the frame takes the deeper of its two shadows, as raised controls
  // do under the pointer; nothing else about it changes.
  const dragging = css.match(/\.webPipDragging \{([^}]*)\}/)[1];
  // As the frame's own shadow layer: a box-shadow of its own replaced the
  // ring and shadow list, so the shadow faded out and back on every press.
  assert.match(dragging, /--tw-shadow: var\(--popover-shadow-hover\)/);
  assert.doesNotMatch(dragging, /(^|[^-])box-shadow:|will-change/);
  assert.doesNotMatch(dragging, /transition: none/);
  // Hover keeps the resting shadow: the pointer is always over the frame
  // before a drag, so a deeper hover shadow left the drag nothing to add.
  const pipSource = readFileSync(join(webPath, "components/center-tabs/pip/web-tab-pip.tsx"), "utf8");
  const frame = pipSource.match(/export const PIP_FRAME = "([^"]*)"/)[1];
  assert.match(frame, /\bshadow-\(--popover-shadow\)/);
  assert.doesNotMatch(frame, /hover:shadow/);
  globalThis.livePipTest = true;
  globalThis.pipFinalBounds = [];
  const proto = window.HTMLImageElement.prototype;
  const decode = proto.decode;
  const natural = ["naturalWidth", "naturalHeight"].map((key) => [key, Object.getOwnPropertyDescriptor(proto, key)]);
  const getContext = window.HTMLCanvasElement.prototype.getContext;
  proto.decode = async () => {};
  Object.defineProperty(proto, "naturalWidth", { configurable: true, get: () => 600 });
  Object.defineProperty(proto, "naturalHeight", { configurable: true, get: () => 338 });
  // The page's bottom row: a dark background, and a few pixels of text.
  const row = new Uint8ClampedArray(600 * 4);
  for (let i = 0; i < 600; i += 1) row.set(i % 50 < 3 ? [230, 230, 230, 255] : [24, 26, 32, 255], i * 4);
  const drawn = [];
  window.HTMLCanvasElement.prototype.getContext = () => ({
    drawImage: (...args) => drawn.push(args.slice(1)),
    getImageData: () => ({ data: row }),
  });
  try {
    await withShell(async ({ host }) => {
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const live = host.querySelector('[data-pip-live="native"]');
      const chrome = host.querySelector('[data-pip="true"]').firstElementChild;
      await act(async () => {
        dispatchPointer(chrome, "pointerdown", { clientX: 100, clientY: 90 });
        dispatchPointer(chrome, "pointermove", { clientX: 120, clientY: 90 });
        await relayout();
      });
      assert.equal(host.querySelector('[data-pip-gesture-frame]').hidden, false);
      assert.deepEqual(drawn.at(-1), [0, 337, 600, 1, 0, 0, 600, 1], "the picture's bottom row is sampled");
      assert.equal(live.style.backgroundColor, "rgb(24, 26, 32)", "below the picture: the page's own edge colour");
      await act(async () => { dispatchPointer(chrome, "pointerup", { clientX: 120, clientY: 90 }); await relayout(); });
      assert.equal(host.querySelector('[data-pip-gesture-frame]').hidden, true);
      assert.equal(live.style.backgroundColor, "", "the live page back: no fill left behind");
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    globalThis.livePipTest = false;
    proto.decode = decode;
    for (const [key, descriptor] of natural) {
      if (descriptor) Object.defineProperty(proto, key, descriptor); else delete proto[key];
    }
    window.HTMLCanvasElement.prototype.getContext = getContext;
  }
});

test("the page is measured after its frame has followed the pane", async () => {
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 560, y: 80, width: 400, height: 250 }));
      flushObservers(); flushRaf();
      // Shown at a new size, the page lays out there before it goes live.
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const pip = host.querySelector('[data-pip="true"]');
      assert.equal(boundsCalls.at(-1)?.x, 560);
      // A sidebar opens and the pane narrows. In a frame the scheduled
      // measurement runs ahead of the resize observers: the frame must
      // follow its pane first, or the page is sent to last frame's offset.
      stageWidth = 800;
      window.dispatchEvent(new window.Event("resize"));
      flushRaf();
      assert.equal(pip.style.left, "360px", "the frame kept its distance to the pane's right side");
      assert.equal(boundsCalls.at(-1).x, 360, "the page went where the frame is");
      // The observers then find nothing left to move.
      const sent = boundsCalls.length;
      flushObservers(); flushRaf();
      assert.equal(pip.style.left, "360px");
      assert.ok(boundsCalls.slice(sent).every(bounds => bounds.x === 360));
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("following its pane commits only the frame, not the header and page surface inside", async () => {
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 560, y: 80, width: 400, height: 250 }));
      flushObservers(); flushRaf();
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const pip = host.querySelector('[data-pip="true"]');
      const title = pip.querySelector("span");
      const rendered = globalThis.surfaceRenders;
      assert.ok(rendered > 0, "the surface is counted");
      // A panel slides open over several frames; each frame the pane narrows
      // and the frame follows it.
      for (const width of [1000, 900, 800]) {
        stageWidth = width;
        window.dispatchEvent(new window.Event("resize"));
        flushRaf(); flushObservers(); flushRaf();
      }
      assert.equal(pip.style.left, "360px", "the frame followed the pane");
      assert.equal(boundsCalls.at(-1).x, 360, "and its page with it");
      assert.equal(globalThis.surfaceRenders, rendered, "the subtree inside the frame did not render again");
      assert.equal(pip.querySelector("span"), title, "the header is the same element");
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("a docked preview stands at its free place while its dock slot is hidden, and docks again when it shows", async () => {
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  // The slot lives in the main chat view; a split's peer pane leaves that
  // view display:none, and the slot's box is then empty.
  const column = document.createElement("div");
  column.className = "dag-side-column";
  const slot = document.createElement("div");
  slot.setAttribute("data-pip-dock", "");
  column.append(slot);
  document.body.append(column);
  let slotShown = true;
  column.getBoundingClientRect = () => box(800, 40, 300, 700);
  slot.getBoundingClientRect = () => slotShown ? box(810, 60, 280, 186) : box(0, 0, 0, 0);
  try {
    await withShell(async ({ host }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 560, y: 80, width: 400, height: 250 }));
      await act(async () => useWebTabPip.setState({ docked: true, dockSlot: slot }));
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const pip = host.querySelector('[data-pip="true"]');
      assert.equal(pip.getAttribute("data-pip-docked"), "true");
      assert.equal(pip.style.width, "280px", "docked over the slot");

      slotShown = false;
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      assert.equal(pip.getAttribute("data-pip-docked"), null, "not docked to a slot with no box");
      assert.equal(pip.style.width, "400px", "the frame keeps its free size, not the empty box");
      assert.equal(pip.style.left, "560px");
      assert.equal(useWebTabPip.getState().docked, true, "the choice to dock is kept");

      slotShown = true;
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      assert.equal(pip.getAttribute("data-pip-docked"), "true", "docked again once the slot is laid out");
      assert.equal(pip.style.width, "280px");
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    useWebTabPip.setState({ dockSlot: null });
    column.remove();
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("the frame follows a pane that moves while the frame commits", async () => {
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 560, y: 80, width: 400, height: 250 }));
      flushObservers(); flushRaf();
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const pip = host.querySelector('[data-pip="true"]');
      assert.equal(boundsCalls.at(-1)?.x, 560);
      // A sidebar drag's first step: the frame reads its pane, and the
      // commit that moves the frame also lands the sidebar's new width, so
      // the pane it read is gone. The scheduled measurement must still find
      // the frame where the pane that stands puts it.
      const sent = boundsCalls.length;
      // The settle reads the pane, then the frame follows what it read.
      stageShift = { reads: 2, width: 800 };
      window.dispatchEvent(new window.Event("resize"));
      flushRaf();
      assert.equal(stageShift, null, "the pane moved during the settle");
      assert.equal(pip.style.left, "360px", "the frame kept its distance to the pane's right side");
      assert.deepEqual(boundsCalls.slice(sent).map(bounds => bounds.x), [360], "the page was sent only where the frame is");
      flushObservers(); flushRaf();
      assert.equal(pip.style.left, "360px");
      assert.ok(boundsCalls.slice(sent).every(bounds => bounds.x === 360));
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    stageShift = null;
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("late gesture capture cannot hide a closed preview", async () => {
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  let resolve;
  try {
    await withShell(async ({ host }) => {
      const chrome = host.querySelector('[data-pip="true"]').firstElementChild;
      await act(async () => {
        dispatchPointer(chrome, "pointerdown");
        dispatchPointer(chrome, "pointermove", { clientX: 20, clientY: 0 });
      });
      await act(async () => useWebTabPip.getState().hide());
      const removed = globalThis.webTabBoundsRemoved.count;
      const shown = boundsCalls.length;
      await act(async () => resolve("data:image/png;base64,LATE"));
      assert.equal(globalThis.webTabBoundsRemoved.count, removed);
      assert.equal(boundsCalls.length, shown);
      assert.equal(host.querySelector('[data-pip-live="native"]'), null);
    }, { capture: () => new Promise(r => { resolve = r; }) });
  } finally { globalThis.livePipTest = false; window.HTMLImageElement.prototype.decode = decode; }
});

test("capture rejection and quick release restore the page without replaying gesture frames", async () => {
  globalThis.livePipTest = true;
  globalThis.pipFinalBounds = [];
  let captures = 0;
  try {
    await withShell(async ({ host }) => {
      const chrome = host.querySelector('[data-pip="true"]').firstElementChild;
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      captures = 0;
      globalThis.pipFinalBounds = [];
      const shown = boundsCalls.length;
      await act(async () => {
        dispatchPointer(chrome, "pointerdown");
        dispatchPointer(chrome, "pointerup", { clientX: 10, clientY: 10 });
      });
      // The press's picture and the release's first paint.
      assert.equal(captures, 2);
      assert.equal(globalThis.pipFinalBounds.length, 1);
      assert.ok(boundsCalls.length > shown);
      assert.ok(host.querySelector('[data-pip-live="native"]'));
    }, { capture: async () => { captures++; throw new Error("capture unavailable"); } });
  } finally { globalThis.livePipTest = false; }
});


test("a press or click on the preview without moving it leaves the live page up and shows no picture", async () => {
  // Every press used to take the page down for its picture and put it
  // back on release: the page flickered under a plain click.
  globalThis.livePipTest = true;
  globalThis.pipFinalBounds = [];
  const captures = [];
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const pip = host.querySelector('[data-pip="true"]');
      const chrome = pip.firstElementChild;
      const still = host.querySelector('[data-pip-gesture-frame]');
      const removed = globalThis.webTabBoundsRemoved.count;
      globalThis.pipFinalBounds = [];
      captures.length = 0;
      const style = pip.getAttribute("style");
      await act(async () => {
        dispatchPointer(chrome, "pointerdown", { clientX: 100, clientY: 90 });
        // A click's jitter is not a move.
        dispatchPointer(chrome, "pointermove", { clientX: 102, clientY: 91 });
        flushRaf();
        await relayout();
      });
      assert.equal(still.hidden, true, "pressed: no picture");
      assert.equal(globalThis.webTabBoundsRemoved.count, removed, "pressed: the live page stays up");
      assert.equal(pip.getAttribute("style"), style, "pressed: the frame is not redrawn");
      assert.deepEqual(captures, ["presentation"], "the picture is taken, ready for a move");
      await act(async () => { dispatchPointer(chrome, "pointerup", { clientX: 102, clientY: 91 }); await relayout(); });
      assert.equal(still.hidden, true, "released: still no picture");
      assert.equal(globalThis.webTabBoundsRemoved.count, removed, "released: the page never went down");
      assert.equal(globalThis.pipFinalBounds.length, 0, "nothing to put back");
      assert.equal(pip.className.includes("webPipDragging"), false);
      // Moved past the jitter, the press's picture stands in at once.
      await act(async () => {
        dispatchPointer(chrome, "pointerdown", { clientX: 100, clientY: 90 });
        dispatchPointer(chrome, "pointermove", { clientX: 130, clientY: 90 });
        await relayout();
      });
      assert.equal(still.hidden, false, "moved: the picture stands in");
      assert.ok(globalThis.webTabBoundsRemoved.count > removed, "moved: the live page is down");
      assert.deepEqual(captures, ["presentation", "presentation"], "the press's picture, no second one");
      await act(async () => { dispatchPointer(chrome, "pointerup", { clientX: 130, clientY: 90 }); await relayout(); });
      assert.equal(still.hidden, true);
      assert.equal(globalThis.pipFinalBounds.length, 1, "released: the live page is back at the new place");
    }, { capture: async (_id, mode) => { captures.push(mode); return "data:image/png;base64,FRAME"; } });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("a menu or popover over the preview shows the page's picture, not an empty frame, until it goes", async () => {
  // Covered, the page used to be taken down with nothing in its place.
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      for (let i = 0; i < 2; i++) await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const still = host.querySelector('[data-pip-gesture-frame]');
      assert.equal(still.hidden, true);
      const removed = globalThis.webTabBoundsRemoved.count;
      const popover = document.createElement("div");
      popover.setAttribute("data-native-view-occluder", "true");
      popover.getBoundingClientRect = () => box(0, 0, 2000, 2000);
      host.append(popover);
      await act(async () => { window.dispatchEvent(new window.Event("resize")); flushRaf(); await relayout(); });
      assert.equal(still.hidden, false, "covered: the page's picture stands in");
      assert.ok(still.src.includes("COVERED"), "a picture taken as it was covered");
      assert.ok(globalThis.webTabBoundsRemoved.count > removed, "and the native page is down under the popover");
      popover.remove();
      const shown = boundsCalls.length;
      await act(async () => { window.dispatchEvent(new window.Event("resize")); flushRaf(); await relayout(); });
      assert.ok(boundsCalls.length > shown, "uncovered: the live page is back");
      assert.equal(still.hidden, true, "and its picture is gone");
    }, { capture: async () => "data:image/png;base64,COVERED" });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("a fold starts from the frame where it stands, the first fold on a page too", async () => {
  // With no start given, motion read the frame's `none` and began the first
  // fold at scale 0: the frame vanished at once, then moved to the chip.
  globalThis.pipAnimations = [];
  const chip = document.createElement("div");
  chip.setAttribute("data-web-surface-chip", "w:https://page.test/1");
  chip.getBoundingClientRect = () => box(20, 10, 40, 24);
  document.body.append(chip);
  try {
    await withShell(async ({ host }) => {
      const frame = host.querySelector('[data-pip="true"]');
      await act(async () => { useWebTabPip.setState({ motion: "folding" }); await Promise.resolve(); });
      const fold = globalThis.pipAnimations.find(([el]) => el === frame);
      assert.ok(fold, "the frame folds");
      const { transform, opacity } = fold[1];
      assert.ok(Array.isArray(transform), "the fold names where it starts");
      assert.equal(transform[0], "translate(0px, 0px) scale(1)");
      assert.match(transform[1], /^translate\(-?[.\d]+px, -?[.\d]+px\) scale\(0\.\d+\)$/);
      assert.deepEqual(opacity, [1, 0]);
    });
  } finally {
    chip.remove();
    globalThis.pipAnimations = undefined;
  }
});

test("with no chip naming its page on screen, the preview fades where it is instead of folding into another page's chip", async () => {
  globalThis.pipAnimations = [];
  const chip = document.createElement("div");
  chip.setAttribute("data-web-surface-chip", "w:https://split.test/other");
  chip.getBoundingClientRect = () => box(20, 10, 40, 24);
  document.body.append(chip);
  try {
    await withShell(async ({ host }) => {
      const frame = host.querySelector('[data-pip="true"]');
      await act(async () => { useWebTabPip.setState({ motion: "folding" }); await Promise.resolve(); });
      const fold = globalThis.pipAnimations.find(([el]) => el === frame);
      assert.ok(fold, "the frame still goes with a motion");
      assert.deepEqual(fold[1].transform, ["translate(0px, 0px) scale(1)", "translate(0px, 0px) scale(1)"], "in place");
      assert.deepEqual(fold[1].opacity, [1, 0], "fading");
    });
  } finally {
    chip.remove();
    globalThis.pipAnimations = undefined;
  }
});

test("a popover over the preview takes the page down at once under its last picture, not after a new one", async () => {
  // The page used to stay up, drawn over the opening popover, until a new
  // picture came (up to PIP_PICTURE_WAIT_MS): a flash at every open.
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  let pending = null;
  try {
    await withShell(async ({ host }) => {
      for (let i = 0; i < 2; i++) await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      const still = host.querySelector('[data-pip-gesture-frame]');
      assert.equal(still.hidden, true);
      const removed = globalThis.webTabBoundsRemoved.count;
      let resolveCapture;
      pending = new Promise(resolve => { resolveCapture = resolve; });
      const popover = document.createElement("div");
      popover.setAttribute("data-native-view-occluder", "true");
      popover.getBoundingClientRect = () => box(0, 0, 2000, 2000);
      host.append(popover);
      await act(async () => { window.dispatchEvent(new window.Event("resize")); flushRaf(); await Promise.resolve(); });
      assert.equal(still.hidden, false, "the last picture stands in at once");
      assert.ok(still.src.includes("SHOWN"), "the picture taken as the page last came up");
      assert.ok(globalThis.webTabBoundsRemoved.count > removed, "and the page is down before a new picture comes");
      await act(async () => { resolveCapture("data:image/png;base64,COVERED"); await relayout(); });
      assert.ok(still.src.includes("COVERED"), "the new picture replaces it");
      popover.remove();
      pending = null;
      await act(async () => { window.dispatchEvent(new window.Event("resize")); flushRaf(); await relayout(); });
      assert.equal(still.hidden, true, "uncovered: the live page is back");
    }, { capture: async () => pending ?? "data:image/png;base64,SHOWN" });
  } finally {
    globalThis.livePipTest = false;
    window.HTMLImageElement.prototype.decode = decode;
  }
});

test("occluding a preview settles instead of observing its own hidden attribute forever", async () => {
  const previous = window.MutationObserver;
  window.MutationObserver = RealMutationObserver;
  globalThis.MutationObserver = RealMutationObserver;
  globalThis.livePipTest = true;
  try {
    await withShell(async ({ host }) => {
      const modal = document.createElement("div");
      modal.setAttribute("role", "dialog");
      modal.getBoundingClientRect = () => box(0, 0, 2000, 2000);
      host.append(modal);
      for (let i = 0; i < 6; i++) await act(async () => { flushRaf(); });
      assert.equal(rafQueue.size, 0, "occlusion must not cause a self-sustaining rAF loop");
    });
  } finally {
    globalThis.livePipTest = false;
    window.MutationObserver = previous;
    globalThis.MutationObserver = previous;
  }
});



test("the page is measured again only after changes that can move it and that no measurement has covered", async () => {
  const previous = window.MutationObserver;
  window.MutationObserver = RealMutationObserver;
  globalThis.MutationObserver = RealMutationObserver;
  globalThis.livePipTest = true;
  const settle = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };
  // Runs the frames already asked for until none is left.
  const drain = async () => { for (let i = 0; i < 6 && rafQueue.size; i++) { await act(async () => { flushRaf(); }); await settle(); } assert.equal(rafQueue.size, 0); };
  try {
    await withShell(async ({ host }) => {
      for (let i = 0; i < 3; i++) await act(async () => { flushObservers(); flushRaf(); });
      await settle(); await drain();
      const pip = host.querySelector('[data-pip="true"]');
      // An animated drawing changes its parts every frame; nothing outside it moves.
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      const eye = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      Object.defineProperty(eye, "ownerSVGElement", { value: svg });
      svg.append(eye);
      host.append(svg);
      await settle(); await drain();
      eye.setAttribute("opacity", "0.5");
      await settle();
      assert.equal(rafQueue.size, 0, "a drawing's parts schedule no measurement");
      // The frame's place, written and then announced: the announcement measures it.
      // (React writes it through the style property; the attribute is the same mutation.)
      pip.setAttribute("style", `${pip.getAttribute("style")};left:300px`);
      pip.dispatchEvent(new window.Event("op:pip-geometry"));
      await settle();
      assert.equal(rafQueue.size, 0, "a change the measurement saw is not measured again");
      // A change nothing measured still gets its measurement, as before.
      pip.setAttribute("style", `${pip.getAttribute("style")};left:310px`);
      await settle();
      assert.equal(rafQueue.size, 1, "the frame's place written without its event");
      await drain();
      // Elsewhere on the page: the composer's rows beside the preview, a
      // reply streaming in.
      const elsewhere = document.createElement("section");
      document.body.append(elsewhere);
      await settle(); await drain();
      elsewhere.append(document.createElement("div"));
      elsewhere.setAttribute("style", "left:20px");
      elsewhere.firstChild.setAttribute("data-compact", "true");
      await settle();
      assert.equal(rafQueue.size, 0, "nodes and attributes elsewhere move nothing");
      // An occluder coming, moving and going is measured each time.
      const wrapper = document.createElement("div");
      wrapper.innerHTML = '<div role="menu"></div>';
      elsewhere.append(wrapper);
      await settle();
      assert.equal(rafQueue.size, 1, "a menu opened");
      await drain();
      wrapper.setAttribute("style", "transform:translate(10px, 10px)");
      await settle();
      assert.equal(rafQueue.size, 1, "the menu's wrapper placed it");
      await drain();
      wrapper.remove();
      await settle();
      assert.equal(rafQueue.size, 1, "the menu closed");
      await drain();
      elsewhere.remove();
    });
  } finally {
    globalThis.livePipTest = false;
    window.MutationObserver = previous;
    globalThis.MutationObserver = previous;
  }
});

test("a page whose pane moves without resizing anything is measured where it went", async () => {
  // IntersectionObservers report what the browser laid out; here they report
  // the stubbed boxes when flushed.
  const observers = new Set();
  class StubIntersectionObserver {
    constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); observers.add(this); }
    observe(target) { this.targets.add(target); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); observers.delete(this); }
    takeRecords() { return []; }
  }
  const flushIntersections = () => {
    for (const observer of [...observers]) {
      for (const target of observer.targets) {
        observer.callback([{ target, boundingClientRect: target.getBoundingClientRect(), intersectionRatio: 1 }], observer);
      }
    }
  };
  const previous = globalThis.IntersectionObserver;
  globalThis.IntersectionObserver = window.IntersectionObserver = StubIntersectionObserver;
  globalThis.livePipTest = true;
  const decode = window.HTMLImageElement.prototype.decode;
  window.HTMLImageElement.prototype.decode = async () => {};
  try {
    await withShell(async ({ host }) => {
      await act(async () => useWebTabPip.getState().setRect({ x: 560, y: 80, width: 400, height: 250 }));
      flushObservers(); flushRaf();
      // Shown at a new size, the page lays out there before it goes live.
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      await act(async () => { flushObservers(); flushRaf(); await relayout(); });
      await act(async () => { flushIntersections(); flushRaf(); });
      const page = host.querySelector('[data-pip-live="native"]');
      const before = boundsCalls.at(-1);
      assert.ok(before, "the page was shown");
      const watch = [...observers].find(observer => observer.targets.has(page));
      assert.ok(watch, "the page's place is watched");
      const [top, right, bottom, left] = watch.options.rootMargin.split(" ").map(parseFloat);
      const r = page.getBoundingClientRect();
      assert.deepEqual([-top, -right, -bottom, -left], [Math.floor(r.top), Math.floor(1200 - r.right), Math.floor(800 - r.bottom), Math.floor(r.left)], "the watch's root is the page's own box");
      // Nothing moved: nothing is measured.
      await act(async () => { flushIntersections(); });
      assert.equal(rafQueue.size, 0);
      // The pane moves up 40px; no size and no attribute changes.
      windowShift.y = -40;
      await act(async () => { flushIntersections(); });
      assert.equal(rafQueue.size, 1, "the move asks for a measurement");
      await act(async () => { flushRaf(); });
      const after = boundsCalls.at(-1);
      assert.equal(after.y, before.y - 40, "the page went where its frame is drawn");
      assert.equal(after.x, before.x);
      // Watched again around its new place.
      await act(async () => { flushIntersections(); });
      assert.equal(rafQueue.size, 0);
    }, { capture: async () => "data:image/png;base64,FRAME" });
  } finally {
    window.HTMLImageElement.prototype.decode = decode;
    windowShift.y = 0;
    globalThis.livePipTest = false;
    globalThis.IntersectionObserver = window.IntersectionObserver = previous;
  }
});

test("More cancellation and unmount discard stale actions and remove listeners", async () => {
  const menu = installOverlayMenu();
  await withShell(async ({ host }) => {
    await act(async () => clickButton(chromeButton(host, "More")));
    const old = menu.popups[0].items[0].id;
    await act(async () => { for (const cb of menu.actions) cb("tabmenu:close"); });
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    await act(async () => menu.dismiss());
    assert.equal(chromeButton(host, "More").getAttribute("aria-expanded"), "false");
    await act(async () => { for (const cb of menu.actions) cb(old); });
    assert.equal(getPreviewPreference("a", null).mode, "manual");
    await act(async () => clickButton(chromeButton(host, "More")));
    await act(async () => clickButton(chromeButton(host, "Close preview")));
    assert.equal(menu.closed.length, 1);
    assert.equal(menu.actions.size, 0);
    assert.equal(menu.closures.size, 0);
  });
});

test("application menu renders an unchecked box and a separate wrapping explanation", async () => {
  const { renderToStaticMarkup } = await import("react-dom/server");
  globalThis.menuParams = new URLSearchParams({ width: "360", items: JSON.stringify([
    { id: "follow", label: "Automatically show Agent page", checked: false,
      description: "When enabled, follow the page. <script>not markup</script>" },
  ]) });
  try {
    const { document: doc } = parseHTML(renderToStaticMarkup(createElement(ContextMenuPage)));
    const option = doc.querySelector('[role="menuitemcheckbox"]');
    assert.equal(option.getAttribute("aria-checked"), "false");
    const title = [...option.querySelectorAll("span")].find(el => el.textContent === "Automatically show Agent page");
    assert.ok(title);
    assert.match(title.className, /block whitespace-normal/);
    assert.match(title.nextElementSibling.textContent, /When enabled/);
    assert.match(title.nextElementSibling.className, /block whitespace-normal/);
    assert.ok(option.querySelector('[aria-hidden="true"]'));
    assert.equal(doc.querySelector("script"), null);
  } finally { delete globalThis.menuParams; }
});


test("Show actions can be unchecked and checked in the same open menu", async () => {
  const menu = installOverlayMenu();
  await withShell(async ({ host }) => {
    await act(async () => clickButton(chromeButton(host, "More")));
    const checked = () => menu.popups[0].items.find(item => item.id.endsWith(":show-actions")).checked;
    const initial = checked();
    await act(async () => menu.resolvers[0]("show-actions"));
    assert.equal(checked(), !initial);
    assert.equal(chromeButton(host, "More").getAttribute("aria-expanded"), "true");
    await act(async () => menu.resolvers[0]("show-actions"));
    assert.equal(checked(), initial);
    assert.equal(menu.popups.length, 1);
    assert.equal(menu.closed.length, 0);
  });
});


test("embedded PiP scales its fixed page without recreating the iframe", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(WebTabPipSurface, { tabId: "iframe-test", url: "https://example.test", native: false })));
    const iframe = host.querySelector("iframe");
    const body = iframe.parentElement;
    for (const [width, height, scale] of [[240, 135, 0.125], [960, 540, 0.5], [600, 200, 200 / 1080]]) {
      Object.defineProperty(body, "clientWidth", { configurable: true, value: width });
      Object.defineProperty(body, "clientHeight", { configurable: true, value: height });
      flushObservers();
      assert.equal(iframe.style.transform, `scale(${scale})`);
      assert.equal(host.querySelector("iframe"), iframe);
    }
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
