// journal recovery: original sequential assertions and shared fixtures.
export async function run(testContext) {


// Two concurrent tokens: each commit/rollback leaves the other's staged
// delta and the user's interleaved edits intact.
(testContext.concurrentBase = {
  version: 2,
  tabs: [{ id: "s:existing", kind: "session", title: "Existing", sessionId: "existing" }],
  activeId: "s:existing",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.secondaryTabsModule.replaceCenterTabsPayload(testContext.concurrentBase, { persist: true });

testContext.secondarySessionModule.applySessionTransfer(testContext.effectSessionSnapshot, { persist: true });

(testContext.entryA = testContext.stageDestinationEntry("concurrent-a", "alpha", "alpha"));

(testContext.entryB = testContext.stageDestinationEntry("concurrent-b", "beta"));

testContext.secondaryTabs.getState().openNewTabPage();

testContext.secondaryTabs.getState().openSessionTab("user-two", "User two");

testContext.secondarySession.getState().setCurrentDraft("user-two");

testContext.secondarySession.getState().setComposerInput("user two edit");

(testContext.concurrentPendingCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(
  testContext.concurrentPendingCenter.tabs.map((tab) => tab.id),
  ["s:existing", "s:user-two"],
  "both pending tokens must stay projected out of persistence",
);

(testContext.concurrentPendingSession = JSON.parse(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
));

testContext.assert.equal(testContext.concurrentPendingSession.composerDrafts.alpha, undefined);

testContext.assert.equal(testContext.concurrentPendingSession.composerDrafts["user-two"], "user two edit");

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.entryA.token,
  "rollback",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

testContext.assert.deepEqual(
  testContext.secondaryTabs.getState().tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two"],
  "rolling back A must keep B's staged tab and the user's new tab",
);

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts.alpha, undefined);

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts["user-two"], "user two edit");

(testContext.afterRollbackCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(
  testContext.afterRollbackCenter.tabs.map((tab) => tab.id),
  ["s:existing", "s:user-two"],
  "still-pending token B must remain projected out after A rolls back",
);

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.entryB.token,
  "commit",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

testContext.assert.deepEqual(
  testContext.secondaryTabs.getState().tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two"],
);

(testContext.afterCommitCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(
  testContext.afterCommitCenter.tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two"],
);

testContext.assert.equal(testContext.afterCommitCenter.activeId, "s:user-two");

testContext.assert.deepEqual(testContext.pendingProjection.pendingTransfers("secondary"), []);

testContext.assert.deepEqual(testContext.readTransferJournal("secondary").entries, {});

(testContext.entryC = testContext.stageDestinationEntry("crash-commit", "gamma", "gamma"));

testContext.secondaryTabs.getState().openNewTabPage();

testContext.secondaryTabs.getState().openSessionTab("user-three", "User three");

testContext.simulateRendererCrash(testContext.entryC.token);

testContext.assert.deepEqual(
  testContext.secondaryTabs.getState().tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two", "s:user-three"],
);

(testContext.journaledC = testContext.readTransferJournal("secondary").entries[testContext.entryC.token]);

testContext.assert.equal(testContext.recoverTransferJournalEntry(
  testContext.journaledC,
  "committed",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

(testContext.crashCommittedTabs = ["s:existing", "s:beta", "s:user-two", "s:gamma", "s:user-three"]);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), testContext.crashCommittedTabs);

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts.gamma, "gamma draft");

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts["user-two"], "user two edit");

(testContext.crashCommittedCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.crashCommittedCenter.tabs.map((tab) => tab.id), testContext.crashCommittedTabs);

testContext.assert.equal(testContext.readTransferJournal("secondary").entries[testContext.entryC.token], undefined);

testContext.assert.equal(testContext.recoverTransferJournalEntry(
  testContext.journaledC,
  "committed",
  testContext.actualRecoveryHandlers,
  "secondary",
), true, "committed recovery must be idempotent");

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), testContext.crashCommittedTabs);

testContext.assert.deepEqual(testContext.pendingProjection.pendingTransfers("secondary"), []);


// Crash recovery of a rolled-back journal drops only its own delta.
(testContext.entryD = testContext.stageDestinationEntry("crash-rollback", "delta", "delta"));

