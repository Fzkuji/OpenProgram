import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return { url: new URL("../../lib/session-store/index.ts", import.meta.url).href, shortCircuit: true };
    }
    if (specifier.startsWith("@/")) {
      return { url: new URL(`../../${specifier.slice(2)}.ts`, import.meta.url).href, shortCircuit: true };
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
const { MAX_CLOSED_TABS } = await import("../../lib/tabs/store/closed-tabs.ts");
const state = () => useCenterTabs.getState();
const active = () => state().tabs.find(t => t.id === state().activeId);
function reset() {
  useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null, closedTabs: [], navigationRoute: undefined, fileNavigationHistory: { entries: [], index: -1 }, fileNavigationRestore: null });
}
function openSessions(...ids) {
  for (const id of ids) {
    state().openNewTabPage();
    state().openSessionTab(id, id.toUpperCase());
  }
}

test("closing then reopening brings the tab back in place and focused", () => {
  reset();
  openSessions("a", "b", "c");
  const closing = state().tabs[1];
  state().closeTab(closing.id);
  assert.deepEqual(state().tabs.map(t => t.sessionId), ["a", "c"]);
  assert.equal(sessionAckIsActive("b"), false);

  const id = state().reopenClosedTab();
  assert.equal(id, closing.id);
  assert.deepEqual(state().tabs.map(t => t.sessionId), ["a", "b", "c"]);
  assert.equal(active().sessionId, "b");
  assert.equal(sessionAckIsActive("b"), true);
  assert.equal(state().closedTabs.length, 0);
  assert.equal(state().reopenClosedTab(), null);
});

test("the stack reopens newest first and keeps the last ten", () => {
  reset();
  const ids = Array.from({ length: MAX_CLOSED_TABS + 2 }, (_, i) => `s${i}`);
  openSessions(...ids);
  for (const tab of [...state().tabs]) state().closeTab(tab.id);
  assert.equal(state().closedTabs.length, MAX_CLOSED_TABS);
  state().reopenClosedTab();
  assert.equal(active().sessionId, ids.at(-1));
  state().reopenClosedTab();
  assert.equal(active().sessionId, ids.at(-2));
});

test("drafts, new-tab pages and agent pages are not remembered", () => {
  reset();
  state().openNewTabPage();
  state().closeTab(state().activeId);
  const draft = state().openDraftSessionTab();
  state().closeTab(draft);
  assert.equal(state().closedTabs.length, 0);
});

test("a deleted conversation is skipped, never reopened as an empty tab", () => {
  reset();
  openSessions("keep", "gone", "deleted");
  const [keep, gone, deleted] = state().tabs;
  state().closeTab(keep.id);
  state().closeTab(gone.id);
  state().closeTab(deleted.id);
  // Deleted from the sidebar while closed: dropped from the stack.
  state().removeSessionFromHistory("deleted");
  assert.deepEqual(state().closedTabs.map(e => e.tab.sessionId), ["keep", "gone"]);
  // Missing from the conversation list at reopen time: skipped.
  const id = state().reopenClosedTab(sessionId => sessionId === "gone");
  assert.equal(state().tabs.find(t => t.id === id).sessionId, "keep");
  assert.deepEqual(state().tabs.map(t => t.sessionId), ["keep"]);
  assert.equal(state().closedTabs.length, 0);
});

test("a session already open again is focused instead of duplicated", () => {
  reset();
  openSessions("a", "b");
  const b = state().tabs[1];
  state().closeTab(b.id);
  openSessions("b");
  state().setActive(state().tabs[0].id);
  state().reopenClosedTab();
  assert.equal(state().tabs.filter(t => t.sessionId === "b").length, 1);
  assert.equal(active().sessionId, "b");
});
