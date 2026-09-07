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

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const { topLevelTabs, groupWebPages } = await import("../lib/state/web-page-management.ts");
const { normalizeCenterTabsPayload } = await import("../lib/state/center-tabs-persistence.ts");

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
  assert.equal(groupWebPages(tabs).find(g=>g.agent).sessionId, null);
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
