/**
 * A desktop window's split layout is its own. Each window keeps its tabs,
 * splits and sizes under `centerTabs:<window id>`, so opening, closing or
 * resizing a split in one window leaves every other window as it was, and a
 * window started again reads back its own layout.
 *
 * One process holds one store, so the test plays the windows in turn: a
 * window "starts" by taking the bridge's window id and reading its own key,
 * as the store does when its page loads.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { installLoader } from "../../_support/load.mjs";

installLoader();

const storage = new Map();
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
};

const { useCenterTabs, replaceCenterTabsPayload } = await import("../../../lib/tabs/center-tabs-store.ts");
const { readCenterTabsPayload } = await import("../../../lib/tabs/center-tabs-persistence.ts");

const state = () => useCenterTabs.getState();
const stored = (windowId) => JSON.parse(storage.get(`centerTabs:${windowId}`));
const splits = (payload) => payload.groups.map((group) => group.memberIds);

/** The window with this id loads its page. */
function start(windowId) {
  globalThis.window.openprogramDesktop = { isDesktop: true, windowId };
  replaceCenterTabsPayload(readCenterTabsPayload(), { persist: false });
}

/** Two conversations side by side in the window that is running. */
function openSplit(left, right) {
  state().openNewTabPage();
  state().openSessionTab(left, left);
  state().openNewTabPage();
  state().openSessionTab(right, right);
  assert.equal(state().groupTab(`s:${right}`, `s:${left}`, 1), true);
}

test("closing, resizing or changing a split in one window leaves the other window's split alone", () => {
  storage.clear();
  start("main");
  openSplit("A", "B");
  start("window-two");
  openSplit("C", "D");
  const other = storage.get("centerTabs:window-two");
  assert.deepEqual(splits(stored("window-two")), [["s:C", "s:D"]]);

  start("main");
  assert.deepEqual(splits(state()), [["s:A", "s:B"]], "the main window reads back its own split");
  state().setSplitRatio(0.6);
  state().ungroupTab("s:B");
  assert.deepEqual(splits(stored("main")), []);
  state().closeTab("s:A");
  state().closeTab("s:B");

  assert.equal(storage.get("centerTabs:window-two"), other, "nothing the main window did touched the other window's layout");
  start("window-two");
  assert.deepEqual(splits(state()), [["s:C", "s:D"]]);
  assert.equal(state().splitRatio, 0.5);
});

test("a new window starts with no tabs and no split, whatever the other windows show", () => {
  storage.clear();
  start("main");
  openSplit("A", "B");
  start("window-new");
  assert.deepEqual(state().tabs, []);
  assert.deepEqual(state().groups, []);
  assert.equal(state().activeId, null);
  assert.deepEqual(splits(stored("main")), [["s:A", "s:B"]], "starting it changed nothing in the main window");
});

test("after a restart each window restores its own layout", () => {
  storage.clear();
  start("main");
  openSplit("A", "B");
  start("window-two");
  state().openNewTabPage();
  state().openSessionTab("C", "C");
  // Restart: both pages load again from what they saved.
  start("main");
  assert.deepEqual(splits(state()), [["s:A", "s:B"]]);
  assert.deepEqual(state().tabs.map((tab) => tab.id), ["s:A", "s:B"]);
  start("window-two");
  assert.deepEqual(splits(state()), []);
  assert.deepEqual(state().tabs.map((tab) => tab.id), ["s:C"]);
});
