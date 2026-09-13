// transfer destination: original sequential assertions and shared fixtures.
export async function run(testContext) {


(testContext.t5Base = {
  version: 2,
  tabs: [{ id: "s:home", kind: "session", title: "Home", sessionId: "home" }],
  activeId: "s:home",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

(testContext.t5Payload = {
  tabs: [{ id: "w:moved", kind: "web", title: "Moved", url: "https://moved.test/" }],
  source: { windowId: "source", kind: "tab" },
  fileDrafts: [],
  chats: [],
});


// Destination staging: journal write + journalOpened strictly precede the
// {persist:false} mutation; committed storage bytes stay unchanged.
testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5Base, { persist: true });

testContext.plainSessionModule.applySessionTransfer(testContext.emptySessionSnapshot, { persist: true });

(testContext.committedCenterBytes = testContext.values.get("centerTabs:main"));

(testContext.committedSessionBytes = testContext.values.get("openprogram.sessionDraftState:main"));

{
  const journalOpenedObservations = [];
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload, {
    journalOpened: async (token, role) => {
      journalOpenedObservations.push({
        role,
        journalHasToken: !!testContext.readTransferJournal("main").entries[token],
        storeMutated: testContext.plainTabs.getState().tabs.some((tab) => tab.id === "w:moved"),
      });
      return true;
    },
  });
  testContext.assert.equal(
    await testContext.bridgeModule.stageIncomingTransfer(bridge, "t5-stage", { kind: "strip-end" }),
    true,
  );
  testContext.assert.deepEqual(journalOpenedObservations, [{
    role: "destination",
    journalHasToken: true,
    storeMutated: false,
  }]);
  testContext.assert.deepEqual(
    testContext.plainTabs.getState().tabs.map((tab) => tab.id),
    ["s:home", "w:moved"],
  );
  testContext.assert.equal(testContext.values.get("centerTabs:main"), testContext.committedCenterBytes);
  testContext.assert.equal(testContext.values.get("openprogram.sessionDraftState:main"), testContext.committedSessionBytes);
  testContext.assert.ok(testContext.bridgeModule.acceptedTransfers.has("t5-stage"));
  testContext.assert.ok(
    testContext.bridgeModule.serializeWebViewBookkeeping().liveIds.includes("w:moved"),
  );
  testContext.assert.deepEqual(calls.at(-1), ["destinationReady", "t5-stage", true]);

  // Destination rollback: bridge bookkeeping is forgotten BEFORE the store
  // reverts, acceptedTransfers clears, destinationUndone acknowledges; the
  // journal survives until main reports rolled-back.
  let forgottenBeforeStoreRevert = null;
  const unsubscribe = testContext.plainTabs.subscribe(() => {
    if (forgottenBeforeStoreRevert === null) {
      forgottenBeforeStoreRevert = !testContext.bridgeModule
        .serializeWebViewBookkeeping().liveIds.includes("w:moved");
    }
  });
  await testContext.bridgeModule.handleUndoDestination(bridge, { token: "t5-stage" });
  unsubscribe();
  testContext.assert.equal(forgottenBeforeStoreRevert, true);
  testContext.assert.deepEqual(testContext.plainTabs.getState().tabs.map((tab) => tab.id), ["s:home"]);
  testContext.assert.equal(testContext.bridgeModule.acceptedTransfers.has("t5-stage"), false);
  testContext.assert.deepEqual(calls.at(-1), ["destinationUndone", "t5-stage", true]);
  testContext.assert.ok(testContext.readTransferJournal("main").entries["t5-stage"]);
  await testContext.bridgeModule.handleTransferRolledBack(bridge, {
    token: "t5-stage",
    sourceId: "source",
    destinationId: "main",
  });
  testContext.assert.equal(testContext.readTransferJournal("main").entries["t5-stage"], undefined);
  testContext.assert.deepEqual(
    calls.at(-1),
    ["journalFinalized", "t5-stage", "destination"],
  );
  testContext.assert.equal(testContext.values.get("centerTabs:main"), testContext.committedCenterBytes);
}


// A rolled-back tear-off window is destroyed after destinationUndone. Its
// window-keyed tab/session/journal records must be removed before that ack.
{
  const detachedId = "window-rollback-cleanup";
  const keys = [
    `centerTabs:${detachedId}`,
    `openprogram.sessionDraftState:${detachedId}`,
    `openprogram.tabTransferJournal:${detachedId}`,
  ];
  for (const key of keys) testContext.values.set(key, "temporary");
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload);
  bridge.windowId = detachedId;
  await testContext.bridgeModule.handleUndoDestination(bridge, {
    token: "detached-cleanup",
    discardWindowState: true,
  });
  for (const key of keys) testContext.assert.equal(testContext.values.has(key), false);
  testContext.assert.deepEqual(calls.at(-1), ["destinationUndone", "detached-cleanup", true]);
}


