import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
    }
    // Extensionless relative imports between source modules (Node needs the
    // extension; TypeScript and the Next build resolve them on their own).
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      // Append to href, not to pathname: pathname is percent-encoded and
      // re-parsing it against the same base double-encodes any space in the
      // repo path.
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
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

// Pointer drag contract: 6px start threshold (≈ Chrome slop); detach needs
// the cursor a full tab-height (40px) past the strip edge, so a small twitch
// doesn't close the slot. Come-home is asymmetric (strip rect) — see
// check-center-tabs.mjs.
assert.equal(drag.DRAG_START_THRESHOLD_PX, 6);
assert.equal(drag.DETACH_HYSTERESIS_PX, 40);
assert.equal(drag.DETACH_DISTANCE_PX, undefined, "the distance dead-zone is gone");

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

// Reorder swaps at 50% overlap of the NEIGHBOUR (not the dragged tab's
// centre crossing a midpoint), so the swap feels immediate and stays
// correct for unequal widths.
assert.equal(drag.SWAP_OVERLAP_RATIO, 0.5);
{
  // Mirror of the strip's slotOverlapRatio (kept local: it is view
  // geometry, not store logic).
  const ratio = (slot, d) => {
    if (slot.width <= 0) return 0;
    const ov = Math.min(slot.left + slot.width, d.left + d.width)
      - Math.max(slot.left, d.left);
    return ov <= 0 ? 0 : Math.min(1, ov / slot.width);
  };
  const neighbour = { left: 208, width: 200 };
  const draggedAt = (left) => ({ left, width: 200 });
  const swaps = (left) => ratio(neighbour, draggedAt(left)) >= drag.SWAP_OVERLAP_RATIO;
  // Equal widths: 50% overlap === leading edge at the neighbour midpoint.
  assert.ok(Math.abs(ratio(neighbour, draggedAt(107)) - 0.495) < 1e-9);
  assert.equal(swaps(107), false, "49.5% overlap must not swap");
  assert.equal(swaps(108), true, "exactly 50% overlap swaps");
  assert.equal(swaps(109), true);
  // 0.49 vs 0.5 boundary stated explicitly.
  assert.equal(ratio(neighbour, { left: 110, width: 196 }) >= 0.5, false);
  // Unequal widths: a narrow tab must still cover half the NEIGHBOUR.
  const narrow = { left: 208, width: 80 };
  assert.equal(ratio(neighbour, narrow) >= 0.5, false, "80px covers 40% of a 200px neighbour");
  const wide = { left: 208, width: 100 };
  assert.equal(ratio(neighbour, wide) >= 0.5, true, "100px covers exactly half");
  // No overlap / degenerate slots never swap.
  assert.equal(ratio(neighbour, { left: 600, width: 200 }), 0);
  assert.equal(ratio({ left: 0, width: 0 }, draggedAt(0)), 0);
}

// ---- Live shift range: ALL crossed tabs move ------------------------
// Dragging across several tabs must slide every tab in between by one
// slot, not just the adjacent one (regression: an early `break` in the
// overlap scan pinned the marker to the nearest neighbour).
{
  const STRIP_GAP = 8;
  const W = 200;
  const step = W + STRIP_GAP;
  const entries = ["A", "B", "C", "D", "E"].map((t) => ({
    id: `tab:${t}`,
    kind: "tab",
    tabId: t,
  }));
  // Local port of computeLiveShifts' before/after ranges (view geometry,
  // not store logic — the strip owns the real one).
  const shiftsFor = (marker, draggedId) => {
    const out = new Map();
    const ti = entries.findIndex((e) => e.tabId === marker.targetTabId);
    const si = entries.findIndex((e) => e.tabId === draggedId);
    const insertion = ti + (marker.mode === "after" ? 1 : 0);
    if (insertion === si || insertion === si + 1) return out;
    if (insertion > si) {
      for (let i = si + 1; i < insertion; i++) out.set(entries[i].tabId, -step);
    } else {
      for (let i = insertion; i < si; i++) out.set(entries[i].tabId, step);
    }
    return out;
  };
  // Drag index 1 (B) to index 4 (E): C, D and E must ALL shift by -step.
  const right = shiftsFor({ mode: "after", targetTabId: "E" }, "B");
  assert.deepEqual(
    [...right.entries()].sort(),
    [["C", -step], ["D", -step], ["E", -step]].sort(),
    "every tab crossed to the right shifts one slot left",
  );
  assert.equal(right.get("A") ?? 0, 0, "tabs before the source never move");
  // Intermediate target: only the tabs actually crossed move.
  const partial = shiftsFor({ mode: "after", targetTabId: "D" }, "B");
  assert.deepEqual(
    [...partial.entries()].sort(),
    [["C", -step], ["D", -step]].sort(),
  );
  assert.equal(partial.get("E") ?? 0, 0);
  // Mirror: drag index 3 (D) to the far left — A, B, C all shift +step.
  const left = shiftsFor({ mode: "before", targetTabId: "A" }, "D");
  assert.deepEqual(
    [...left.entries()].sort(),
    [["A", step], ["B", step], ["C", step]].sort(),
    "every tab crossed to the left shifts one slot right",
  );
  assert.equal(left.get("E") ?? 0, 0);
  // Every shift in a given drag points the same way and is one slot.
  for (const map of [right, partial, left]) {
    const values = [...map.values()];
    assert.equal(new Set(values).size, 1, "one uniform direction per drag");
    assert.equal(Math.abs(values[0]), step, "each shift is exactly one slot");
  }
}

