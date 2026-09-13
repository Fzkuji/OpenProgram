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
const { topLevelTabs, groupWebPages, revealExistingWebTab } = await import("../../lib/browser/web-page-management.ts");
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
  assert.deepEqual(groupWebPages(state.tabs).map(g => g.sessionId), ["session-a", "session-b", null]);
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

const { canNavigateTabPage } = await import("../../lib/tabs/tab-page-history.ts");
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
