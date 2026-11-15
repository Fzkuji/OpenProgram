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

const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");
const { topLevelTabs, groupWebPages, revealExistingWebTab } = await import("../../lib/browser/web-tab/web-page-management.ts");
const { normalizeCenterTabsPayload } = await import("../../lib/tabs/center-tabs-persistence.ts");

test("agent pages retain session ownership, pinning and popup provenance", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const store = useCenterTabs.getState();
  const a = store.ensureExclusiveWebTab("https://same.test/");
  const b = store.ensureExclusiveWebTab("https://same.test/");
  store.markAgentWebTab(a, "session-a");
  store.markAgentWebTab(b, "session-b");
  store.openWebTab("https://manual.test/");
  let state = useCenterTabs.getState();
  assert.equal(topLevelTabs(state.tabs, state.groups).length, 1);
  // The manual page opened from the active tab sits right of it.
  assert.deepEqual(groupWebPages(state.tabs).map(g => g.sessionId), ["session-a", null, "session-b"]);
  store.setWebTabPinned(a, true);
  assert.equal(topLevelTabs(useCenterTabs.getState().tabs, []).length, 2);
  const popup = store.openPopupWebTab("https://popup.test/", a);
  const child = useCenterTabs.getState().tabs.find(t => t.id === popup);
  assert.equal(child.agentSessionId, "session-a");
  assert.equal(child.agentOpened, true);
  assert.equal(child.webPinned, undefined);
  const payload = normalizeCenterTabsPayload(useCenterTabs.getState());
  assert.equal(payload.tabs.find(t => t.id === a).webPinned, true);
  assert.equal(payload.tabs.find(t => t.id === b).agentSessionId, "session-b");
  store.closeTab(a);
  assert.equal(useCenterTabs.getState().tabs.find(t => t.id === popup).agentSessionId, "session-a");
});

test("explicit split groups and legacy pages remain reachable in the strip", () => {
  const tabs = [{id:"s:a",kind:"session",title:"A"}, {id:"w:a",kind:"web",title:"Page",agentOpened:true}, {id:"w:old",kind:"web",title:"Legacy"}];
  const groups = [{id:"g",memberIds:["s:a","w:a"],visibleIds:["s:a","w:a"],focusedId:"s:a"}];
  assert.deepEqual(topLevelTabs(tabs, groups), tabs);
  assert.deepEqual(topLevelTabs(tabs, []), tabs);
  assert.equal(groupWebPages(tabs).find(g=>g.agent).sessionId, null);
});

test("explicit reveal keeps the exact page identity and only then appears in the strip", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const store = useCenterTabs.getState();
  const id = store.ensureExclusiveWebTab("https://reveal.test/");
  store.markAgentWebTab(id, "owner");
  store.openSessionTab("owner", "Chat");
  const afterOpen = useCenterTabs.getState();
  assert.equal(afterOpen.tabs.find(tab => tab.id === id).url, "https://reveal.test/");
  assert.ok(!topLevelTabs(afterOpen.tabs, afterOpen.groups).some(tab => tab.id === id));
  assert.equal(revealExistingWebTab("missing", afterOpen), false);
  assert.equal(revealExistingWebTab(id, useCenterTabs.getState()), true);
  const revealed = useCenterTabs.getState();
  const page = revealed.tabs.find(tab => tab.id === id);
  assert.equal(page.id, id);
  assert.equal(page.url, "https://reveal.test/");
  assert.equal(page.agentOpened, true);
  assert.equal(page.agentSessionId, "owner");
  assert.equal(revealed.activeId, id);
  assert.ok(topLevelTabs(revealed.tabs, revealed.groups).some(tab => tab.id === id));
  assert.equal(revealExistingWebTab(id, useCenterTabs.getState()), true);
  assert.equal(useCenterTabs.getState().tabs.filter(tab => tab.id === id).length, 1);
});

test("manually reopening a managed URL pins it without changing its owner", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const store = useCenterTabs.getState();
  const id = store.ensureWebTab("https://reopen.test/");
  store.markAgentWebTab(id, "owner");
  store.openWebTab("https://reopen.test/", true);
  assert.equal(useCenterTabs.getState().tabs[0].webPinned, undefined);
  store.openWebTab("https://reopen.test/");
  assert.equal(useCenterTabs.getState().tabs[0].webPinned, true);
  assert.equal(useCenterTabs.getState().tabs[0].agentSessionId, "owner");
});

test("closing the final visible tab keeps owned pages hidden with no visible tabs", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const store = useCenterTabs.getState();
  const pageId = store.ensureWebTab("https://retained.test/");
  store.markAgentWebTab(pageId, "owner");
  store.openSessionTab("owner", "Chat");
  const page = useCenterTabs.getState().tabs.find(t => t.id === pageId);
  store.closeTab(useCenterTabs.getState().activeId);
  const state = useCenterTabs.getState();
  assert.equal(state.activeId, null);
  assert.equal(normalizeCenterTabsPayload(state).activeId, null);
  assert.deepEqual(topLevelTabs(state.tabs, state.groups), []);
  assert.deepEqual(state.tabs.find(t => t.id === pageId), page);
});

