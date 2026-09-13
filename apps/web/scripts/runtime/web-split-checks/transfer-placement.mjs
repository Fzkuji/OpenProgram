// transfer placement: original sequential assertions and shared fixtures.
export async function run(testContext) {

(testContext.sourceProjectedCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.sourceProjectedCenter.tabs.map((tab) => tab.id), [
  "s:existing",
  "s:local_one",
  "s:user-source",
]);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:user-source",
]);

testContext.pendingProjection.unregisterPendingTransfer(testContext.orderedSourceEntry.token, "secondary");

testContext.assert.equal(testContext.deleteTransferJournal(testContext.orderedSourceEntry.token, "secondary"), true);

testContext.assert.equal(testContext.secondaryTabsModule.replaceCenterTabsPayload(testContext.removed.after, {
  persist: true,
}), true);

(testContext.realTransferPayload = {
  ...testContext.transferPayload,
  tabs: [{ id: "s:real", kind: "session", title: "Real", sessionId: "real" }],
  source: { windowId: "source", kind: "tab" },
  chats: [{ chatKey: "real", wasActive: false }],
});

testContext.assert.equal(testContext.secondaryTabsModule.insertTransferredTabs(
  testContext.realTransferPayload,
  { kind: "strip-end" },
  { persist: false },
).ok, true);

testContext.assert.equal(testContext.secondaryTabsModule.removeTransferredTabs(
  ["s:real"],
  { persist: false },
).ok, true);

testContext.assert.equal(
  testContext.secondaryTabsModule.sessionAckIsActive("real"),
  true,
  "transfer removal must not create a close tombstone",
);

testContext.assert.equal(testContext.values.get("centerTabs:secondary"), testContext.secondaryTabBytes);


(testContext.groupedPayload = {
  ...testContext.transferPayload,
  tabs: [
    { id: "w:left", kind: "web", title: "Left", url: "https://left.test/" },
    { id: "w:right", kind: "web", title: "Right", url: "https://right.test/" },
  ],
  source: {
    windowId: "source",
    kind: "group",
    groupId: "g:source",
    memberIndex: 0,
    memberIds: ["w:left", "w:right"],
    visibleIds: ["w:left", "w:right"],
    focusedId: "w:right",
  },
});

(testContext.grouped = testContext.secondaryTabsModule.insertTransferredTabs(
  testContext.groupedPayload,
  { kind: "before", targetTabId: "s:existing" },
  { persist: false },
));

testContext.assert.equal(testContext.grouped.ok, true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "w:left",
  "w:right",
  "s:existing",
]);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().groups[0], {
  id: "g:source",
  memberIds: ["w:left", "w:right"],
  visibleIds: ["w:left", "w:right"],
  focusedId: "w:right",
});

testContext.assert.equal(testContext.groupedPayload.source.memberIndex, 0);


(testContext.duplicateBefore = structuredClone(testContext.secondaryTabs.getState().tabs));

testContext.assert.deepEqual(
  testContext.secondaryTabsModule.validateTransferredTabs(
    {
      ...testContext.transferPayload,
      tabs: [testContext.groupedPayload.tabs[0]],
      source: { windowId: "source", kind: "tab" },
    },
    { kind: "strip-end" },
  ),
  { ok: false, reason: "duplicate", duplicateId: "w:left" },
);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs, testContext.duplicateBefore);


(testContext.fourthMember = testContext.secondaryTabsModule.validateTransferredTabs(
  {
    ...testContext.groupedPayload,
    tabs: [
      { id: "w:three", kind: "web", title: "Three", url: "https://three.test/" },
      { id: "w:four", kind: "web", title: "Four", url: "https://four.test/" },
    ],
    source: {
      ...testContext.groupedPayload.source,
      groupId: "g:other",
      memberIds: ["w:three", "w:four"],
      visibleIds: ["w:three", "w:four"],
      focusedId: "w:four",
    },
  },
  { kind: "merge", targetTabId: "w:left", memberIndex: 1 },
));

testContext.assert.deepEqual(testContext.fourthMember, { ok: false, reason: "group-full" });


(testContext.groupedRemoval = testContext.secondaryTabsModule.removeTransferredTabs(
  ["w:left", "w:right"],
  { persist: false },
));

testContext.assert.equal(testContext.groupedRemoval.ok, true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), ["s:existing"]);


(testContext.groupedAfter = testContext.secondaryTabsModule.insertTransferredTabs(
  testContext.groupedPayload,
  { kind: "after", targetTabId: "s:existing" },
  { persist: false },
));

testContext.assert.equal(testContext.groupedAfter.ok, true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "w:left",
  "w:right",
]);

testContext.secondaryTabsModule.replaceCenterTabsPayload(testContext.groupedAfter.before, { persist: false });

(testContext.groupedMerge = testContext.secondaryTabsModule.insertTransferredTabs(
  testContext.groupedPayload,
  { kind: "merge", targetTabId: "s:existing", memberIndex: 1 },
  { persist: false },
));

testContext.assert.equal(testContext.groupedMerge.ok, false);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
]);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().groups, []);