testContext.secondaryTabs.getState().openNewTabPage();

testContext.secondaryTabs.getState().openSessionTab("user-four", "User four");

testContext.simulateRendererCrash(testContext.entryD.token);

(testContext.journaledD = testContext.readTransferJournal("secondary").entries[testContext.entryD.token]);

testContext.assert.equal(testContext.recoverTransferJournalEntry(
  testContext.journaledD,
  "rolled-back",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

(testContext.crashRolledBackTabs = [...testContext.crashCommittedTabs, "s:user-four"]);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), testContext.crashRolledBackTabs);

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts.delta, undefined);

testContext.assert.equal(testContext.secondarySession.getState().composerDrafts.gamma, "gamma draft");

(testContext.crashRolledBackCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(
  testContext.crashRolledBackCenter.tabs.map((tab) => tab.id),
  testContext.crashRolledBackTabs,
);

testContext.assert.deepEqual(testContext.pendingProjection.pendingTransfers("secondary"), []);

testContext.assert.deepEqual(testContext.readTransferJournal("secondary").entries, {});


testContext.secondaryTabsModule.replaceCenterTabsPayload(
  testContext.orderedDestinationEntry.beforeCenterTabs,
  { persist: false },
);

// Start this recovery scenario from its saved fixture, not earlier scenarios' user edits.
testContext.secondarySession.setState({ composerDrafts: { ...JSON.parse(testContext.effectSessionBytes).composerDrafts } });

testContext.values.set("openprogram.sessionDraftState:secondary", testContext.effectSessionBytes);

testContext.secondarySessionModule.applySessionTransfer(testContext.effectSessionSnapshot, { persist: false });

(testContext.startupEntry = {
  ...testContext.orderedDestinationEntry,
  token: "startup-destination-staged",
});

testContext.assert.equal(testContext.writeTransferJournal(testContext.startupEntry, "secondary"), true);

testContext.assert.equal(testContext.recoverTransferJournalEntry(
  testContext.startupEntry,
  "destination-staged",
  testContext.actualRecoveryHandlers,
), true);

testContext.secondaryTabs.getState().renameSessionTab("existing", "Startup effect write");

testContext.secondarySession.getState().setCurrentDraft("local_one");

testContext.channelDrafts.setDraftChannelChoice(testContext.secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "startup-effect",
  account_id: "startup-effect",
});

(testContext.startupProjectedCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.startupProjectedCenter.tabs.map((tab) => tab.id), ["s:existing"]);

testContext.assert.equal(testContext.startupProjectedCenter.tabs[0].title, "Startup effect write");

testContext.assert.equal(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
  testContext.effectSessionBytes,
  "startup recovery must project affected session writes out",
);

testContext.pendingProjection.unregisterPendingTransfer(testContext.startupEntry.token, "secondary");

testContext.assert.equal(testContext.deleteTransferJournal(testContext.startupEntry.token, "secondary"), true);

testContext.secondaryTabsModule.replaceCenterTabsPayload(testContext.startupEntry.afterCenterTabs, {
  persist: false,
});

testContext.secondarySessionModule.applySessionTransfer(testContext.startupEntry.afterSession, {
  persist: false,
});


(testContext.losslessCommitEntry = {
  ...testContext.startupEntry,
  token: "lossless-commit",
});

testContext.assert.equal(testContext.stageTransferMutation(
  testContext.losslessCommitEntry,
  () => testContext.secondaryTabsModule.replaceCenterTabsPayload(
    testContext.losslessCommitEntry.afterCenterTabs,
    { persist: false },
  ),
  () => { throw new Error("unexpected lossless commit rejection"); },
  "secondary",
), true);

testContext.values.set("openprogram.sessionDraftState:secondary", "{stale-session-bytes");

globalThis.localStorage.setItem = (key, value) => {
  if (key === "centerTabs:secondary") throw new Error("center quota");
  testContext.originalSetItem(key, value);
};

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.losslessCommitEntry.token,
  "commit",
  testContext.actualRecoveryHandlers,
  "secondary",
), false);

testContext.assert.equal(
  testContext.readTransferJournal("secondary").entries[testContext.losslessCommitEntry.token].phase,
  "committing",
);

globalThis.localStorage.setItem = testContext.originalSetItem;

