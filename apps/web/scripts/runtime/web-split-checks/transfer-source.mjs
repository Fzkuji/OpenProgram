// transfer source: original sequential assertions and shared fixtures.
export async function run(testContext) {

(testContext.t5SourceBase = {
  ...testContext.t5Base,
  tabs: [
    ...testContext.t5Base.tabs,
    {
      id: "s:local_move",
      kind: "session",
      title: "Draft move",
      sessionId: "local_move",
      draft: true,
    },
  ],
});

{
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5SourceBase, { persist: true });
  testContext.plainSessionModule.applySessionTransfer({
    ...testContext.emptySessionSnapshot,
    composerDrafts: { local_move: "moving text" },
  }, { persist: true });
  const sourceJournalObservations = [];
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5SourcePayload, {
    journalOpened: async (token, role) => {
      sourceJournalObservations.push({
        role,
        journalHasToken: !!testContext.readTransferJournal("main").entries[token],
        tabStillPresent: testContext.plainTabs.getState().tabs
          .some((tab) => tab.id === "s:local_move"),
      });
      return true;
    },
  });
  await testContext.bridgeModule.handleRemoveSource(bridge, {
    token: "t5-source",
    payload: testContext.t5SourcePayload,
  });
  testContext.assert.deepEqual(sourceJournalObservations, [{
    role: "source",
    journalHasToken: true,
    tabStillPresent: true,
  }]);
  testContext.assert.deepEqual(testContext.plainTabs.getState().tabs.map((tab) => tab.id), ["s:home"]);
  const persistedCenter = JSON.parse(testContext.values.get("centerTabs:main"));
  testContext.assert.deepEqual(persistedCenter.tabs.map((tab) => tab.id), ["s:home"]);
  const persistedSession = JSON.parse(
    testContext.values.get("openprogram.sessionDraftState:main"),
  );
  testContext.assert.equal(persistedSession.composerDrafts.local_move, undefined);
  testContext.assert.equal(testContext.readTransferJournal("main").entries["t5-source"], undefined);
  testContext.assert.deepEqual(
    calls.filter(([name]) => name === "sourceRemoved" || name === "journalFinalized"),
    [
      ["sourceRemoved", "t5-source", true, false],
      ["journalFinalized", "t5-source", "source"],
    ],
  );
}


// Session navigation invalidates a prepared transfer, including while main
// acknowledges the source journal. Rollback must retain the newer history.
for (const duringJournal of [false, true]) {
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5SourceBase, { persist: true });
  testContext.plainTabs.getState().setActive("s:local_move");
  const token = `session-navigation-race-${duringJournal}`;
  const navigate = () => testContext.plainTabs.getState().openSessionTab("new-destination", "New destination");
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5SourcePayload, {
    journalOpened: async () => { if (duringJournal) navigate(); return true; },
  });
  if (!duringJournal) navigate();
  await testContext.bridgeModule.handleRemoveSource(bridge, { token, payload: testContext.t5SourcePayload });
  const current = testContext.plainTabs.getState().tabs.find(tab => tab.id === "s:local_move");
  testContext.assert.equal(current?.sessionId, "new-destination", "stale transfer cannot remove a navigated session");
  testContext.assert.deepEqual(current.sessionHistory.entries.map(entry => entry.sessionId), ["local_move", "new-destination"]);
  testContext.assert.ok(calls.some(([name, receipt, ok]) => name === "sourceRemoved" && receipt === token && ok === false));
  await testContext.bridgeModule.handleTransferRolledBack(bridge, { token, sourceId: "main", destinationId: "other" });
  testContext.assert.deepEqual(testContext.plainTabs.getState().tabs.find(tab => tab.id === current.id), current);
  testContext.assert.deepEqual(JSON.parse(testContext.values.get("centerTabs:main")).tabs.find(tab => tab.id === current.id), current);
}


