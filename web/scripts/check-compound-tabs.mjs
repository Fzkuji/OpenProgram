import assert from "node:assert/strict";

const groups = await import("../lib/state/center-tab-groups.ts");

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
assert.deepEqual(groups.moveCenterTabGroup(groupedLayout, "g:move", "b"), groupedLayout);
assert.deepEqual(groups.moveCenterTabGroup(groupedLayout, "g:move", "missing"), groupedLayout);
const movedGroup = groups.moveCenterTabGroup(groupedLayout, "g:move", "d");
assert.deepEqual(movedGroup.tabIds, ["c", "a", "b", "d"]);
assert.deepEqual(movedGroup.groups, groupedLayout.groups);

const entries = groups.centerTabStripEntries(result.layout);
assert.deepEqual(entries.map((entry) => entry.id), ["group:g:ab", "tab:d"]);

console.log("compound-tabs pure checks passed");
