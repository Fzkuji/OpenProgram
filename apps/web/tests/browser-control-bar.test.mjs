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
      showActionsEnabled,
    } from "./lib/state/browser-control";
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
    b.onLoad({ filter: /.*/, namespace: "test-services" }, () => ({ contents: `
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
  BrowserControlBar, resetBrowserControl, recordOperationCue, showActionsEnabled,
  ingestBrowserResource, resetBrowserResources, setBrowserConnection,
} = await import(pathToFileURL(bundle));

const LONG_LABELS = [
  "Show actions",
  "Operation history",
  "Pause Agent and take over",
  "Resume Agent",
  "Yielding",
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
    assertIconButton(labeledButton(host, "Pause Agent and take over"), "Pause Agent and take over");
    const pause = labeledButton(host, "Pause Agent and take over");
    assert.equal(pause.disabled, false);
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
    assert.equal(labeledButton(host, "Pause Agent and take over").disabled, false);
  });
});

test("pause click posts pause, then resume posts after acknowledgement", async () => {
  await mounted(async host => {
    let settlePause;
    globalThis.controlReply = ({ body }) => {
      if (body.action === "pause") return new Promise(resolve => { settlePause = resolve; });
      return pageRow("active", 3);
    };
    const pause = labeledButton(host, "Pause Agent and take over");
    await act(async () => {
      pause.click();
      await Promise.resolve();
    });
    assert.equal(globalThis.controlPosts.length, 1);
    assert.equal(globalThis.controlPosts[0].body.action, "pause");
    assert.match(globalThis.controlPosts[0].url, /\/api\/session\/a\/resources\/page-a\/control$/);
    const yielding = labeledButton(host, "Yielding");
    assertIconButton(yielding, "Yielding");
    assert.equal(yielding.disabled, true);
    assert.equal(labeledButton(host, "Resume Agent"), undefined);
    await act(async () => { settlePause(pageRow("paused", 2)); });
    const resume = labeledButton(host, "Resume Agent");
    assertIconButton(resume, "Resume Agent");
    assert.equal(resume.disabled, false);
    await act(async () => {
      resume.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(globalThis.controlPosts.at(-1).body.action, "resume");
  });
});

test("yielding unknown and disconnect disable takeover", async () => {
  await mounted(host => {
    const pause = labeledButton(host, "Yielding");
    assertIconButton(pause, "Yielding");
    assert.equal(pause.disabled, true);
  }, { controlState: "yielding" });

  await mounted(host => {
    const pause = labeledButton(host, "Pause Agent and take over");
    assert.equal(pause.disabled, true);
  }, { controlState: "unknown" });

  await mounted(host => {
    const pause = labeledButton(host, "Pause Agent and take over");
    assert.equal(pause.disabled, true);
  }, { connected: false });
});

test("resume stays off after disconnect from a paused page", async () => {
  await mounted(async host => {
    const resume = labeledButton(host, "Resume Agent");
    assertIconButton(resume, "Resume Agent");
    assert.equal(resume.disabled, false);
    await act(async () => {
      setBrowserConnection(false);
      ingestBrowserResource(pageRow("paused", 2), "a");
    });
    const disconnected = labeledButton(host, "Pause Agent and take over") || labeledButton(host, "Resume Agent");
    assert.ok(disconnected);
    assert.equal(disconnected.disabled, true);
  }, { controlState: "paused" });
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