testContext.secondaryTabs.getState().renameSessionTab("existing", "Failed commit effect");

testContext.secondarySession.getState().setCurrentDraft("local_one");

testContext.channelDrafts.setDraftChannelChoice(testContext.secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "failed-commit-effect",
  account_id: "failed-commit-effect",
});

(testContext.failedCommitProjection = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.failedCommitProjection.tabs.map((tab) => tab.id), ["s:existing"]);

testContext.assert.equal(testContext.failedCommitProjection.tabs[0].title, "Failed commit effect");

testContext.assert.equal(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
  testContext.effectSessionBytes,
  "a failed commit must keep projecting its affected session state out",
);

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.losslessCommitEntry.token,
  "commit",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

testContext.assert.equal(
  testContext.readTransferJournal("secondary").entries[testContext.losslessCommitEntry.token],
  undefined,
);


(testContext.losslessRollbackEntry = {
  ...testContext.startupEntry,
  token: "lossless-rollback",
});

testContext.assert.equal(testContext.stageTransferMutation(
  testContext.losslessRollbackEntry,
  () => testContext.secondaryTabsModule.replaceCenterTabsPayload(
    testContext.losslessRollbackEntry.afterCenterTabs,
    { persist: false },
  ),
  () => { throw new Error("unexpected lossless rollback rejection"); },
  "secondary",
), true);

testContext.values.set("openprogram.sessionDraftState:secondary", "{stale-session-bytes");

globalThis.localStorage.setItem = (key, value) => {
  if (key !== "openprogram.sessionDraftState:secondary") {
    testContext.originalSetItem(key, value);
  }
};

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.losslessRollbackEntry.token,
  "rollback",
  testContext.actualRecoveryHandlers,
  "secondary",
), false);

testContext.assert.equal(
  testContext.readTransferJournal("secondary").entries[testContext.losslessRollbackEntry.token].phase,
  "rolling-back",
);

globalThis.localStorage.setItem = testContext.originalSetItem;

testContext.secondaryTabs.getState().renameSessionTab("existing", "Failed rollback effect");

testContext.secondarySession.getState().setCurrentDraft("local_one");

testContext.channelDrafts.setDraftChannelChoice(testContext.secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "failed-rollback-effect",
  account_id: "failed-rollback-effect",
});

(testContext.failedRollbackProjection = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.failedRollbackProjection.tabs.map((tab) => tab.id), ["s:existing"]);

testContext.assert.equal(testContext.failedRollbackProjection.tabs[0].title, "Failed rollback effect");

testContext.assert.equal(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
  testContext.effectSessionBytes,
  "a failed rollback must keep projecting its affected session state out",
);

testContext.assert.equal(testContext.finalizeTransferJournal(
  testContext.losslessRollbackEntry.token,
  "rollback",
  testContext.actualRecoveryHandlers,
  "secondary",
), true);

testContext.assert.equal(
  testContext.readTransferJournal("secondary").entries[testContext.losslessRollbackEntry.token],
  undefined,
);


(testContext.fileDraft = { draft: "edited", baselineContent: "base", baselineMtime: 4 });

testContext.fileDrafts.set("project:file.txt", testContext.fileDraft);

(testContext.fileSnapshot = testContext.snapshotFileDrafts(["project:file.txt", "project:missing.txt"]));

testContext.fileDrafts.delete("project:file.txt");

testContext.fileDrafts.set("project:missing.txt", testContext.fileDraft);

testContext.applyFileDraftSnapshot(testContext.fileSnapshot);

testContext.assert.deepEqual(testContext.fileDrafts.get("project:file.txt"), testContext.fileDraft);

testContext.assert.equal(testContext.fileDrafts.has("project:missing.txt"), false);


delete globalThis.window.openprogramDesktop;

testContext.values.clear();


({ useCenterTabs: testContext.useCenterTabs } = await import("../../../lib/tabs/center-tabs-store.ts"));

