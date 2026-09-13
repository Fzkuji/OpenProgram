// transfer failures checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkPrecommitFailurePathsAndDynamicRoles() {
  const destinationFailure = await testContext.stageTransferForRollback(
    "destination-failure",
    { ready: false },
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      false,
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.status(
      destinationFailure.sourceCtx,
      destinationFailure.token,
    ).status,
    "rolling-back",
  );
  testContext.assert.equal(testContext.loadTransferDecision(destinationFailure.token).status, "rolled-back");
  testContext.assert.equal(
    testContext.hooks.tabTransfers.accept(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      { kind: "strip-end" },
    ),
    null,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      true,
    ),
    false,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      destinationFailure.destinationCtx,
      destinationFailure.token,
      true,
    ),
    true,
  );
  testContext.assert.equal(
    destinationFailure.controlled.record.ownerId,
    destinationFailure.sourceCtx.id,
  );
  await testContext.finalizeBoth(destinationFailure);

  const cancelled = await testContext.stageTransferForRollback("staged-cancel", { ready: false });
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(cancelled.sourceCtx, cancelled.token), true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      cancelled.destinationCtx,
      cancelled.token,
      true,
    ),
    true,
  );
  testContext.assert.equal(cancelled.controlled.record.ownerId, cancelled.sourceCtx.id);
  await testContext.finalizeBoth(cancelled);

  // A transient committed-decision write failure retries automatically:
  // first write fails, the retry succeeds, and the transfer commits.
  const decisionRetry = await testContext.stageTransferForRollback("decision-write-retry");
  const restoreRename = testContext.installOneShotRenameFailure();
  let retrySettled = null;
  let retryReply;
  try {
    retryReply = Promise.resolve(testContext.hooks.tabTransfers.sourceRemoved(
      decisionRetry.sourceCtx,
      decisionRetry.token,
      { ok: true, sourceEmpty: false },
    )).then((value) => {
      retrySettled = value;
      return value;
    });
    await Promise.resolve();
    testContext.assert.equal(retrySettled, null);
    testContext.assert.equal(
      testContext.hooks.tabTransfers.status(decisionRetry.sourceCtx, decisionRetry.token).status,
      "committing",
    );
    testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(decisionRetry.token), true);
    testContext.assert.equal(testContext.loadTransferDecision(decisionRetry.token), null);
    // The retry window is not subject to the transfer timeout.
    testContext.assert.equal(testContext.clock.pendingIds().includes(decisionRetry.timer), false);
    testContext.clock.runCleared(decisionRetry.timer);
    testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(decisionRetry.token), true);
    testContext.assert.equal(
      testContext.hooks.tabTransfers.status(decisionRetry.sourceCtx, decisionRetry.token).status,
      "committing",
    );
    // A duplicate source acknowledgement joins the same pending commit.
    const duplicateReply = testContext.hooks.tabTransfers.sourceRemoved(
      decisionRetry.sourceCtx,
      decisionRetry.token,
      { ok: true, sourceEmpty: false },
    );
    testContext.assert.equal(typeof duplicateReply?.then, "function");
    testContext.clock.advance(100);
    await Promise.resolve();
    testContext.assert.equal(await retryReply, true);
    testContext.assert.equal(await duplicateReply, true);
  } finally {
    restoreRename();
  }
  testContext.assert.equal(testContext.loadTransferDecision(decisionRetry.token).status, "committed");
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(decisionRetry.token), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("decision-write-retry-web"), false);
  testContext.assert.equal(
    decisionRetry.controlled.record.ownerId,
    decisionRetry.destinationCtx.id,
  );
  for (const win of [decisionRetry.sourceWin, decisionRetry.destinationWin]) {
    testContext.assert.ok(win.sent.some(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === decisionRetry.token,
    ));
    testContext.assert.equal(win.sent.some(
      ([channel, item]) => (channel === "tab-transfer:rolled-back"
        || channel === "tab-transfer:undo-destination")
        && item.token === decisionRetry.token,
    ), false);
  }
  await testContext.finalizeBoth(decisionRetry);

  // A persistently failing committed-decision write exhausts its retries and
  // then takes the ordinary pre-commit rollback path, in plan order.
  const decisionFailure = await testContext.stageTransferForRollback("decision-write-failure");
  const exhaustedFault = testContext.installCommittedDecisionWriteFailure(decisionFailure.token);
  const exhaustedTrace = [];
  decisionFailure.destinationWin.onSend = (channel, item) => {
    if (item?.token !== decisionFailure.token) return;
    if (channel === "tab-transfer:undo-destination") {
      // The rolled-back decision is durable before the destination undo.
      testContext.assert.equal(testContext.loadTransferDecision(decisionFailure.token).status, "rolled-back");
      exhaustedTrace.push("undo-destination");
      return;
    }
    if (channel === "tab-transfer:rolled-back") exhaustedTrace.push("rolled-back");
  };
  let exhaustedSettled = null;
  let exhaustedReply;
  try {
    exhaustedReply = Promise.resolve(testContext.hooks.tabTransfers.sourceRemoved(
      decisionFailure.sourceCtx,
      decisionFailure.token,
      { ok: true, sourceEmpty: false },
    )).then((value) => {
      exhaustedSettled = value;
      return value;
    });
    await Promise.resolve();
    testContext.assert.equal(exhaustedSettled, null);
    testContext.assert.equal(
      testContext.hooks.tabTransfers.status(decisionFailure.sourceCtx, decisionFailure.token).status,
      "committing",
    );
    testContext.assert.equal(testContext.clock.pendingIds().includes(decisionFailure.timer), false);
    for (let elapsed = 0; elapsed < 20_000 && exhaustedSettled === null; elapsed += 1_000) {
      testContext.clock.advance(1_000);
      await Promise.resolve();
    }
    testContext.assert.equal(await exhaustedReply, false);
    testContext.assert.ok(exhaustedFault.failures() >= 5);
  } finally {
    exhaustedFault.restore();
  }
  testContext.assert.equal(
    testContext.hooks.tabTransfers.status(decisionFailure.sourceCtx, decisionFailure.token).status,
    "rolling-back",
  );
  testContext.assert.equal(testContext.loadTransferDecision(decisionFailure.token).status, "rolled-back");
  testContext.flushRendererQueue();
  testContext.assert.deepEqual(exhaustedTrace, ["undo-destination"]);
  testContext.assert.equal(
    decisionFailure.sourceWin.sent.some(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === decisionFailure.token,
    ),
    false,
  );
  testContext.assert.equal(
    decisionFailure.destinationWin.sent.some(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === decisionFailure.token,
    ),
    false,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      decisionFailure.destinationCtx,
      decisionFailure.token,
      true,
    ),
    true,
  );
  testContext.assert.equal(
    decisionFailure.controlled.record.ownerId,
    decisionFailure.sourceCtx.id,
  );
  testContext.flushRendererQueue();
  testContext.assert.deepEqual(exhaustedTrace, ["undo-destination", "rolled-back"]);
  await testContext.finalizeBoth(decisionFailure);

  const ambiguousCommit = await testContext.stageTransferForRollback("ambiguous-commit");
  const ambiguousFault = testContext.installAmbiguousCommitFailure(ambiguousCommit.token);
  let ambiguousResult;
  try {
    ambiguousResult = await testContext.hooks.tabTransfers.sourceRemoved(
      ambiguousCommit.sourceCtx,
      ambiguousCommit.token,
      { ok: true, sourceEmpty: false },
    );
  } finally {
    ambiguousFault.restore();
  }
  testContext.assert.equal(ambiguousFault.sawCommitted(), true);
  testContext.assert.equal(ambiguousResult, true);
  testContext.assert.equal(testContext.loadTransferDecision(ambiguousCommit.token).status, "committed");
  testContext.assert.equal(ambiguousCommit.controlled.record.ownerId, ambiguousCommit.destinationCtx.id);
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(ambiguousCommit.token), false);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      ambiguousCommit.destinationCtx,
      ambiguousCommit.token,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      ambiguousCommit.sourceCtx,
      ambiguousCommit.token,
      "source",
    ),
    true,
  );

  const unconfirmedCommit = await testContext.stageTransferForRollback("unconfirmed-commit");
  const unconfirmedTimer = testContext.hooks.tabTransfers.activeTransfers.get(
    unconfirmedCommit.token,
  ).timer;
  const unconfirmedFault = testContext.installAmbiguousCommitFailure(
    unconfirmedCommit.token,
    { blockReconcileReads: true },
  );
  let unconfirmedSettled = false;
  let sourceRecovered = false;
  let unconfirmedReply;
  try {
    unconfirmedReply = Promise.resolve(testContext.hooks.tabTransfers.sourceRemoved(
      unconfirmedCommit.sourceCtx,
      unconfirmedCommit.token,
      { ok: true, sourceEmpty: false },
    )).then((result) => {
      unconfirmedSettled = true;
      if (!result) sourceRecovered = true;
      return result;
    });
    await Promise.resolve();
    testContext.assert.equal(unconfirmedSettled, false);
    testContext.assert.equal(sourceRecovered, false);
    testContext.assert.equal(
      testContext.hooks.tabTransfers.status(
        unconfirmedCommit.destinationCtx,
        unconfirmedCommit.token,
      ).status,
      "commit-indeterminate",
    );
    testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(unconfirmedCommit.token), true);
    testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("unconfirmed-commit-web"), true);
    testContext.assert.equal(testContext.clock.pendingIds().includes(unconfirmedTimer), false);
    testContext.assert.equal(
      unconfirmedCommit.controlled.record.ownerId,
      unconfirmedCommit.destinationCtx.id,
    );
    testContext.hooks.tabTransfers.rollback(unconfirmedCommit.token, "manual-during-indeterminate");
    testContext.hooks.tabTransfers.cancel(unconfirmedCommit.sourceCtx, unconfirmedCommit.token);
    let closePrevented = false;
    testContext.assert.equal(
      testContext.hooks.tabTransfers.windowClosing(unconfirmedCommit.sourceCtx, {
        preventDefault() { closePrevented = true; },
      }),
      true,
    );
    testContext.assert.equal(closePrevented, true);
    testContext.clock.runCleared(unconfirmedTimer);
    for (let elapsed = 0; elapsed < 60_000; elapsed += 1_000) {
      testContext.clock.advance(1_000);
      await Promise.resolve();
    }
    testContext.assert.ok(unconfirmedFault.reconcileReadFailures() >= 2);
    testContext.assert.equal(unconfirmedSettled, false);
    testContext.assert.equal(sourceRecovered, false);
    testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(unconfirmedCommit.token), true);
    testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("unconfirmed-commit-web"), true);
    testContext.assert.equal(
      unconfirmedCommit.controlled.record.ownerId,
      unconfirmedCommit.destinationCtx.id,
    );
    testContext.assert.equal(
      unconfirmedCommit.destinationWin.sent.some(
        ([channel, item]) => channel === "tab-transfer:undo-destination"
          && item.token === unconfirmedCommit.token,
      ),
      false,
    );
    testContext.assert.equal(
      [...unconfirmedCommit.sourceWin.sent, ...unconfirmedCommit.destinationWin.sent].some(
        ([channel, item]) => channel === "tab-transfer:rolled-back"
          && item.token === unconfirmedCommit.token,
      ),
      false,
    );
    unconfirmedFault.releaseReconcileReads();
    for (let elapsed = 0; elapsed < 10_000 && !unconfirmedSettled; elapsed += 1_000) {
      testContext.clock.advance(1_000);
      await Promise.resolve();
    }
    testContext.assert.equal(await unconfirmedReply, true);
  } finally {
    unconfirmedFault.restore();
  }
  testContext.assert.equal(sourceRecovered, false);
  testContext.assert.equal(testContext.loadTransferDecision(unconfirmedCommit.token).status, "committed");
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(unconfirmedCommit.token), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("unconfirmed-commit-web"), false);
  testContext.assert.ok(
    unconfirmedCommit.destinationWin.sent.find(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === unconfirmedCommit.token,
    ),
  );
  testContext.assert.ok(
    unconfirmedCommit.sourceWin.sent.find(
      ([channel, item]) => channel === "tab-transfer:committed"
        && item.token === unconfirmedCommit.token,
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      unconfirmedCommit.destinationCtx,
      unconfirmedCommit.token,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      unconfirmedCommit.sourceCtx,
      unconfirmedCommit.token,
      "source",
    ),
    true,
  );

  const postCommitReadFailure = await testContext.stageTransferForRollback(
    "post-commit-read-failure",
  );
  const postCommitReadFault = testContext.installReadFailureAt(3);
  let postCommitResult;
  try {
    postCommitResult = testContext.hooks.tabTransfers.sourceRemoved(
      postCommitReadFailure.sourceCtx,
      postCommitReadFailure.token,
      { ok: true, sourceEmpty: false },
    );
    testContext.assert.equal(postCommitResult, true);
    testContext.assert.throws(
      () => testContext.hooks.tabTransfers.status(
        postCommitReadFailure.sourceCtx,
        postCommitReadFailure.token,
      ),
      /injected read failure at call 3/,
    );
  } finally {
    postCommitReadFault.restore();
  }
  testContext.assert.equal(postCommitReadFault.triggered(), true);
  testContext.assert.equal(postCommitReadFault.calls(), 3);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.status(
      postCommitReadFailure.sourceCtx,
      postCommitReadFailure.token,
    ).status,
    "committed",
  );
  testContext.assert.equal(testContext.loadTransferDecision(postCommitReadFailure.token).status, "committed");
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      postCommitReadFailure.destinationCtx,
      postCommitReadFailure.token,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      postCommitReadFailure.sourceCtx,
      postCommitReadFailure.token,
      "source",
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(postCommitReadFailure.token), null);

  const destinationClose = await testContext.stageTransferForRollback("destination-close");
  destinationClose.destinationWin.close();
  testContext.assert.equal(destinationClose.destinationWin.destroyed, false);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.status(destinationClose.sourceCtx, destinationClose.token).status,
    "rolling-back",
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      destinationClose.destinationCtx,
      destinationClose.token,
      true,
    ),
    true,
  );
  testContext.assert.equal(destinationClose.controlled.record.ownerId, destinationClose.sourceCtx.id);
  await testContext.finalizeBoth(destinationClose);

  const dynamicSourceWin = testContext.fakeWindow(105);
  const dynamicDestinationWin = testContext.fakeWindow(106);
  const dynamicSource = testContext.registerContext("dynamic-source", dynamicSourceWin);
  const dynamicDestination = testContext.registerContext(
    "dynamic-destination",
    dynamicDestinationWin,
  );
  const dynamicToken = testContext.prepareThroughIpc(
    dynamicSourceWin,
    testContext.webTransferPayload(["dynamic-metadata"]),
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(dynamicDestination, dynamicToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      dynamicDestination,
      dynamicToken,
      "destination",
    ),
    true,
  );
  testContext.assert.ok(
    testContext.hooks.tabTransfers.accept(dynamicDestination, dynamicToken, { kind: "strip-end" }),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(dynamicDestination, dynamicToken, false),
    true,
  );
  testContext.assert.deepEqual(testContext.loadTransferDecision(dynamicToken).requiredRoles, [
    { role: "destination", windowId: dynamicDestination.id },
  ]);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(dynamicDestination, dynamicToken, true),
    true,
  );
  testContext.assert.deepEqual(
    testContext.plain(testContext.hooks.tabTransfers.pendingTerminal(dynamicDestination, dynamicDestination.id)),
    [{
      token: dynamicToken,
      status: "rolled-back",
      sourceId: dynamicSource.id,
      destinationId: dynamicDestination.id,
      role: "destination",
      windowId: dynamicDestination.id,
      orphaned: false,
    }],
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(dynamicSource, dynamicToken, "source"),
    false,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      dynamicDestination,
      dynamicToken,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(dynamicToken), null);

  const detachedSourceWin = testContext.fakeWindow(107);
  const detachedSource = testContext.registerContext("detached-rollback-source", detachedSourceWin);
  const detachedToken = testContext.prepareThroughIpc(
    detachedSourceWin,
    testContext.webTransferPayload(["detached-rollback-metadata"]),
  );
  const detachedId = await testContext.hooks.tabTransfers.detach(detachedSource, detachedToken);
  const detachedDestination = testContext.hooks.windows.get(detachedId);
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(detachedDestination, detachedToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      detachedDestination,
      detachedToken,
      "destination",
    ),
    true,
  );
  testContext.assert.ok(
    testContext.hooks.tabTransfers.accept(
      detachedDestination,
      detachedToken,
      { kind: "strip-end" },
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(detachedSource, detachedToken, "source"),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(detachedDestination, detachedToken, false),
    true,
  );
  const detachedUndo = detachedDestination.win.sent.find(
    ([channel, detail]) =>
      channel === "tab-transfer:undo-destination"
      && detail.token === detachedToken,
  );
  testContext.assert.equal(detachedUndo?.[1].discardWindowState, true);
  testContext.assert.equal(
    testContext.loadTransferDecision(detachedToken).discardDestinationState,
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(detachedDestination, detachedToken, false),
    false,
  );
  testContext.clock.advance(2_000);
  testContext.assert.equal(detachedDestination.win.closeCalls, 1);
  testContext.assert.equal(detachedDestination.win.destroyed, true);
  const detachedOrphan = detachedSource.win.sent.find(
    ([channel, detail]) =>
      channel === "tab-transfer:finalize-orphaned"
      && detail.token === detachedToken
      && detail.role === "destination",
  );
  testContext.assert.equal(detachedOrphan?.[1].discardWindowState, true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      detachedSource,
      detachedToken,
      "destination",
      detachedDestination.id,
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(detachedSource, detachedToken, "source"),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(detachedToken), null);

  const preparedJournalSourceWin = testContext.fakeWindow(108);
  const preparedJournalDestinationWin = testContext.fakeWindow(109);
  const preparedJournalSource = testContext.registerContext(
    "prepared-journal-source",
    preparedJournalSourceWin,
  );
  const preparedJournalDestination = testContext.registerContext(
    "prepared-journal-destination",
    preparedJournalDestinationWin,
  );
  const preparedJournalToken = testContext.prepareThroughIpc(
    preparedJournalSourceWin,
    testContext.webTransferPayload(["prepared-journal-metadata"]),
  );
  testContext.assert.ok(
    testContext.hooks.tabTransfers.inspect(preparedJournalDestination, preparedJournalToken),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      preparedJournalDestination,
      preparedJournalToken,
      "destination",
    ),
    true,
  );
  preparedJournalDestinationWin.destroyed = true;
  testContext.hooks.cleanupWindowContext(preparedJournalDestination);
  const preparedDecision = testContext.loadTransferDecision(preparedJournalToken);
  testContext.assert.equal(preparedDecision.status, "rolled-back");
  testContext.assert.deepEqual(preparedDecision.requiredRoles, [{
    role: "destination",
    windowId: preparedJournalDestination.id,
  }]);
  const preparedDelegation = preparedJournalSourceWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === preparedJournalToken
      && item.role === "destination",
  );
  testContext.assert.ok(preparedDelegation);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      preparedJournalSource,
      preparedJournalToken,
      "destination",
      preparedJournalDestination.id,
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(preparedJournalToken), null);

  const preparedSourceJournalWin = testContext.fakeWindow(119);
  const preparedSourceJournalWorkerWin = testContext.fakeWindow(120);
  const preparedSourceJournal = testContext.hooks.makeWindowContext(
    "prepared-source-journal-owner",
    preparedSourceJournalWin,
  );
  const preparedSourceJournalWorker = testContext.hooks.makeWindowContext(
    "prepared-source-journal-worker",
    preparedSourceJournalWorkerWin,
  );
  const isolatedWindows = new Map([
    [preparedSourceJournal.id, preparedSourceJournal],
    [preparedSourceJournalWorker.id, preparedSourceJournalWorker],
  ]);
  const preparedSourceCoordinator = testContext.hooks.makeTransferCoordinator({
    windows: isolatedWindows,
    decisionFilePath: testContext.transferDecisionFile,
    createWindow: testContext.hooks.createWindow,
    setTimer: testContext.clock.setTimeout,
    clearTimer: testContext.clock.clearTimeout,
  });
  const preparedSourceToken = preparedSourceCoordinator.prepare(
    preparedSourceJournal,
    testContext.webTransferPayload(["prepared-source-journal-metadata"]),
  );
  testContext.assert.equal(
    preparedSourceCoordinator.journalOpened(
      preparedSourceJournal,
      preparedSourceToken,
      "source",
    ),
    true,
  );
  preparedSourceJournalWin.destroyed = true;
  preparedSourceCoordinator.contextDestroyed(preparedSourceJournal);
  const preparedSourceDecision = testContext.loadTransferDecision(preparedSourceToken);
  testContext.assert.equal(preparedSourceDecision.status, "rolled-back");
  testContext.assert.deepEqual(preparedSourceDecision.requiredRoles, [{
    role: "source",
    windowId: preparedSourceJournal.id,
  }]);
  const preparedSourceDelegation = preparedSourceJournalWorkerWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === preparedSourceToken
      && item.role === "source",
  );
  testContext.assert.ok(preparedSourceDelegation);
  testContext.assert.equal(
    preparedSourceCoordinator.journalFinalized(
      preparedSourceJournalWorker,
      preparedSourceToken,
      "source",
      preparedSourceJournal.id,
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(preparedSourceToken), null);
}
return { checkPrecommitFailurePathsAndDynamicRoles };
};
