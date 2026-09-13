import assert from "node:assert/strict";
import test, { after } from "node:test";
import { parseHTML } from "linkedom";

const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = {
  getItem(key) { return key === "agentic_locale" ? "en" : null; },
  setItem() {},
  removeItem() {},
};
Object.defineProperty(window, "location", { value: { pathname: "/chat" } });
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  displayedControlState,
  markScopeYielding,
  resetBrowserControl,
  useBrowserControlStore,
} = await import("../../lib/browser/browser-control.ts");
const {
  ingestBrowserResource,
  resetBrowserResources,
  setBrowserConnection,
  useBrowserResourceStore,
} = await import("../../lib/chat/session-resources.ts");

after(() => {
  resetBrowserControl();
  resetBrowserResources();
});

function pausedResource() {
  return {
    id: "page-a",
    resourceId: "page-a",
    conversationSessionId: "a",
    generation: 1,
    controlState: "paused",
  };
}

function PausedProbe({ resource, onRender }) {
  onRender();
  useBrowserControlStore(s => s.pending);
  useBrowserResourceStore(s => s.connected);
  const state = displayedControlState(resource);
  return createElement("div", { "data-state": state }, state);
}

test("connected paused control can render and read again without a store update loop", async () => {
  resetBrowserControl();
  resetBrowserResources();
  setBrowserConnection(true);
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
    status: "open", source: "browser", control_state: "paused", generation: 1, sequence: 1,
    execution_id: "exec-a",
  }, "a");

  let renders = 0;
  let updates = 0;
  const unsub = useBrowserControlStore.subscribe(() => { updates += 1; });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const resource = pausedResource();
  try {
    await act(async () => root.render(createElement(PausedProbe, {
      resource,
      onRender() {
        renders += 1;
        if (renders > 25) throw new Error("paused displayedControlState update loop");
      },
    })));
    assert.equal(host.firstElementChild?.getAttribute("data-state"), "paused");
    assert.equal(updates, 0);
    const committed = renders;
    await act(async () => root.render(createElement(PausedProbe, {
      resource,
      onRender() {
        renders += 1;
        if (renders > 25) throw new Error("paused displayedControlState update loop");
      },
    })));
    assert.equal(displayedControlState(resource), "paused");
    assert.equal(displayedControlState(resource), "paused");
    assert.equal(host.firstElementChild?.getAttribute("data-state"), "paused");
    assert.equal(updates, 0);
    assert.ok(renders >= committed);
    assert.ok(renders < 25);
  } finally {
    unsub();
    await act(async () => root.unmount());
    host.remove();
  }
});

test("yielding lease survives paused render until ingest acknowledgement", async () => {
  resetBrowserControl();
  resetBrowserResources();
  setBrowserConnection(true);
  markScopeYielding({
    id: "assoc-a",
    resourceId: "page-a",
    conversationSessionId: "a",
    generation: 1,
    controlState: "active",
  });
  assert.ok(useBrowserControlStore.getState().pending["page-a:1"]);

  let renders = 0;
  let updates = 0;
  const unsub = useBrowserControlStore.subscribe(() => { updates += 1; });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const resource = pausedResource();
  try {
    await act(async () => root.render(createElement(PausedProbe, {
      resource,
      onRender() {
        renders += 1;
        if (renders > 25) throw new Error("paused displayedControlState update loop");
      },
    })));
    assert.equal(host.firstElementChild?.getAttribute("data-state"), "paused");
    assert.equal(updates, 0);
    assert.ok(useBrowserControlStore.getState().pending["page-a:1"]);
    await act(async () => {
      ingestBrowserResource({
        id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
        tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
        status: "open", source: "browser", control_state: "paused", generation: 1, sequence: 2,
        execution_id: "exec-a",
      }, "a");
    });
    assert.equal(useBrowserControlStore.getState().pending["page-a:1"], undefined);
    assert.equal(host.firstElementChild?.getAttribute("data-state"), "paused");
    assert.ok(updates >= 1);
    assert.ok(renders < 25);
  } finally {
    unsub();
    await act(async () => root.unmount());
    host.remove();
  }
});
