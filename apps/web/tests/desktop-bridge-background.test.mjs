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

const listeners = new Map();
const storage = new Map();
globalThis.window = {
  addEventListener(type, handler) {
    listeners.set(type, handler);
  },
  dispatchEvent() {},
  location: { pathname: "/s/origin" },
};
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const { setSocket } = await import("../lib/runtime-bridge/state.ts");
const {
  installDesktopMenuHandlers,
  setWebTabReady,
} = await import("../lib/desktop-bridge.ts");

function transferStub() {
  const unsubscribe = () => {};
  return {
    onRemoveSource: () => unsubscribe,
    onUndoDestination: () => unsubscribe,
    onCommitted: () => unsubscribe,
    onRejected: () => unsubscribe,
    onRolledBack: () => unsubscribe,
    onFinalizeOrphaned: () => unsubscribe,
    onStageIncoming: () => unsubscribe,
    pendingTerminal: async () => [],
    claimPending: async () => null,
  };
}

test("agent Page open reports cleanup failure and visible reuse ownership", async () => {
  useCenterTabs.setState({
    tabs: [{ id: "s:origin", kind: "session", title: "Origin", sessionId: "origin" }],
    activeId: "s:origin",
    groups: [],
    splitWebTabId: null,
    splitRatio: 0.45,
  });

  const ensured = [];
  const resolved = [];
  const activated = [];
  const shown = [];
  const destroyed = [];
  let activationTarget = null;
  let resolveRejects = true;
  window.openprogramDesktop = {
    isDesktop: true,
    windowId: "main",
    openExternal() {},
    webTab: {
      ensure(id, url) { ensured.push([id, url]); },
      navigate() {},
      async activate(id) { activated.push(id); return activationTarget; },
      async resolve(id) {
        resolved.push(id);
        if (resolveRejects) throw new Error("resolve rejected");
        return null;
      },
      preview: async () => null,
      setBounds() {},
      show(id) { shown.push(id); },
      hide() {},
      syncVisible() {},
      destroy(id) { destroyed.push(id); },
      reload() {},
      stop() {},
      goBack() {},
      goForward() {},
      onState: () => () => {},
      onPopup: () => () => {},
    },
    tabTransfer: transferStub(),
    updates: {},
  };

  const sent = [];
  setSocket({
    readyState: WebSocket.OPEN,
    send(payload) { sent.push(JSON.parse(payload)); },
  });
  installDesktopMenuHandlers();
  const closeTab = useCenterTabs.getState().closeTab;
  const ensureExclusiveWebTab = useCenterTabs.getState().ensureExclusiveWebTab;
  const resetTabs = () => useCenterTabs.setState({
    tabs: [{ id: "s:origin", kind: "session", title: "Origin", sessionId: "origin" }],
    activeId: "s:origin",
    groups: [],
    splitWebTabId: null,
    splitRatio: 0.45,
    closeTab,
    ensureExclusiveWebTab,
  });
  const open = async (url, reqId, background = false) => {
    listeners.get("op:ws-message")({
      detail: {
        type: "webtab.command",
        data: {
          op: "open",
          url,
          req_id: reqId,
          window_id: "main",
          ...(background ? { background: true } : {}),
        },
      },
    });
    await new Promise((resolve) => setImmediate(resolve));
  };

  // Created background Page: rollback without activation or focus.
  await open("https://background.test/", "background-reject", true);

  assert.equal(ensured.length, 1);
  const [[pageId]] = ensured;
  assert.deepEqual(resolved, [pageId]);
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === pageId),
    false,
    "the exact background Page must be removed when target resolution rejects",
  );
  assert.equal(useCenterTabs.getState().activeId, "s:origin");
  assert.deepEqual(destroyed, [pageId]);
  assert.deepEqual(activated, [], "background Page creation must not activate a Page");
  assert.deepEqual(shown, [], "background Page creation must not show or focus a Page");
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-reject",
    ok: false,
    created: true,
    reused: false,
    error: "desktop web tab did not expose a CDP target",
  }]);

  // Reused background Page: preserve the user-owned Page.
  const reusedBackgroundUrl = "https://background-user.test/";
  resetTabs();
  const reusedBackgroundId = useCenterTabs.getState().ensureWebTab(
    reusedBackgroundUrl,
  );
  useCenterTabs.setState({
    ensureExclusiveWebTab: () => reusedBackgroundId,
  });
  resolveRejects = false;
  sent.length = 0;
  destroyed.length = 0;

  await open(reusedBackgroundUrl, "background-reuse-null-target", true);

  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === reusedBackgroundId),
    true,
    "a reused background Page must remain when target resolution fails",
  );
  assert.equal(useCenterTabs.getState().activeId, "s:origin");
  assert.deepEqual(destroyed, []);
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-reuse-null-target",
    ok: false,
    created: false,
    reused: true,
    error: "desktop web tab did not expose a CDP target",
  }]);
  useCenterTabs.setState({ ensureExclusiveWebTab });

  // Transient close failure: retry once before reporting the open failure.
  resetTabs();
  let transientCloseAttempts = 0;
  useCenterTabs.setState({
    closeTab: (id) => {
      transientCloseAttempts += 1;
      if (transientCloseAttempts === 1) throw new Error("transient close failure");
      closeTab(id);
    },
  });
  resolveRejects = true;
  sent.length = 0;

  await open(
    "https://background-close-retries.test/",
    "background-close-retries",
    true,
  );

  assert.equal(transientCloseAttempts, 2);
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-close-retries",
    ok: false,
    created: true,
    reused: false,
    error: "desktop web tab did not expose a CDP target",
  }]);

  // Persistent close failure: return the cleanup handoff.
  resetTabs();
  let persistentCloseAttempts = 0;
  useCenterTabs.setState({
    closeTab: () => {
      persistentCloseAttempts += 1;
      throw new Error("close failed");
    },
  });
  resolveRejects = true;
  sent.length = 0;

  await open(
    "https://background-close-fails.test/",
    "background-close-fails",
    true,
  );

  assert.equal(persistentCloseAttempts, 2);
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-close-fails",
    ok: false,
    created: true,
    reused: false,
    error: (
      "desktop web tab did not expose a CDP target; "
      + "agent-created Page cleanup failed"
    ),
    reason_code: "page_cleanup_failed",
  }]);
  useCenterTabs.setState({ closeTab });
  const failedBackgroundId = ensured.at(-1)[0];
  useCenterTabs.getState().closeTab(failedBackgroundId);

  // Created visible Page: rollback when activation fails.
  const createdVisibleUrl = "https://visible-created.test/";
  const createdVisibleId = `w:${createdVisibleUrl}`;
  resetTabs();
  setWebTabReady(createdVisibleId, true);
  activationTarget = null;
  sent.length = 0;

  await open(createdVisibleUrl, "visible-created-target-fails");

  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === createdVisibleId),
    false,
    "a newly created visible Page must be removed when activation fails",
  );
  assert.equal(useCenterTabs.getState().activeId, "s:origin");
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "visible-created-target-fails",
    ok: false,
    created: true,
    reused: false,
    error: "desktop web tab did not expose a CDP target",
  }]);

  // Visible same-URL Page: reuse instead of duplicating it.
  const visibleUrl = "https://visible.test/";
  resetTabs();
  const visibleId = useCenterTabs.getState().ensureWebTab(visibleUrl);
  setWebTabReady(visibleId, true);
  activationTarget = "target-visible";
  sent.length = 0;

  await open(visibleUrl, "visible-reuse");

  assert.equal(
    useCenterTabs.getState().tabs.filter((tab) => tab.id === visibleId).length,
    1,
    "a same-URL user Page must be reused rather than duplicated",
  );
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "visible-reuse",
    ok: true,
    window_id: "main",
    url: visibleUrl,
    tab_id: visibleId,
    target_id: "target-visible",
    geometry_revision: 0,
    created: false,
    reused: true,
  }]);
});

