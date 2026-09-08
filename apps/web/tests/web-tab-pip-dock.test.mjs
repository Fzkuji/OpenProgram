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
      pipDockEdge,
      pipHostMode,
      pipPresentationSize,
      PIP_DEFAULT_WIDTH,
      PIP_EXPANDED_HEIGHT,
      PIP_EXPANDED_WIDTH,
      PIP_MIN_WIDTH,
      getSnapshot,
      setSnapshot,
    } from "./lib/state/web-tab-pip-store";
    export {
      resetBrowserResources,
      getPreviewPreference,
      selectResourcePreview,
      togglePreviewExpanded,
      hideResourcePreview,
    } from "./lib/state/session-resources";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "dock-services", setup(b) {
    b.onResolve({ filter: /desktop-bridge/ }, () => ({ path: "desktop-bridge", namespace: "test-services" }));
    b.onResolve({ filter: /browser-control-bar/ }, () => ({ path: "control-bar", namespace: "test-services" }));
    b.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "next-nav", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, a => ({ contents: a.path === "control-bar"
      ? "export function BrowserControlBar() { return null; }"
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
    return this.getAttribute("data-pip-host") === "page"
      ? box(stageWidth - 360, 80, 360, 520)
      : box(stageWidth - 372, 78, 360, 220);
  }
  return box(0, 0, stageWidth, 700);
};

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  WebTabPip, WebTabPane, useCenterTabs, useWebTabPip,
  resetBrowserResources, getPreviewPreference, selectResourcePreview,
  togglePreviewExpanded, hideResourcePreview, getSnapshot, setSnapshot,
  pipChatRect, pipDockEdge, pipHostMode, pipPresentationSize,
  PIP_DEFAULT_WIDTH, PIP_EXPANDED_HEIGHT, PIP_EXPANDED_WIDTH, PIP_MIN_WIDTH,
} = await import(pathToFileURL(bundle));

function overlap(a, b) {
  return Math.max(a.x, b.x) < Math.min(a.x + a.width, b.x + b.width)
    && Math.max(a.y, b.y) < Math.min(a.y + a.height, b.y + b.height);
}

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
  assert.equal(pipDockEdge(900, PIP_DEFAULT_WIDTH), "end");
  assert.equal(pipDockEdge(500, PIP_DEFAULT_WIDTH), "bottom");
  assert.equal(pipDockEdge(PIP_MIN_WIDTH + 319, PIP_MIN_WIDTH), "bottom");
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

async function withShell(run) {
  resetBrowserResources();
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
    globalThis.webTabCapture = undefined;
  }
}

test("chat PiP toolbar keeps full names without overlapping control text", async () => {
  await withShell(async ({ host }) => {
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(pip);
    assert.equal(pip.getAttribute("data-pip-host"), "chat");
    const follow = [...host.querySelectorAll("button")]
      .find(button => button.getAttribute("aria-label") === "Follow current branch");
    const usePage = [...host.querySelectorAll("button")]
      .find(button => button.getAttribute("aria-label") === "Use in webpage");
    const expand = [...host.querySelectorAll("button")]
      .find(button => button.getAttribute("aria-label") === "Expand");
    const hide = [...host.querySelectorAll("button")]
      .find(button => button.getAttribute("aria-label") === "Hide");
    assert.ok(follow && usePage && expand && hide);
    assert.equal(follow.getAttribute("title"), "Follow current branch");
    assert.ok(!follow.textContent.includes("Follow current branch"));
    assert.ok(!follow.textContent.includes("Manual inspection"));
    const title = pip.querySelector("span");
    const mode = pip.querySelector("small");
    assert.equal(title?.textContent, "Resource test 1");
    assert.equal(mode?.textContent, "Manual inspection");
    assert.notEqual(title?.parentElement, follow.parentElement);
  });
});

test("activating the page mounts a fresh pane and portals PiP into the dock", async () => {
  await withShell(async ({ host, page, session }) => {
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    assert.equal(host.querySelector("[data-pip='true']")?.getAttribute("data-pip-host"), "chat");
    const floatRect = { x: 40, y: 90, width: 400, height: 250 };
    useWebTabPip.getState().setRect(floatRect);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => { flushObservers(); });
    assert.equal(pipHostMode(page.id, session.id, useCenterTabs.getState()), "page");
    const dock = host.querySelector("[data-web-pip-dock]");
    const stage = host.querySelector("[data-pip-dock]");
    const pip = host.querySelector("[data-pip='true']");
    assert.ok(dock);
    assert.equal(stage.getAttribute("data-pip-dock"), "end");
    assert.equal(pip?.getAttribute("data-pip-host"), "page");
    assert.equal(dock.contains(pip), true);
    const latest = boundsCalls.at(-1);
    assert.equal(latest.id, page.id);
    const frame = { x: latest.x, y: latest.y, width: latest.width, height: latest.height };
    const dockBox = dock.getBoundingClientRect();
    const reserved = { x: dockBox.left, y: dockBox.top, width: dockBox.width, height: dockBox.height };
    assert.equal(overlap(frame, reserved), false);
    assert.ok(latest.width < stageWidth);
    assert.equal(useWebTabPip.getState().rect.x, floatRect.x);
    assert.equal(useWebTabPip.getState().rect.y, floatRect.y);
  });
});