(testContext.segmentPayload = {
  ...testContext.transferPayload,
  tabs: [{ id: "w:segment-b", kind: "web", title: "B", url: "https://b.test/" }],
  source: {
    windowId: "source",
    kind: "segment",
    groupId: "g:segment",
    memberIndex: 1,
    memberIds: ["w:segment-a", "w:segment-b", "w:segment-c"],
    visibleIds: ["w:segment-b", "w:segment-c"],
    focusedId: "w:segment-b",
  },
  chats: [],
});

testContext.secondaryTabsModule.replaceCenterTabsPayload({
  version: 2,
  tabs: [{ id: "s:target", kind: "session", title: "Target", sessionId: "target" }],
  activeId: "s:target",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
}, { persist: false });

(testContext.segmentPlacements = [
  { kind: "strip-end" },
  { kind: "before", targetTabId: "s:target" },
  { kind: "after", targetTabId: "s:target" },
  { kind: "merge", targetTabId: "s:target" },
]);

for (const placement of testContext.segmentPlacements) {
  testContext.assert.equal(
    testContext.secondaryTabsModule.validateTransferredTabs(testContext.segmentPayload, placement).ok,
    true,
    `a one-tab segment must be valid for ${placement.kind}`,
  );
}

for (const count of [2, 4]) {
  const names = ["b", "c", "d", "e"].slice(0, count);
  const invalidSegment = {
    ...testContext.segmentPayload,
    tabs: names.map((name) => ({
      id: `w:segment-${name}`,
      kind: "web",
      title: name.toUpperCase(),
      url: `https://${name}.segment.test/`,
    })),
    source: {
      ...testContext.segmentPayload.source,
      memberIds: ["w:segment-a", ...names.map((name) => `w:segment-${name}`), "w:segment-f"],
    },
  };
  for (const placement of testContext.segmentPlacements) {
    testContext.assert.deepEqual(
      testContext.secondaryTabsModule.validateTransferredTabs(invalidSegment, placement),
      { ok: false, reason: "invalid" },
      `${count}-tab segments must be invalid for ${placement.kind}`,
    );
  }
}

(testContext.segmentValidation = testContext.secondaryTabsModule.validateTransferredTabs(
  testContext.segmentPayload,
  { kind: "strip-end" },
));

testContext.assert.equal(testContext.segmentValidation.ok, true);

testContext.assert.deepEqual(testContext.segmentValidation.after.groups, []);

testContext.assert.equal(testContext.secondaryTabsModule.insertTransferredTabs(
  testContext.segmentPayload,
  { kind: "strip-end" },
  { persist: false },
).ok, true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:target",
  "w:segment-b",
]);


testContext.secondaryTabsModule.replaceCenterTabsPayload({
  version: 2,
  tabs: [
    { id: "w:segment-a", kind: "web", title: "A", url: "https://a.test/" },
    { id: "w:segment-b", kind: "web", title: "B", url: "https://b.test/" },
    { id: "w:segment-c", kind: "web", title: "C", url: "https://c.test/" },
  ],
  activeId: "w:segment-b",
  groups: [{
    id: "g:segment",
    memberIds: ["w:segment-a", "w:segment-b", "w:segment-c"],
    visibleIds: ["w:segment-b", "w:segment-c"],
    focusedId: "w:segment-b",
  }],
  splitWebTabId: null,
  splitRatio: 0.45,
}, { persist: false });

(testContext.segmentRemoval = testContext.secondaryTabsModule.removeTransferredTabs(
  ["w:segment-b"],
  { persist: false },
));

testContext.assert.equal(testContext.segmentRemoval.ok, true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "w:segment-a",
  "w:segment-c",
]);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().groups, []);


(testContext.oversizedGroupPayload = {
  ...testContext.groupedPayload,
  tabs: ["one", "two", "three", "four"].map((name) => ({
    id: `w:oversized-${name}`,
    kind: "web",
    title: name,
    url: `https://${name}.oversized.test/`,
  })),
  source: {
    ...testContext.groupedPayload.source,
    groupId: "g:oversized",
    memberIds: [
      "w:oversized-one",
      "w:oversized-two",
      "w:oversized-three",
      "w:oversized-four",
    ],
    visibleIds: ["w:oversized-one", "w:oversized-two"],
    focusedId: "w:oversized-one",
  },
});

(testContext.beforeOversized = structuredClone(testContext.secondaryTabs.getState().tabs));

testContext.assert.deepEqual(
  testContext.secondaryTabsModule.validateTransferredTabs(
    testContext.oversizedGroupPayload,
    { kind: "strip-end" },
  ),
  { ok: false, reason: "group-full" },
);

testContext.assert.equal(testContext.secondaryTabsModule.insertTransferredTabs(
  testContext.oversizedGroupPayload,
  { kind: "strip-end" },
  { persist: false },
).ok, false);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs, testContext.beforeOversized);


({
  applyFileDraftSnapshot: testContext.applyFileDraftSnapshot,
  fileDrafts: testContext.fileDrafts,
  snapshotFileDrafts: testContext.snapshotFileDrafts,
} = await import("../../../lib/files/files-shared.ts"));