test("background Page resolve deadline never sends a late reply", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 0 });

  const closeTab = useCenterTabs.getState().closeTab;
  const ensureExclusiveWebTab = useCenterTabs.getState().ensureExclusiveWebTab;
  const bridge = window.openprogramDesktop;
  const sent = [];
  const resolved = [];
  let pendingResolution;

  bridge.webTab.resolve = (id) => {
    resolved.push(id);
    return pendingResolution.promise;
  };
  setSocket({
    readyState: WebSocket.OPEN,
    send(payload) { sent.push(JSON.parse(payload)); },
  });

  const resetTabs = () => {
    useCenterTabs.setState({
      tabs: [{ id: "s:origin", kind: "session", title: "Origin", sessionId: "origin" }],
      activeId: "s:origin",
      groups: [],
      splitWebTabId: null,
      splitRatio: 0.45,
      closeTab,
      ensureExclusiveWebTab,
    });
  };
  const expireOpen = async (url, reqId) => {
    let resolve;
    pendingResolution = {
      promise: new Promise((done) => { resolve = done; }),
      resolve,
    };
    listeners.get("op:ws-message")({
      detail: {
        type: "webtab.command",
        data: {
          op: "open",
          url,
          req_id: reqId,
          window_id: "main",
          background: true,
        },
      },
    });
    await Promise.resolve();
    t.mock.timers.tick(14_999);
    await Promise.resolve();
    return pendingResolution;
  };
  const resolveLate = async (pending, value) => {
    pending.resolve(value);
    await Promise.resolve();
    await Promise.resolve();
  };

  // Deadline with a transient close failure.
  resetTabs();
  let transientCloseAttempts = 0;
  useCenterTabs.setState({
    closeTab: (id) => {
      transientCloseAttempts += 1;
      if (transientCloseAttempts === 1) throw new Error("transient close failure");
      closeTab(id);
    },
  });
  const transient = await expireOpen(
    "https://background-timeout-transient.test/",
    "background-timeout-transient",
  );
  const transientId = resolved.at(-1);

  assert.equal(transientCloseAttempts, 2);
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === transientId),
    false,
  );
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-timeout-transient",
    ok: false,
    created: true,
    reused: false,
    error: "desktop web tab did not expose a CDP target",
  }]);
  await resolveLate(transient, "late-target-transient");
  assert.equal(sent.length, 1, "a late target must not send a second result");
  assert.equal(transientCloseAttempts, 2, "an already removed Page must not be closed again");
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === transientId),
    false,
    "a late target must not restore the removed agent-created Page",
  );

  // Deadline with a persistent close failure.
  resetTabs();
  sent.length = 0;
  let persistentCloseAttempts = 0;
  useCenterTabs.setState({
    closeTab: () => {
      persistentCloseAttempts += 1;
      throw new Error("persistent close failure");
    },
  });
  const persistent = await expireOpen(
    "https://background-timeout-persistent.test/",
    "background-timeout-persistent",
  );
  const persistentId = resolved.at(-1);

  assert.equal(persistentCloseAttempts, 2);
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-timeout-persistent",
    ok: false,
    created: true,
    reused: false,
    error: (
      "desktop web tab did not expose a CDP target; "
      + "agent-created Page cleanup failed"
    ),
    reason_code: "page_cleanup_failed",
  }]);
  await resolveLate(persistent, "late-target-persistent");
  assert.equal(sent.length, 1, "cleanup failure must remain the only result");
  assert.equal(persistentCloseAttempts, 2, "handoff transfers cleanup ownership to the user");
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === persistentId),
    true,
    "late resolution must not close a Page after manual handoff",
  );
  useCenterTabs.setState({ closeTab });
  closeTab(persistentId);

  // Deadline on a reused Page.
  resetTabs();
  sent.length = 0;
  const reusedUrl = "https://background-timeout-reused.test/";
  const reusedId = useCenterTabs.getState().ensureWebTab(reusedUrl);
  let reusedCloseAttempts = 0;
  useCenterTabs.setState({
    ensureExclusiveWebTab: () => reusedId,
    closeTab: () => { reusedCloseAttempts += 1; },
  });
  const reused = await expireOpen(reusedUrl, "background-timeout-reused");

  assert.equal(reusedCloseAttempts, 0);
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === reusedId),
    true,
  );
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-timeout-reused",
    ok: false,
    created: false,
    reused: true,
    error: "desktop web tab did not expose a CDP target",
  }]);
  await resolveLate(reused, "late-target-reused");
  assert.equal(sent.length, 1, "a reused Page also gets only one result");
  useCenterTabs.setState({ closeTab, ensureExclusiveWebTab });
  closeTab(reusedId);

  // Wall-clock expiry wins even when the timer callback was stalled.
  resetTabs();
  sent.length = 0;
  let stalledCloseAttempts = 0;
  useCenterTabs.setState({
    closeTab: (id) => {
      stalledCloseAttempts += 1;
      closeTab(id);
    },
  });
  let resolveStalled;
  pendingResolution = {
    promise: new Promise((done) => { resolveStalled = done; }),
    resolve: resolveStalled,
  };
  listeners.get("op:ws-message")({
    detail: {
      type: "webtab.command",
      data: {
        op: "open",
        url: "https://background-event-loop-stall.test/",
        req_id: "background-event-loop-stall",
        window_id: "main",
        background: true,
      },
    },
  });
  await Promise.resolve();
  const stalledId = resolved.at(-1);
  t.mock.timers.setTime(Date.now() + 14_999);
  await resolveLate(pendingResolution, "target-after-event-loop-stall");

  assert.equal(stalledCloseAttempts, 1);
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === stalledId),
    false,
    "an overdue resolved target must still remove its agent-created Page",
  );
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "background-event-loop-stall",
    ok: false,
    created: true,
    reused: false,
    error: "desktop web tab did not expose a CDP target",
  }]);
  t.mock.timers.tick(0);
  assert.equal(sent.length, 1, "the cleared overdue timer must not send another result");

  useCenterTabs.setState({ closeTab, ensureExclusiveWebTab });
  t.mock.timers.reset();
});