test("narrow pane docks below the live page instead of hiding it", async () => {
  await withShell(async ({ host, page }) => {
    stageWidth = 500;
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => { flushObservers(); });
    const stage = host.querySelector("[data-pip-dock]");
    assert.equal(stage.getAttribute("data-pip-dock"), "bottom");
    const latest = boundsCalls.at(-1);
    assert.ok(latest.width > 0 && latest.height > 0);
    const dock = host.querySelector("[data-web-pip-dock]");
    const reserved = dock.getBoundingClientRect();
    assert.equal(overlap(
      { x: latest.x, y: latest.y, width: latest.width, height: latest.height },
      { x: reserved.left, y: reserved.top, width: reserved.width, height: reserved.height },
    ), false);
    assert.ok(host.querySelector("[data-pip='true']"));
  });
});

test("hide restores the native viewport and returning to chat keeps the float rect", async () => {
  await withShell(async ({ host, page, session }) => {
    const floatRect = { x: 52, y: 110, width: 380, height: 240 };
    useWebTabPip.getState().setRect(floatRect);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => { flushObservers(); });
    const docked = boundsCalls.at(-1);
    assert.ok(docked.width < stageWidth);
    await act(async () => {
      hideResourcePreview("a", null);
      useWebTabPip.getState().hide();
    });
    await act(async () => { flushObservers(); });
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    assert.equal(host.querySelector("[data-pip='true']"), null);
    const restored = boundsCalls.at(-1);
    assert.ok(restored.width >= docked.width);
    await act(async () => {
      useCenterTabs.getState().setActive(session.id);
      useWebTabPip.getState().show(page.id, session.id);
    });
    assert.equal(useWebTabPip.getState().rect.x, floatRect.x);
    assert.equal(useWebTabPip.getState().rect.y, floatRect.y);
    const chatPip = host.querySelector("[data-pip='true']");
    assert.equal(chatPip?.getAttribute("data-pip-host"), "chat");
    assert.equal(chatPip?.closest("[data-web-pip-dock]"), null);
  });
});

test("float to dock to float reapplies the stored snapshot without a capture tick", async () => {
  await withShell(async ({ host, page, session }) => {
    setSnapshot(page.id, "data:image/png,keep-frame");
    globalThis.webTabCapture = () => new Promise(() => {});
    const chatImg = host.querySelector("[data-pip='true'] img");
    assert.ok(chatImg);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    const dockPip = host.querySelector("[data-web-pip-dock] [data-pip='true']");
    const dockImg = dockPip?.querySelector("img");
    assert.ok(dockPip);
    assert.ok(dockImg);
    assert.ok((dockImg.getAttribute("src") || dockImg.src).includes("keep-frame"));
    assert.equal(dockImg.style.display, "block");
    assert.equal(getSnapshot(page.id), "data:image/png,keep-frame");
    await act(async () => {
      useCenterTabs.getState().setActive(session.id);
    });
    const chatPip = host.querySelector("[data-pip='true']");
    const restored = chatPip?.querySelector("img");
    assert.equal(chatPip?.getAttribute("data-pip-host"), "chat");
    assert.equal(host.querySelector("[data-web-pip-dock]"), null);
    assert.equal(chatPip?.closest("[data-web-pip-dock]"), null);
    assert.ok((restored?.getAttribute("src") || restored?.src || "").includes("keep-frame"));
    assert.equal(restored.style.display, "block");
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

test("expand while docked updates dock size and native leftover bounds", async () => {
  await withShell(async ({ host, page }) => {
    const floatRect = { x: 30, y: 80, width: 410, height: 230 };
    useWebTabPip.getState().setRect(floatRect);
    await act(async () => {
      useCenterTabs.getState().setActive(page.id);
    });
    await act(async () => { flushObservers(); });
    const collapsed = boundsCalls.at(-1);
    const stage = host.querySelector("[data-pip-dock]");
    assert.equal(stage.getAttribute("data-pip-dock"), "end");
    assert.equal(stage.style.getPropertyValue("--web-pip-dock-width"), "410px");
    const expand = [...host.querySelectorAll("button")]
      .find(button => button.getAttribute("aria-label") === "Expand");
    assert.ok(expand);
    await act(async () => { expand.click(); });
    await act(async () => { flushObservers(); });
    assert.equal(getPreviewPreference("a", null).expanded, true);
    assert.equal(useWebTabPip.getState().rect.width, 410);
    const expandedStage = host.querySelector("[data-pip-dock]");
    assert.equal(
      expandedStage.style.getPropertyValue("--web-pip-dock-width"),
      `${PIP_EXPANDED_WIDTH}px`,
    );
    assert.equal(
      expandedStage.style.getPropertyValue("--web-pip-dock-height"),
      `${PIP_EXPANDED_HEIGHT}px`,
    );
    const expandedBounds = boundsCalls.at(-1);
    assert.ok(
      expandedBounds.width !== collapsed.width || expandedBounds.height !== collapsed.height,
      "native leftover must change when the dock expands",
    );
    const collapse = [...host.querySelectorAll("button")]
      .find(button => button.getAttribute("aria-label") === "Collapse");
    assert.ok(collapse);
    await act(async () => { collapse.click(); });
    await act(async () => { flushObservers(); });
    assert.equal(getPreviewPreference("a", null).expanded, false);
    assert.equal(useWebTabPip.getState().rect.width, 410);
    assert.equal(useWebTabPip.getState().rect.x, 30);
    const restoredStage = host.querySelector("[data-pip-dock]");
    assert.equal(restoredStage.style.getPropertyValue("--web-pip-dock-width"), "410px");
  });
});
