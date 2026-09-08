import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".pip-dock-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "dock.mjs");
const boundsCalls = [];
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { WebTabPip } from "./components/center-tabs/web-tab-pip";
    export { WebTabPane } from "./components/center-tabs/web-tab-pane";
    export { useCenterTabs } from "./lib/state/center-tabs-store";
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
      getSnapshot,
      setSnapshot,
    } from "./lib/state/web-tab-pip-store";
    export {
      ingestBrowserResource,
      resetBrowserResources,
      getPreviewPreference,
      selectResourcePreview,
      togglePreviewExpanded,
      hideResourcePreview,
      followCurrentBranch,
    } from "./lib/state/session-resources";
    export { recordOperationCue, resetBrowserControl, resumeErrorFor } from "./lib/state/browser-control";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "dock-services", setup(b) {
    b.onResolve({ filter: /desktop-bridge/ }, () => ({ path: "desktop-bridge", namespace: "test-services" }));
    b.onResolve({ filter: /net\/fetch-client/ }, () => ({ path: "fetch-client", namespace: "test-services" }));
    b.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "next-nav", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "fetch-client"
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
window.getComputedStyle = (el) => ({ display: el?.className?.includes?.("center-pane-chat") ? "flex" : "block" });
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
  assert.equal(PIP_DEFAULT_WIDTH, 360);
  assert.equal(PIP_DEFAULT_HEIGHT, 280);
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

function pipBarButtons(host) {
  const pip = host.querySelector("[data-pip='true']");
  return [...(pip?.children[1]?.querySelectorAll("button") || [])];
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

async function withShell(run) {
  resetBrowserResources();
  resetBrowserControl();
  globalThis.controlPosts = [];
  globalThis.controlReply = undefined;
  boundsCalls.length = 0;
  stageWidth = 1000;
  globalThis.webTabCapture = undefined;
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

test("chat PiP chrome is two named rows without duplicate Eye or Follow wrapping", async () => {
  await withShell(async ({ host }) => {
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(pip);
    assert.equal(pip.getAttribute("data-pip-host"), "chat");
    assert.equal(pip.children.length, 3);
    const openPage = pipBarButtons(host).find(button => button.textContent === "Open page");
    const follow = pipBarButtons(host).find(button => button.textContent === "Follow");
    const takeover = pipBarButtons(host).find(button =>
      button.textContent === "Take over" || button.textContent === "Pause Agent and take over");
    const more = labeledButton(host, "More");
    const expand = labeledButton(host, "Expand");
    const hide = labeledButton(host, "Hide");
    assert.ok(openPage && follow && more && expand && hide);
    assert.equal(takeover, undefined);
    assert.equal(labeledButton(host, "Use in webpage"), undefined);
    assert.equal(labeledButton(host, "Follow current branch"), undefined);
    assert.equal(labeledButton(host, "Show actions"), undefined);
    const title = pip.querySelector("span");
    const status = pip.querySelector("small");
    assert.equal(title?.textContent, "Resource test 1");
    assert.equal(status?.textContent, "Manual inspection · Idle");
    assert.equal(title.title.includes("Manual inspection"), true);
  });
});

test("Follow is only on the bar while inspecting manually", async () => {
  await withShell(async ({ host, session }) => {
    assert.ok(pipBarButtons(host).some(button => button.textContent === "Follow"));
    await act(async () => {
      followCurrentBranch("a", null);
    });
    assert.equal(getPreviewPreference("a", null).mode, "follow");
    assert.equal(pipBarButtons(host).some(button => button.textContent === "Follow"), false);
    assert.equal(useCenterTabs.getState().activeId, session.id);
  });
});

test("idle chat PiP has no enabled Take over; active shows Take over", async () => {
  await withShell(async ({ host, page }) => {
    assert.equal(pipBarButtons(host).some(button => button.textContent === "Take over"), false);
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
        sequence: 2,
        execution_id: "exec-a",
      }, "a");
    });
    const takeover = pipBarButtons(host).find(button => button.textContent === "Take over");
    assert.ok(takeover);
    assert.equal(takeover.disabled, false);
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
        control_state: "paused",
        generation: 1,
        sequence: 3,
        execution_id: "exec-a",
      }, "a");
    });
    const resume = pipBarButtons(host).find(button => button.textContent === "Resume");
    assert.ok(resume);
    assert.equal(resume.disabled, false);
    assert.equal(pipBarButtons(host).some(button => button.textContent === "Take over"), false);
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
        control_state: "stop_unconfirmed",
        generation: 1,
        sequence: 4,
        execution_id: "exec-a",
      }, "a");
    });
    const unconfirmed = pipBarButtons(host).find(button => button.textContent === "Take over");
    assert.ok(unconfirmed);
    assert.equal(unconfirmed.disabled, true);
    assert.equal(pipBarButtons(host).some(button => button.textContent === "Resume"), false);
  });
});

