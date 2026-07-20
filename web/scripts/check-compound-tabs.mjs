import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const groups = await import("../lib/state/center-tab-groups.ts");
const drag = await import("../lib/tab-drag-coordinator.ts");

const coordinator = drag.createTabDragCoordinator();
const tabPrepared = {
  subject: { kind: "tab", tabIds: ["a"] },
  started: false,
  cancelled: false,
  committed: false,
};
assert.equal(coordinator.prepare(tabPrepared), tabPrepared, "prepare returns its payload synchronously");
assert.equal(coordinator.current(), tabPrepared, "prepare must expose the same payload synchronously");
assert.equal(coordinator.start(), tabPrepared);
assert.equal(tabPrepared.started, true);
assert.equal(coordinator.start(), null, "start is single-use");
assert.equal(coordinator.commit(), tabPrepared);
assert.equal(tabPrepared.committed, true);
assert.equal(coordinator.current(), null);
assert.equal(coordinator.commit(), null, "commit is single-use");

const clickPrepared = {
  subject: { kind: "tab", tabIds: ["click"] },
  started: false,
  cancelled: false,
  committed: false,
};
coordinator.prepare(clickPrepared);
assert.equal(coordinator.cancel(), clickPrepared, "pointer release before start cancels");
assert.equal(clickPrepared.cancelled, true);
assert.equal(coordinator.current(), null, "cancellation clears the prepared entry");
assert.equal(coordinator.cancel(), null, "cancel is single-use");

coordinator.prepare({
  subject: { kind: "tab", tabIds: ["clear"] },
  started: false,
  cancelled: false,
  committed: false,
});
coordinator.clear();
assert.equal(coordinator.current(), null, "clear removes the prepared entry");

// Desktop preparation: the main-process transfer token rides the same
// prepared record through start, and cancel hands it back for release.
const tokenPrepared = {
  subject: { kind: "tab", tabIds: ["t"] },
  transferToken: "token-1",
  started: false,
  cancelled: false,
  committed: false,
};
coordinator.prepare(tokenPrepared);
assert.equal(coordinator.current()?.transferToken, "token-1");
assert.equal(coordinator.start()?.transferToken, "token-1", "start keeps the prepared token");
assert.equal(coordinator.cancel()?.transferToken, "token-1", "cancel returns the token for main-process release");

const segmentGroup = {
  id: "g:segment",
  memberIds: ["a", "b", "c"],
  visibleIds: ["a", "c"],
  focusedId: "c",
};
const segmentPrepared = {
  subject: {
    kind: "segment",
    tabIds: ["b"],
    sourceGroup: segmentGroup,
    memberIndex: 1,
  },
  started: false,
  cancelled: false,
  committed: false,
};
coordinator.prepare(segmentPrepared);
assert.equal(coordinator.current()?.subject.memberIndex, 1);
assert.deepEqual(coordinator.current()?.subject.sourceGroup.visibleIds, ["a", "c"]);
assert.equal(coordinator.current()?.subject.sourceGroup.focusedId, "c");

const groupPrepared = {
  subject: { kind: "group", tabIds: [...segmentGroup.memberIds], sourceGroup: segmentGroup },
  started: false,
  cancelled: false,
  committed: false,
};
coordinator.prepare(groupPrepared);
assert.deepEqual(coordinator.current()?.subject.tabIds, ["a", "b", "c"]);

// Chrome-style midpoint reorder: position only ever yields before/after —
// left of the target midpoint is before, right is after. Merge is never
// positional; it is a dwell upgrade owned by the strip's onDragOver.
const rect = { left: 100, width: 200 };
const target = { tabId: "target", groupId: "g:target", memberIndex: 2 };
assert.deepEqual(drag.resolveTabDropIntent(rect, 100, target), {
  mode: "before",
  targetTabId: "target",
});
assert.deepEqual(drag.resolveTabDropIntent(rect, 199.999, target), {
  mode: "before",
  targetTabId: "target",
});
assert.deepEqual(drag.resolveTabDropIntent(rect, 200, target), {
  mode: "after",
  targetTabId: "target",
});
assert.deepEqual(drag.resolveTabDropIntent(rect, 300, target), {
  mode: "after",
  targetTabId: "target",
});

