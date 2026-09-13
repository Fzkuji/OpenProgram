// transfer ownership checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkAtomicReparentAndMetadataOnlyTransfer() {
  const sourceWin = testContext.fakeWindow(90);
  const destinationWin = testContext.fakeWindow(91);
  const sourceCtx = testContext.registerContext("atomic-source", sourceWin);
  const destinationCtx = testContext.registerContext("atomic-destination", destinationWin);
  const first = testContext.controlledRecord("atomic-a");
  const second = testContext.controlledRecord("atomic-b");
  testContext.attachControlledRecord(sourceCtx, first, { x: 1, y: 1, width: 200, height: 200 });
  testContext.attachControlledRecord(sourceCtx, second, { x: 201, y: 1, width: 200, height: 200 });
  destinationCtx.visibleViewIds = new Set(["destination-existing", "atomic-a"]);
  const sourceVisibleBefore = [...sourceCtx.visibleViewIds];
  const destinationVisibleBefore = [...destinationCtx.visibleViewIds];
  const token = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["atomic-a", "atomic-b"]));
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, token));
  destinationWin.failAddAt = 2;
  testContext.assert.equal(testContext.hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }), null);
  testContext.assert.strictEqual(sourceCtx.views.get("atomic-a"), first.record);
  testContext.assert.strictEqual(sourceCtx.views.get("atomic-b"), second.record);
  testContext.assert.equal(first.record.ownerId, sourceCtx.id);
  testContext.assert.equal(second.record.ownerId, sourceCtx.id);
  testContext.assert.equal(destinationCtx.views.has("atomic-a"), false);
  testContext.assert.equal(destinationCtx.views.has("atomic-b"), false);
  testContext.assert.deepEqual([...sourceCtx.visibleViewIds], sourceVisibleBefore);
  testContext.assert.deepEqual([...destinationCtx.visibleViewIds], destinationVisibleBefore);
  testContext.assert.equal(first.closeCallCount(), 0);
  testContext.assert.equal(second.closeCallCount(), 0);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("atomic-a"), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, token), true);

  const conflictSourceWin = testContext.fakeWindow(94);
  const conflictDestinationAWin = testContext.fakeWindow(95);
  const conflictDestinationBWin = testContext.fakeWindow(96);
  const conflictSource = testContext.registerContext("lock-conflict-source", conflictSourceWin);
  const conflictDestinationA = testContext.registerContext(
    "lock-conflict-destination-a",
    conflictDestinationAWin,
  );
  const conflictDestinationB = testContext.registerContext(
    "lock-conflict-destination-b",
    conflictDestinationBWin,
  );
  const conflictA = testContext.controlledRecord("lock-conflict-a");
  const conflictB = testContext.controlledRecord("lock-conflict-b");
  testContext.attachControlledRecord(
    conflictSource,
    conflictA,
    { x: 1, y: 1, width: 200, height: 200 },
  );
  testContext.attachControlledRecord(
    conflictSource,
    conflictB,
    { x: 201, y: 1, width: 200, height: 200 },
  );
  const conflictTokenA = testContext.prepareThroughIpc(
    conflictSourceWin,
    testContext.webTransferPayload(["lock-conflict-b"]),
  );
  const conflictTokenB = testContext.prepareThroughIpc(
    conflictSourceWin,
    testContext.webTransferPayload(["lock-conflict-a", "lock-conflict-b"]),
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(conflictDestinationA, conflictTokenA));
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(conflictDestinationB, conflictTokenB));
  testContext.assert.ok(
    testContext.hooks.tabTransfers.accept(
      conflictDestinationA,
      conflictTokenA,
      { kind: "strip-end" },
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.accept(
      conflictDestinationB,
      conflictTokenB,
      { kind: "strip-end" },
    ),
    null,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("lock-conflict-a"), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("lock-conflict-b"), true);
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(conflictSource, conflictTokenB), true);
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(conflictSource, conflictTokenA), true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationUndone(
      conflictDestinationA,
      conflictTokenA,
      true,
    ),
    true,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("lock-conflict-b"), false);
  testContext.assert.strictEqual(conflictSource.views.get("lock-conflict-a"), conflictA.record);
  testContext.assert.strictEqual(conflictSource.views.get("lock-conflict-b"), conflictB.record);

  const metadataSourceWin = testContext.fakeWindow(92);
  const metadataDestinationWin = testContext.fakeWindow(93);
  const metadataSource = testContext.registerContext("metadata-source", metadataSourceWin);
  const metadataDestination = testContext.registerContext("metadata-destination", metadataDestinationWin);
  const metadataToken = testContext.prepareThroughIpc(
    metadataSourceWin,
    testContext.webTransferPayload(["metadata-only"]),
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(metadataDestination, metadataToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      metadataDestination,
      metadataToken,
      "destination",
    ),
    true,
  );
  const metadataAccepted = testContext.hooks.tabTransfers.accept(
    metadataDestination,
    metadataToken,
    { kind: "strip-end" },
  );
  testContext.assert.deepEqual(Array.from(metadataAccepted.recordIds), []);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(metadataSource, metadataToken, "source"),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(metadataDestination, metadataToken, true),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      metadataSource,
      metadataToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  const beforeEnsure = testContext.generatedNativeViews;
  const created = testContext.hooks.ensureView(
    metadataDestination,
    "metadata-only",
    "https://example.com/metadata-only",
  );
  testContext.assert.ok(created);
  testContext.assert.strictEqual(
    testContext.hooks.ensureView(
      metadataDestination,
      "metadata-only",
      "https://example.com/metadata-only",
    ),
    created,
  );
  testContext.assert.equal(testContext.generatedNativeViews, beforeEnsure + 1);
  testContext.assert.equal(metadataDestination.views.size, 1);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      metadataDestination,
      metadataToken,
      "destination",
    ),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      metadataSource,
      metadataToken,
      "source",
    ),
    true,
  );
}

