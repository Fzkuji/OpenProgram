import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webPath = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const dir = await mkdtemp(join(webPath, ".control-bar-test-"));
after(() => rm(dir, { recursive: true, force: true }));
const bundle = join(dir, "control-bar.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { BrowserControlBar } from "./components/center-tabs/browser-control-bar";
    export {
      resetBrowserControl,
      recordOperationCue,
      revealPendingApproval,
      showActionsEnabled,
    } from "./lib/state/browser-control";
    export { useCenterTabs } from "./lib/state/center-tabs-store";
    export { useSessionStore } from "./lib/session-store";
    export {
      ingestBrowserResource,
      resetBrowserResources,
      setBrowserConnection,
    } from "./lib/state/session-resources";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: bundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "control-bar-services", setup(b) {
    b.onResolve({ filter: /net\/fetch-client/ }, () => ({ path: "fetch-client", namespace: "test-services" }));
    b.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "next-nav", namespace: "test-services" }));
    b.onLoad({ filter: /.*/, namespace: "test-services" }, (args) => ({ contents: args.path === "next-nav"
      ? "export const useRouter = () => ({ push() {}, replace() {} }); export const usePathname = () => '/chat';"
      : `
      export async function jsonFetch(url, init) {
        const body = JSON.parse(init.body || "{}");
        globalThis.controlPosts.push({ url: String(url), body });
        if (typeof globalThis.controlReply === "function") return globalThis.controlReply({ url, body });
        return pageRow(body.action === "resume" ? "active" : "paused", body.action === "resume" ? 3 : 2);
      }
      function pageRow(control_state, sequence) {
        return {
          id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
          tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
          status: "open", source: "browser", control_state, generation: 1, sequence,
          execution_id: "exec-a",
        };
      }
    ` }));
  }}],
});

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.Element = window.Element;
globalThis.HTMLElement = window.HTMLElement;
globalThis.Node = window.Node;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
globalThis.PointerEvent = window.PointerEvent || window.MouseEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = {
  getItem(key) { return key === "agentic_locale" ? "en" : null; },
  setItem() {},
  removeItem() {},
};
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
Object.defineProperty(window, "location", { value: { pathname: "/chat" } });
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.HTMLElement.prototype.hasPointerCapture = () => false;
window.HTMLElement.prototype.setPointerCapture = () => {};
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.scrollIntoView = () => {};
window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  return new DOMRect(8, 16, 26, 26);
};
window.HTMLElement.prototype.focus = function focus() {
  window.__focused = this;
};
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  BrowserControlBar, resetBrowserControl, recordOperationCue, revealPendingApproval,
  showActionsEnabled, ingestBrowserResource, resetBrowserResources, setBrowserConnection,
  useCenterTabs, useSessionStore,
} = await import(pathToFileURL(bundle));

const LONG_LABELS = [
  "Show actions",
  "Operation history",
  "I will operate",
  "Continue Agent",
  "Pausing…",
  "Retry pause",
  "Review request",
];

function pageRow(control_state = "active", sequence = 1) {
  return {
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
    status: "open", source: "browser", control_state, generation: 1, sequence,
    execution_id: "exec-a",
  };
}

function controlResource() {
  return {
    id: "assoc-a",
    resourceId: "page-a",
    tabId: "w:a",
    conversationSessionId: "a",
    generation: 1,
    controlState: "active",
  };
}

function tap(el) {
  const down = new window.Event("pointerdown", { bubbles: true, cancelable: true });
  Object.defineProperties(down, { pointerId: { value: 1 }, button: { value: 0 }, clientX: { value: 1 }, clientY: { value: 1 } });
  const up = new window.Event("pointerup", { bubbles: true, cancelable: true });
  Object.defineProperties(up, { pointerId: { value: 1 }, button: { value: 0 }, clientX: { value: 1 }, clientY: { value: 1 } });
  el.dispatchEvent(down);
  el.dispatchEvent(up);
}

function clickButton(button, { detail = 0, clientX = 0, clientY = 0 } = {}) {
  const event = new window.Event("click", { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    detail: { value: detail },
    clientX: { value: clientX },
    clientY: { value: clientY },
  });
  button.dispatchEvent(event);
}

function labeledButton(host, label) {
  return [...host.querySelectorAll("button")].find(button =>
    button.getAttribute("aria-label") === label || button.getAttribute("title") === label);
}

function assertIconButton(button, label) {
  assert.ok(button, `missing button for ${label}`);
  assert.equal(button.getAttribute("aria-label"), label);
  assert.equal(button.getAttribute("title"), label);
  assert.ok(button.querySelector("svg"), `${label} must render an icon, not wrapping text`);
  for (const longLabel of LONG_LABELS) {
    assert.equal(button.textContent.includes(longLabel), false, `${label} must not wrap ${JSON.stringify(longLabel)} as button text`);
  }
}