// Pointer drag contract: 4px start threshold, 48px vertical detach.
assert.equal(drag.DRAG_START_THRESHOLD_PX, 4);
assert.equal(drag.DETACH_DISTANCE_PX, 48);

// Dragging in the strip is PURE REORDER — Chrome's model. Every merge
// measure is gone; splitting is an explicit context-menu action.
assert.equal(drag.mergeCoverage, undefined, "the coverage measure is gone");
assert.equal(drag.MERGE_COVERAGE_THRESHOLD, undefined);
assert.equal(drag.isInMergeZone, undefined, "the centre-point test is gone");
assert.equal(drag.MERGE_EDGE_FRACTION, undefined);
assert.equal(drag.MERGE_LEADING_FRACTION, undefined);
assert.equal(drag.MERGE_DWELL_MS, undefined);
assert.equal(drag.PANE_MERGE_DWELL_MS, undefined, "the pane dwell is gone");
// Reorder intents remain purely positional: midpoint before/after.
assert.deepEqual(drag.resolveTabDropIntent(rect, 150, target), {
  mode: "before",
  targetTabId: "target",
});
assert.deepEqual(drag.resolveTabDropIntent(rect, 250, target), {
  mode: "after",
  targetTabId: "target",
});

// ---- Split picker candidates -----------------------------------------
// Exclude the subject itself and anything already sharing its split
// group; everything else in the window is offerable.
{
  const { splitCandidates } = groups;
  const pickerTabs = [
    { id: "a", kind: "session", title: "A" },
    { id: "b", kind: "web", title: "B", url: "https://b.test/x" },
    { id: "c", kind: "file", title: "C", path: "/p/c.ts" },
  ];
  // No groups: every other tab is a candidate.
  assert.deepEqual(
    splitCandidates(pickerTabs, [], "a").map((t) => t.id),
    ["b", "c"],
    "the subject itself is never offered",
  );
  // a and b already split together: only c remains.
  const grouped = [
    { id: "g:ab", memberIds: ["a", "b"], visibleIds: ["a", "b"], focusedId: "a" },
  ];
  assert.deepEqual(
    splitCandidates(pickerTabs, grouped, "a").map((t) => t.id),
    ["c"],
    "existing split members are not offered again",
  );
  // A lone tab has nothing to pair with.
  assert.deepEqual(splitCandidates([pickerTabs[0]], [], "a"), []);
}

const broken = groups.normalizeCenterTabLayout({
  tabIds: ["a", "b", "c", "d"],
  groups: [{
    id: "g:one",
    memberIds: ["a", "c"],
    visibleIds: ["a", "c", "d"],
    focusedId: "missing",
  }],
});
assert.deepEqual(broken.tabIds, ["a", "c", "b", "d"]);
assert.deepEqual(broken.groups, [{
  id: "g:one",
  memberIds: ["a", "c"],
  visibleIds: ["a", "c"],
  focusedId: "a",
}]);

const oneMember = groups.normalizeCenterTabLayout({
  tabIds: ["a", "b"],
  groups: [{ id: "g:one", memberIds: ["a"], visibleIds: ["a"], focusedId: "a" }],
});
assert.deepEqual(oneMember.groups, []);

const duplicateGroupIds = groups.normalizeCenterTabLayout({
  tabIds: ["a", "b", "c", "d"],
  groups: [
    { id: "g:duplicate", memberIds: ["a", "b"], visibleIds: ["a", "b"], focusedId: "a" },
    { id: "g:duplicate", memberIds: ["c", "d"], visibleIds: ["c", "d"], focusedId: "c" },
  ],
});
assert.deepEqual(duplicateGroupIds.groups, [{
  id: "g:duplicate",
  memberIds: ["a", "b"],
  visibleIds: ["a", "b"],
  focusedId: "a",
}]);
assert.deepEqual(
  groups.centerTabStripEntries(duplicateGroupIds).map((entry) => entry.id),
  ["group:g:duplicate", "tab:c", "tab:d"],
);