test("visible route failure rolls back only an agent-created Page", async () => {
  const closeTab = useCenterTabs.getState().closeTab;
  const ensureWebTab = useCenterTabs.getState().ensureWebTab;
  const ensureExclusiveWebTab = useCenterTabs.getState().ensureExclusiveWebTab;
  const priorPathname = window.location.pathname;
  const sent = [];
  setSocket({
    readyState: WebSocket.OPEN,
    send(payload) { sent.push(JSON.parse(payload)); },
  });
  window.location.pathname = "/settings";

  const resetTabs = () => {
    useCenterTabs.setState({
      tabs: [{ id: "s:origin", kind: "session", title: "Origin", sessionId: "origin" }],
      activeId: "s:origin",
      groups: [],
      splitWebTabId: null,
      splitRatio: 0.45,
      closeTab,
      ensureWebTab,
      ensureExclusiveWebTab,
    });
  };
  const open = async (url, reqId) => {
    listeners.get("op:ws-message")({
      detail: {
        type: "webtab.command",
        data: {
          op: "open",
          url,
          req_id: reqId,
          window_id: "main",
        },
      },
    });
    await Promise.resolve();
  };

  // Created Page with a transient close failure.
  resetTabs();
  const transientClosed = [];
  useCenterTabs.setState({
    closeTab: (id) => {
      transientClosed.push(id);
      if (transientClosed.length === 1) throw new Error("transient close failure");
      closeTab(id);
    },
  });
  await open(
    "https://visible-route-transient.test/",
    "visible-route-transient",
  );
  const transientId = transientClosed[0];

  assert.equal(transientClosed.length, 2);
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === transientId),
    false,
  );
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "visible-route-transient",
    ok: false,
    created: true,
    reused: false,
    error: "center-tab navigation unavailable",
  }]);

  // Reused Page remains user-owned.
  resetTabs();
  sent.length = 0;
  const reusedUrl = "https://visible-route-reused.test/";
  const reusedId = useCenterTabs.getState().ensureWebTab(reusedUrl);
  let reusedCloseAttempts = 0;
  useCenterTabs.setState({
    ensureWebTab: () => reusedId,
    ensureExclusiveWebTab: () => reusedId,
    closeTab: () => { reusedCloseAttempts += 1; },
  });
  await open(reusedUrl, "visible-route-reused");

  assert.equal(reusedCloseAttempts, 0);
  assert.equal(
    useCenterTabs.getState().tabs.some((tab) => tab.id === reusedId),
    true,
  );
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "visible-route-reused",
    ok: false,
    created: false,
    reused: true,
    error: "center-tab navigation unavailable",
  }]);
  useCenterTabs.setState({ closeTab, ensureWebTab, ensureExclusiveWebTab });
  closeTab(reusedId);

  // Created Page with a persistent close failure.
  resetTabs();
  sent.length = 0;
  const persistentClosed = [];
  useCenterTabs.setState({
    closeTab: (id) => {
      persistentClosed.push(id);
      throw new Error("persistent close failure");
    },
  });
  await open(
    "https://visible-route-persistent.test/",
    "visible-route-persistent",
  );
  const persistentId = persistentClosed[0];

  assert.equal(persistentClosed.length, 2);
  assert.deepEqual(sent, [{
    action: "webtab_result",
    req_id: "visible-route-persistent",
    ok: false,
    created: true,
    reused: false,
    error: (
      "desktop web tab did not expose a CDP target; "
      + "agent-created Page cleanup failed"
    ),
    reason_code: "page_cleanup_failed",
  }]);
  useCenterTabs.setState({ closeTab });
  closeTab(persistentId);
  window.location.pathname = priorPathname;
});
