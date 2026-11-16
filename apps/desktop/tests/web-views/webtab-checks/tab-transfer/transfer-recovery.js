// transfer recovery checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkSourceEmptyDurabilityAndRestartAcknowledgements() {
  const sourceWin = testContext.fakeWindow(110);
  const destinationWin = testContext.fakeWindow(111);
  const sourceCtx = testContext.registerContext("empty-source", sourceWin);
  const destinationCtx = testContext.registerContext("empty-destination", destinationWin);
  const token = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["empty-metadata"]));
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, token));
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(destinationCtx, token, "destination"), true);
  testContext.assert.ok(testContext.hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }));
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(sourceCtx, token, "source"), true);
  testContext.assert.equal(testContext.hooks.tabTransfers.destinationReady(destinationCtx, token, true), true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(sourceCtx, token, { ok: true, sourceEmpty: true }),
    true,
  );
  testContext.assert.equal(sourceWin.closeCalls, 0);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(destinationCtx, token, "destination"),
    true,
  );
  testContext.assert.equal(sourceWin.closeCalls, 0);
  const restoreRename = testContext.installOneShotRenameFailure();
  try {
    testContext.assert.equal(
      testContext.hooks.tabTransfers.journalFinalized(sourceCtx, token, "source"),
      false,
    );
  } finally {
    restoreRename();
  }
  testContext.assert.equal(sourceWin.closeCalls, 0);
  const deletedDecisionReadFailure = testContext.installReadFailureWhenDecisionMissing(token);
  let finalAckResult;
  try {
    finalAckResult = testContext.hooks.tabTransfers.journalFinalized(sourceCtx, token, "source");
    testContext.assert.equal(finalAckResult, true);
    testContext.assert.throws(
      () => testContext.loadTransferDecision(token),
      /injected read failure after durable decision deletion/,
    );
  } finally {
    deletedDecisionReadFailure.restore();
  }
  testContext.assert.equal(deletedDecisionReadFailure.triggered(), true);
  testContext.assert.equal(sourceWin.closeCalls, 1);
  testContext.assert.equal(testContext.loadTransferDecision(token), null);

  const crashSourceWin = testContext.fakeWindow(112);
  const crashDestinationWin = testContext.fakeWindow(113);
  const crashSource = testContext.registerContext("crash-source", crashSourceWin);
  const crashDestination = testContext.registerContext("crash-destination", crashDestinationWin);
  const crashToken = testContext.prepareThroughIpc(
    crashSourceWin,
    testContext.webTransferPayload(["crash-metadata"]),
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(crashDestination, crashToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(crashDestination, crashToken, "destination"),
    true,
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.accept(crashDestination, crashToken, { kind: "strip-end" }));
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(crashSource, crashToken, "source"), true);
  testContext.assert.equal(testContext.hooks.tabTransfers.destinationReady(crashDestination, crashToken, true), true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      crashSource,
      crashToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.journalFinalized(crashSource, crashToken, "source"), true);
  testContext.assert.deepEqual(testContext.loadTransferDecision(crashToken).finalizedRoles, [
    { role: "source", windowId: crashSource.id },
  ]);
  const sourceAckBytes = testContext.fs.readFileSync(testContext.transferDecisionFile());

  const restarted = testContext.hooks.makeTransferCoordinator({
    windows: testContext.hooks.windows,
    decisionFilePath: testContext.transferDecisionFile,
    createWindow: testContext.hooks.createWindow,
    setTimer: testContext.clock.setTimeout,
    clearTimer: testContext.clock.clearTimeout,
  });
  testContext.assert.equal(restarted.status(crashDestination, crashToken).status, "committed");
  testContext.assert.deepEqual(
    testContext.plain(restarted.pendingTerminal(crashDestination, crashDestination.id)),
    [{
      token: crashToken,
      status: "committed",
      sourceId: crashSource.id,
      destinationId: crashDestination.id,
      role: "destination",
      windowId: crashDestination.id,
      orphaned: false,
    }],
  );
  testContext.assert.equal(restarted.journalFinalized(crashSource, crashToken, "source"), true);
  testContext.assert.deepEqual(testContext.fs.readFileSync(testContext.transferDecisionFile()), sourceAckBytes);
  testContext.assert.equal(restarted.journalFinalized(crashDestination, crashToken, "destination"), true);
  testContext.assert.equal(restarted.status(crashDestination, crashToken), null);
  testContext.assert.equal(testContext.loadTransferDecision(crashToken), null);

  const reverseSourceWin = testContext.fakeWindow(115);
  const reverseDestinationWin = testContext.fakeWindow(116);
  const reverseSource = testContext.registerContext("reverse-crash-source", reverseSourceWin);
  const reverseDestination = testContext.registerContext(
    "reverse-crash-destination",
    reverseDestinationWin,
  );
  const reverseToken = testContext.prepareThroughIpc(
    reverseSourceWin,
    testContext.webTransferPayload(["reverse-crash-metadata"]),
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(reverseDestination, reverseToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      reverseDestination,
      reverseToken,
      "destination",
    ),
    true,
  );
  testContext.assert.ok(
    testContext.hooks.tabTransfers.accept(
      reverseDestination,
      reverseToken,
      { kind: "strip-end" },
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(reverseSource, reverseToken, "source"),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(reverseDestination, reverseToken, true),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      reverseSource,
      reverseToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      reverseDestination,
      reverseToken,
      "destination",
    ),
    true,
  );
  testContext.assert.deepEqual(testContext.loadTransferDecision(reverseToken).finalizedRoles, [
    { role: "destination", windowId: reverseDestination.id },
  ]);
  const destinationAckBytes = testContext.fs.readFileSync(testContext.transferDecisionFile());
  const reverseRestarted = testContext.hooks.makeTransferCoordinator({
    windows: testContext.hooks.windows,
    decisionFilePath: testContext.transferDecisionFile,
    createWindow: testContext.hooks.createWindow,
    setTimer: testContext.clock.setTimeout,
    clearTimer: testContext.clock.clearTimeout,
  });
  testContext.assert.equal(reverseRestarted.status(reverseSource, reverseToken).status, "committed");
  testContext.assert.deepEqual(
    testContext.plain(reverseRestarted.pendingTerminal(reverseSource, reverseSource.id)),
    [{
      token: reverseToken,
      status: "committed",
      sourceId: reverseSource.id,
      destinationId: reverseDestination.id,
      role: "source",
      windowId: reverseSource.id,
      orphaned: false,
    }],
  );
  testContext.assert.equal(
    reverseRestarted.journalFinalized(
      reverseDestination,
      reverseToken,
      "destination",
    ),
    true,
  );
  testContext.assert.deepEqual(testContext.fs.readFileSync(testContext.transferDecisionFile()), destinationAckBytes);
  testContext.assert.equal(
    reverseRestarted.journalFinalized(reverseSource, reverseToken, "source"),
    true,
  );
  testContext.assert.equal(reverseRestarted.status(reverseSource, reverseToken), null);
  testContext.assert.equal(testContext.loadTransferDecision(reverseToken), null);
}