// ---- Fast flick: every shift is exactly ONE slot ---------------------
// Whipping the tab across the whole strip in a single frame must still
// move each crossed tab by one slot — never a multiple, never scaled by
// pointer speed. (The step is a constant derived from the dragged tab's
// width; nothing in the shift path reads velocity.)
{
  const STRIP_GAP = 8;
  const W = 200;
  const step = W + STRIP_GAP;
  const names = ["A", "B", "C", "D", "E", "F"];
  const entries = names.map((t) => ({ id: `tab:${t}`, tabId: t }));
  const shiftsFor = (marker, draggedId) => {
    const out = new Map();
    const ti = entries.findIndex((e) => e.tabId === marker.targetTabId);
    const si = entries.findIndex((e) => e.tabId === draggedId);
    const insertion = ti + (marker.mode === "after" ? 1 : 0);
    if (insertion === si || insertion === si + 1) return out;
    if (insertion > si) {
      for (let i = si + 1; i < insertion; i++) out.set(entries[i].tabId, -step);
    } else {
      for (let i = insertion; i < si; i++) out.set(entries[i].tabId, step);
    }
    return out;
  };
  // One frame, tx jumps from rest to the far end: B (index 1) -> after F.
  const flick = shiftsFor({ mode: "after", targetTabId: "F" }, "B");
  assert.equal(flick.size, 4, "C, D, E and F all yield");
  for (const [tabId, value] of flick) {
    assert.equal(
      Math.abs(value),
      step,
      `${tabId} must move exactly one slot, not ${Math.abs(value) / step}x`,
    );
    assert.equal(value, -step, "and all in the same direction");
  }
  // Mirror flick to the far left.
  const back = shiftsFor({ mode: "before", targetTabId: "A" }, "F");
  assert.equal(back.size, 5);
  for (const value of back.values()) {
    assert.equal(Math.abs(value), step, "one slot regardless of distance");
  }
  // The step never depends on how far the drag travelled.
  const near = shiftsFor({ mode: "after", targetTabId: "C" }, "B");
  const far = shiftsFor({ mode: "after", targetTabId: "F" }, "B");
  assert.equal(
    Math.abs([...near.values()][0]),
    Math.abs([...far.values()][0]),
    "a long flick shifts by the same amount as a short nudge",
  );
}

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
  // a and b already form a complete split entry: it cannot accept c.
  const grouped = [
    { id: "g:ab", memberIds: ["a", "b"], visibleIds: ["a", "b"], focusedId: "a" },
  ];
  assert.deepEqual(
    splitCandidates(pickerTabs, grouped, "a").map((t) => t.id),
    [],
    "an existing split entry cannot grow a hidden third member",
  );
  assert.deepEqual(
    splitCandidates(pickerTabs, grouped, "c").map((t) => t.id),
    [],
    "members of another complete split are not selectable targets",
  );
  // A lone tab has nothing to pair with.
  assert.deepEqual(splitCandidates([pickerTabs[0]], [], "a"), []);
  // Two chats can now split against each other.
  const twoSessions = [
    { id: "a", kind: "session", title: "A" },
    { id: "a2", kind: "session", title: "A2" },
  ];
  assert.deepEqual(
    splitCandidates(twoSessions, [], "a").map((t) => t.id),
    ["a2"],
    "a session tab can pair with another session tab",
  );
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
assert.equal(result.accepted, false);
assert.deepEqual(result.layout.groups[0].memberIds, ["a", "b"]);
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
assert.equal(mergedWholeGroup.accepted, false);
assert.equal(mergedWholeGroup.layout, wholeGroupLayout);

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
assert.deepEqual(focusedA.groups[0].visibleIds, ["a", "b"]);
assert.equal(focusedA.groups[0].focusedId, "a");
const dissolved = groups.ungroupCenterTab(focusedA, "b");
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
// Two chats split side by side are SYMMETRIC: both render as React panes
// and the singleton legacy shell backs neither (it can only exist once).
assert.deepEqual(groups.resolveCenterTabPanes(sessionsOnly, paneTabs, "s:b"), [
  { key: "peer:s:a", kind: "peer", tabId: "s:a" },
  { key: "peer:s:b", kind: "peer", tabId: "s:b" },
]);
// Which session is focused changes no layout — both stay peer panes, same
// order. Focus is only bookkeeping (URL / right rail / DAG).
assert.deepEqual(
  groups.resolveCenterTabPanes({ ...sessionsOnly, focusedId: "s:a" }, paneTabs, "s:a"),
  [
    { key: "peer:s:a", kind: "peer", tabId: "s:a" },
    { key: "peer:s:b", kind: "peer", tabId: "s:b" },
  ],
  "focus must not reorder or reshape the panes",
);
// A LONE session still uses the legacy singleton shell — the unchanged,
// non-split path.
assert.deepEqual(groups.resolveCenterTabPanes(undefined, paneTabs, "s:a"), [
  { key: "session", kind: "session", activeTabId: "s:a", memberIds: ["s:a"] },
]);

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