({
  SPLIT_CHAT_MIN_WIDTH: testContext.SPLIT_CHAT_MIN_WIDTH,
  SPLIT_DIVIDER_WIDTH: testContext.SPLIT_DIVIDER_WIDTH,
  SPLIT_WEB_MIN_WIDTH: testContext.SPLIT_WEB_MIN_WIDTH,
  clampSplitRatioForWidth: testContext.clampSplitRatioForWidth,
  createSplitLayoutMeasureScheduler: testContext.createSplitLayoutMeasureScheduler,
  isSplitLayoutAvailable: testContext.isSplitLayoutAvailable,
} = await import("../../../lib/tabs/split-layout.ts"));

({
  registerVisibleWebTabBounds: testContext.registerVisibleWebTabBounds,
  browserPageInventory: testContext.browserPageInventory,
  removeVisibleWebTabBounds: testContext.removeVisibleWebTabBounds,
  isDesktopSplitLayoutAvailable: testContext.isDesktopSplitLayoutAvailable,
  isWebTabReady: testContext.isWebTabReady,
  restorePriorActiveTabAfterFailedWebOpen: testContext.restorePriorActiveTabAfterFailedWebOpen,
  setDesktopSplitLayoutAvailable: testContext.setDesktopSplitLayoutAvailable,
  setWebTabReady: testContext.setWebTabReady,
  closeAgentWebTabResult: testContext.closeAgentWebTabResult,
  surfaceRefForChat: testContext.surfaceRefForChat,
  visibleWebTab: testContext.visibleWebTab,
  waitForWebTabReady: testContext.waitForWebTabReady,
} = await import("../../../lib/desktop/desktop-bridge.ts"));

({ isWebTabOccluded: testContext.isWebTabOccluded, measureWebTabBounds: testContext.measureWebTabBounds } = await import("../../../lib/browser/web-tab-bounds.ts"));

({
  focusCenterTabGroupMember: testContext.focusCenterTabGroupMember,
  resolveCenterTabPanes: testContext.resolveCenterTabPanes,
} = await import("../../../lib/tabs/center-tab-groups.ts"));


testContext.assert.equal(testContext.SPLIT_CHAT_MIN_WIDTH, 360);

testContext.assert.equal(testContext.SPLIT_WEB_MIN_WIDTH, 480);

testContext.assert.equal(testContext.SPLIT_DIVIDER_WIDTH, 6);

(testContext.rect = (left, top, width, height) => ({
  left,
  top,
  right: left + width,
  bottom: top + height,
  width,
  height,
}));

(testContext.webBody = { x: 600, y: 180, width: 500, height: 620 });

testContext.assert.equal(
  testContext.isWebTabOccluded(testContext.webBody, [{ getBoundingClientRect: () => testContext.rect(150, 900, 420, 360) }]),
  false,
  "a project menu confined to the chat pane must not hide the split WebTab",
);

testContext.assert.equal(
  testContext.isWebTabOccluded(testContext.webBody, [{ getBoundingClientRect: () => testContext.rect(900, 240, 300, 220) }]),
  true,
  "an overlay intersecting the native page body must hide it",
);

testContext.assert.equal(
  testContext.isWebTabOccluded(testContext.webBody, [{ getBoundingClientRect: () => testContext.rect(100, 300, 500, 200) }]),
  false,
  "edge contact without positive overlap must not hide the WebTab",
);

testContext.assert.equal(
  testContext.isWebTabOccluded(testContext.webBody, [
    { getBoundingClientRect: () => testContext.rect(40, 240, 500, 300) },
    { getBoundingClientRect: () => testContext.rect(0, 0, 1200, 900) },
  ]),
  true,
  "a modal backdrop must occlude the WebTab even when dialog content is elsewhere",
);

(testContext.leftWebBody = { x: 520, y: 180, width: 420, height: 620 });

(testContext.rightWebBody = { x: 946, y: 180, width: 420, height: 620 });

(testContext.paneMenu = [{ getBoundingClientRect: () => testContext.rect(700, 260, 180, 240) }]);

testContext.assert.equal(testContext.isWebTabOccluded(testContext.leftWebBody, testContext.paneMenu), true);

testContext.assert.equal(
  testContext.isWebTabOccluded(testContext.rightWebBody, testContext.paneMenu),
  false,
  "each visible WebTab must evaluate the same menu against its own body bounds",
);

(testContext.thresholdWidth = 846);

(testContext.thresholdRatio = testContext.SPLIT_CHAT_MIN_WIDTH / testContext.thresholdWidth);