async function checkOrphanFinalizationPendingTerminalAndWindowClose() {
  const orphan = await testContext.stageTransferForRollback("orphan-destination");
  const orphanedJournals = new Set([
    `${orphan.token}:destination:${orphan.destinationCtx.id}`,
  ]);
  let delegatedDestinationAck = null;
  orphan.sourceWin.onSend = (channel, item) => {
    if (
      channel !== "tab-transfer:finalize-orphaned"
      || item.token !== orphan.token
      || item.role !== "destination"
    ) {
      return;
    }
    orphanedJournals.delete(`${item.token}:${item.role}:${item.windowId}`);
    delegatedDestinationAck = testContext.hooks.tabTransfers.journalFinalized(
      orphan.sourceCtx,
      item.token,
      item.role,
      item.windowId,
    );
  };
  orphan.destinationWin.destroyed = true;
  testContext.hooks.tabTransfers.contextDestroyed(orphan.destinationCtx);
  testContext.flushRendererQueue();
  testContext.assert.equal(orphan.controlled.record.ownerId, orphan.sourceCtx.id);
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(orphan.token), false);
  const orphanEvents = orphan.sourceWin.sent.filter(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === orphan.token,
  );
  testContext.assert.equal(orphanEvents.length, 1);
  const [orphanEvent] = orphanEvents;
  testContext.assert.ok(orphanEvent);
  testContext.assert.deepEqual(testContext.plain(orphanEvent[1]), {
    token: orphan.token,
    status: "rolled-back",
    role: "destination",
    windowId: orphan.destinationCtx.id,
    orphaned: true,
  });
  testContext.assert.deepEqual([...orphanedJournals], []);
  testContext.assert.equal(delegatedDestinationAck, true);
  testContext.assert.deepEqual(testContext.loadTransferDecision(orphan.token).finalizedRoles, [{
    role: "destination",
    windowId: orphan.destinationCtx.id,
  }]);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      orphan.sourceCtx,
      orphan.token,
      "source",
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(orphan.token), null);

  const sourceGoneWin = testContext.fakeWindow(117);
  const sourceGoneDestinationWin = testContext.fakeWindow(118);
  const sourceGone = testContext.registerContext("orphan-source", sourceGoneWin);
  const sourceGoneDestination = testContext.registerContext(
    "orphan-source-destination",
    sourceGoneDestinationWin,
  );
  const sourceGoneToken = testContext.prepareThroughIpc(
    sourceGoneWin,
    testContext.webTransferPayload(["orphan-source-metadata"]),
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(sourceGoneDestination, sourceGoneToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      sourceGoneDestination,
      sourceGoneToken,
      "destination",
    ),
    true,
  );
  testContext.assert.ok(
    testContext.hooks.tabTransfers.accept(
      sourceGoneDestination,
      sourceGoneToken,
      { kind: "strip-end" },
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(sourceGone, sourceGoneToken, "source"),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(
      sourceGoneDestination,
      sourceGoneToken,
      true,
    ),
    true,
  );
  sourceGoneWin.destroyed = true;
  testContext.hooks.cleanupWindowContext(sourceGone);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      sourceGoneDestination,
      sourceGoneToken,
      true,
    ),
    true,
  );
  const delegatedSource = sourceGoneDestinationWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === sourceGoneToken
      && item.role === "source",
  );
  testContext.assert.ok(delegatedSource);
  testContext.assert.deepEqual(testContext.plain(delegatedSource[1]), {
    token: sourceGoneToken,
    status: "rolled-back",
    role: "source",
    windowId: sourceGone.id,
    orphaned: true,
  });
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      sourceGoneDestination,
      sourceGoneToken,
      "source",
      sourceGone.id,
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      sourceGoneDestination,
      sourceGoneToken,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(sourceGoneToken), null);

  const sourceNativeGone = await testContext.stageTransferForRollback("orphan-source-native");
  sourceNativeGone.sourceWin.destroyed = true;
  testContext.hooks.cleanupWindowContext(sourceNativeGone.sourceCtx);
  const sourceNativeUndoTimer = testContext.hooks.tabTransfers.activeTransfers.get(
    sourceNativeGone.token,
  ).undoTimer;
  testContext.assert.notEqual(sourceNativeUndoTimer, null);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      sourceNativeGone.destinationCtx,
      sourceNativeGone.token,
      true,
    ),
    true,
  );
  testContext.assert.equal(
    sourceNativeGone.destinationCtx.views.has("orphan-source-native-web"),
    false,
  );
  testContext.assert.equal(
    sourceNativeGone.destinationCtx.visibleViewIds.has("orphan-source-native-web"),
    false,
  );
  testContext.assert.equal(sourceNativeGone.controlled.record.ownerId, null);
  testContext.assert.equal(sourceNativeGone.controlled.closeCallCount(), 1);
  const sourceNativeRollbackReceipts = sourceNativeGone.destinationWin.sent.filter(
    ([channel, item]) => channel === "tab-transfer:rolled-back"
      && item.token === sourceNativeGone.token,
  ).length;
  testContext.clock.runCleared(sourceNativeUndoTimer);
  testContext.assert.equal(sourceNativeGone.controlled.closeCallCount(), 1);
  testContext.assert.equal(
    sourceNativeGone.destinationWin.sent.filter(
      ([channel, item]) => channel === "tab-transfer:rolled-back"
        && item.token === sourceNativeGone.token,
    ).length,
    sourceNativeRollbackReceipts,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("orphan-source-native-web"), false);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.activeTransfers.has(sourceNativeGone.token),
    false,
  );
  testContext.assert.equal(testContext.loadTransferDecision(sourceNativeGone.token).status, "rolled-back");
  testContext.assert.ok(
    sourceNativeGone.destinationWin.sent.find(
      ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
        && item.token === sourceNativeGone.token
        && item.role === "source",
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      sourceNativeGone.destinationCtx,
      sourceNativeGone.token,
      "source",
      sourceNativeGone.sourceCtx.id,
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      sourceNativeGone.destinationCtx,
      sourceNativeGone.token,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(sourceNativeGone.token), null);

  const bothGone = await testContext.stageTransferForRollback("orphan-both");
  bothGone.sourceWin.destroyed = true;
  bothGone.destinationWin.destroyed = true;
  testContext.hooks.tabTransfers.contextDestroyed(bothGone.destinationCtx);
  testContext.assert.ok(testContext.loadTransferDecision(bothGone.token));
  const restarted = testContext.hooks.makeTransferCoordinator({
    windows: testContext.hooks.windows,
    decisionFilePath: testContext.transferDecisionFile,
    createWindow: testContext.hooks.createWindow,
    setTimer: testContext.clock.setTimeout,
    clearTimer: testContext.clock.clearTimeout,
  });
  const recoveryWin = testContext.fakeWindow(114);
  const recoveryCtx = testContext.registerContext("orphan-recovery", recoveryWin);
  const terminal = restarted.pendingTerminal(recoveryCtx, recoveryCtx.id);
  testContext.assert.deepEqual(
    Array.from(terminal, (item) => [item.role, item.windowId, item.orphaned]).sort(),
    [
      ["destination", bothGone.destinationCtx.id, true],
      ["source", bothGone.sourceCtx.id, true],
    ].sort(),
  );
  testContext.assert.equal(restarted.status(recoveryCtx, bothGone.token).status, "rolled-back");
  testContext.assert.equal(
    restarted.journalFinalized(
      recoveryCtx,
      bothGone.token,
      terminal[0].role,
      terminal[0].windowId,
    ),
    true,
  );
  testContext.assert.ok(testContext.loadTransferDecision(bothGone.token));
  testContext.assert.equal(
    restarted.journalFinalized(
      recoveryCtx,
      bothGone.token,
      terminal[1].role,
      terminal[1].windowId,
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(bothGone.token), null);

  const closing = await testContext.stageTransferForRollback("source-close");
  closing.sourceWin.close();
  testContext.assert.equal(closing.sourceWin.destroyed, false);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.status(closing.sourceCtx, closing.token).status,
    "rolling-back",
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(closing.destinationCtx, closing.token, true),
    true,
  );
  testContext.assert.equal(closing.controlled.record.ownerId, closing.sourceCtx.id);
  await testContext.finalizeBoth(closing);
}
return { checkSourceEmptyDurabilityAndRestartAcknowledgements, checkOrphanFinalizationPendingTerminalAndWindowClose };
};