(testContext.actualRecoveryHandlers = {
  applyCenterTabs: (payload, options) =>
    testContext.secondaryTabsModule.replaceCenterTabsPayload(payload, options),
  applySession: (snapshot, options) =>
    testContext.secondarySessionModule.applySessionTransfer(snapshot, options),
  applyFileDrafts: testContext.applyFileDraftSnapshot,
  applyBridge: () => {},
  rebuildAccepted: () => {},
  resumeSourceRemoved: () => {},
  clearAccepted: () => {},
  deleteJournal: (token) => testContext.deleteTransferJournal(token, "secondary"),
  snapshotCenterTabs: () => testContext.secondaryTabsModule.snapshotCenterTabsPayload(),
  snapshotSession: () => testContext.secondarySessionModule.snapshotSessionTransfer([]),
});


(testContext.projectionPayload = {
  ...testContext.transferPayload,
  tabs: [{
    id: "s:moving",
    kind: "session",
    title: "Moving",
    sessionId: "moving",
    draft: true,
  }],
  chats: [{ chatKey: "moving", composerDraft: "transferred", wasActive: true }],
});

(testContext.projectionBeforeCenter = {
  version: 2,
  tabs: [{ id: "s:existing", kind: "session", title: "Existing", sessionId: "existing" }],
  activeId: "s:existing",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.secondaryTabsModule.replaceCenterTabsPayload(testContext.projectionBeforeCenter, { persist: false });

testContext.secondarySessionModule.applySessionTransfer(testContext.effectSessionSnapshot, { persist: false });

testContext.assert.equal(testContext.secondaryTabsModule.persistCurrentCenterTabsPayload(), true);

testContext.assert.equal(testContext.secondarySessionModule.persistCurrentSessionTransfer(["local_one"]), true);

(testContext.projectionAfterCenter = testContext.secondaryTabsModule.validateTransferredTabs(
  testContext.projectionPayload,
  { kind: "strip-end" },
));

testContext.assert.equal(testContext.projectionAfterCenter.ok, true);

(testContext.projectionAfterSession = {
  ...testContext.effectSessionSnapshot,
  activeChatKey: "moving",
  currentSessionId: null,
  composerDrafts: {
    ...testContext.effectSessionSnapshot.composerDrafts,
    moving: "transferred",
  },
});

(testContext.projectionEntry = {
  ...testContext.orderedDestinationEntry,
  token: "projection-unrelated",
  payload: testContext.projectionPayload,
  beforeCenterTabs: testContext.projectionBeforeCenter,
  afterCenterTabs: testContext.projectionAfterCenter.after,
  beforeSession: testContext.effectSessionSnapshot,
  afterSession: testContext.projectionAfterSession,
});

testContext.assert.equal(testContext.stageTransferMutation(
  testContext.projectionEntry,
  () => {
    const center = testContext.secondaryTabsModule.insertTransferredTabs(
      testContext.projectionPayload,
      { kind: "strip-end" },
      { persist: false },
    );
    const session = testContext.secondarySessionModule.applySessionTransfer(
      testContext.projectionAfterSession,
      { persist: false },
    );
    return center.ok && session;
  },
  () => { throw new Error("unexpected projection stage rejection"); },
  "secondary",
), true);

testContext.secondaryTabs.getState().openNewTabPage();

testContext.secondaryTabs.getState().openSessionTab("user", "User");

testContext.secondarySession.getState().setCurrentDraft("user");

testContext.secondarySession.getState().setComposerInput("user edit");

(testContext.projectedCenterBytes = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(
  testContext.projectedCenterBytes.tabs.map((tab) => tab.id),
  ["s:existing", "s:user"],
  "ordinary center persistence must exclude only the pending destination tab",
);

testContext.assert.equal(testContext.projectedCenterBytes.activeId, "s:user");

(testContext.projectedSessionBytes = JSON.parse(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
));

testContext.assert.equal(testContext.projectedSessionBytes.composerDrafts.user, "user edit");

testContext.assert.equal(testContext.projectedSessionBytes.composerDrafts.moving, undefined);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:moving",
  "s:user",
]);

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts.user, "user edit");

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.projectionEntry.token,
  "commit",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:moving",
  "s:user",
]);

testContext.assert.equal(testContext.secondaryTabs.getState().activeId, "s:user");

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts.user, "user edit");

(testContext.committedProjectionCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.committedProjectionCenter.tabs.map((tab) => tab.id), [
  "s:existing",
  "s:moving",
  "s:user",
]);

testContext.assert.equal(testContext.committedProjectionCenter.activeId, "s:user");

(testContext.committedProjectionSession = JSON.parse(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
));

testContext.assert.equal(testContext.committedProjectionSession.composerDrafts.moving, "transferred");

testContext.assert.equal(testContext.committedProjectionSession.composerDrafts.user, "user edit");

testContext.assert.equal(
  testContext.pendingProjection.pendingTransfer(testContext.projectionEntry.token, "secondary"),
  undefined,
);
}
