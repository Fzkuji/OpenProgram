import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

const webPath = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = scratchDir(webPath, "control-bar-test");
const bundle = join(dir, "control-bar.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { BrowserControlBar } from "./components/center-tabs/browser/toolbar/browser-control-bar";
    export {
      resetBrowserControl,
      recordOperationCue,
      revealPendingApproval,
      showActionsEnabled,
    } from "./lib/browser/control/browser-control";
    export { useCenterTabs } from "./lib/tabs/center-tabs-store";
    export { useSessionStore } from "./lib/session-store";
    export {
      ingestBrowserResource,
      resetBrowserResources,
      setBrowserConnection,
    } from "./lib/chat/resources/session-resources";
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
  BrowserControlBar, resetBrowserControl,
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

test("an idle page keeps the control without a Pause; a closed page has none", async () => {
  // The Agent let the page go: the button stays so Show actions and the
  // Operation history are still at hand, but nothing offers a takeover.
  await mounted(host => {
    assert.ok(host.querySelector("[data-browser-control]"));
    assert.ok(labeledButton(host, "Show actions"));
    assert.ok(labeledButton(host, "Operation history"));
    assert.equal(labeledButton(host, "I will operate"), undefined);
    assert.equal(labeledButton(host, "Continue Agent"), undefined);
    assert.equal(labeledButton(host, "Retry pause"), undefined);
  }, { controlState: "idle" });

  await mounted(host => {
    assert.equal(host.querySelector("[data-browser-control]"), null);
    assert.equal(labeledButton(host, "I will operate"), undefined);
    assert.equal(labeledButton(host, "Continue Agent"), undefined);
  }, { controlState: "closed" });
});

// Pointer, keyboard, dismissal and focus behavior for the real renderer menu
// live in tests/e2e/web/test_browser_control_menu.py (Web and desktop bridge).

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

// ---- The browser's ⋮ menu (browser-controls.tsx BrowserMenu) -----------
// Built separately: its radix dropdown is swapped for one that renders every
// row, its router and tab store record what a row does, and CSS-module
// classes keep their names so the menu can find its pane.
const menuBundle = join(dir, "browser-menu.mjs");
await build({
  absWorkingDir: webPath,
  stdin: { contents: `
    export { BrowserMenu } from "./components/center-tabs/browser/toolbar/browser-controls";
    export { showBookmarksBar, setShowBookmarksBar, consumeBrowserImportRequest } from "./lib/browser/browser-prefs";
  `, resolveDir: webPath },
  bundle: true, format: "esm", jsx: "automatic", outfile: menuBundle,
  packages: "external", platform: "node", tsconfig: join(webPath, "tsconfig.json"),
  loader: { ".css": "empty" },
  plugins: [{ name: "browser-menu-services", setup(b) {
    const stub = (filter, path) => b.onResolve({ filter }, () => ({ path, namespace: "menu-stubs" }));
    stub(/\.module\.css$/, "css-module");
    stub(/^@\/components\/ui\/dropdown-menu$/, "dropdown");
    stub(/^next\/navigation$/, "next-nav");
    stub(/^@\/lib\/tabs\/center-tabs-store$/, "tabs");
    stub(/^@\/components\/solar-icons$/, "solar");
    stub(/^@\/lib\/desktop\/desktop-bridge$/, "bridge");
    const sources = {
      "css-module": "export default new Proxy({}, { get: (_, key) => typeof key === 'string' ? key : undefined });",
      dropdown: `
        import { createElement as h } from "react";
        const pass = ({ children }) => h("div", null, children);
        export const DropdownMenu = pass;
        export const DropdownMenuTrigger = ({ children }) => children;
        export const DropdownMenuContent = ({ children }) => h("div", { role: "menu" }, children);
        export const DropdownMenuItem = ({ children, onSelect, disabled }) =>
          h("div", { role: "menuitem", "aria-disabled": disabled ? "true" : undefined, onClick: () => { if (!disabled) onSelect?.(); } }, children);
        export const DropdownMenuSeparator = () => h("hr");
        export const DropdownMenuSub = pass;
        export const DropdownMenuSubTrigger = pass;
        export const DropdownMenuSubContent = pass;`,
      "next-nav": "export const useRouter = () => ({ push: (href) => globalThis.menuCalls.push(['push', href]), replace() {} }); export const usePathname = () => '/chat';",
      tabs: "const state = { openBuiltinTab: (page) => globalThis.menuCalls.push(['builtin', page]) }; export const useCenterTabs = Object.assign((selector) => selector(state), { getState: () => state });",
      bridge: "export const desktopBridge = () => globalThis.window?.openprogramDesktop ?? null;",
      solar: "import { createElement as h } from 'react'; export const SolarIcon = ({ name }) => h('i', { 'data-glyph': name });",
    };
    b.onLoad({ filter: /.*/, namespace: "menu-stubs" }, (args) => ({ contents: sources[args.path], resolveDir: webPath }));
  }}],
});
const { BrowserMenu, showBookmarksBar, setShowBookmarksBar, consumeBrowserImportRequest } = await import(pathToFileURL(menuBundle));

function browserMenuActions() {
  const used = [];
  const record = (name) => () => used.push(name);
  return {
    used,
    actions: {
      home: record("home"), forward: record("forward"), openExternal: record("openExternal"),
      find: record("find"), zoomIn: record("zoomIn"), zoomOut: record("zoomOut"), resetZoom: record("resetZoom"),
      print: record("print"), collapseToPip: record("collapseToPip"),
    },
  };
}

/** Render the menu inside a (26px wide, so narrow) browser pane. */
async function mountedMenu(props, check) {
  globalThis.menuCalls = [];
  const sessionValues = new Map();
  const local = new Map();
  globalThis.localStorage = window.localStorage = {
    getItem: (key) => key === "agentic_locale" ? "en" : local.get(key) ?? null,
    setItem: (key, value) => local.set(key, String(value)),
    removeItem: (key) => local.delete(key),
  };
  globalThis.sessionStorage = window.sessionStorage = {
    getItem: (key) => sessionValues.get(key) ?? null,
    setItem: (key, value) => sessionValues.set(key, String(value)),
    removeItem: (key) => sessionValues.delete(key),
  };
  window.innerWidth = globalThis.innerWidth = 1280;
  window.innerHeight = globalThis.innerHeight = 800;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement("div", { className: "webPane" }, createElement(BrowserMenu, props))));
    await check(host);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    delete window.openprogramDesktop;
  }
}