const normalLayout = { tabIds: ["a", "b", "c"], groups: [] };
assert.deepEqual(groups.moveCenterTab(normalLayout, "missing", "a"), normalLayout);
assert.deepEqual(groups.moveCenterTab(normalLayout, "c", "missing"), normalLayout);
assert.deepEqual(groups.moveCenterTab(normalLayout, "b", "b"), normalLayout);
for (const [sourceId, targetId] of [["missing", "a"], ["c", "missing"]]) {
  const rejected = groups.groupCenterTabs(
    normalLayout,
    sourceId,
    targetId,
    1,
    "g:missing",
  );
  assert.equal(rejected.accepted, false);
  assert.deepEqual(rejected.layout, normalLayout);
}

let result = groups.groupCenterTabs(
  { tabIds: ["a", "b", "c", "d"], groups: [] },
  "b",
  "a",
  1,
  "g:ab",
);
assert.equal(result.accepted, true);
assert.deepEqual(result.layout.groups[0].memberIds, ["a", "b"]);
assert.deepEqual(result.layout.tabIds, ["a", "b", "c", "d"]);

result = groups.groupCenterTabs(result.layout, "c", "a", 2, "unused");
assert.equal(result.accepted, true);
assert.deepEqual(result.layout.groups[0].memberIds, ["a", "b", "c"]);
assert.deepEqual(result.layout.groups[0].visibleIds, ["a", "b"]);

const full = groups.groupCenterTabs(result.layout, "d", "a", 3, "unused");
assert.equal(full.accepted, false);
assert.deepEqual(full.layout, result.layout);

const wholeGroupLayout = {
  tabIds: ["a", "b", "c", "d"],
  groups: [{
    id: "g:whole",
    memberIds: ["a", "b"],
    visibleIds: ["a", "b"],
    focusedId: "b",
  }],
};
const mergedWholeGroup = groups.mergeCenterTabGroup(
  wholeGroupLayout,
  "g:whole",
  "c",
  1,
);
assert.equal(mergedWholeGroup.accepted, true);
assert.deepEqual(mergedWholeGroup.layout.tabIds, ["c", "a", "b", "d"]);
assert.deepEqual(mergedWholeGroup.layout.groups, [{
  id: "g:whole",
  memberIds: ["c", "a", "b"],
  visibleIds: ["a", "b"],
  focusedId: "b",
}]);

const threeMemberGroupLayout = {
  tabIds: ["a", "b", "c", "d"],
  groups: [{
    id: "g:full-source",
    memberIds: ["a", "b", "c"],
    visibleIds: ["a", "b"],
    focusedId: "b",
  }],
};
const rejectedWholeGroup = groups.mergeCenterTabGroup(
  threeMemberGroupLayout,
  "g:full-source",
  "d",
  1,
);
assert.equal(rejectedWholeGroup.accepted, false);
assert.equal(rejectedWholeGroup.layout, threeMemberGroupLayout);

const focusedA = groups.focusCenterTabGroupMember(result.layout, "g:ab", "a");
const focusedC = groups.focusCenterTabGroupMember(focusedA, "g:ab", "c");
assert.deepEqual(focusedC.groups[0].visibleIds, ["c", "b"]);
assert.equal(focusedC.groups[0].focusedId, "c");

const reorderedWithinGroup = groups.groupCenterTabs(focusedC, "c", "a", 1, "unused");
assert.equal(reorderedWithinGroup.accepted, true);
assert.deepEqual(reorderedWithinGroup.layout.tabIds, ["a", "c", "b", "d"]);
assert.deepEqual(reorderedWithinGroup.layout.groups[0].memberIds, ["a", "c", "b"]);
assert.deepEqual(reorderedWithinGroup.layout.groups[0].visibleIds, ["c", "b"]);
assert.equal(reorderedWithinGroup.layout.groups[0].focusedId, "c");

const ungroupedC = groups.ungroupCenterTab(focusedC, "c", "d");
assert.deepEqual(ungroupedC.groups[0].memberIds, ["a", "b"]);
assert.deepEqual(ungroupedC.tabIds, ["a", "b", "c", "d"]);
const dissolved = groups.ungroupCenterTab(ungroupedC, "b");
assert.deepEqual(dissolved.groups, []);