async function checkRejectCancelExpiryDetachAndClaim() {
  const sourceWin = testContext.fakeWindow(100);
  const destinationWin = testContext.fakeWindow(101);
  const otherWin = testContext.fakeWindow(102);
  const sourceCtx = testContext.registerContext("reject-source", sourceWin);
  const destinationCtx = testContext.registerContext("reject-destination", destinationWin);
  const otherCtx = testContext.registerContext("reject-other", otherWin);
  const record = testContext.controlledRecord("reject-web");
  testContext.attachControlledRecord(sourceCtx, record, { x: 1, y: 2, width: 300, height: 200 });
  const sourceRenderer = {
    tabs: ["reject-web"],
    drafts: [["draft:reject-web", "source draft"]],
    preparedTokens: new Set(),
  };
  const destinationRenderer = {
    tabs: ["existing-web"],
    drafts: [["draft:existing-web", "destination draft"]],
    activeId: null,
  };
  const rendererStoresBefore = JSON.stringify({
    sourceTabs: sourceRenderer.tabs,
    sourceDrafts: sourceRenderer.drafts,
    destinationTabs: destinationRenderer.tabs,
    destinationDrafts: destinationRenderer.drafts,
  });
  sourceWin.onSend = (channel, item) => {
    if (channel === "tab-transfer:rejected") {
      sourceRenderer.preparedTokens.delete(item.token);
    }
  };

  const duplicateToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["reject-web"]));
  sourceRenderer.preparedTokens.add(duplicateToken);
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, duplicateToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.reject(otherCtx, duplicateToken, "duplicate", "existing-web"),
    null,
  );
  const duplicateResult = testContext.plain(testContext.hooks.tabTransfers.reject(
      destinationCtx,
      duplicateToken,
      "duplicate",
      "existing-web",
    ));
  testContext.assert.deepEqual(duplicateResult, { reason: "duplicate", duplicateId: "existing-web" });
  destinationRenderer.activeId = duplicateResult.duplicateId;
  testContext.assert.equal(destinationRenderer.activeId, "existing-web");
  testContext.flushRendererQueue();
  testContext.assert.equal(sourceRenderer.preparedTokens.has(duplicateToken), false);
  testContext.assert.equal(JSON.stringify({
    sourceTabs: sourceRenderer.tabs,
    sourceDrafts: sourceRenderer.drafts,
    destinationTabs: destinationRenderer.tabs,
    destinationDrafts: destinationRenderer.drafts,
  }), rendererStoresBefore);
  testContext.assert.deepEqual(testContext.plain(sourceWin.sent.at(-1)), [
    "tab-transfer:rejected",
    { token: duplicateToken, reason: "duplicate", duplicateId: "existing-web" },
  ]);
  testContext.assert.strictEqual(sourceCtx.views.get("reject-web"), record.record);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, duplicateToken), null);
  testContext.assert.equal(testContext.hooks.tabTransfers.inspect(destinationCtx, duplicateToken), null);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.accept(destinationCtx, duplicateToken, { kind: "strip-end" }),
    null,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.reject(
      destinationCtx,
      duplicateToken,
      "duplicate",
      "existing-web",
    ),
    null,
  );

  const fullToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["reject-web"]));
  sourceRenderer.preparedTokens.add(fullToken);
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, fullToken));
  testContext.assert.deepEqual(
    testContext.plain(testContext.hooks.tabTransfers.reject(destinationCtx, fullToken, "group-full")),
    { reason: "group-full" },
  );
  testContext.flushRendererQueue();
  testContext.assert.equal(sourceWin.sent.at(-1)[1].reason, "group-full");
  testContext.assert.equal(sourceRenderer.preparedTokens.has(fullToken), false);
  testContext.assert.equal(JSON.stringify({
    sourceTabs: sourceRenderer.tabs,
    sourceDrafts: sourceRenderer.drafts,
    destinationTabs: destinationRenderer.tabs,
    destinationDrafts: destinationRenderer.drafts,
  }), rendererStoresBefore);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, fullToken), null);

  const cancelToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["reject-web"]));
  const cancelTimer = testContext.hooks.tabTransfers.activeTransfers.get(cancelToken).timer;
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, cancelToken), true);
  testContext.clock.runCleared(cancelTimer);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, cancelToken), null);

  const expiryToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["reject-web"]));
  testContext.clock.advance(14_999);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, expiryToken).status, "prepared");
  testContext.clock.advance(1);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, expiryToken), null);
  testContext.assert.equal(sourceWin.sent.at(-1)[0], "tab-transfer:rejected");
  testContext.assert.equal(sourceWin.sent.at(-1)[1].reason, "expired");

  const detachToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["reject-web"]));
  const detachedWindowId = await testContext.hooks.tabTransfers.detach(sourceCtx, detachToken);
  testContext.assert.equal(typeof detachedWindowId, "string");
  const detachedCtx = testContext.hooks.windows.get(detachedWindowId);
  testContext.assert.ok(detachedCtx);
  testContext.assert.equal(detachedCtx.win.shown, false);
  testContext.assert.deepEqual(detachedCtx.win.sent, []);
  testContext.assert.equal(testContext.hooks.tabTransfers.claimPending(otherCtx, detachedWindowId), null);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId),
    detachToken,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId),
    detachToken,
  );
  detachedCtx.win.webContents.emit("did-finish-load");
  testContext.assert.equal(
    detachedCtx.win.sent.some((item) => String(item[0]).startsWith("tab-transfer:")),
    false,
    "did-finish-load on a detached window must not deliver a transfer token",
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId),
    detachToken,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, detachToken), true);
  testContext.assert.equal(detachedCtx.win.closeCalls, 1);
  testContext.assert.equal(testContext.hooks.tabTransfers.claimPending(detachedCtx, detachedWindowId), null);

  // Committed detach path: the hidden window shows only inside the
  // source-success commit branch, and a committed token can no longer be
  // claimed after renderer reload.
  const commitDetachToken = testContext.prepareThroughIpc(
    sourceWin,
    testContext.webTransferPayload(["reject-web"]),
  );
  const commitDetachedId = await testContext.hooks.tabTransfers.detach(sourceCtx, commitDetachToken);
  const commitDetachedCtx = testContext.hooks.windows.get(commitDetachedId);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.claimPending(commitDetachedCtx, commitDetachedId),
    commitDetachToken,
  );
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(commitDetachedCtx, commitDetachToken));
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalOpened(
      commitDetachedCtx,
      commitDetachToken,
      "destination",
    ),
    true,
  );
  testContext.assert.ok(
    testContext.hooks.tabTransfers.accept(
      commitDetachedCtx,
      commitDetachToken,
      { kind: "strip-end" },
    ),
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.destinationReady(commitDetachedCtx, commitDetachToken, true),
    true,
  );
  testContext.assert.equal(commitDetachedCtx.win.shown, false);
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(sourceCtx, commitDetachToken, "source"), true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      sourceCtx,
      commitDetachToken,
      { ok: true, sourceEmpty: false },
    ),
    true,
  );
  testContext.assert.equal(commitDetachedCtx.win.shown, true);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.claimPending(commitDetachedCtx, commitDetachedId),
    null,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(sourceCtx, commitDetachToken, "source"),
    true,
  );
  testContext.assert.equal(
    testContext.hooks.tabTransfers.journalFinalized(
      commitDetachedCtx,
      commitDetachToken,
      "destination",
    ),
    true,
  );
}

