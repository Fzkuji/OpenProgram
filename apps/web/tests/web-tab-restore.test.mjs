import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../lib/session-store/index.ts", import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const storage = new Map();
globalThis.window = {
  fetch: globalThis.fetch,
  addEventListener() {},
  dispatchEvent() {},
  location: { pathname: "/s/parent", hash: "" },
};
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const {
  ingestBrowserResource,
  resetBrowserResources,
  applyResourceSnapshot,
  selectResourcePreview,
  hideResourcePreview,
  showResourcePreview,
  getPreviewPreference,
  followPreviewBinding,
} = await import("../lib/state/session-resources.ts");
const {
  reconcileRestoredWebUrl,
  restoreRetainedWebViews,
  retryRestoreWebTab,
} = await import("../lib/desktop-bridge.ts");

test("native-confirmed tab URL wins over older SQLite display target", () => {
  assert.equal(
    reconcileRestoredWebUrl(
      "",
      "https://example.test/b",
      "https://example.test/c",
      true,
    ),
    "https://example.test/c",
  );
  assert.equal(
    reconcileRestoredWebUrl(
      "",
      "https://arxiv.org/abs/1",
      "https://www.google.com/search?q=arxiv",
      false,
    ),
    "https://arxiv.org/abs/1",
  );
});

test("query and hash survive display-only resource targets", () => {
  assert.equal(
    reconcileRestoredWebUrl(
      "",
      "https://example.test/search",
      "https://example.test/search?q=open#hits",
    ),
    "https://example.test/search?q=open#hits",
  );
  assert.equal(
    reconcileRestoredWebUrl(
      "",
      "https://arxiv.org/abs/1",
      "https://www.google.com/search?q=arxiv",
    ),
    "https://arxiv.org/abs/1",
  );
  assert.equal(
    reconcileRestoredWebUrl(
      "https://arxiv.org/abs/1?utm=native#pdf",
      "https://arxiv.org/abs/1",
      "https://www.google.com/search?q=arxiv",
    ),
    "https://arxiv.org/abs/1?utm=native#pdf",
  );
});

test("hidden restore waits for snapshot and does not reload a live native URL", async () => {
  resetBrowserResources();
  useCenterTabs.setState({
    tabs: [{
      id: "w:hidden",
      kind: "web",
      title: "Google",
      url: "https://www.google.com/search?q=arxiv",
      agentSessionId: "parent",
    }],
    activeId: "s:parent",
    groups: [],
    splitWebTabId: null,
  });
  const ensured = [];
  const inspected = [];
  const bridge = {
    windowId: "main",
    webTab: {
      ensure(id, url) { ensured.push([id, url]); },
      inspect: async (id) => {
        inspected.push(id);
        return null;
      },
      onState: () => () => {},
    },
  };
  const recover = [];
  globalThis.fetch = async (url) => {
    recover.push(String(url));
    ingestBrowserResource({
      id: "browser:page:old:parent",
      resource_id: "page:old",
      session_id: "parent",
      conversation_session_id: "parent",
      tab_id: "w:hidden",
      window_id: "main",
      kind: "web",
      title: "arXiv",
      target: "https://arxiv.org/abs/1",
      status: "unknown",
      source: "browser",
      control_state: "idle",
    }, "parent", { origin: "snapshot" });
    return {
      ok: true,
      text: async () => JSON.stringify({
        items: [{
          id: "browser:page:old:parent",
          resource_id: "page:old",
          session_id: "parent",
          conversation_session_id: "parent",
          tab_id: "w:hidden",
          window_id: "main",
          kind: "web",
          title: "arXiv",
          target: "https://arxiv.org/abs/1",
          status: "unknown",
          source: "browser",
          control_state: "idle",
        }],
      }),
    };
  };
  await restoreRetainedWebViews(bridge);
  assert.equal(recover.length > 0, true, "empty resource rows must wait for session snapshot");
  assert.deepEqual(ensured, [["w:hidden", "https://arxiv.org/abs/1"]]);
  assert.equal(useCenterTabs.getState().tabs[0].url, "https://arxiv.org/abs/1");
  assert.equal(useCenterTabs.getState().tabs[0].title, "arXiv");

  ensured.length = 0;
  inspected.length = 0;
  bridge.webTab.inspect = async (id) => {
    inspected.push(id);
    return { target_id: "t1", url: "https://arxiv.org/abs/1?utm=live#pdf", title: "arXiv" };
  };
  await restoreRetainedWebViews(bridge);
  assert.deepEqual(inspected, ["w:hidden"]);
  assert.equal(ensured.length, 0, "already-live native Page must not be ensured again");
  assert.equal(
    useCenterTabs.getState().tabs[0].url,
    "https://arxiv.org/abs/1?utm=live#pdf",
  );
});

