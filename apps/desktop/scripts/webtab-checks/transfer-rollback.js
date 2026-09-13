// transfer rollback checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function stageTransferForRollback(prefix, { ready = true, activeFind = false } = {}) {
  const sourceWin = testContext.fakeWindow(testContext.nextGeneratedWindowId++);
  const destinationWin = testContext.fakeWindow(testContext.nextGeneratedWindowId++);
  const sourceCtx = testContext.registerContext(`${prefix}-source`, sourceWin);
  const destinationCtx = testContext.registerContext(`${prefix}-destination`, destinationWin);
  const controlled = testContext.controlledRecord(`${prefix}-web`);
  const originalBounds = { x: 17, y: 19, width: 410, height: 330 };
  testContext.attachControlledRecord(sourceCtx, controlled, originalBounds);
  if (activeFind) controlled.record.findRequestId = 7;
  const token = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload([`${prefix}-web`]));
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, token));
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(destinationCtx, token, "destination"), true);
  testContext.assert.ok(testContext.hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }));
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(sourceCtx, token, "source"), true);
  if (ready) {
    testContext.assert.equal(testContext.hooks.tabTransfers.destinationReady(destinationCtx, token, true), true);
  }
  return {
    sourceWin,
    destinationWin,
    sourceCtx,
    destinationCtx,
    controlled,
    originalBounds,
    token,
    timer: testContext.hooks.tabTransfers.activeTransfers.get(token).timer,
  };
}

async function checkLockedRecordsRejectOrdinaryIpc() {
  const transaction = await stageTransferForRollback("locked-ipc", { activeFind: true });
  const { controlled, destinationCtx, destinationWin, sourceCtx, sourceWin, token } =
    transaction;
  const boundsBefore = controlled.currentBounds();
  const visibilityCallsBefore = controlled.visibility.length;
  const generatedBefore = testContext.generatedNativeViews;
  testContext.assert.equal(controlled.record.findRequestId, 7);
  testContext.assert.deepEqual(controlled.nativeCalls.stopFind, []);
  const destinationMessagesBeforeFind = destinationWin.sent.length;
  testContext.assert.equal(testContext.hooks.forwardFindResult(controlled.record, {
    requestId: 7,
    activeMatchOrdinal: 1,
    matches: 1,
    finalUpdate: true,
  }), false);
  let lockedShortcutPrevented = false;
  testContext.assert.equal(testContext.hooks.handleWebTabShortcut(controlled.record, {
    preventDefault() { lockedShortcutPrevented = true; },
  }, { type: "keyDown", meta: true, key: "f" }), false);
  testContext.assert.equal(lockedShortcutPrevented, false);
  testContext.assert.equal(destinationWin.sent.length, destinationMessagesBeforeFind);

  testContext.ipcListeners.get("webtab:ensure")(
    testContext.eventFor(sourceWin),
    "locked-ipc-web",
    "https://example.com/stale-source",
  );
  testContext.ipcListeners.get("webtab:navigate")(
    testContext.eventFor(sourceWin),
    "locked-ipc-web",
    "https://example.com/stale-source",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:activate")(
      testContext.eventFor(sourceWin),
      "locked-ipc-web",
      "https://example.com/stale-source",
    ),
    null,
  );
  testContext.ipcListeners.get("webtab:set-bounds")(
    testContext.eventFor(destinationWin),
    "locked-ipc-web",
    { x: 999, y: 999, width: 999, height: 999 },
  );
  testContext.ipcListeners.get("webtab:reload")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.ipcListeners.get("webtab:stop")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.ipcListeners.get("webtab:go-back")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.ipcListeners.get("webtab:go-forward")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.ipcListeners.get("webtab:find")(testContext.eventFor(destinationWin), "locked-ipc-web", "blocked", {});
  testContext.ipcListeners.get("webtab:stop-find")(testContext.eventFor(destinationWin), "locked-ipc-web", "clearSelection");
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:zoom")(testContext.eventFor(destinationWin), "locked-ipc-web", "in"),
    null,
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:print")(testContext.eventFor(destinationWin), "locked-ipc-web"),
    false,
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:capture")(testContext.eventFor(destinationWin), "locked-ipc-web"),
    null,
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")(testContext.eventFor(destinationWin), "locked-ipc-web", {
      x: 1,
      y: 1,
      width: 410,
      height: 330,
      sequence: 1,
    }),
    false,
  );
  testContext.ipcListeners.get("webtab:set-pip-zoom")(
    testContext.eventFor(destinationWin),
    "locked-ipc-web",
    640,
  );
  testContext.ipcListeners.get("webtab:show")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.ipcListeners.get("webtab:hide")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.ipcListeners.get("webtab:sync-visible")(testContext.eventFor(destinationWin), [{
    id: "locked-ipc-web",
    bounds: { x: 999, y: 999, width: 999, height: 999 },
  }]);
  testContext.ipcListeners.get("webtab:navigate")(
    testContext.eventFor(destinationWin),
    "locked-ipc-web",
    "https://example.com/stale-destination",
  );
  testContext.ipcListeners.get("webtab:destroy")(testContext.eventFor(destinationWin), "locked-ipc-web");
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:activate")(
      testContext.eventFor(destinationWin),
      "locked-ipc-web",
      "https://example.com/stale-destination",
    ),
    null,
  );

  testContext.assert.equal(testContext.generatedNativeViews, generatedBefore);
  testContext.assert.equal(sourceCtx.views.has("locked-ipc-web"), false);
  testContext.assert.strictEqual(destinationCtx.views.get("locked-ipc-web"), controlled.record);
  testContext.assert.deepEqual(controlled.calls, []);
  testContext.assert.deepEqual(controlled.nativeCalls, {
    reload: 0,
    stop: 0,
    back: 0,
    forward: 0,
    find: [],
    stopFind: [],
    zoom: [],
    print: [],
    printToPDF: [],
    capturePage: 0,
  });
  testContext.assert.deepEqual(controlled.currentBounds(), boundsBefore);
  testContext.assert.equal(controlled.visibility.length, visibilityCallsBefore);
  testContext.assert.equal(controlled.closeCallCount(), 0);

  testContext.assert.equal(testContext.hooks.tabTransfers.sourceRemoved(sourceCtx, token, { ok: false }), true);
  testContext.assert.equal(testContext.hooks.tabTransfers.destinationUndone(destinationCtx, token, true), true);
  await finalizeBoth(transaction);
  testContext.assert.equal(controlled.record.ownerId, sourceCtx.id);
  testContext.assert.equal(controlled.record.findRequestId, 7);
  testContext.assert.deepEqual(controlled.nativeCalls.stopFind, []);
}

