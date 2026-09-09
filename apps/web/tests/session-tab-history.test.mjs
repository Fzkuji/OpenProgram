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
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
const { useCenterTabs, sessionAckIsActive } = await import("../lib/state/center-tabs-store.ts");
const { normalizeCenterTabsPayload, readCenterTabsPayload } = await import("../lib/state/center-tabs-persistence.ts");
const state = () => useCenterTabs.getState();
const active = () => state().tabs.find(t => t.id === state().activeId);
function reset() { useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null }); }
test("shared session opener reuses active session tab", () => {
  reset(); state().openSessionTab("A", "Alpha"); const id = active().id;
  state().openSessionTab("B", "Beta");
  assert.equal(state().tabs.length, 1);
  assert.equal(active().id, id);
  assert.equal(active().sessionId, "B");
});

test("back, forward, same target and branching are local to the active tab", () => {
  reset(); state().openSessionTab("A", "Alpha");
  state().openSessionTab("B", "Beta"); state().openSessionTab("B", "Beta");
  assert.equal(active().sessionHistory.entries.length, 2);
  state().navigateSessionHistory(-1); assert.equal(active().sessionId, "A");
  state().navigateSessionHistory(-1); assert.equal(active().sessionId, "A");
  state().navigateSessionHistory(1); assert.equal(active().sessionId, "B");
  state().navigateSessionHistory(-1); state().openSessionTab("C", "Gamma");
  state().navigateSessionHistory(1); assert.equal(active().sessionId, "C");
  assert.deepEqual(active().sessionHistory.entries.map(e => e.sessionId), ["A", "C"]);
});

test("already open target retains both independent tabs and webpage creates another", () => {
  reset(); state().openSessionTab("A", "Alpha"); const first = active().id;
  state().openWebTab("https://example.test"); const web = active().id;
  state().openSessionTab("A", "Alpha"); const second = active().id;
  assert.notEqual(first, second); assert.equal(state().tabs.length, 3);
  state().openSessionTab("B", "Beta"); state().openSessionTab("A", "Alpha");
  assert.equal(active().id, second); assert.equal(state().tabs.length, 3);
  state().navigateSessionHistory(-1); assert.equal(active().sessionId, "B");
  assert.equal(state().tabs.find(t => t.id === first).sessionId, "A");
  state().setActive(web); const before = state().tabs;
  state().navigateSessionHistory(-1); assert.equal(state().tabs, before);
});

test("draft history survives a background ACK, rename and reload", () => {
  reset(); const draft = state().openDraftSessionTab(); const id = active().id;
  state().openSessionTab("ready", "Ready");
  assert.equal(sessionAckIsActive(draft), false);
  state().markSessionReady(draft); state().renameSessionTab(draft, "Sent draft");
  assert.equal(active().sessionId, "ready");
  state().navigateSessionHistory(-1);
  assert.equal(active().id, id); assert.equal(active().sessionId, draft);
  assert.equal(active().draft, false); assert.equal(active().title, "Sent draft");
  assert.equal(sessionAckIsActive(draft), true);
  const restored = readCenterTabsPayload();
  assert.deepEqual(restored.tabs, state().tabs);
  state().closeTab(id); assert.equal(sessionAckIsActive("ready"), false);
});

test("unsent draft is restored and groups keep stable member references", () => {
  reset(); const draft = state().openDraftSessionTab(); const id = active().id;
  state().openWebTabInSplit("https://example.test"); state().setActive(id);
  const groups = structuredClone(state().groups);
  state().openSessionTab("group-target", "Target");
  assert.deepEqual(state().groups, groups);
  state().navigateSessionHistory(-1);
  assert.equal(active().sessionId, draft); assert.equal(active().draft, true);
  assert.deepEqual(state().groups, groups);
});

test("known deletion prunes every history and falls back without resurrecting sessions", () => {
  reset(); state().openSessionTab("A", "Alpha"); state().openSessionTab("B", "Beta");
  state().openSessionTab("C", "Gamma"); state().navigateSessionHistory(-1);
  state().removeSessionFromHistory("B"); assert.equal(active().sessionId, "A");
  state().navigateSessionHistory(1); assert.equal(active().sessionId, "C");
  state().removeSessionFromHistory("A"); state().navigateSessionHistory(-1);
  assert.equal(active().sessionId, "C");
  state().removeSessionFromHistory("C"); assert.equal(active().kind, "ntp");
});

test("legacy and malformed persisted histories preserve the current session", () => {
  const tab = { id: "s:A", kind: "session", sessionId: "A", title: "Alpha" };
  assert.deepEqual(normalizeCenterTabsPayload({ tabs: [tab] }).tabs, [tab]);
  for (const sessionHistory of [null, false, "invalid", { entries: [], index: 0 }, { entries: [null], index: 0 },
    { entries: [{sessionId:"B", title:"Beta"}], index: 0 }, {entries: [], index: -1}]) {
    assert.deepEqual(normalizeCenterTabsPayload({tabs:[{...tab, sessionHistory}]}).tabs, [tab]);
  }
});

test("desktop transfer keeps history and rejects inconsistent current entries", async () => {
  const { createRequire } = await import("node:module");
  const { validateTransferPayload } = createRequire(import.meta.url)("../../desktop/tab-transfer-validation.js");
  const entries = ["A", "B", "C", "D"].map(sessionId => ({ sessionId, title: sessionId, draft: false }));
  const tab = { id: "s:A", kind: "session", sessionId: "D", title: "D", draft: false,
    sessionHistory: { entries, index: 3 } };
  const payload = { tabs: [tab], source: { windowId: "main", kind: "tab" }, chats: entries.map(e => ({ chatKey: e.sessionId, composerDraft: e.title })) };
  const { payload: normalized } = validateTransferPayload({ id: "main" }, payload);
  assert.deepEqual(normalized.tabs[0].sessionHistory, tab.sessionHistory);
  assert.equal(normalized.chats.length, 4);
  for (const patch of [{ sessionId: "Z" }, { title: "Wrong" }, { draft: true }]) {
    assert.throws(() => validateTransferPayload({id: "main"}, { ...payload, tabs: [{ ...tab, ...patch }] }), /does not match/);
  }
  assert.throws(() => validateTransferPayload({id: "main"}, { ...payload,
    tabs: [{ ...tab, sessionHistory: { entries, index: -1 } }] }), /Invalid session history/);
});


test("discarded forward history cannot receive an activating late ACK", () => {
  reset(); state().openSessionTab("old-A", "A"); state().openSessionTab("old-B", "B");
  state().navigateSessionHistory(-1); state().openSessionTab("new-C", "C");
  assert.equal(sessionAckIsActive("old-B"), false);
});
