import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../../lib/session-store/index.ts", import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../../${specifier.slice(2)}.ts`, import.meta.url).href,
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
const { useCenterTabs, sessionAckIsActive } = await import("../../lib/tabs/center-tabs-store.ts");
const { normalizeCenterTabsPayload, readCenterTabsPayload } = await import("../../lib/tabs/center-tabs-persistence.ts");
const state = () => useCenterTabs.getState();
const active = () => state().tabs.find(t => t.id === state().activeId);
function reset() { useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null, navigationRoute: undefined, fileNavigationHistory: { entries: [], index: -1 }, fileNavigationRestore: null }); }
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
  assert.equal(active().kind, "builtin");
  assert.equal(active().page, "files");
  assert.equal(state().fileNavigationHistory.entries[state().fileNavigationHistory.index].path, "src");
  s.navigateFileHistory(1);
  assert.equal(state().activeId, aId);
  s.navigateFileHistory(1);
  assert.equal(state().activeId, bId);
  s.recordFileNavigation({ projectId: "p", path: "docs", selectedType: "dir", expanded: ["docs"], scroll: null });
  assert.equal(state().canNavigateFile(1), false);
});

test("opening an existing session focuses its tab from sessions and launchers", () => {
  reset(); state().openSessionTab("A", "Alpha"); const first = active().id;
  state().openWebTab("https://example.test"); const web = active().id;
  state().openSessionTab("A", "Alpha"); const second = active().id;
  assert.equal(first, second); assert.equal(state().tabs.length, 2);
  state().setActive(web); state().openSessionTab("B", "Beta");
  assert.equal(state().tabs.length, 3);
  const third = active().id;
  state().openSessionTab("A", "Alpha"); assert.equal(active().id, first);
  assert.equal(state().tabs.find(tab => tab.id === third).sessionId, "B");
  state().openNewTabPage(); const launcher = active().id;
  state().openSessionTab("A", "Alpha"); assert.equal(active().id, first);
  assert.equal(state().tabs.find(tab => tab.id === launcher).kind, "ntp");
  assert.equal(state().tabs.find(tab => tab.id === first).sessionId, "A");
  state().setActive(web); const before = state().tabs;
  state().navigateSessionHistory(-1); assert.equal(state().tabs, before);
});

test("history navigation focuses existing sessions without changing source history", () => {
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
  assert.equal(active().sessionId, "C");
  assert.equal(state().tabs.find(t => t.id === second).sessionId, "C");
});