async function finalizeBoth(transaction) {
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      transaction.destinationCtx,
      transaction.token,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      transaction.sourceCtx,
      transaction.token,
      "source",
    ),
    true,
  );
}

async function checkRollbackOrderingAndLateSourceRace() {
  const transaction = await stageTransferForRollback("rollback-order");
  const trace = [];
  const sourceState = {
    tabs: new Set(),
    drafts: new Map(),
    session: new Set(),
  };
  const destinationState = {
    tabs: new Set(["rollback-order-web"]),
    drafts: new Map([["draft:rollback-order-web", "changed"]]),
    ready: new Set(["rollback-order-web"]),
    bounds: new Map([["rollback-order-web", { width: 100 }]]),
    bridge: new Set(["rollback-order-web"]),
  };
  const sourceBridge = new Set();
  const closeBefore = transaction.controlled.closeCallCount();
  transaction.destinationWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:undo-destination" || item.token !== transaction.token) {
      return;
    }
    const active = testContext.hooks.tabTransfers.activeTransfers.get(transaction.token);
    testContext.assert.notEqual(active.undoTimer, null);
    testContext.assert.equal(testContext.clock.pendingIds().includes(active.undoTimer), true);
    destinationState.bridge.delete("rollback-order-web");
    destinationState.ready.delete("rollback-order-web");
    destinationState.bounds.delete("rollback-order-web");
    trace.push("forgetTransferredWebView/clear ready+bounds");
    testContext.ipcListeners.get("webtab:destroy")(
      testContext.eventFor(transaction.destinationWin),
      "rollback-order-web",
    );
    testContext.assert.equal(transaction.controlled.closeCallCount(), closeBefore);
    testContext.assert.equal(transaction.destinationCtx.views.has("rollback-order-web"), true);
    trace.push("stale store-subscription webtab:destroy rejected");
    destinationState.tabs.delete("rollback-order-web");
    destinationState.drafts.clear();
    trace.push("transient store/session undo");
    trace.push("destination-undone acknowledgement");
    testContext.assert.equal(
      testContext.hooks.tabTransfers.destinationUndone(
        transaction.destinationCtx,
        transaction.token,
        true,
      ),
      true,
    );
  };
  transaction.sourceWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:rolled-back" || item.token !== transaction.token) return;
    testContext.assert.equal(transaction.controlled.record.ownerId, transaction.sourceCtx.id);
    trace.push("native reparent-to-source");
    sourceBridge.add("rollback-order-web");
    trace.push("source bridge restore");
  };
  sourceState.tabs.add("rollback-order-web");
  sourceState.drafts.set("draft:rollback-order-web", "source draft");
  sourceState.session.add("rollback-order-session");
  trace.push("source recovery");
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      transaction.sourceCtx,
      transaction.token,
      { ok: false, sourceEmpty: false },
    ),
    true,
  );
  const rollbackUndoTimer = testContext.hooks.tabTransfers.activeTransfers.get(
    transaction.token,
  ).undoTimer;
  testContext.assert.notEqual(rollbackUndoTimer, null);
  testContext.assert.equal(testContext.clock.pendingIds().includes(rollbackUndoTimer), true);
  testContext.assert.equal(transaction.controlled.record.ownerId, transaction.destinationCtx.id);
  testContext.flushRendererQueue();
  testContext.assert.equal(testContext.clock.pendingIds().includes(rollbackUndoTimer), false);
  testContext.assert.strictEqual(
    transaction.sourceCtx.views.get("rollback-order-web"),
    transaction.controlled.record,
  );
  testContext.assert.equal(transaction.controlled.record.ownerId, transaction.sourceCtx.id);
  testContext.assert.deepEqual(transaction.controlled.currentBounds(), transaction.originalBounds);
  testContext.assert.equal(transaction.sourceCtx.visibleViewIds.has("rollback-order-web"), true);
  testContext.assert.equal(transaction.sourceWin.sent.at(-1)[0], "tab-transfer:rolled-back");
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(transaction.token), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("rollback-order-web"), false);
  testContext.assert.deepEqual([...destinationState.tabs], []);
  testContext.assert.deepEqual([...destinationState.drafts], []);
  testContext.assert.deepEqual([...destinationState.ready], []);
  testContext.assert.deepEqual([...destinationState.bounds], []);
  testContext.assert.deepEqual([...destinationState.bridge], []);
  testContext.assert.deepEqual([...sourceState.tabs], ["rollback-order-web"]);
  testContext.assert.deepEqual([...sourceState.drafts], [["draft:rollback-order-web", "source draft"]]);
  testContext.assert.deepEqual([...sourceState.session], ["rollback-order-session"]);
  testContext.assert.deepEqual([...sourceBridge], ["rollback-order-web"]);
  testContext.assert.deepEqual(trace, [
    "source recovery",
    "forgetTransferredWebView/clear ready+bounds",
    "stale store-subscription webtab:destroy rejected",
    "transient store/session undo",
    "destination-undone acknowledgement",
    "native reparent-to-source",
    "source bridge restore",
  ]);
  await finalizeBoth(transaction);

  const race = await stageTransferForRollback("timeout-race", { ready: false });
  let pendingSourceRemoval = null;
  const sourceRecovery = { restored: false };
  race.sourceWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:remove-source" || item.token !== race.token) return;
    pendingSourceRemoval = () => {
      const accepted = testContext.hooks.tabTransfers.sourceRemoved(
        race.sourceCtx,
        race.token,
        { ok: true, sourceEmpty: false },
      );
      if (!accepted) sourceRecovery.restored = true;
      return accepted;
    };
  };
  race.destinationWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:undo-destination" || item.token !== race.token) return;
    testContext.assert.equal(
      testContext.hooks.tabTransfers.destinationUndone(race.destinationCtx, race.token, true),
      true,
    );
  };
  testContext.assert.equal(testContext.hooks.tabTransfers.destinationReady(race.destinationCtx, race.token, true), true);
  testContext.flushRendererQueue();
  testContext.assert.equal(typeof pendingSourceRemoval, "function");
  testContext.clock.runCleared(race.timer);
  const raceUndoTimer = testContext.hooks.tabTransfers.activeTransfers.get(race.token).undoTimer;
  testContext.assert.notEqual(raceUndoTimer, null);
  testContext.flushRendererQueue();
  testContext.assert.equal(testContext.clock.pendingIds().includes(raceUndoTimer), false);
  testContext.assert.equal(pendingSourceRemoval(), false);
  testContext.assert.equal(sourceRecovery.restored, true);
  testContext.assert.equal(race.controlled.record.ownerId, race.sourceCtx.id);
  testContext.assert.equal(race.controlled.closeCallCount(), 0);
  await finalizeBoth(race);
}