function desktopMenu() {
  const menu = { opened: [], listeners: new Set() };
  window.openprogramDesktop = {
    mainMenu: {
      open: (payload) => menu.opened.push(payload),
      onAction: (cb) => { menu.listeners.add(cb); return () => menu.listeners.delete(cb); },
      onClosed: () => () => {},
      close() {},
    },
  };
  menu.fire = (id) => act(async () => { for (const cb of menu.listeners) cb(id); });
  return menu;
}

test("browser menu: in the desktop app it opens the overlay below the trigger, right-aligned, every row with its icon", async () => {
  const menu = desktopMenu();
  const { actions } = browserMenuActions();
  await mountedMenu({ ownerId: "pane-a", actions }, async (host) => {
    await act(async () => labeledButton(host, "Browser menu").click());
    const [payload] = menu.opened;
    assert.deepEqual(
      { right: payload.anchor.right, y: payload.anchor.y, align: payload.anchor.align },
      { right: 34, y: 46, align: "end" },
    );
    assert.ok(payload.items.every((item) => item.id.startsWith("browsermenu:pane-a:")), "rows are owned by this pane");
    assert.ok(payload.items.every((item) => typeof item.glyph === "string" && item.glyph), "every row names a glyph");
    const ids = payload.items.map((item) => item.id.slice("browsermenu:pane-a:".length));
    assert.deepEqual(ids, [
      "new-tab", "collapse-to-pip", "home", "forward", "open-external",
      "find", "zoom-in", "zoom-out", "reset-zoom", "print",
      "bookmarks", "history", "downloads", "toggle-bookmarks-bar",
      "import", "clear-data", "settings",
    ]);
    const byId = Object.fromEntries(payload.items.map((item, index) => [ids[index], item]));
    for (const id of ["find", "bookmarks", "import", "settings"]) assert.equal(byId[id].separatorBefore, true, `${id} starts a group`);
    assert.equal(byId.import.disabled, true, "import needs the desktop import bridge");
    assert.equal(byId["toggle-bookmarks-bar"].checked, true);
    assert.equal(byId.find.disabled, false);
  });
});