// Source removal with a stale main acknowledgement restores the tab and
// keeps the journal for the rolled-back event to finalize.
{
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5SourceBase, { persist: true });
  testContext.plainSessionModule.applySessionTransfer({
    ...testContext.emptySessionSnapshot,
    composerDrafts: { local_move: "moving text" },
  }, { persist: true });
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5SourcePayload, {
    sourceRemoved: async (...args) => {
      calls.push(["sourceRemoved", ...args]);
      return false;
    },
  });
  await testContext.bridgeModule.handleRemoveSource(bridge, {
    token: "t5-source-stale",
    payload: testContext.t5SourcePayload,
  });
  testContext.assert.deepEqual(
    testContext.plainTabs.getState().tabs.map((tab) => tab.id),
    ["s:home", "s:local_move"],
    "a stale main response must restore the source tabs locally",
  );
  testContext.assert.ok(testContext.readTransferJournal("main").entries["t5-source-stale"]);
  testContext.assert.equal(
    calls.some(([name]) => name === "journalFinalized"),
    false,
  );
  await testContext.bridgeModule.handleTransferRolledBack(bridge, {
    token: "t5-source-stale",
    sourceId: "main",
    destinationId: "other",
  });
  testContext.assert.equal(testContext.readTransferJournal("main").entries["t5-source-stale"], undefined);
  testContext.assert.deepEqual(
    calls.at(-1),
    ["journalFinalized", "t5-source-stale", "source"],
  );
  testContext.assert.deepEqual(
    testContext.plainTabs.getState().tabs.map((tab) => tab.id),
    ["s:home", "s:local_move"],
  );
}


// Rejected: clears the prepared drag coordinator token.
{
  const { dragCoordinator } = await import("../../../lib/tabs/tab-drag-coordinator.ts");
  dragCoordinator.prepare({
    subject: { kind: "tab", tabIds: ["s:home"] },
    transferToken: "t5-rejected",
    started: false,
    cancelled: false,
    committed: false,
  });
  testContext.bridgeModule.handleTransferRejected({ token: "t5-rejected", reason: "duplicate" });
  testContext.assert.equal(dragCoordinator.current(), null);
}


// A live orphan-finalization notification must not wait for the next renderer
// restart. The surviving window acknowledges the destroyed destination role.
{
  let orphanHandler = null;
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload, {
    onFinalizeOrphaned: (handler) => {
      orphanHandler = handler;
      return () => {};
    },
  });
  const cleanup = testContext.bridgeModule.installTabTransferHandlers(bridge);
  testContext.assert.equal(typeof orphanHandler, "function");
  await orphanHandler({
    token: "detached-orphan",
    status: "rolled-back",
    role: "destination",
    windowId: "window-destroyed",
    orphaned: true,
  });
  testContext.assert.deepEqual(
    calls.at(-1),
    ["journalFinalized", "detached-orphan", "destination", "window-destroyed"],
  );
  cleanup();
}


// A detached destination whose first cleanup failed must be discarded by the
// surviving renderer before it acknowledges the orphaned journal role.
{
  const ownerWindowId = "window-detached-cleanup";
  const token = "detached-cleanup-retry";
  testContext.assert.equal(testContext.writeTransferJournal({
    ...testContext.journalEntry(token),
    role: "destination",
  }, ownerWindowId), true);
  testContext.values.set(`centerTabs:${ownerWindowId}`, "stale-center");
  testContext.values.set(`openprogram.sessionDraftState:${ownerWindowId}`, "stale-session");
  testContext.assert.equal(
    testContext.bridgeModule.finalizeOrphanTransferJournal(
      token,
      "rolled-back",
      ownerWindowId,
      true,
    ),
    true,
  );
  testContext.assert.equal(testContext.values.has(`centerTabs:${ownerWindowId}`), false);
  testContext.assert.equal(
    testContext.values.has(`openprogram.sessionDraftState:${ownerWindowId}`),
    false,
  );
  testContext.assert.equal(
    testContext.values.has(`openprogram.tabTransferJournal:${ownerWindowId}`),
    false,
  );
}


// Structure gates: dragstart stays synchronous and never awaits before
// writing DataTransfer.
(testContext.stripSource = testContext.readCenterTabStripSource());

testContext.assert.doesNotMatch(
  testContext.stripSource,
  /async function onDragStart|onDragStart\s*[=:]\s*async/,
  "onDragStart must not be async",
);

testContext.assert.doesNotMatch(
  testContext.stripSource,
  /await[^\n]*setData/,
  "DataTransfer.setData must not be awaited",
);