function cueClick() {
  recordOperationCue({
    resourceId: "page-a",
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

async function mounted(check, { controlState = "active", connected = true } = {}) {
  resetBrowserControl();
  resetBrowserResources();
  setBrowserConnection(connected);
  ingestBrowserResource(pageRow(controlState), "a");
  globalThis.controlPosts = [];
  globalThis.controlReply = undefined;
  window.__focused = null;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(BrowserControlBar, {
      resource: { ...controlResource(), controlState },
      compact: true,
    })));
    const float = host.querySelector("[data-browser-control='float']");
    if (float?.getAttribute("data-collapsed") === "true") {
      await act(async () => tap(float));
    }
    await check(host);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    resetBrowserControl();
    resetBrowserResources();
    delete window.openprogramDesktop;
  }
}

test("compact control buttons keep takeover labels on title and aria-label instead of wrapping text", async () => {
  await mounted(host => {
    assert.equal(host.firstElementChild?.getAttribute("data-compact"), "true");
    assertIconButton(labeledButton(host, "Show actions"), "Show actions");
    assertIconButton(labeledButton(host, "Operation history"), "Operation history");
    assert.equal(labeledButton(host, "I will operate"), undefined);
    assert.equal(labeledButton(host, "Show actions").getAttribute("aria-pressed"), "true");
  });
});

test("show actions click toggles pressed state without changing pause", async () => {
  await mounted(async host => {
    const show = labeledButton(host, "Show actions");
    assert.equal(showActionsEnabled(), true);
    await act(async () => show.click());
    assert.equal(showActionsEnabled(), false);
    assert.equal(show.getAttribute("aria-pressed"), "false");
    await act(async () => show.click());
    assert.equal(showActionsEnabled(), true);
    assert.equal(show.getAttribute("aria-pressed"), "true");
    assert.equal(globalThis.controlPosts.length, 0);
    assert.equal(labeledButton(host, "I will operate"), undefined);
  });
});

test("waiting shows confirmation copy and review request instead of continue", async () => {
  await mounted(async host => {
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [] });
    useSessionStore.setState({
      conversations: { a: { id: "a", title: "Owner" } },
      pendingDecisions: [{
        id: "wait_approval", sessionId: "a", executionId: "exec-a",
        waitGeneration: 1, expectedVersion: 1, kind: "approval",
        prompt: "Allow execute_code?", options: [], multi: false, allow_custom: false,
        tool: "execute_code",
      }],
      composerFocusTick: 0,
    });
    assert.equal(host.textContent.includes("Needs your confirmation"), true);
    assert.equal(host.textContent.includes("Paused"), false);
    assert.equal(labeledButton(host, "Continue Agent"), undefined);
    const review = labeledButton(host, "Review request");
    assertIconButton(review, "Review request");
    assert.equal(review.disabled, false);
    await act(async () => {
      review.click();
      await Promise.resolve();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(globalThis.controlPosts.length, 0);
    const tabs = useCenterTabs.getState();
    const active = tabs.tabs.find(tab => tab.id === tabs.activeId);
    assert.equal(active?.kind, "session");
    assert.equal(active?.sessionId, "a");
    assert.equal(useSessionStore.getState().currentSessionId, "a");
    assert.ok(useSessionStore.getState().composerFocusTick > 0);
    assert.equal(useSessionStore.getState().pendingDecisions[0].id, "wait_approval");
  }, { controlState: "waiting" });
});