testContext.assert.equal(testContext.clampSplitRatioForWidth(0.44, testContext.thresholdWidth), testContext.thresholdRatio);

testContext.assert.equal(testContext.clampSplitRatioForWidth(0.70, testContext.thresholdWidth), testContext.thresholdRatio);

testContext.assert.equal(testContext.clampSplitRatioForWidth(0.44, 1200), 0.44);

testContext.assert.equal(
  testContext.clampSplitRatioForWidth(0.70, 1200),
  (1200 - testContext.SPLIT_WEB_MIN_WIDTH - testContext.SPLIT_DIVIDER_WIDTH) / 1200,
);

(testContext.preferredRatio = 0.527651858567543);

testContext.assert.equal(testContext.isSplitLayoutAvailable(864), true);

testContext.assert.equal(testContext.clampSplitRatioForWidth(testContext.preferredRatio, 864), 0.4375);

testContext.assert.equal(
  testContext.clampSplitRatioForWidth(testContext.preferredRatio, 1200),
  testContext.preferredRatio,
  "restoring space must recompute from the preferred ratio",
);

testContext.assert.equal(testContext.isSplitLayoutAvailable(463), false);


(testContext.paneTabs = [
  { id: "s:a", kind: "session", title: "A", sessionId: "a" },
  { id: "s:b", kind: "session", title: "B", sessionId: "b" },
  { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
  { id: "w:two", kind: "web", title: "Two", url: "https://two.test/" },
]);

(testContext.sessionPair = {
  id: "g:sessions",
  memberIds: ["s:a", "s:b"],
  visibleIds: ["s:a", "s:b"],
  focusedId: "s:b",
});

(testContext.sessionPanes = testContext.resolveCenterTabPanes(testContext.sessionPair, testContext.paneTabs, "s:b"));

testContext.assert.equal(testContext.sessionPanes.length, 2, "two sessions split into two panes");

testContext.assert.deepEqual(
  testContext.sessionPanes.map((pane) => pane.kind),
  ["peer", "peer"],
  "a chat+chat split is symmetric — neither side takes the singleton shell",
);

testContext.assert.equal(testContext.sessionPanes[0].tabId, "s:a");

testContext.assert.equal(testContext.sessionPanes[1].tabId, "s:b");


(testContext.sessionWebPanes = testContext.resolveCenterTabPanes({
  ...testContext.sessionPair,
  memberIds: ["s:a", "w:one"],
  visibleIds: ["s:a", "w:one"],
  focusedId: "w:one",
}, testContext.paneTabs, "w:one"));

testContext.assert.deepEqual(testContext.sessionWebPanes.map((pane) => pane.kind), ["session", "tab"]);


(testContext.webPairPanes = testContext.resolveCenterTabPanes({
  id: "g:webs",
  memberIds: ["w:one", "w:two"],
  visibleIds: ["w:one", "w:two"],
  focusedId: "w:two",
}, testContext.paneTabs, "w:two"));

testContext.assert.deepEqual(testContext.webPairPanes.map((pane) => pane.tabId), ["w:one", "w:two"]);


(testContext.inventoryTabs = [
  { id: "w:google", kind: "web", title: "Google", url: "https://google.test/" },
  { id: "w:github", kind: "web", title: "GitHub", url: "https://github.test/" },
  { id: "w:bilibili", kind: "web", title: "Bilibili", url: "https://bilibili.test/" },
  { id: "w:youtube", kind: "web", title: "YouTube", url: "https://youtube.test/" },
]);

testContext.useCenterTabs.setState({
  tabs: testContext.inventoryTabs,
  activeId: "w:youtube",
  groups: [{
    id: "g3",
    memberIds: ["w:bilibili", "w:youtube"],
    visibleIds: ["w:bilibili", "w:youtube"],
    focusedId: "w:youtube",
  }],
  splitRatio: 0.5,
});

(testContext.inventoryBridge = {
  windowId: "window-1",
  webTab: {
    inspect: async (id) => ({
      target_id: `target:${id}`,
      url: testContext.inventoryTabs.find((tab) => tab.id === id).url,
      title: testContext.inventoryTabs.find((tab) => tab.id === id).title,
    }),
    syncVisible: () => {},
  },
});
}