// Task 6: pointer-down payload building and shared drop-intent geometry.
{
  const { useCenterTabs } = await import("../../../lib/tabs/center-tabs-store.ts");
  const { useSessionStore } = await import("../../../lib/session-store/index.ts");
  const filesShared = await import("../../../lib/files/files-shared.ts");
  useCenterTabs.setState({
    tabs: [
      {
        id: "s:chatA",
        kind: "session",
        sessionId: "chatA",
        title: "Chat A",
        draft: false,
      },
      {
        id: "f:readme",
        kind: "file",
        projectId: "proj",
        path: "README.md",
        title: "README.md",
      },
    ],
    groups: [],
    activeId: "s:chatA",
  });
  useSessionStore.setState({
    activeChatKey: "chatA",
    composerDrafts: { chatA: "draft text" },
    composerSettingsBySession: { chatA: { model: "m1" } },
    pendingProjectsByChat: { chatA: "proj" },
  });
  const channelHostModule = await import(
    "../../../lib/runtime-bridge/draft-channel-choice.ts"
  );
  channelHostModule.draftChannelChoiceHost.__pendingChannelChoices = {
    chatA: { channel: "web" },
  };
  const draftKey = filesShared.fileDraftKey("proj", "README.md");
  filesShared.fileDrafts.set(draftKey, { content: "edited", baseMtime: 1 });

  const group = {
    id: "g:one",
    memberIds: ["s:chatA", "f:readme"],
    visibleIds: ["s:chatA"],
    focusedId: "s:chatA",
  };
  const payload = testContext.bridgeModule.buildTransferPayload(
    { kind: "group", tabIds: ["s:chatA", "f:readme"], sourceGroup: group },
    "win-src",
  );
  testContext.assert.deepEqual(payload.tabs.map((tab) => tab.id), ["s:chatA", "f:readme"]);
  testContext.assert.deepEqual(payload.source, {
    windowId: "win-src",
    kind: "group",
    groupId: "g:one",
    memberIds: ["s:chatA", "f:readme"],
    visibleIds: ["s:chatA"],
    focusedId: "s:chatA",
  });
  testContext.assert.deepEqual(payload.fileDrafts, [
    { key: draftKey, value: { content: "edited", baseMtime: 1 } },
  ]);
  testContext.assert.deepEqual(payload.chats, [
    {
      chatKey: "chatA",
      wasActive: true,
      composerDraft: "draft text",
      composerSettings: { model: "m1" },
      pendingProjectId: "proj",
      draftChannelChoice: { channel: "web" },
    },
  ]);
  useCenterTabs.getState().openSessionTab("chatB", "Chat B");
  const historyPayload = testContext.bridgeModule.buildTransferPayload(
    { kind: "tab", tabIds: ["s:chatA"] }, "win-src",
  );
  testContext.assert.deepEqual(historyPayload.tabs[0].sessionHistory.entries.map(entry => entry.sessionId), ["chatA", "chatB"]);
  testContext.assert.equal(historyPayload.chats.find(chat => chat.chatKey === "chatA").composerDraft, "draft text");
  testContext.assert.equal(historyPayload.chats.find(chat => chat.chatKey === "chatA").wasActive, false);
  const segmentPayload = testContext.bridgeModule.buildTransferPayload(
    {
      kind: "segment",
      tabIds: ["f:readme"],
      sourceGroup: group,
      memberIndex: 1,
    },
    "win-src",
  );
  testContext.assert.equal(segmentPayload.source.memberIndex, 1);
  testContext.assert.equal(segmentPayload.chats.length, 0);
  testContext.assert.equal(
    testContext.bridgeModule.buildTransferPayload(
      { kind: "tab", tabIds: ["missing"] },
      "win-src",
    ),
    null,
    "an unknown tab id must not produce a partial payload",
  );
  delete window.__pendingChannelChoices;
  filesShared.fileDrafts.delete(draftKey);

  testContext.assert.deepEqual(
    testContext.bridgeModule.placementForDropIntent({ mode: "before", targetTabId: "x" }),
    { kind: "before", targetTabId: "x" },
  );
  testContext.assert.deepEqual(
    testContext.bridgeModule.placementForDropIntent({
      mode: "merge",
      targetTabId: "x",
      groupId: "g",
      memberIndex: 2,
    }),
    { kind: "merge", targetTabId: "x", groupId: "g", memberIndex: 2 },
  );
  testContext.assert.deepEqual(
    testContext.bridgeModule.placementForDropIntent({ mode: "after", targetTabId: "x" }),
    { kind: "after", targetTabId: "x" },
  );
}
}