test("review request reuses the existing owner session tab", async () => {
  await mounted(async host => {
    useCenterTabs.setState({ tabs: [], activeId: null, groups: [] });
    useSessionStore.setState({
      conversations: { a: { id: "a", title: "Owner" } },
      pendingDecisions: [{
        id: "wait_approval", sessionId: "a", executionId: "exec-a",
        waitGeneration: 1, expectedVersion: 1, kind: "approval",
        prompt: "Allow execute_code?", options: [], multi: false, allow_custom: false,
        tool: "execute_code",
      }],
      composerFocusTick: 0,
    });
    useCenterTabs.getState().openSessionTab("a", "Owner");
    useCenterTabs.getState().openWebTab("https://a.test");
    const before = useCenterTabs.getState().tabs.length;
    const ownerId = useCenterTabs.getState().tabs.find(tab => tab.kind === "session" && tab.sessionId === "a")?.id;
    assert.ok(ownerId);
    assert.notEqual(useCenterTabs.getState().activeId, ownerId);
    const review = labeledButton(host, "Review request");
    await act(async () => {
      review.click();
      await Promise.resolve();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    const tabs = useCenterTabs.getState();
    assert.equal(tabs.tabs.length, before);
    assert.equal(tabs.tabs.filter(tab => tab.kind === "session" && tab.sessionId === "a").length, 1);
    assert.equal(tabs.activeId, ownerId);
    assert.equal(globalThis.controlPosts.length, 0);
  }, { controlState: "waiting" });
});

test("idle and closed do not render an enabled Pause", async () => {
  await mounted(host => {
    assert.equal(host.querySelector("[data-browser-control]"), null);
    assert.equal(labeledButton(host, "I will operate"), undefined);
    assert.equal(labeledButton(host, "Continue Agent"), undefined);
    assert.equal(labeledButton(host, "Retry pause"), undefined);
  }, { controlState: "idle" });

  await mounted(host => {
    assert.equal(labeledButton(host, "I will operate"), undefined);
    assert.equal(labeledButton(host, "Continue Agent"), undefined);
  }, { controlState: "closed" });
});

test("native history uses context menu popup and does not occlude the page", async () => {
  const menu = installNativeMenu();
  await mounted(async host => {
    await act(async () => { cueClick(); });
    const history = labeledButton(host, "Operation history");
    await act(async () => clickButton(history, { detail: 1, clientX: 12, clientY: 34 }));
    assert.equal(menu.popups.length, 1);
    assert.equal(menu.popups[0].x, 12);
    assert.equal(menu.popups[0].y, 34);
    assert.equal(history.getAttribute("aria-expanded"), "true");
    assert.deepEqual(menu.popups[0].items.map(item => ({ id: item.id, label: item.label, disabled: item.disabled })), [
      { id: "op-1", label: "click · acknowledged", disabled: true },
    ]);
    assert.equal(document.querySelector("[data-native-view-occluder]"), null);
    assert.equal(document.querySelector('[role="menu"]'), null);
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.body.textContent.includes("click · acknowledged"), false);
  });
  assert.equal(menu.closed.length, 1);
  assert.equal(menu.closed[0], menu.popups[0].requestId);
});

test("native history ignores a stale dismissal from an earlier popup", async () => {
  const menu = installNativeMenu();
  await mounted(async host => {
    await act(async () => { cueClick(); });
    const history = labeledButton(host, "Operation history");
    await act(async () => history.click());
    const first = menu.popups[0];
    await act(async () => history.click());
    assert.deepEqual(menu.closed, [first.requestId]);
    await act(async () => history.click());
    const second = menu.popups[1];
    assert.ok(second);
    assert.notEqual(second.requestId, first.requestId);
    await act(async () => { menu.resolvers[0](null); });
    assert.equal(history.getAttribute("aria-expanded"), "true");
    assert.equal(document.querySelector('[role="menu"]'), null);
    await act(async () => { menu.resolvers[1](null); });
    assert.equal(history.getAttribute("aria-expanded"), "false");
    assert.equal(window.__focused, history);
  });
});

test("history keyboard activation opens the same native menu", async () => {
  const menu = installNativeMenu();
  await mounted(async host => {
    await act(async () => { cueClick(); });
    const history = labeledButton(host, "Operation history");
    await act(async () => clickButton(history, { detail: 0 }));
    assert.equal(menu.popups.length, 1);
    assert.equal(menu.popups[0].x, 8);
    assert.equal(menu.popups[0].y, 42);
    assert.equal(menu.popups[0].items[0].label, "click · acknowledged");
    assert.equal(menu.popups[0].items[0].disabled, true);
    assert.equal(document.querySelector('[role="menu"]'), null);
  });
});

test("child pointer and keyboard do not fold; cancel does not toggle; expanded role is group", async () => {
  await mounted(async host => {
    const float = host.querySelector("[data-browser-control='float']");
    assert.ok(float);
    assert.equal(float.getAttribute("data-collapsed"), "false");
    assert.equal(float.getAttribute("role"), "group");
    const pause = labeledButton(host, "Show actions");
    assert.ok(pause);
    await act(async () => {
      const down = new window.Event("pointerdown", { bubbles: true, cancelable: true });
      Object.defineProperties(down, { pointerId: { value: 2 }, button: { value: 0 }, clientX: { value: 4 }, clientY: { value: 4 } });
      pause.dispatchEvent(down);
      const up = new window.Event("pointerup", { bubbles: true, cancelable: true });
      Object.defineProperties(up, { pointerId: { value: 2 }, button: { value: 0 }, clientX: { value: 4 }, clientY: { value: 4 } });
      pause.dispatchEvent(up);
    });
    assert.equal(float.getAttribute("data-collapsed"), "false");
    assert.equal(labeledButton(host, "I will operate"), undefined);
    await act(async () => {
      const key = new window.Event("keydown", { bubbles: true, cancelable: true });
      Object.defineProperty(key, "key", { value: "Enter" });
      pause.dispatchEvent(key);
    });
    assert.equal(float.getAttribute("data-collapsed"), "false");
    await act(async () => {
      const down = new window.Event("pointerdown", { bubbles: true, cancelable: true });
      Object.defineProperties(down, { pointerId: { value: 3 }, button: { value: 0 }, clientX: { value: 2 }, clientY: { value: 2 } });
      float.dispatchEvent(down);
      const cancel = new window.Event("pointercancel", { bubbles: true, cancelable: true });
      Object.defineProperties(cancel, { pointerId: { value: 3 }, button: { value: 0 } });
      float.dispatchEvent(cancel);
    });
    assert.equal(float.getAttribute("data-collapsed"), "false");
    assert.equal(labeledButton(host, "I will operate"), undefined);
  });
});