const moved = groups.moveCenterTab(
  { tabIds: ["a", "b", "c"], groups: [] },
  "c",
  "a",
);
assert.deepEqual(moved.tabIds, ["c", "a", "b"]);

const groupedLayout = {
  tabIds: ["a", "b", "c", "d"],
  groups: [{
    id: "g:move",
    memberIds: ["a", "b"],
    visibleIds: ["a", "b"],
    focusedId: "a",
  }],
};
assert.deepEqual(groups.moveCenterTab(groupedLayout, "b", "b"), groupedLayout);
assert.deepEqual(groups.moveCenterTabGroup(groupedLayout, "g:move", "b"), groupedLayout);
assert.deepEqual(groups.moveCenterTabGroup(groupedLayout, "g:move", "missing"), groupedLayout);
const movedGroup = groups.moveCenterTabGroup(groupedLayout, "g:move", "d");
assert.deepEqual(movedGroup.tabIds, ["c", "a", "b", "d"]);
assert.deepEqual(movedGroup.groups, groupedLayout.groups);

const paneTabs = [
  { id: "s:a", kind: "session", title: "A", sessionId: "a" },
  { id: "s:b", kind: "session", title: "B", sessionId: "b" },
  { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
  { id: "w:two", kind: "web", title: "Two", url: "https://two.test/" },
];
const sessionsOnly = {
  id: "g:sessions",
  memberIds: ["s:a", "s:b"],
  visibleIds: ["s:a", "s:b"],
  focusedId: "s:b",
};
assert.deepEqual(groups.resolveCenterTabPanes(sessionsOnly, paneTabs, "s:b"), [{
  key: "session",
  kind: "session",
  activeTabId: "s:b",
  memberIds: ["s:a", "s:b"],
}]);

const sessionAndWeb = {
  ...sessionsOnly,
  memberIds: ["s:a", "w:one"],
  visibleIds: ["s:a", "w:one"],
  focusedId: "w:one",
};
assert.deepEqual(
  groups.resolveCenterTabPanes(sessionAndWeb, paneTabs, "w:one").map((pane) => pane.kind),
  ["session", "tab"],
);

const webOnly = {
  id: "g:web",
  memberIds: ["w:one", "w:two"],
  visibleIds: ["w:one", "w:two"],
  focusedId: "w:two",
};
assert.deepEqual(groups.resolveCenterTabPanes(webOnly, paneTabs, "w:two"), [
  { key: "w:one", kind: "tab", tabId: "w:one" },
  { key: "w:two", kind: "tab", tabId: "w:two" },
]);

const hiddenThird = {
  id: "g:hidden",
  memberIds: ["s:a", "w:one", "w:two"],
  visibleIds: ["s:a", "w:one", "w:two"],
  focusedId: "w:two",
};
assert.equal(groups.resolveCenterTabPanes(hiddenThird, paneTabs, "w:two").length, 2);
assert.deepEqual(groups.resolveCenterTabPanes({
  ...webOnly,
  visibleIds: ["missing", "w:two"],
  focusedId: "missing",
}, paneTabs, "missing"), [
  { key: "w:two", kind: "tab", tabId: "w:two" },
]);

const memberLayout = {
  tabIds: ["s:a", "w:one", "w:two"],
  groups: [{
    id: "g:member",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "w:one",
  }],
};
const movedMember = groups.moveCenterTabGroupMember(
  memberLayout,
  "g:member",
  "w:two",
  0,
);
assert.deepEqual(movedMember.groups[0].memberIds, ["w:two", "s:a", "w:one"]);
assert.deepEqual(movedMember.tabIds, ["w:two", "s:a", "w:one"]);
assert.deepEqual(movedMember.groups[0].visibleIds, ["s:a", "w:one"]);
assert.equal(movedMember.groups[0].focusedId, "w:one");
assert.deepEqual(
  movedMember.tabIds.slice(0, movedMember.groups[0].memberIds.length),
  movedMember.groups[0].memberIds,
);

const entries = groups.centerTabStripEntries(result.layout);
assert.deepEqual(entries.map((entry) => entry.id), ["group:g:ab", "tab:d"]);

const storageValues = new Map([
  ["centerTabs", JSON.stringify({
    tabs: [
      { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
      { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
    ],
    activeId: "s:chat",
  })],
  ["openprogram.webSplit", JSON.stringify({ tabId: "w:one", ratio: 0.51 })],
]);
const storageReads = [];
const storageWrites = [];
globalThis.window = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  location: { pathname: "/chat" },
  openprogramDesktop: { isDesktop: true, windowId: "main" },
};
globalThis.localStorage = {
  getItem(key) {
    storageReads.push(key);
    return storageValues.get(key) ?? null;
  },
  setItem(key, value) {
    storageWrites.push(key);
    storageValues.set(key, String(value));
  },
  removeItem: (key) => storageValues.delete(key),
};

const storeSource = await readFile(
  new URL("../lib/state/center-tabs-store.ts", import.meta.url),
  "utf8",
);
assert.equal(
  storeSource.match(/export interface CenterTabsPersistedPayload/g)?.length,
  1,
  "the unified persisted payload has one exported definition",
);

const { useCenterTabs } = await import(
  "../lib/state/center-tabs-store.ts?compound-main"
);
const migrated = JSON.parse(storageValues.get("centerTabs:main"));
assert.deepEqual(Object.keys(migrated).sort(), [
  "activeId",
  "groups",
  "splitRatio",
  "splitWebTabId",
  "tabs",
  "version",
]);
assert.equal(migrated.version, 2);
assert.equal(migrated.activeId, "s:chat");
assert.equal(migrated.splitWebTabId, "w:one");
assert.equal(migrated.splitRatio, 0.51);
assert.deepEqual(migrated.groups[0].memberIds, ["s:chat", "w:one"]);
assert.equal(
  [...storageReads, ...storageWrites].includes("openprogram.centerTabGroups"),
  false,
  "group state must never use a separate storage key",
);

const wholeGroupTabs = [
  { id: "a", kind: "web", title: "A", url: "https://a.test/" },
  { id: "b", kind: "web", title: "B", url: "https://b.test/" },
  { id: "c", kind: "web", title: "C", url: "https://c.test/" },
  { id: "d", kind: "web", title: "D", url: "https://d.test/" },
];
useCenterTabs.setState({
  tabs: wholeGroupTabs,
  groups: wholeGroupLayout.groups,
  activeId: "b",
  splitWebTabId: null,
  splitRatio: 0.44,
});
storageWrites.length = 0;
assert.equal(useCenterTabs.getState().mergeGroup("g:whole", "c", 1), true);
let wholeGroupState = useCenterTabs.getState();
assert.deepEqual(wholeGroupState.tabs.map((tab) => tab.id), ["c", "a", "b", "d"]);
assert.deepEqual(wholeGroupState.groups[0].memberIds, ["c", "a", "b"]);
assert.equal(wholeGroupState.activeId, "b");
assert.equal(wholeGroupState.groups[0].focusedId, "b");
assert.deepEqual(wholeGroupState.groups[0].visibleIds, ["a", "b"]);
assert.equal(storageWrites.length, 1, "whole-group merge must persist atomically");

useCenterTabs.setState({
  tabs: wholeGroupTabs,
  groups: wholeGroupLayout.groups,
  activeId: "c",
  splitWebTabId: null,
  splitRatio: 0.44,
});
assert.equal(useCenterTabs.getState().mergeGroup("g:whole", "c", 1), true);
wholeGroupState = useCenterTabs.getState();
assert.equal(wholeGroupState.activeId, "c");
assert.equal(wholeGroupState.groups[0].focusedId, "c");
assert.deepEqual(wholeGroupState.groups[0].visibleIds, ["a", "c"]);

useCenterTabs.setState({
  tabs: migrated.tabs,
  groups: migrated.groups,
  activeId: migrated.activeId,
  splitWebTabId: migrated.splitWebTabId,
  splitRatio: migrated.splitRatio,
});

const thirdTab = {
  id: "w:two",
  kind: "web",
  title: "Two",
  url: "https://two.test/",
};
useCenterTabs.setState({
  tabs: [...useCenterTabs.getState().tabs, thirdTab],
  groups: [],
  activeId: "s:chat",
  splitWebTabId: null,
  splitRatio: 0.44,
});
storageWrites.length = 0;
useCenterTabs.getState().setSplitWebTab("w:one");
let state = useCenterTabs.getState();
assert.equal(state.activeId, "s:chat", "opening split preserves the session");
assert.equal(state.splitWebTabId, "w:one");
assert.deepEqual(state.groups[0].memberIds, ["s:chat", "w:one"]);
assert.deepEqual(state.groups[0].visibleIds, ["s:chat", "w:one"]);
assert.equal(storageWrites.length, 1, "one mutation writes one payload");
assert.equal(storageWrites[0], "centerTabs:main");

state.ungroupTab("s:chat");
state = useCenterTabs.getState();
assert.deepEqual(state.groups, [], "ungrouping the active split session must persist");
assert.equal(state.splitWebTabId, null);

state.setSplitWebTab("w:one");
state = useCenterTabs.getState();
state.moveTab("s:chat", "w:two");
state = useCenterTabs.getState();
assert.deepEqual(state.groups, [], "moving the active split session must detach it");
assert.equal(state.splitWebTabId, null);
assert.deepEqual(state.tabs.map((tab) => tab.id), ["w:one", "s:chat", "w:two"]);

state.setSplitWebTab("w:one");
state = useCenterTabs.getState();
assert.equal(state.groupTab("w:two", "s:chat", 2), true);
state = useCenterTabs.getState();
const groupId = state.groups[0].id;
state.setActive("w:two");
state = useCenterTabs.getState();
assert.deepEqual(state.groups[0].memberIds, ["s:chat", "w:one", "w:two"]);
assert.deepEqual(state.groups[0].visibleIds, ["w:two", "w:one"]);
assert.equal(state.groups[0].focusedId, "w:two");
let persisted = JSON.parse(storageValues.get("centerTabs:main"));
assert.equal(persisted.groups[0].visibleIds.includes(persisted.groups[0].focusedId), true);

state.moveGroupMember(groupId, "w:two", 0);
state = useCenterTabs.getState();
assert.deepEqual(state.groups[0].memberIds, ["w:two", "s:chat", "w:one"]);
assert.deepEqual(
  state.tabs.slice(0, state.groups[0].memberIds.length).map((tab) => tab.id),
  state.groups[0].memberIds,
);

state.ungroupTab("w:two");
useCenterTabs.getState().closeTab("w:one");
state = useCenterTabs.getState();
assert.deepEqual(state.groups, [], "a one-member group dissolves after close");
assert.equal(state.splitWebTabId, null, "closing the split web member clears split state");
persisted = JSON.parse(storageValues.get("centerTabs:main"));
assert.deepEqual(persisted.groups, []);
assert.equal(persisted.splitWebTabId, null);

useCenterTabs.setState({
  tabs: [{ id: "w:one", kind: "web", title: "One", url: "https://one.test/" }],
  groups: [],
  activeId: "w:one",
  splitWebTabId: null,
  splitRatio: 0.44,
});
useCenterTabs.getState().setSplitWebTab("w:one");
assert.deepEqual(useCenterTabs.getState().groups, []);
const draftId = useCenterTabs.getState().openDraftSessionTab();
state = useCenterTabs.getState();
assert.deepEqual(state.groups[0].memberIds, [`s:${draftId}`, "w:one"]);
assert.deepEqual(state.groups[0].visibleIds, [`s:${draftId}`, "w:one"]);
assert.equal(state.activeId, `s:${draftId}`);

const mainPayload = storageValues.get("centerTabs:main");
window.openprogramDesktop.windowId = "secondary";
const { useCenterTabs: secondaryTabs } = await import(
  "../lib/state/center-tabs-store.ts?compound-secondary"
);
assert.deepEqual(secondaryTabs.getState().tabs, []);
secondaryTabs.getState().openNewTabPage();
assert.equal(storageValues.get("centerTabs:main"), mainPayload);
assert.equal(JSON.parse(storageValues.get("centerTabs:secondary")).tabs.length, 1);
assert.equal(
  [...storageReads, ...storageWrites].includes("openprogram.centerTabGroups"),
  false,
);

console.log("compound-tabs store checks passed");