assert.deepEqual(groups.resolveCenterTabPanes({
  ...webOnly,
  visibleIds: ["missing", "w:two"],
  focusedId: "missing",
}, paneTabs, "missing"), [
  { key: "w:two", kind: "tab", tabId: "w:two" },
]);

const entries = groups.centerTabStripEntries(result.layout);
assert.deepEqual(entries.map((entry) => entry.id), ["group:g:ab", "tab:c", "tab:d"]);

const storageValues = new Map([
  ["centerTabs", JSON.stringify({
    tabs: [
      { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
      { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
      { id: "b:extensions", kind: "builtin", title: "Extensions", page: "extensions" },
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

// The persisted payload shape now lives in its own persistence module
// (the store re-exports the type); its single interface definition is
// asserted there.
const persistenceSource = await readFile(
  new URL("../lib/state/center-tabs-persistence.ts", import.meta.url),
  "utf8",
);
assert.equal(
  persistenceSource.match(/export interface CenterTabsPersistedPayload/g)?.length,
  1,
  "the unified persisted payload has one exported definition",
);

const tabItemsSource = await readFile(
  new URL("../components/center-tabs/tab-items.tsx", import.meta.url),
  "utf8",
);
const compoundItemSource = tabItemsSource.slice(
  tabItemsSource.indexOf("export function CompoundTabItem"),
  tabItemsSource.indexOf("/** One strip tab."),
);
assert.equal(
  compoundItemSource.match(/role="tab"/g)?.length,
  1,
  "a split group must expose one top-level tab target",
);
assert.doesNotMatch(
  compoundItemSource,
  /<TabItem/,
  "split members must not render as independent top-level tabs",
);
assert.doesNotMatch(
  compoundItemSource,
  /group\.memberIds\.map/,
  "the top strip must not render one selectable segment per split member",
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
assert.equal(
  migrated.tabs.some((tab) => tab.kind === "builtin" && tab.page === "extensions"),
  false,
  "removed built-in pages must be discarded from persisted browser state",
);
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

// A newly accepted pair is one atomic UI transition: it becomes the active
// entry immediately and always starts from an even divider, independent of a
// ratio left by an older split.
useCenterTabs.setState({
  tabs: wholeGroupTabs,
  groups: [],
  activeId: "d",
  splitWebTabId: null,
  splitRatio: 0.37,
});
storageWrites.length = 0;
assert.equal(useCenterTabs.getState().groupTab("a", "b", 1, "g:new"), true);
let newlyGroupedState = useCenterTabs.getState();
assert.deepEqual(newlyGroupedState.groups[0].memberIds, ["b", "a"]);
assert.equal(newlyGroupedState.activeId, "a", "the selected member activates the new pair immediately");
assert.equal(newlyGroupedState.splitRatio, 0.5, "a new pair never inherits an old ratio");
assert.equal(storageWrites.length, 1, "group, activation and ratio persist atomically");

useCenterTabs.setState({
  tabs: wholeGroupTabs,
  groups: wholeGroupLayout.groups,
  activeId: "b",
  splitWebTabId: null,
  splitRatio: 0.44,
});
storageWrites.length = 0;
assert.equal(useCenterTabs.getState().mergeGroup("g:whole", "c", 1), false);
let wholeGroupState = useCenterTabs.getState();
assert.deepEqual(wholeGroupState.tabs.map((tab) => tab.id), ["a", "b", "c", "d"]);
assert.deepEqual(wholeGroupState.groups[0].memberIds, ["a", "b"]);
assert.equal(wholeGroupState.activeId, "b");
assert.equal(wholeGroupState.groups[0].focusedId, "b");
assert.deepEqual(wholeGroupState.groups[0].visibleIds, ["a", "b"]);
assert.equal(storageWrites.length, 0, "a complete split group rejects a third member");

useCenterTabs.setState({
  tabs: wholeGroupTabs,
  groups: wholeGroupLayout.groups,
  activeId: "c",
  splitWebTabId: null,
  splitRatio: 0.44,
});
assert.equal(useCenterTabs.getState().mergeGroup("g:whole", "c", 1), false);
wholeGroupState = useCenterTabs.getState();
assert.equal(wholeGroupState.activeId, "c");
assert.equal(wholeGroupState.groups[0].focusedId, "b");
assert.deepEqual(wholeGroupState.groups[0].visibleIds, ["a", "b"]);

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
state.setActive("w:one");
state = useCenterTabs.getState();
state.ungroupTab("s:chat");
state = useCenterTabs.getState();
assert.deepEqual(state.groups, [], "dissolving a composite removes the group");
assert.equal(
  state.splitWebTabId,
  null,
  "dissolving a composite clears split ownership even when the web member is active",
);

state.setActive("s:chat");
state.setSplitWebTab("w:one");
state = useCenterTabs.getState();
state.moveTab("s:chat", "w:two");
state = useCenterTabs.getState();
assert.deepEqual(state.groups, [], "moving the active split session must detach it");
assert.equal(state.splitWebTabId, null);
assert.deepEqual(state.tabs.map((tab) => tab.id), ["w:one", "s:chat", "w:two"]);

state.setSplitWebTab("w:one");
state = useCenterTabs.getState();
assert.equal(state.groupTab("w:two", "s:chat", 2), false);
state = useCenterTabs.getState();
assert.deepEqual(state.groups[0].memberIds, ["s:chat", "w:one"]);
assert.deepEqual(state.groups[0].visibleIds, ["s:chat", "w:one"]);
let persisted = JSON.parse(storageValues.get("centerTabs:main"));
assert.equal(persisted.groups[0].visibleIds.includes(persisted.groups[0].focusedId), true);

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

useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id: "f:p:readme.md", kind: "file", title: "readme.md", projectId: "p", path: "readme.md" },
    { id: "w:pip", kind: "web", title: "Pip", url: "https://pip.test/" },
  ],
  groups: [{
    id: "g:file",
    memberIds: ["s:chat", "f:p:readme.md"],
    visibleIds: ["s:chat", "f:p:readme.md"],
    focusedId: "s:chat",
  }],
  activeId: "s:chat",
  splitWebTabId: null,
  splitRatio: 0.5,
});
useCenterTabs.getState().setSplitWebTab("w:pip");
state = useCenterTabs.getState();
assert.deepEqual(
  state.groups[0].memberIds,
  ["s:chat", "w:pip"],
  "pip expand must evict the file member and pair session+web",
);
assert.deepEqual(state.groups[0].visibleIds, ["s:chat", "w:pip"]);
assert.equal(state.splitWebTabId, "w:pip");
assert.equal(state.tabs.some((tab) => tab.id === "f:p:readme.md"), true);
assert.equal(
  state.groups.some((group) => group.memberIds.includes("f:p:readme.md")),
  false,
);

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