test("persisted native navigation C beats stale backend B after restart", async () => {
  resetBrowserResources();
  ingestBrowserResource({
    id: "browser:page:old:parent",
    resource_id: "page:old",
    session_id: "parent",
    conversation_session_id: "parent",
    tab_id: "w:nav",
    window_id: "main",
    kind: "web",
    title: "B",
    target: "https://example.test/b",
    status: "unknown",
    source: "browser",
    control_state: "idle",
  }, "parent", { origin: "snapshot" });
  useCenterTabs.setState({
    tabs: [{
      id: "w:nav",
      kind: "web",
      title: "C",
      url: "https://example.test/c",
      urlNativeAt: 1_700_000_000_000,
      agentSessionId: "parent",
    }],
    activeId: "s:parent",
    groups: [],
    splitWebTabId: null,
  });
  const ensured = [];
  const bridge = {
    windowId: "main",
    webTab: {
      ensure(id, url) { ensured.push([id, url]); },
      inspect: async () => null,
      onState: () => () => {},
    },
  };
  await restoreRetainedWebViews(bridge);
  assert.deepEqual(ensured, [["w:nav", "https://example.test/c"]]);
});

test("manual pin and hidden pref keep association id after successor snapshot", () => {
  resetBrowserResources();
  const assocId = "browser:page:old:unassigned";
  const predecessor = {
    id: assocId,
    resource_id: "page:old",
    session_id: "parent",
    conversation_session_id: "parent",
    tab_id: "w:a",
    window_id: "main",
    kind: "web",
    title: "Same",
    target: "https://example.test/",
    status: "open",
    source: "browser",
    control_state: "idle",
    generation: 40,
    sequence: 2,
  };
  ingestBrowserResource(predecessor, "parent", { origin: "snapshot" });
  selectResourcePreview("parent", null, assocId);
  hideResourcePreview("parent", null);
  const tabs = [
    { id: "s:parent", kind: "session", sessionId: "parent" },
    { id: "w:a", kind: "web" },
    { id: "w:b", kind: "web" },
  ];
  assert.equal(followPreviewBinding("parent", null, tabs), null);
  assert.equal(getPreviewPreference("parent", null).hidden, true);
  assert.equal(getPreviewPreference("parent", null).targetId, assocId);
  applyResourceSnapshot([{
    ...predecessor,
    resource_id: "page:new",
    generation: 41,
    sequence: 3,
    status: "open",
    control_state: "idle",
  }, {
    id: "browser:other",
    resource_id: "page:other",
    session_id: "parent",
    conversation_session_id: "parent",
    tab_id: "w:b",
    window_id: "main",
    kind: "web",
    title: "Same",
    target: "https://example.test/",
    status: "open",
    source: "browser",
    control_state: "idle",
    generation: 1,
    sequence: 1,
  }], "parent");
  assert.equal(getPreviewPreference("parent", null).hidden, true);
  assert.equal(getPreviewPreference("parent", null).targetId, assocId);
  showResourcePreview("parent", null);
  assert.deepEqual(followPreviewBinding("parent", null, tabs), {
    tabId: "w:a",
    ownerTabId: "s:parent",
  });
});

test("preview retry recovers a failed row without touching closed tabs", async () => {
  resetBrowserResources();
  ingestBrowserResource({
    id: "browser:page:fail:parent",
    resource_id: "page:fail",
    session_id: "parent",
    conversation_session_id: "parent",
    tab_id: "w:fail",
    window_id: "main",
    kind: "web",
    title: "Retry",
    target: "https://example.test/retry",
    status: "restore_failed",
    source: "browser",
    control_state: "idle",
  }, "parent", { origin: "snapshot" });
  ingestBrowserResource({
    id: "browser:page:closed:parent",
    resource_id: "page:closed",
    session_id: "parent",
    conversation_session_id: "parent",
    tab_id: "w:closed",
    window_id: "main",
    kind: "web",
    title: "Closed",
    target: "https://example.test/closed",
    status: "closed",
    source: "browser",
    control_state: "closed",
  }, "parent", { origin: "snapshot" });
  useCenterTabs.setState({
    tabs: [{
      id: "w:fail",
      kind: "web",
      title: "Retry",
      url: "https://example.test/retry",
      agentSessionId: "parent",
    }, {
      id: "w:closed",
      kind: "web",
      title: "Closed",
      url: "https://example.test/closed",
      agentSessionId: "parent",
    }],
    activeId: "s:parent",
    groups: [],
    splitWebTabId: null,
  });
  const ensured = [];
  const bridge = {
    windowId: "main",
    webTab: {
      ensure(id, url) { ensured.push([id, url]); },
      inspect: async () => null,
      onState: () => () => {},
    },
  };
  await retryRestoreWebTab(bridge, "w:closed");
  assert.deepEqual(ensured, []);
  await retryRestoreWebTab(bridge, "w:fail");
  assert.ok(ensured.some((item) => item[0] === "w:fail"));
  assert.ok(!ensured.some((item) => item[0] === "w:closed"));
});