test("close selects the next visible neighbor rather than an owned hidden page", () => {
  const tabs = [{id:"s:a",kind:"session",sessionId:"a",title:"A"},
    {id:"w:hidden",kind:"web",url:"https://hidden.test",title:"Hidden",agentOpened:true,agentSessionId:"a"},
    {id:"s:b",kind:"session",sessionId:"b",title:"B"}];
  useCenterTabs.setState({ tabs, groups: [], activeId: "s:a", splitWebTabId: null });
  useCenterTabs.getState().closeTab("s:a");
  assert.equal(useCenterTabs.getState().activeId, "s:b");
});

test("closing a session in a split does not activate its now hidden owned page", () => {
  const tabs = [{id:"s:a",kind:"session",sessionId:"a",title:"A"},
    {id:"w:hidden",kind:"web",url:"https://hidden.test",title:"Hidden",agentOpened:true,agentSessionId:"a"}];
  useCenterTabs.setState({ tabs, groups: [{id:"g",memberIds:tabs.map(t=>t.id),visibleIds:tabs.map(t=>t.id),focusedId:"s:a"}], activeId:"s:a", splitWebTabId:null });
  useCenterTabs.getState().closeTab("s:a");
  const state = useCenterTabs.getState();
  assert.equal(state.activeId, null);
  assert.equal(normalizeCenterTabsPayload(state).activeId, null);
  assert.deepEqual(state.groups, []);
  assert.ok(state.tabs.some(t=>t.id==="w:hidden"));
});

const { canNavigateTabPage } = await import("../../lib/tabs/navigation/page-history.ts");
test("New tab is preserved behind application and built-in page navigation", () => {
  for (const open of [s => s.openApplicationTab("calculator", "a".repeat(64), "Calculator"),
    s => s.openBuiltinTab("files"), s => s.openBuiltinTab("browser"), s => s.openBuiltinTab("terminal")]) {
    useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
    const s = useCenterTabs.getState();
    s.openNewTabPage(); const homeId = useCenterTabs.getState().activeId;
    open(s); const targetId = useCenterTabs.getState().activeId;
    assert.equal(useCenterTabs.getState().tabs.length, 1);
    assert.equal(canNavigateTabPage(useCenterTabs.getState().tabs[0], -1), true);
    s.navigateSessionHistory(-1);
    assert.equal(useCenterTabs.getState().activeId, homeId);
    assert.equal(useCenterTabs.getState().tabs[0].kind, "ntp");
    assert.equal(canNavigateTabPage(useCenterTabs.getState().tabs[0], 1), true);
    useCenterTabs.setState(normalizeCenterTabsPayload(useCenterTabs.getState()));
    s.navigateSessionHistory(1);
    assert.equal(useCenterTabs.getState().activeId, targetId);
    assert.equal(useCenterTabs.getState().tabs.length, 1);
    s.navigateSessionHistory(-1);
    s.openBuiltinTab("files");
    assert.equal(canNavigateTabPage(useCenterTabs.getState().tabs[0], 1), false);
    assert.equal(useCenterTabs.getState().tabs[0].pageHistory.entries.length, 2);
  }
});

test("conversation history can return to New tab and forward to the same draft", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const s = useCenterTabs.getState(); s.openNewTabPage();
  const home = useCenterTabs.getState().activeId;
  const draft = s.claimDraftSessionTab();
  s.openSessionTab("next", "Next");
  s.navigateSessionHistory(-1);
  assert.equal(useCenterTabs.getState().tabs[0].sessionId, draft);
  s.navigateSessionHistory(-1);
  assert.equal(useCenterTabs.getState().activeId, home);
  s.navigateSessionHistory(1);
  assert.equal(useCenterTabs.getState().tabs[0].sessionId, draft);
  s.navigateSessionHistory(1);
  assert.equal(useCenterTabs.getState().tabs[0].sessionId, "next");
});

test("deleting a conversation removes its forward page after returning to New tab", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const s = useCenterTabs.getState(); s.openNewTabPage(); s.openSessionTab("removed", "Removed");
  s.navigateSessionHistory(-1); s.removeSessionFromHistory("removed");
  assert.equal(canNavigateTabPage(useCenterTabs.getState().tabs[0], 1), false);
  s.navigateSessionHistory(1);
  assert.equal(useCenterTabs.getState().tabs[0].kind, "ntp");
});

