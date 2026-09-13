// detached recovery: original sequential assertions and shared fixtures.
export async function run(testContext) {


// ---------------------------------------------------------------- Task 7:
// renderer startup recovery order — journal recovery, terminal/orphan acks,
// native reconciliation, then claimPending for a detached window.

// Fresh detached claim: pendingTerminal is consulted before claimPending,
// reconcile runs between them, and the claimed token stages at strip end.
{
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5Base, { persist: true });
  testContext.plainSessionModule.applySessionTransfer(testContext.emptySessionSnapshot, { persist: true });
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload, {
    claimPending: async (windowId) => {
      calls.push(["claimPending", windowId]);
      return "t7-claim";
    },
  });
  let reconciledAt = -1;
  await testContext.bridgeModule.recoverPendingTabTransfers(bridge, () => {
    reconciledAt = calls.length;
  });
  const order = calls.map(([name]) => name);
  const terminalIndex = order.indexOf("pendingTerminal");
  const claimIndex = order.indexOf("claimPending");
  testContext.assert.ok(terminalIndex >= 0 && claimIndex > terminalIndex);
  testContext.assert.ok(
    reconciledAt > terminalIndex && reconciledAt <= claimIndex,
    "native reconciliation must run after terminal cleanup, before claim",
  );
  testContext.assert.ok(testContext.plainTabs.getState().tabs.some((tab) => tab.id === "w:moved"));
  testContext.assert.ok(testContext.bridgeModule.acceptedTransfers.has("t7-claim"));
  // startup staging leaves the destination journal pending until commit
  testContext.assert.ok(testContext.readTransferJournal("main").entries["t7-claim"]);
  await testContext.bridgeModule.handleTransferCommitted(bridge, {
    token: "t7-claim",
    sourceId: "source",
    destinationId: "main",
  });
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5Base, { persist: true });
}


// A token already represented by a recovered journal resumes idempotently:
// no second accept/insert, and its live pre-commit journal defers the
// journalFinalized ack to the normal commit/rollback path.
{
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5Base, { persist: true });
  const resumePayload = {
    ...testContext.t5Payload,
    tabs: [{ id: "w:resume", kind: "web", title: "Resume", url: "https://resume.test/" }],
  };
  const validated = testContext.plainTabsModule.validateTransferredTabs(
    resumePayload,
    { kind: "strip-end" },
  );
  testContext.assert.equal(validated.ok, true);
  testContext.assert.equal(testContext.writeTransferJournal({
    ...testContext.journalEntry("t7-resume"),
    payload: resumePayload,
    beforeCenterTabs: testContext.plainTabsModule.snapshotCenterTabsPayload(),
    afterCenterTabs: validated.after,
    beforeSession: testContext.plainSessionModule.snapshotSessionTransfer([]),
    afterSession: testContext.plainSessionModule.snapshotSessionTransfer([]),
  }, "main"), true);
  const { bridge, calls } = testContext.makeTransferBridge(resumePayload, {
    status: async () => ({
      status: "destination-staged",
      sourceId: "source",
      destinationId: "main",
    }),
    claimPending: async () => "t7-resume",
    pendingTerminal: async () => [{
      token: "t7-resume",
      status: "committed",
      role: "destination",
      windowId: "main",
      orphaned: false,
    }],
  });
  await testContext.bridgeModule.recoverPendingTabTransfers(bridge);
  testContext.assert.equal(calls.some(([name]) => name === "accept"), false);
  testContext.assert.equal(
    testContext.plainTabs.getState().tabs.filter((tab) => tab.id === "w:resume").length,
    1,
  );
  testContext.assert.ok(testContext.bridgeModule.acceptedTransfers.has("t7-resume"));
  testContext.assert.equal(
    calls.some(([name]) => name === "journalFinalized"),
    false,
    "a live pre-commit journal must not be acked during startup",
  );
  await testContext.bridgeModule.handleTransferCommitted(bridge, {
    token: "t7-resume",
    sourceId: "source",
    destinationId: "main",
  });
  testContext.plainTabsModule.replaceCenterTabsPayload(testContext.t5Base, { persist: true });
}


// Own terminal role whose journal was cleared before the crash: idempotent
// journalFinalized ack, no state change.
{
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload, {
    pendingTerminal: async () => [{
      token: "t7-ack",
      status: "committed",
      role: "source",
      windowId: "main",
      orphaned: false,
    }],
  });
  const beforeBytes = testContext.values.get("centerTabs:main");
  await testContext.bridgeModule.recoverPendingTabTransfers(bridge);
  testContext.assert.deepEqual(
    calls.find(([name]) => name === "journalFinalized"),
    ["journalFinalized", "t7-ack", "source", "main"],
  );
  testContext.assert.equal(testContext.values.get("centerTabs:main"), beforeBytes);
}


// Orphan cleanup: apply the durable decision to the destroyed owner's keyed
// storage, delete that keyed journal, then ack the orphan role.
{
  const orphanEntry = {
    ...testContext.journalEntry("t7-orphan"),
    role: "destination",
  };
  testContext.assert.equal(testContext.writeTransferJournal(orphanEntry, "win-orphan"), true);
  const { bridge, calls } = testContext.makeTransferBridge(testContext.t5Payload, {
    pendingTerminal: async () => [{
      token: "t7-orphan",
      status: "committed",
      role: "destination",
      windowId: "win-orphan",
      orphaned: true,
    }],
  });
  await testContext.bridgeModule.recoverPendingTabTransfers(bridge);
  testContext.assert.deepEqual(
    JSON.parse(testContext.values.get("centerTabs:win-orphan")),
    orphanEntry.afterCenterTabs,
  );
  testContext.assert.equal(
    JSON.parse(testContext.values.get("openprogram.sessionDraftState:win-orphan"))
      .composerDrafts.local_one,
    orphanEntry.afterSession.composerDrafts.local_one,
  );
  testContext.assert.deepEqual(testContext.readTransferJournal("win-orphan"), { version: 1, entries: {} });
  testContext.assert.deepEqual(
    calls.find(([name]) => name === "journalFinalized"),
    ["journalFinalized", "t7-orphan", "destination", "win-orphan"],
  );
  testContext.pendingProjection.unregisterPendingTransfer("t7-orphan", "win-orphan");
}


console.log("web-split checks passed");
}