test("failed Resume shows lease expired on chat PiP and the Page toolbar, then success clears it", async () => {
  await withShell(async ({ host, page, session }) => {
    const pausedRow = {
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
      control_state: "paused",
      generation: 1,
      sequence: 3,
      execution_id: "exec-a",
    };
    await act(async () => { ingestBrowserResource(pausedRow, "a"); });
    const resume = pipBarButtons(host).find(button => button.textContent === "Resume");
    assert.ok(resume);
    assert.equal(host.querySelector("[data-pip='true']")?.getAttribute("data-state"), "paused");
    globalThis.controlReply = () => { throw new Error("lease expired"); };
    await act(async () => {
      resume.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(globalThis.controlPosts.at(-1)?.body.action, "resume");
    assert.equal(resumeErrorFor("page-1"), "lease expired");
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(pip);
    assert.equal(pip.getAttribute("data-state"), "paused");
    assert.equal(pip.textContent.includes("lease expired"), true);
    const status = pip.querySelector("[data-resume-error='true']");
    assert.ok(status);
    assert.equal(status.getAttribute("role"), "status");
    assert.equal(status.getAttribute("aria-live"), "polite");
    assert.equal(status.getAttribute("title").includes("lease expired"), true);
    const stillResume = pipBarButtons(host).find(button => button.textContent === "Resume");
    assert.ok(stillResume);
    assert.equal(stillResume.disabled, false);
    assert.equal(stillResume.getAttribute("aria-label"), "Resume: lease expired");
    assert.equal(useCenterTabs.getState().tabs.filter(tab => tab.kind === "web").length, 1);

    await act(async () => { useCenterTabs.getState().setActive(page.id); });
    await act(async () => { flushObservers(); });
    assert.equal(host.querySelector("[data-pip='true']"), null);
    assert.equal(host.textContent.includes("lease expired"), true);
    assert.equal(useCenterTabs.getState().activeId, page.id);

    await act(async () => { useCenterTabs.getState().setActive(session.id); });
    const chatPip = host.querySelector("[data-pip='true']");
    assert.ok(chatPip);
    assert.equal(chatPip.getAttribute("data-state"), "paused");
    assert.equal(chatPip.textContent.includes("lease expired"), true);

    globalThis.controlReply = () => ({ ...pausedRow, control_state: "active", sequence: 4 });
    const retry = pipBarButtons(host).find(button => button.textContent === "Resume");
    await act(async () => {
      retry.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(resumeErrorFor("page-1"), undefined);
    const recovered = host.querySelector("[data-pip='true']");
    assert.ok(recovered);
    assert.equal(recovered.textContent.includes("lease expired"), false);
    assert.equal(recovered.getAttribute("data-state"), "active");
    assert.ok(pipBarButtons(host).some(button => button.textContent === "Take over"));
    assert.equal(useCenterTabs.getState().tabs.filter(tab => tab.kind === "web").length, 1);
    assert.equal(useCenterTabs.getState().activeId, session.id);
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

test("split visibility of the same Page hides the chat preview", async () => {
  await withShell(async ({ host, page, session }) => {
    await act(async () => {
      useCenterTabs.setState({
        groups: [{
          id: "g1",
          memberIds: [session.id, page.id],
          visibleIds: [session.id, page.id],
          focusedId: session.id,
        }],
        activeId: session.id,
      });
    });
    assert.equal(pipCoversCenter(page.id, session.id, useCenterTabs.getState()), false);
    assert.equal(host.querySelector("[data-pip='true']"), null);
    assert.equal(useWebTabPip.getState().tabId, page.id);
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