async function checkOnDemandTearOffBootAndFollow() {
  const sourceWin = testContext.fakeWindow(140);
  const sourceCtx = testContext.registerContext("detach-source", sourceWin);

  // --- EXACTLY ONE window per tear-off: +1 on detach, 0 after cancel ---
  // Regression guard for the old double-window bug (mid-drag beginDetach
  // created one window and the release path created another). Drop-to-place
  // creates the window once, at release.
  const liveCount = () =>
    [...testContext.hooks.windows.values()].filter((c) => !c.win.destroyed).length;
  const oneToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["one-web"]));
  testContext.fakeElectron.cursorPoint = { x: 700, y: 40 };
  const countBefore = liveCount();
  const oneId = await testContext.hooks.tabTransfers.detach(sourceCtx, oneToken);
  testContext.assert.equal(
    liveCount(),
    countBefore + 1,
    "a single tear-off must create exactly one window",
  );
  // A repeat detach for the same token is idempotent — still one window.
  testContext.assert.equal(await testContext.hooks.tabTransfers.detach(sourceCtx, oneToken), oneId,
    "a repeat detach must return the same window, never create a second");
  testContext.assert.equal(liveCount(), countBefore + 1, "an idempotent detach must not add a window");
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, oneToken), true);
  testContext.assert.equal(liveCount(), countBefore, "cancel must leave zero new windows (no orphan)");

  // --- created hidden at the drop point, clamped on-screen -------------
  const clampToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["clamp-web"]));
  testContext.fakeElectron.cursorPoint = { x: 10, y: 10 };
  const clampId = await testContext.hooks.tabTransfers.detach(sourceCtx, clampToken);
  const clampCtx = testContext.hooks.windows.get(clampId);
  testContext.assert.equal(clampCtx.win.shown, false, "the tear-off window must boot hidden (revealed at commit)");
  // A window whose width/2 would put it at x=-390 is clamped onto the work
  // area, so the drop point is never offscreen.
  testContext.assert.equal(clampCtx.win.bounds.x, 0, "the drop-point placement must be clamped on-screen");
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, clampToken), true);
  testContext.assert.equal(
    clampCtx.win.destroyed,
    true,
    "cancelling the token must close its window, leaving no orphan",
  );

  // --- concurrent detaches share one boot: never orphan a second window
  // (a release races the window-at-cursor hit test, both may call detach()).
  const commitToken = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["commit-web"]));
  testContext.fakeElectron.cursorPoint = { x: 700, y: 40 };
  const before = new Set(testContext.hooks.windows.keys());
  const bootA = testContext.hooks.tabTransfers.detach(sourceCtx, commitToken);
  const bootB = testContext.hooks.tabTransfers.detach(sourceCtx, commitToken);
  const [idA, idB] = await Promise.all([bootA, bootB]);
  testContext.assert.equal(idA, idB, "concurrent detaches must resolve to one window");
  const created = [...testContext.hooks.windows.keys()].filter((id) => !before.has(id));
  testContext.assert.deepEqual(
    created,
    [idA],
    "a release must boot exactly one window, never orphan a second",
  );
  const commitCtx = testContext.hooks.windows.get(idA);
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, commitToken), true);
  testContext.assert.equal(
    commitCtx.win.destroyed,
    true,
    "cancelling the token must destroy its window, leaving no orphan",
  );

  testContext.fakeElectron.cursorPoint = { x: 700, y: 40 };
}

async function checkInspectedDestinationCloseClearsPreparedToken() {
  const sourceWin = testContext.fakeWindow(103);
  const destinationWin = testContext.fakeWindow(104);
  const sourceCtx = testContext.registerContext("inspect-close-source", sourceWin);
  const destinationCtx = testContext.registerContext("inspect-close-destination", destinationWin);
  const controlled = testContext.controlledRecord("inspect-close-web");
  testContext.attachControlledRecord(
    sourceCtx,
    controlled,
    { x: 2, y: 3, width: 300, height: 200 },
  );
  const token = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["inspect-close-web"]));
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, token));
  destinationWin.destroyed = true;
  testContext.hooks.cleanupWindowContext(destinationCtx);

  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(token), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, token), null);
  testContext.assert.strictEqual(sourceCtx.views.get("inspect-close-web"), controlled.record);
  testContext.assert.equal(controlled.record.ownerId, sourceCtx.id);
  testContext.assert.equal(controlled.closeCallCount(), 0);
}
return { checkAtomicReparentAndMetadataOnlyTransfer, checkRejectCancelExpiryDetachAndClaim, checkOnDemandTearOffBootAndFollow, checkInspectedDestinationCloseClearsPreparedToken };
};