test("restore retains the active grouped session and removes duplicates", () => {
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
  const { validateTransferPayload } = createRequire(import.meta.url)("../../../desktop/tab-transfer-validation.js");
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

test("explicit new tabs have no history belonging to their opener", () => {
  reset(); state().openSessionTab("return-chat", "Chat"); const chat = structuredClone(active());
  state().openBuiltinTab("files"); const files = active().id;
  assert.equal(state().canNavigateHistory(-1), false);
  state().navigateHistory(-1); assert.equal(active().id, files);
  assert.deepEqual(state().tabs.find(tab => tab.id === chat.id), chat);
});

test("two launchers keep independent histories when selecting the same page", () => {
  reset(); state().openNewTabPage(); const firstHome = active().id;
  state().openBuiltinTab("files"); const first = structuredClone(active());
  state().openNewTabPage(); const secondHome = active().id;
  state().openBuiltinTab("files"); const second = structuredClone(active());
  assert.notEqual(first.id, second.id);
  assert.equal(state().tabs.length, 2);
  state().navigateHistory(-1); assert.equal(active().id, secondHome);
  state().navigateHistory(-1); assert.equal(active().id, secondHome);
  assert.deepEqual(state().tabs.find(tab => tab.id === first.id), first);
  state().setActive(first.id);
  state().navigateHistory(-1); assert.equal(active().id, firstHome);
  assert.equal(state().tabs.find(tab => tab.id === secondHome).pageHistory.index, 0);
  state().navigateHistory(1); assert.equal(active().id, first.id);
  state().setActive(secondHome); state().navigateHistory(1);
  assert.deepEqual(active(), second);
});

test("branching one tab preserves another tab's forward history and background pages", () => {
  reset(); state().openNewTabPage(); state().openSessionTab("branch-A", "A");
  const first = active().id;
  state().openSessionTab("branch-B", "B"); state().navigateHistory(-1);
  const untouched = structuredClone(active());
  state().openNewTabPage(); state().openBuiltinTab("files"); state().navigateHistory(-1);
  state().openBuiltinTab("terminal");
  state().ensureWebTab("https://background.test");
  assert.equal(state().canNavigateHistory(1), false);
  assert.deepEqual(state().tabs.find(tab => tab.id === first), untouched);
  state().setActive(first); state().navigateHistory(1);
  assert.equal(active().sessionId, "branch-B");
});

test("closing a different tab cannot change the current tab's history", () => {
  reset(); state().openNewTabPage(); const other = active().id;
  state().openNewTabPage(); state().openBuiltinTab("files"); const current = structuredClone(active());
  state().closeTab(other); assert.deepEqual(active(), current);
  state().navigateHistory(-1); assert.equal(active().kind, "ntp");
  state().navigateHistory(1); assert.deepEqual(active(), current);
});

test("default launcher remains the origin when opening an existing destination", () => {
  reset(); state().openBuiltinTab("files");
  state().openNewTabPage(); const home = active().id;
  state().openBuiltinTab("files");
  state().navigateHistory(-1); assert.equal(active().id, home);
  state().navigateHistory(1); assert.equal(active().page, "files");
});

test("page and session navigation returns to its own launcher", () => {
  reset(); state().openNewTabPage(); const home = active().id;
  state().openSessionTab("ordered-A", "A"); state().openSessionTab("ordered-B", "B");
  state().recordRouteNavigation("/skills");
  state().navigateHistory(-1); assert.equal(active().sessionId, "ordered-B");
  assert.equal(state().navigationRoute, undefined);
  state().navigateHistory(-1); assert.equal(active().sessionId, "ordered-A");
  state().navigateHistory(-1); assert.equal(active().id, home);
});

test("tab switches restore each tab's sidebar route without adding visits", () => {
  reset(); state().openNewTabPage(); const first = active().id;
  state().recordRouteNavigation("/skills"); const before = structuredClone(active());
  state().openNewTabPage(); const second = active().id;
  state().recordRouteNavigation("/programs");
  state().setActive(first); assert.equal(state().navigationRoute, "/skills");
  assert.deepEqual(active(), before);
  state().navigateHistory(-1); assert.equal(state().navigationRoute, undefined);
  state().setActive(second); assert.equal(state().navigationRoute, "/programs");
});

test("sidebar routes also return to the launcher in visit order", () => {
  reset(); state().openNewTabPage(); const home = active().id;
  state().recordRouteNavigation("/skills"); state().recordRouteNavigation("/programs");
  state().navigateHistory(-1); assert.equal(state().navigationRoute, "/skills");
  state().navigateHistory(-1); assert.equal(active().id, home); assert.equal(state().navigationRoute, undefined);
  state().navigateHistory(1); assert.equal(state().navigationRoute, "/skills");
  state().openBuiltinTab("files"); assert.equal(state().canNavigateHistory(1), false);
  state().navigateHistory(-1); assert.equal(state().navigationRoute, "/skills");
});

test("launcher round trip preserves a draft acknowledgement and title", () => {
  reset(); state().openNewTabPage();
  const draft = state().claimDraftSessionTab();
  state().navigateHistory(-1); assert.equal(active().kind, "ntp");
  state().markSessionReady(draft); state().renameSessionTab(draft, "Acknowledged");
  state().navigateHistory(1);
  assert.equal(active().sessionId, draft);
  assert.equal(active().draft, false);
  assert.equal(active().title, "Acknowledged");
});


test("acknowledgements cannot activate a session retained behind the launcher or closed", () => {
  reset(); state().openNewTabPage(); state().openSessionTab("retained-ready", "Ready");
  state().navigateHistory(-1);
  assert.equal(sessionAckIsActive("retained-ready"), false);
  state().closeTab(active().id);
  assert.equal(sessionAckIsActive("retained-ready"), false);
});


test("switching tabs does not create return history in the selected tab", () => {
  reset(); state().openNewTabPage(); state().openBuiltinTab("files"); const first=active().id;
  state().openNewTabPage(); const second=active().id;
  state().setActive(first); state().setActive(second);
  const untouched=structuredClone(state().tabs.find(tab=>tab.id===first));
  assert.equal(state().canNavigateHistory(-1), false);
  state().navigateHistory(-1); assert.equal(active().id,second);
  assert.deepEqual(state().tabs.find(tab=>tab.id===first),untouched);
});


test("native transfer preserves launcher, file view and isolated application histories", async () => {
  const { createRequire } = await import("node:module");
  const { validateTransferPayload } = createRequire(import.meta.url)("../../../desktop/tab-transfer-validation.js");
  const { replaceCenterTabsPayload } = await import("../../lib/tabs/center-tabs-store.ts");
  reset(); state().openNewTabPage(); const home = active().id;
  state().openBuiltinTab("files");
  state().recordFileNavigation({ projectId: "p", path: "src", selectedType: "dir", expanded: ["src"], scroll: {path: "src", offset: 12} });
  state().openFileTab("p", "src/a.ts");
  state().recordFileNavigation({ projectId: "p", path: "src/a.ts", selectedType: "file", expanded: ["src"], scroll: null });
  const source = structuredClone(active());
  const transfer = tab => validateTransferPayload({id: "main"}, { tabs: [tab], source: {kind: "tab"}, chats: [] }).payload.tabs[0];
  const received = transfer(source);
  assert.deepEqual(received.pageHistory, source.pageHistory);
  replaceCenterTabsPayload({version: 2, tabs:[received], activeId:received.id, groups:[], splitWebTabId:null, splitRatio:0.5}, {persist:false});
  state().navigateHistory(-1); assert.equal(active().fileNavigationSnapshot.path, "src");
  state().navigateHistory(-1); assert.equal(active().id, home);
  const instance = "a".repeat(64);
  state().openApplicationTab("calculator", instance, "Calculator");
  state().openNewTabPage(); state().openApplicationTab("calculator", instance, "Calculator");
  assert.match(active().id, /:tab:/);
  assert.equal(transfer(active()).id, active().id);
  assert.throws(() => transfer({...active(), applicationInstanceId: "b".repeat(64)}), /identity/);
  assert.throws(() => transfer({...source, pageHistory:{entries:[{...source}],index:0}}), /Nested page history/);
});

test("passive sidebar file-tree seeding does not replace a session or its history", () => {
  reset(); state().openNewTabPage(); state().openSessionTab("sidebar-owner", "Owner");
  const before = structuredClone(active());
  for (const path of ["", "src"]) state().recordFileNavigation({projectId:"p",path,selectedType:"dir",expanded:[],scroll:null});
  assert.deepEqual(active(), before);
  state().navigateHistory(-1); assert.equal(active().kind, "ntp");
  state().navigateHistory(1); assert.equal(active().sessionId, "sidebar-owner");
});

test("renaming a file preserves its launcher history and another same-target tab", () => {
  reset(); state().openFileTab("p", "b.ts"); const other = structuredClone(active());
  state().openNewTabPage(); const home = active().id;
  state().openBuiltinTab("files"); state().openFileTab("p", "a.ts");
  state().recordFileNavigation({projectId:"p",path:"a.ts",selectedType:"file",expanded:[],scroll:{path:"a.ts",offset:9}});
  state().retargetFileTab(active().id, "p", "b.ts");
  assert.notEqual(active().id, other.id);
  assert.deepEqual(state().tabs.find(tab => tab.id === other.id), other);
  assert.equal(active().path, "b.ts");
  assert.equal(active().fileNavigationSnapshot.path, "b.ts");
  assert.equal(active().fileNavigationSnapshot.scroll.path, "b.ts");
  state().navigateHistory(-1); assert.equal(active().page, "files");
  state().navigateHistory(-1); assert.equal(active().id, home);
  state().navigateHistory(1); state().navigateHistory(1);
  assert.equal(active().path, "b.ts");
});

test("transfers include historical file drafts and preserve keys still used by another tab", async () => {
  window.location = { pathname: "/chat" };
  const { buildTransferPayload, handleRemoveSource } = await import("../../lib/desktop/bridge-transfer.ts");
  const { fileDrafts, fileDraftKey } = await import("../../lib/files/files-shared.ts");
  const { createRequire } = await import("node:module");
  const { validateTransferPayload } = createRequire(import.meta.url)("../../../desktop/tab-transfer-validation.js");
  reset(); state().openNewTabPage();
  const keys = [];
  for (const path of ["a.ts", "b.ts", "c.ts", "d.ts"]) {
    state().openFileTab("p", path);
    const key = fileDraftKey("p", path); keys.push(key);
    fileDrafts.set(key, {draft: `unsaved ${path}`, baselineContent: "before", baselineMtime: 1});
  }
  const moving = active().id;
  state().openNewTabPage(); state().openFileTab("p", "a.ts");
  const remaining = structuredClone(active());
  const payload = buildTransferPayload({kind: "tab", tabIds:[moving]}, "source");
  assert.deepEqual(payload.fileDrafts.map(draft => draft.key), keys);
  const normalized = validateTransferPayload({id:"source"}, payload).payload;
  assert.deepEqual(normalized.fileDrafts, payload.fileDrafts);
  const receipts = [];
  await handleRemoveSource({webTab:{syncVisible:async () => true}, tabTransfer:{
    journalOpened: async () => true,
    sourceRemoved: async (...args) => { receipts.push(args); return true; },
    journalFinalized: async () => true,
  }}, {token:"historical-file-drafts", payload});
  assert.ok(receipts.some(([, ok]) => ok));
  assert.deepEqual(active(), remaining);
  assert.equal(fileDrafts.get(keys[0]).draft, "unsaved a.ts");
  for (const key of keys.slice(1)) assert.equal(fileDrafts.has(key), false);
  for (const key of keys) fileDrafts.delete(key);
});


test("transfers reject duplicate conversation identities without replacing either slot", async () => {
  const { validateTransferredTabs, insertTransferredTabs } = await import("../../lib/tabs/center-tabs-store.ts");
  for (const wasActive of [false, true]) {
    reset(); state().openSessionTab("transfer-A", "A");
    const before = structuredClone(state().tabs);
    const payload = { tabs: [{ id: "s:another-slot", kind: "session", sessionId: "transfer-A", title: "A" }],
      source: { kind: "tab" }, chats: wasActive ? [{ chatKey: "transfer-A", wasActive: true }] : [] };
    const placement = { kind: "strip-end" };
    assert.equal(validateTransferredTabs(payload, placement).ok, false);
    assert.equal(insertTransferredTabs(payload, placement, { persist: false }).ok, false);
    assert.deepEqual(state().tabs, before);
  }
  reset();
  const tabs = ["one", "two"].map(id => ({ id, kind: "session", sessionId: "A", title: "A" }));
  assert.equal(validateTransferredTabs({ tabs, chats: [], source: {kind: "group", memberIds: ["one", "two"]} }, {kind: "strip-end"}).ok, false);
});


test("application settings leave tab content and forward history unchanged", (t) => {
  const location = window.location;
  delete window.location;
  t.after(() => { window.location = location; });
  for (const kind of ["session", "ntp", "files", "browser"]) {
    reset(); state().openNewTabPage();
    if (kind === "session") state().openSessionTab("settings-owner", "Owner");
    if (kind === "files" || kind === "browser") state().openBuiltinTab(kind);
    state().recordRouteNavigation("/skills");
    state().recordRouteNavigation("/programs");
    state().navigateHistory(-1);
    const before = structuredClone(state().tabs);
    for (const path of ["/settings", "/settings/general", "/settings/providers/provider", "/settings/browser#clear-data"]) {
      state().recordRouteNavigation(path);
      assert.deepEqual(state().tabs, before, `${kind}: ${path}`);
      assert.equal(state().navigationRoute, "/skills");
    }
    state().navigateHistory(1);
    assert.equal(state().navigationRoute, "/programs");
  }
});

test("legacy settings visits are removed on reload without losing current metadata", () => {
  const base = { id: "s:A", kind: "session", sessionId: "A", title: "Old" };
  const pages = [base, { ...base, navigationRoute: "/skills" },
    { ...base, navigationRoute: "/settings/general" },
    { ...base, navigationRoute: "/settings/providers" },
    { ...base, navigationRoute: "/programs" }];
  for (let index = 0; index < pages.length; index++) {
    const tab = { ...pages[index], title: "Latest", pageHistory: { entries: pages, index } };
    storage.set("centerTabs", JSON.stringify({ version: 2, tabs: [tab], activeId: base.id }));
    const restored = readCenterTabsPayload().tabs[0];
    assert.equal(restored.title, "Latest");
    assert.equal(restored.navigationRoute, index === 0 ? undefined : index === 4 ? "/programs" : "/skills");
    assert.equal(restored.pageHistory.entries.length, 3);
    assert.equal(restored.pageHistory.index, index === 0 ? 0 : index === 4 ? 2 : 1);
    assert.equal(restored.pageHistory.entries.some(p => p.navigationRoute?.startsWith("/settings")), false);
    assert.deepEqual(normalizeCenterTabsPayload({ tabs: [restored], activeId: base.id }).tabs[0], restored);
  }
});

test("legacy settings without an underlying visit retain their content identity", () => {
  const tab = { id: "s:orphan", kind: "session", sessionId: "orphan", title: "Draft", draft: true, navigationRoute: "/settings/general" };
  for (const input of [tab, { ...tab, pageHistory: { entries: [tab, { ...tab, navigationRoute: "/settings/browser" }], index: 1 } }]) {
    const restored = normalizeCenterTabsPayload({ tabs: [input], activeId: tab.id }).tabs[0];
    assert.equal(restored.navigationRoute, undefined);
    assert.equal(restored.sessionId, "orphan"); assert.equal(restored.draft, true);
    assert.equal(restored.pageHistory?.entries.some(p => p.navigationRoute?.startsWith("/settings")) ?? false, false);
  }
});
