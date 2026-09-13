import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".floating-control-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "floating.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { WebTabPane } from "./components/center-tabs/web-tab-pane";
    export { BrowserControlBar } from "./components/center-tabs/browser-control-bar";
    export { useCenterTabs } from "./lib/tabs/center-tabs-store";
    export {
      resetBrowserControl,
      recordOperationCue,
    } from "./lib/browser/browser-control";
    export {
      ingestBrowserResource,
      resetBrowserResources,
      setBrowserConnection,
    } from "./lib/chat/session-resources";
    export { cueTravelDurationMs } from "./lib/browser/browser-action-cue";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "floating-services", setup(b) {
    b.onResolve({ filter: /net\/fetch-client/ }, () => ({ path: "fetch-client", namespace: "test-services" }));
    b.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "next-nav", namespace: "test-services" }));
    b.onResolve({ filter: /(?:^|\/|\.)browser-controls$/ }, () => ({ path: "browser-controls", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents:
      a.path === "next-nav"
        ? "export const useRouter = () => ({ push() {}, replace() {} }); export const usePathname = () => '/chat';"
        : a.path === "browser-controls"
        ? "export function BookmarkBar(){return null} export function BookmarksLibraryButton(){return null} export function BrowserMenu(){return null}"
        : "export async function jsonFetch() { return {}; }" }));
  }}],
});

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(window, "location", { value: { pathname: "/chat" } });
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
globalThis.PointerEvent = window.PointerEvent || window.MouseEvent;
globalThis.Element = window.Element;
globalThis.HTMLElement = window.HTMLElement;
globalThis.Node = window.Node;
if (!window.SVGElement) {
  window.SVGElement = class SVGElement extends window.HTMLElement {};
}
globalThis.SVGElement = window.SVGElement;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = { getItem() { return "en"; }, setItem() {}, removeItem() {} };
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(0), 0);
globalThis.cancelAnimationFrame = clearTimeout;
if (!globalThis.DOMRect) {
  globalThis.DOMRect = class DOMRect {
    constructor(x = 0, y = 0, width = 0, height = 0) {
      this.x = x; this.y = y; this.width = width; this.height = height;
      this.top = y; this.left = x; this.right = x + width; this.bottom = y + height;
    }
  };
}
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.HTMLElement.prototype.hasPointerCapture = () => false;
window.HTMLElement.prototype.setPointerCapture = () => {};
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  return new DOMRect(0, 0, 640, 400);
};
const idleAnimation = () => ({
  play() {},
  cancel() {},
  finish() {},
  pause() {},
  playState: "finished",
  currentTime: 0,
  finished: Promise.resolve(),
  ready: Promise.resolve(),
  addEventListener() {},
  removeEventListener() {},
});
window.HTMLElement.prototype.animate = idleAnimation;
window.SVGElement.prototype.animate = idleAnimation;
window.HTMLElement.prototype.getAnimations = () => [];
window.SVGElement.prototype.getAnimations = () => [];
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  WebTabPane, resetBrowserControl, ingestBrowserResource, resetBrowserResources,
  setBrowserConnection, useCenterTabs, recordOperationCue, cueTravelDurationMs,
} = await import(pathToFileURL(bundle));

test("agent controls leave the address toolbar and float over the page while active", async () => {
  resetBrowserControl();
  resetBrowserResources();
  setBrowserConnection(true);
  useCenterTabs.setState({
    tabs: [{ id: "w:https://a.test/", kind: "web", url: "https://a.test/", title: "A" }],
    activeId: "w:https://a.test/",
    groups: [],
    splitWebTabId: null,
  });
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:https://a.test/", kind: "web", title: "A", target: "https://a.test/",
    status: "open", source: "browser", control_state: "active", generation: 1, sequence: 1,
    execution_id: "exec-a",
  }, "a");
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(WebTabPane, {
      tabId: "w:https://a.test/",
      url: "https://a.test/",
    })));
    const toolbar = host.querySelector("input[aria-label='Address']")?.parentElement;
    assert.ok(toolbar);
    assert.equal(toolbar.querySelector("[data-browser-control]"), null);
    assert.equal([...toolbar.querySelectorAll("button")].some((button) =>
      (button.getAttribute("aria-label") || "").includes("I will operate")
      || (button.getAttribute("aria-label") || "").includes("Pause Agent")), false);
    const float = host.querySelector("[data-browser-control='float']");
    assert.ok(float);
    assert.equal(float.getAttribute("data-collapsed"), "true");
    assert.ok(float.querySelector("svg.click-ico, svg"));
    await act(async () => {
      ingestBrowserResource({
        id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
        tab_id: "w:https://a.test/", kind: "web", title: "A", target: "https://a.test/",
        status: "open", source: "browser", control_state: "idle", generation: 1, sequence: 2,
        execution_id: "exec-a",
      }, "a");
    });
    assert.equal(host.querySelector("[data-browser-control='float']"), null);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    resetBrowserControl();
    resetBrowserResources();
  }
});

