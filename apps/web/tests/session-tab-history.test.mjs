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

test("file navigation records folder and file steps and branches after back", () => {
  reset();
  const s = state();
  s.openFileTab("p", "src/a.ts");
  const aId = active().id;
  s.recordFileNavigation({ projectId: "p", path: "", selectedType: "dir", expanded: [], scroll: null });
  s.recordFileNavigation({ projectId: "p", path: "src", selectedType: "dir", expanded: ["src"], scroll: { path: "src", offset: 18 } });
  s.recordFileNavigation({ projectId: "p", path: "src/a.ts", selectedType: "file", expanded: ["src"], scroll: { path: "src/a.ts", offset: 31 } });
  s.openFileTab("p", "src/b.ts");
  const bId = active().id;
  s.recordFileNavigation({ projectId: "p", path: "src/b.ts", selectedType: "file", expanded: ["src"], scroll: { path: "src/b.ts", offset: 42 } });
  assert.equal(s.canNavigateFile(-1), true);
  s.navigateFileHistory(-1);
  assert.equal(state().activeId, aId);
  assert.equal(state().fileNavigationHistory.entries[state().fileNavigationHistory.index].path, "src/a.ts");
  s.navigateFileHistory(-1);
  assert.equal(state().activeId, null);
  assert.equal(state().fileNavigationHistory.entries[state().fileNavigationHistory.index].path, "src");
  s.navigateFileHistory(1);
  assert.equal(state().activeId, aId);
  s.navigateFileHistory(1);
  assert.equal(state().activeId, bId);
  s.recordFileNavigation({ projectId: "p", path: "docs", selectedType: "dir", expanded: ["docs"], scroll: null });
  assert.equal(state().canNavigateFile(1), false);
});

test("already open target activates its existing tab while unopened sessions keep navigation rules", () => {
  reset(); state().openSessionTab("A", "Alpha"); const first = active().id;
  state().openWebTab("https://example.test"); const web = active().id;
  state().openSessionTab("A", "Alpha"); const second = active().id;
  assert.equal(first, second); assert.equal(state().tabs.length, 2);
  state().setActive(web); state().openSessionTab("B", "Beta");
  assert.equal(state().tabs.length, 3);
  state().openSessionTab("A", "Alpha"); assert.equal(active().id, first);
  state().setActive(web); const before = state().tabs;
  state().navigateSessionHistory(-1); assert.equal(state().tabs, before);
});

test("history navigation activates a tab already showing the target session", () => {
  reset(); state().openSessionTab("A", "Alpha"); const first = active().id;
  state().openSessionTab("B", "Beta");
  state().openWebTab("https://example.test");
  state().openSessionTab("C", "Gamma"); const second = active().id;
  const source = { id: "s:source", kind: "session", sessionId: "A", title: "Alpha",
    sessionHistory: { entries: [{sessionId:"A",title:"Alpha",draft:false},{sessionId:"C",title:"Gamma",draft:false}], index: 0 } };
  useCenterTabs.setState({ tabs: [source, ...state().tabs.filter(t => t.id !== first)], activeId: source.id });
  state().navigateSessionHistory(1);
  assert.equal(active().id, second);
  assert.equal(state().tabs.find(t => t.id === source.id).sessionId, "A");
});

test("restore removes duplicate session tabs and repairs groups and active references", () => {
  const payload = normalizeCenterTabsPayload({
    tabs: [
      { id: "s:A", kind: "session", sessionId: "A", title: "Alpha" },
      { id: "s:A:duplicate", kind: "session", sessionId: "A", title: "Alpha" },
      { id: "w:1", kind: "web", title: "Web", url: "https://example.test" },
    ],
    activeId: "s:A:duplicate",
    groups: [{ id: "g", memberIds: ["s:A:duplicate", "w:1"], visibleIds: ["s:A:duplicate", "w:1"], focusedId: "s:A:duplicate" }],
  });
  assert.deepEqual(payload.tabs.map(tab => tab.id), ["s:A:duplicate", "w:1"]);
  assert.equal(payload.activeId, "s:A:duplicate");
  assert.deepEqual(payload.groups, [{ id: "g", memberIds: ["s:A:duplicate", "w:1"], visibleIds: ["s:A:duplicate", "w:1"], focusedId: "s:A:duplicate" }]);
});

test("same-title sessions remain distinct", () => {
  reset(); state().openSessionTab("A", "Same"); const first = active().id;
  state().openWebTab("https://example.test");
  state().openSessionTab("B", "Same");
  assert.equal(state().tabs.filter(tab => tab.kind === "session").length, 2);
  assert.notEqual(active().id, first);
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
  state().removeSessionFromHistory("C"); assert.equal(state().activeId, null);
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

test("closing the final desktop tab leaves the window open with no tabs", () => {
  reset();
  let closes = 0;
  window.openprogramDesktop = { isDesktop: true, windowId: "main", closeWindow() { closes++; } };
  try {
    for (const open of [() => state().openSessionTab("last", "Last"), () => state().openWebTab("https://example.test/last"), () => state().openNewTabPage()]) {
      reset(); open(); state().closeTab(active().id);
      assert.equal(closes, 0);
      assert.equal(state().tabs.length, 0);
      assert.equal(state().activeId, null);
      assert.equal(readCenterTabsPayload().tabs.length, 0);
    }
  } finally { delete window.openprogramDesktop; }
});

test("reopening an existing session preserves its graph view", () => {
  reset(); state().openSessionTab("A", "Alpha"); const first = active().id;
  state().setTabDagView(first, true);
  state().openWebTab("https://example.test");
  state().openSessionTab("A", "Alpha");
  assert.equal(active().id, first);
  assert.equal(active().dagView, true);
});