test("human tab dismissal retains a session Page and reopens its exact navigated identity", () => {
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const store = useCenterTabs.getState();
  store.openSessionTab("owner", "Conversation");
  const id = store.ensureExclusiveWebTab("https://original.test");
  store.markAgentWebTab(id, "owner");
  revealExistingWebTab(id, useCenterTabs.getState());
  store.updateWebTab(id, { url: "https://navigated.test", title: "User navigation" });
  store.closeTab(id, { retainSessionResources: true });
  const hidden = useCenterTabs.getState();
  assert.equal(hidden.tabs.find(tab => tab.id === id)?.url, "https://navigated.test");
  assert.equal(hidden.tabs.find(tab => tab.id === id)?.agentSessionId, "owner");
  assert.ok(!topLevelTabs(hidden.tabs, hidden.groups).some(tab => tab.id === id));
  assert.equal(hidden.activeId, "s:owner");
  const restored = normalizeCenterTabsPayload(hidden);
  assert.ok(restored.tabs.some(tab => tab.id === id));
  assert.ok(!topLevelTabs(restored.tabs, restored.groups).some(tab => tab.id === id));
  revealExistingWebTab(id, useCenterTabs.getState());
  assert.equal(useCenterTabs.getState().activeId, id);
  assert.equal(useCenterTabs.getState().tabs.filter(tab => tab.id === id).length, 1);
  store.closeTab(id);
  assert.ok(!useCenterTabs.getState().tabs.some(tab => tab.id === id), "explicit resource close still destroys");
});

test("human tab dismissal retains manually associated Pages without changing their provenance", async () => {
  const { ingestBrowserResource, resetBrowserResources, sessionResourceRows, listedBrowserResources } = await import("../../lib/chat/resources/session-resources.ts");
  const { agentCanAccessWebTab } = await import("../../lib/browser/web-tab/web-page-management.ts");
  resetBrowserResources();
  useCenterTabs.setState({ tabs: [], groups: [], activeId: null, splitWebTabId: null });
  const store = useCenterTabs.getState();
  store.openWebTab("https://attached.test");
  const id = useCenterTabs.getState().activeId;
  ingestBrowserResource({ id: "attached-association", resource_id: "attached-page", source: "browser", kind: "web",
    session_id: "owner", conversation_session_id: "owner", tab_id: id, window_id: "main", title: "Attached",
    target: "https://attached.test", status: "idle", control_state: "idle", generation: 1, sequence: 1 }, "owner");
  store.closeTab(id, { retainSessionResources: true });
  const state = useCenterTabs.getState();
  assert.ok(state.tabs.some(tab => tab.id === id));
  assert.equal(state.tabs.find(tab => tab.id === id).agentOpened, undefined);
  assert.equal(state.activeId, null);
  assert.deepEqual(topLevelTabs(state.tabs, state.groups), []);
  assert.equal(sessionResourceRows(state.tabs, listedBrowserResources(), "owner").length, 1);
  assert.equal(agentCanAccessWebTab(id, "owner", state), true);
  assert.equal(agentCanAccessWebTab(id, "other", state), false);
  store.openWebTab("https://attached.test");
  assert.ok(topLevelTabs(useCenterTabs.getState().tabs, []).some(tab => tab.id === id));
  resetBrowserResources();
});

test("dismissal removes a retained page from splits and closes only unassociated pages", () => {
  const page = { id: "w:split", kind: "web", title: "Page", url: "https://split.test", agentOpened: true, agentSessionId: "owner", webPinned: true };
  const manual = { id: "w:manual", kind: "web", title: "Manual", url: "https://manual.test" };
  useCenterTabs.setState({ tabs: [page, manual], activeId: page.id, splitWebTabId: page.id,
    groups: [{ id: "g:both", memberIds: [page.id, manual.id], visibleIds: [page.id, manual.id], focusedId: page.id }] });
  const store = useCenterTabs.getState();
  store.closeTab(page.id, { retainSessionResources: true });
  const state = useCenterTabs.getState();
  assert.ok(state.tabs.some(tab => tab.id === page.id));
  assert.ok(state.groups.every(group => !group.memberIds.includes(page.id)));
  assert.equal(state.splitWebTabId, null);
  assert.equal(state.activeId, manual.id);
  store.closeTab(manual.id, { retainSessionResources: true });
  assert.equal(useCenterTabs.getState().activeId, null);
  assert.deepEqual(useCenterTabs.getState().tabs.map(tab => tab.id), [page.id]);
});

test("a tab opened from another one goes to its right, after the ones it opened before", () => {
  const tab = id => ({ id, kind: "web", title: id, url: `https://${id}.test/` });
  useCenterTabs.setState({ tabs: [tab("a"), tab("b"), tab("c")], groups: [], activeId: "a", splitWebTabId: null });
  const store = useCenterTabs.getState();
  const first = store.openPopupWebTab("https://one.test/", "a");
  const second = store.openPopupWebTab("https://two.test/", "a");
  assert.deepEqual(useCenterTabs.getState().tabs.map(t => t.id), ["a", first, second, "b", "c"]);
  useCenterTabs.setState({ activeId: "b" });
  store.openWebTab("https://link.test/");
  assert.deepEqual(useCenterTabs.getState().tabs.map(t => t.url).slice(3, 5), ["https://b.test/", "https://link.test/"]);
});