async function checkDestinationUndoDeadlineDelegatesJournal() {
  const transaction = await stageTransferForRollback("undo-deadline");
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      transaction.sourceCtx,
      transaction.token,
      { ok: false, sourceEmpty: false },
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      transaction.destinationCtx,
      transaction.token,
      false,
    ),
    false,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(transaction.token), true);
  testContext.assert.equal(transaction.controlled.record.ownerId, transaction.destinationCtx.id);
  testContext.clock.advance(1_999);
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(transaction.token), true);
  testContext.clock.advance(1);

  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(transaction.token), false);
  testContext.assert.equal(transaction.controlled.record.ownerId, transaction.sourceCtx.id);
  const delegated = transaction.sourceWin.sent.find(
    ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
      && item.token === transaction.token
      && item.role === "destination",
  );
  testContext.assert.ok(delegated, "destination undo timeout must delegate its journal role");
  testContext.assert.deepEqual(testContext.plain(delegated[1]), {
    token: transaction.token,
    status: "rolled-back",
    role: "destination",
    windowId: transaction.destinationCtx.id,
    orphaned: true,
  });
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      transaction.sourceCtx,
      transaction.token,
      "source",
    ),
    true,
  );
  const sentCounts = new Map(testContext.fakeWindows.map((win) => [win, win.sent.length]));
  transaction.sourceWin.destroyed = true;
  testContext.hooks.tabTransfers.contextDestroyed(transaction.sourceCtx);
  const replacementWin = testContext.fakeWindows.find((win) =>
    win.sent.slice(sentCounts.get(win) || 0).some(
      ([channel, item]) => channel === "tab-transfer:finalize-orphaned"
        && item.token === transaction.token
        && item.role === "destination",
    ));
  testContext.assert.ok(replacementWin, "a surviving renderer must receive the reassigned role");
  const replacementCtx = testContext.hooks.contextsByBrowserWindowId.get(replacementWin.id);
  testContext.assert.ok(replacementCtx);
  const reassigned = testContext.hooks.tabTransfers.pendingTerminal(
    replacementCtx,
    replacementCtx.id,
  );
  testContext.assert.deepEqual(testContext.plain(reassigned), [{
    token: transaction.token,
    status: "rolled-back",
    sourceId: transaction.sourceCtx.id,
    destinationId: transaction.destinationCtx.id,
    role: "destination",
    windowId: transaction.destinationCtx.id,
    orphaned: true,
  }]);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      replacementCtx,
      transaction.token,
      "destination",
      transaction.destinationCtx.id,
    ),
    true,
  );
  testContext.assert.equal(testContext.loadTransferDecision(transaction.token), null);
}
return { stageTransferForRollback, checkLockedRecordsRejectOrdinaryIpc, finalizeBoth, checkRollbackOrderingAndLateSourceRace, checkDestinationUndoDeadlineDelegatesJournal };
};