test("browser menu: page actions the pane cannot do are disabled, and Home or Open in browser follow their flags", async () => {
  const menu = desktopMenu();
  await mountedMenu({ ownerId: "pane-a", actions: { home() {}, openExternal() {}, forward() {} }, canGoHome: false, canOpenExternal: false, canGoForward: false }, async (host) => {
    await act(async () => labeledButton(host, "Browser menu").click());
    const byId = Object.fromEntries(menu.opened[0].items.map((item) => [item.id.split(":").at(-1), item]));
    for (const id of ["home", "forward", "open-external", "find", "zoom-in", "zoom-out", "reset-zoom", "print"]) {
      assert.equal(byId[id].disabled, true, `${id} is disabled`);
    }
    assert.equal(byId["collapse-to-pip"], undefined, "no floating window without the action");
  });
});

test("browser menu: an overlay row runs its action only for the pane that owns the menu", async () => {
  const menu = desktopMenu();
  const { actions, used } = browserMenuActions();
  await mountedMenu({ ownerId: "pane-a", actions }, async () => {
    for (const id of ["find", "zoom-in", "zoom-out", "reset-zoom", "print", "collapse-to-pip", "home", "forward", "open-external"]) {
      await menu.fire(`browsermenu:pane-a:${id}`);
    }
    assert.deepEqual(used, ["find", "zoomIn", "zoomOut", "resetZoom", "print", "collapseToPip", "home", "forward", "openExternal"]);
    await menu.fire("browsermenu:pane-b:find");
    assert.equal(used.length, 9, "another pane's action is ignored");

    for (const id of ["new-tab", "bookmarks", "history", "downloads", "settings", "clear-data"]) await menu.fire(`browsermenu:pane-a:${id}`);
    assert.deepEqual(globalThis.menuCalls, [
      ["builtin", "browser"], ["builtin", "bookmarks"], ["builtin", "history"], ["builtin", "downloads"],
      ["push", "/settings/browser"], ["push", "/settings/browser#clear-data"],
    ]);

    assert.equal(showBookmarksBar(), true);
    await menu.fire("browsermenu:pane-a:toggle-bookmarks-bar");
    assert.equal(showBookmarksBar(), false);

    globalThis.menuCalls = [];
    await menu.fire("browsermenu:pane-a:import");
    assert.deepEqual(globalThis.menuCalls, [["builtin", "browser"]], "import opens the browser home");
    assert.equal(consumeBrowserImportRequest(), true, "and asks it to show the import prompt");
  });
});

test("browser menu: in the Web build the rows are menu items with icons, the bookmarks bar row carries its check", async () => {
  const { actions, used } = browserMenuActions();
  await mountedMenu({ ownerId: "pane-a", actions }, async (host) => {
    setShowBookmarksBar(true);
    const rows = [...host.querySelectorAll("[role=menuitem]")];
    assert.equal(rows.length, 16, "every row but Import, which needs the desktop app");
    assert.ok(rows.every((row) => row.querySelector("[data-glyph]")), "every row has its icon");
    const row = (label) => rows.find((item) => item.textContent === label);
    assert.equal(row("Import browser data"), undefined);
    assert.equal(row("Show bookmarks bar").querySelector("svg") !== null, true, "the visible bar is checked");
    await act(async () => row("Find in page").click());
    await act(async () => row("History").click());
    assert.deepEqual(used, ["find"]);
    assert.deepEqual(globalThis.menuCalls, [["builtin", "history"]]);
  });
});