test("click cue uses the cursor icon rather than a circle or square", async () => {
  resetBrowserControl();
  resetBrowserResources();
  setBrowserConnection(true);
  useCenterTabs.setState({
    tabs: [{ id: "w:https://a.test/", kind: "web", url: "https://a.test/", title: "A" }],
    activeId: "w:https://a.test/",
    groups: [],
    splitWebTabId: null,
  });
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:https://a.test/", kind: "web", title: "A", target: "https://a.test/",
    status: "open", source: "browser", control_state: "active", generation: 1, sequence: 1,
    execution_id: "exec-a",
  }, "a");
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const origRAF = globalThis.requestAnimationFrame;
  const origCAF = globalThis.cancelAnimationFrame;
  const origNow = Date.now;
  const rafQueue = [];
  let rafId = 0;
  let now = 1_000_000;
  globalThis.requestAnimationFrame = (callback) => {
    const id = ++rafId;
    rafQueue.push({ id, callback });
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => {
    const index = rafQueue.findIndex((item) => item.id === id);
    if (index >= 0) rafQueue.splice(index, 1);
  };
  Date.now = () => now;
  const flushOneFrame = async () => {
    const batch = rafQueue.splice(0, rafQueue.length);
    await act(async () => {
      for (const item of batch) item.callback(now);
    });
  };
  try {
    await act(async () => root.render(createElement(WebTabPane, {
      tabId: "w:https://a.test/",
      url: "https://a.test/",
    })));
    await act(async () => {
      recordOperationCue({
        resourceId: "page-a",
        generation: 1,
        controlState: "active",
        operation: {
          id: "op-1",
          action: "click",
          phase: "acknowledged",
          frame_id: "frame-1",
          geometry_revision: 0,
          point: { x: 40, y: 80, width: 640, height: 400 },
        },
      });
    });
    await flushOneFrame();
    const first = host.querySelector("[data-browser-action-cue]");
    assert.ok(first);
    assert.ok(first.querySelector("svg.click-ico, svg"));
    assert.equal(first.getAttribute("data-cue-x"), "40");
    assert.equal(first.getAttribute("data-cue-y"), "80");
    const firstLeft = first.style.left;
    await act(async () => {
      recordOperationCue({
        resourceId: "page-a",
        generation: 1,
        controlState: "active",
        operation: {
          id: "op-2",
          action: "click",
          phase: "acknowledged",
          frame_id: "frame-1",
          geometry_revision: 0,
          point: { x: 140, y: 180, width: 640, height: 400 },
        },
      });
    });
    const duration = cueTravelDurationMs({ x: 40, y: 80 }, { x: 140, y: 180 });
    now += Math.max(1, Math.floor(duration / 3));
    await flushOneFrame();
    const traveling = host.querySelector("[data-browser-action-cue]");
    assert.ok(traveling);
    const midX = Number(traveling.getAttribute("data-cue-x"));
    assert.ok(midX > 40 && midX < 140, `expected intermediate x, got ${midX}`);
    assert.equal(traveling.style.left === firstLeft || midX < 140, true);
    await act(async () => {
      ingestBrowserResource({
        id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
        tab_id: "w:https://a.test/", kind: "web", title: "A", target: "https://a.test/",
        status: "open", source: "browser", control_state: "active", generation: 2, sequence: 2,
        execution_id: "exec-a",
      }, "a");
    });
    const cancelled = host.querySelector("[data-browser-action-cue]");
    if (cancelled) {
      assert.notEqual(cancelled.getAttribute("data-cue-x"), "140");
      assert.ok(Number(cancelled.getAttribute("data-cue-x")) < 140);
    } else {
      assert.equal(cancelled, null);
    }
    await act(async () => {
      recordOperationCue({
        resourceId: "page-a",
        generation: 2,
        controlState: "active",
        operation: {
          id: "op-2b",
          action: "click",
          phase: "acknowledged",
          frame_id: "frame-1",
          geometry_revision: 1,
          point: { x: 140, y: 180, width: 640, height: 400 },
        },
      });
    });
    now += duration;
    await flushOneFrame();
    const arrived = host.querySelector("[data-browser-action-cue]");
    assert.ok(arrived);
    assert.equal(arrived.getAttribute("data-cue-x"), "140");
    assert.equal(arrived.getAttribute("data-cue-y"), "180");
    assert.equal(arrived.getAttribute("data-cue-play"), "once");
    const seqAfterMove = Number(arrived.getAttribute("data-cue-seq"));
    await act(async () => {
      recordOperationCue({
        resourceId: "page-a",
        generation: 2,
        controlState: "active",
        operation: {
          id: "op-3",
          action: "click",
          phase: "acknowledged",
          frame_id: "frame-1",
          geometry_revision: 1,
          point: { x: 140, y: 180, width: 640, height: 400 },
        },
      });
    });
    await flushOneFrame();
    const replay = host.querySelector("[data-browser-action-cue]");
    assert.ok(replay);
    assert.equal(replay.getAttribute("data-cue-x"), "140");
    assert.ok(Number(replay.getAttribute("data-cue-seq")) > seqAfterMove);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    rafQueue.length = 0;
    globalThis.requestAnimationFrame = origRAF;
    globalThis.cancelAnimationFrame = origCAF;
    Date.now = origNow;
    resetBrowserControl();
    resetBrowserResources();
  }
});