// Destination commit: journal after* persists, then accepted state and the
// journal entry are deleted.
{
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload);
  testContext.assert.equal(
    await testContext.bridgeModule.stageIncomingTransfer(bridge, "t5-commit", { kind: "strip-end" }),
    true,
  );
  await testContext.bridgeModule.handleTransferCommitted(bridge, {
    token: "t5-commit",
    sourceId: "source",
    destinationId: "main",
  });
  const persisted = JSON.parse(testContext.values.get("centerTabs:main"));
  testContext.assert.deepEqual(persisted.tabs.map((tab) => tab.id), ["s:home", "w:moved"]);
  testContext.assert.equal(testContext.bridgeModule.acceptedTransfers.has("t5-commit"), false);
  testContext.assert.equal(testContext.readTransferJournal("main").entries["t5-commit"], undefined);
  testContext.assert.deepEqual(calls.at(-1), ["journalFinalized", "t5-commit", "destination"]);
}


// Simulated reload: a destination-staged journal rebuilds acceptedTransfers
// through the recovery handlers.
{
  const { bridge } = testContext.makeTransferBridge(testContext.t5Payload);
  const reloadPayload = {
    ...testContext.t5Payload,
    tabs: [{ id: "w:reloaded", kind: "web", title: "Reloaded", url: "https://reloaded.test/" }],
  };
  const reloadBefore = testContext.plainTabsModule.snapshotCenterTabsPayload();
  const reloadValidated = testContext.plainTabsModule.validateTransferredTabs(
    reloadPayload,
    { kind: "strip-end" },
  );
  testContext.assert.equal(reloadValidated.ok, true);
  const staged = {
    ...testContext.journalEntry("t5-reload"),
    payload: reloadPayload,
    beforeCenterTabs: reloadBefore,
    afterCenterTabs: reloadValidated.after,
    beforeSession: testContext.plainSessionModule.snapshotSessionTransfer([]),
    afterSession: testContext.plainSessionModule.snapshotSessionTransfer([]),
  };
  testContext.assert.equal(testContext.recoverTransferJournalEntry(
    staged,
    "destination-staged",
    testContext.bridgeModule.transferRecoveryHandlers(bridge),
    "main",
  ), true);
  testContext.assert.ok(testContext.bridgeModule.acceptedTransfers.has("t5-reload"));
  testContext.assert.ok(testContext.plainTabs.getState().tabs.some((tab) => tab.id === "w:reloaded"));
  testContext.bridgeModule.transferRecoveryHandlers(bridge).clearAccepted("t5-reload");
  testContext.assert.equal(testContext.bridgeModule.acceptedTransfers.has("t5-reload"), false);
  testContext.pendingProjection.unregisterPendingTransfer("t5-reload", "main");
  testContext.plainTabsModule.replaceCenterTabsPayload(reloadBefore, { persist: true });
}


// Duplicate: activates the existing destination tab, rejects with the
// duplicate id, and never touches accept/journal.
{
  const duplicatePayload = {
    ...testContext.t5Payload,
    tabs: [{ id: "s:home", kind: "session", title: "Home", sessionId: "home" }],
  };
  const { bridge, calls } = testContext.makeTransferBridge(duplicatePayload);
  testContext.plainTabs.getState().openNewTabPage();
  testContext.assert.notEqual(testContext.plainTabs.getState().activeId, "s:home");
  testContext.assert.equal(
    await testContext.bridgeModule.stageIncomingTransfer(bridge, "t5-dup", { kind: "strip-end" }),
    false,
  );
  testContext.assert.equal(testContext.plainTabs.getState().activeId, "s:home");
  testContext.assert.deepEqual(calls.at(-1), ["reject", "t5-dup", "duplicate", "s:home"]);
  testContext.assert.equal(calls.some(([name]) => name === "accept"), false);
  testContext.assert.equal(testContext.readTransferJournal("main").entries["t5-dup"], undefined);
  testContext.plainTabs.getState().closeTab(testContext.plainTabs.getState().tabs.at(-1).id);
}


// Full group: reject("group-full") before any accept/journal/mutation.
{
  const fullGroupPayload = {
    tabs: ["one", "two", "three", "four"].map((name) => ({
      id: `w:t5-${name}`,
      kind: "web",
      title: name,
      url: `https://${name}.t5.test/`,
    })),
    source: {
      windowId: "source",
      kind: "group",
      groupId: "g:t5-full",
      memberIds: ["w:t5-one", "w:t5-two", "w:t5-three", "w:t5-four"],
      visibleIds: ["w:t5-one", "w:t5-two"],
      focusedId: "w:t5-one",
    },
    fileDrafts: [],
    chats: [],
  };
  const { bridge, calls } = testContext.makeTransferBridge(fullGroupPayload);
  testContext.assert.equal(
    await testContext.bridgeModule.stageIncomingTransfer(bridge, "t5-full", { kind: "strip-end" }),
    false,
  );
  testContext.assert.deepEqual(calls.at(-1), ["reject", "t5-full", "group-full"]);
  testContext.assert.equal(calls.some(([name]) => name === "accept"), false);
}


// Source removal: journal before mutation, {persist:false} removal, commit
// finalizes durably; a failed main acknowledgement restores before* state.
(testContext.t5SourcePayload = {
  tabs: [{
    id: "s:local_move",
    kind: "session",
    title: "Draft move",
    sessionId: "local_move",
    draft: true,
  }],
  source: { windowId: "main", kind: "tab" },
  fileDrafts: [],
  chats: [{ chatKey: "local_move", composerDraft: "moving text", wasActive: false }],
});
}
