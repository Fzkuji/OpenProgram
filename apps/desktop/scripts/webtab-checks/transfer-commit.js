// transfer commit checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkSuccessfulTransferAndDurableCommit() {
  const sourceWin = testContext.fakeWindow(80);
  const destinationWin = testContext.fakeWindow(81);
  const sourceCtx = testContext.registerContext("success-source", sourceWin);
  const destinationCtx = testContext.registerContext("success-destination", destinationWin);
  const first = testContext.controlledRecord("success-a");
  const second = testContext.controlledRecord("success-b");
  first.record.findRequestId = 7;
  testContext.attachControlledRecord(sourceCtx, first, { x: 1, y: 2, width: 300, height: 400 });
  testContext.attachControlledRecord(sourceCtx, second, { x: 301, y: 2, width: 320, height: 400 });
  testContext.assert.equal(testContext.hooks.setPipZoom(sourceCtx, "success-a", 480), true);
  testContext.assert.equal(first.nativeCalls.zoom.at(-1), 0.25);

  const successPayload = testContext.webTransferPayload(["success-a", "success-b"]);
  successPayload.fileDrafts = [{ key: "draft:success-a", value: "source draft" }];
  successPayload.chats = [{
    chatKey: "success-chat",
    composerDraft: "source composer",
    wasActive: true,
  }];
  const sourceRenderer = {
    centerTabs: new Map(successPayload.tabs.map((tab) => [tab.id, testContext.plain(tab)])),
    fileDrafts: new Map(successPayload.fileDrafts.map((draft) => [draft.key, draft.value])),
    session: new Map(successPayload.chats.map((chat) => [chat.chatKey, testContext.plain(chat)])),
    bridge: new Set(["success-a", "success-b"]),
    reversible: null,
  };
  const destinationRenderer = {
    centerTabs: new Map(),
    fileDrafts: new Map(),
    session: new Map(),
    bridge: new Set(),
    provisionalTokens: new Set(),
    committedTokens: new Set(),
  };
  const trace = [];
  const token = testContext.prepareThroughIpc(
    sourceWin,
    successPayload,
  );
  const expirationTimer = testContext.hooks.tabTransfers.activeTransfers.get(token).timer;
  trace.push("destination validation");
  testContext.assert.ok(testContext.hooks.tabTransfers.inspect(destinationCtx, token));
  testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(destinationCtx, token, "destination"), true);
  const accepted = testContext.hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" });
  testContext.assert.equal(accepted.status, "destination-staged");
  testContext.assert.strictEqual(destinationCtx.views.get("success-a"), first.record);
  testContext.assert.strictEqual(destinationCtx.views.get("success-b"), second.record);
  testContext.assert.equal(first.record.ownerId, destinationCtx.id);
  testContext.assert.equal(second.record.ownerId, destinationCtx.id);
  testContext.assert.equal(testContext.hooks.setPipZoom(sourceCtx, "success-a", null), false);
  testContext.assert.equal(testContext.hooks.setPipZoom(destinationCtx, "success-a", null), true);
  testContext.assert.equal(
    first.nativeCalls.zoom.at(-1),
    0.25,
    "transfer lock must defer rather than apply the ordinary-pane zoom reset",
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.inspect(destinationCtx, token), null);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }),
    null,
  );
  testContext.assert.equal(sourceCtx.views.has("success-a"), false);
  testContext.assert.equal(sourceCtx.views.has("success-b"), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("success-a"), true);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("success-b"), true);
  testContext.assert.equal(first.record.findRequestId, 7);
  testContext.assert.deepEqual(first.nativeCalls.stopFind, []);
  trace.push("native destination ownership");

  for (const tab of accepted.payload.tabs) {
    destinationRenderer.centerTabs.set(tab.id, testContext.plain(tab));
  }
  for (const draft of accepted.payload.fileDrafts) {
    destinationRenderer.fileDrafts.set(draft.key, draft.value);
  }
  for (const chat of accepted.payload.chats) {
    destinationRenderer.session.set(chat.chatKey, testContext.plain(chat));
  }
  for (const id of accepted.recordIds) destinationRenderer.bridge.add(id);
  destinationRenderer.provisionalTokens.add(token);
  testContext.assert.deepEqual([...destinationRenderer.centerTabs.keys()], ["success-a", "success-b"]);
  testContext.assert.deepEqual([...destinationRenderer.fileDrafts], [["draft:success-a", "source draft"]]);
  testContext.assert.deepEqual([...destinationRenderer.session.keys()], ["success-chat"]);
  testContext.assert.deepEqual([...destinationRenderer.bridge], ["success-a", "success-b"]);
  testContext.assert.deepEqual([...destinationRenderer.provisionalTokens], [token]);
  trace.push("destination provisional insertion");
  let sourceRemovedResult = null;
  destinationWin.onSend = (channel, item) => {
    if (channel !== "tab-transfer:committed" || item.token !== token) return;
    destinationRenderer.provisionalTokens.delete(token);
    destinationRenderer.committedTokens.add(token);
    testContext.assert.deepEqual([...destinationRenderer.centerTabs.keys()], ["success-a", "success-b"]);
    testContext.assert.deepEqual(
      [...destinationRenderer.fileDrafts],
      [["draft:success-a", "source draft"]],
    );
    testContext.assert.deepEqual([...destinationRenderer.session.keys()], ["success-chat"]);
    testContext.assert.deepEqual([...destinationRenderer.bridge], ["success-a", "success-b"]);
    testContext.assert.deepEqual([...destinationRenderer.provisionalTokens], []);
    testContext.assert.deepEqual([...destinationRenderer.committedTokens], [token]);
    trace.push("main committed");
    testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(token), false);
    testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("success-a"), false);
    testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("success-b"), false);
    testContext.assert.equal(testContext.clock.pendingIds().includes(expirationTimer), false);
    testContext.assert.equal(testContext.hooks.tabTransfers.status(destinationCtx, token).status, "committed");
    testContext.assert.ok(testContext.loadTransferDecision(token));
    const committedStoreBytes = testContext.fs.readFileSync(testContext.transferDecisionFile());
    testContext.assert.equal(testContext.hooks.tabTransfers.rollback(token, "manual-after-commit"), false);
    testContext.clock.runCleared(expirationTimer);
    testContext.assert.deepEqual(testContext.fs.readFileSync(testContext.transferDecisionFile()), committedStoreBytes);
    testContext.assert.equal(first.record.ownerId, destinationCtx.id);
    testContext.assert.equal(second.record.ownerId, destinationCtx.id);
    testContext.assert.equal(
      testContext.hooks.tabTransfers.journalFinalized(
        destinationCtx,
        token,
        "destination",
      ),
      true,
    );
    trace.push("destination durable finalize");
  };
  sourceWin.onSend = (channel, item) => {
    if (item.token !== token) return;
    if (channel === "tab-transfer:remove-source") {
      sourceRenderer.reversible = {
        centerTabs: [...sourceRenderer.centerTabs],
        fileDrafts: [...sourceRenderer.fileDrafts],
        session: [...sourceRenderer.session],
        bridge: [...sourceRenderer.bridge],
      };
      for (const tab of item.payload.tabs) sourceRenderer.centerTabs.delete(tab.id);
      for (const draft of item.payload.fileDrafts) {
        sourceRenderer.fileDrafts.delete(draft.key);
      }
      for (const chat of item.payload.chats) sourceRenderer.session.delete(chat.chatKey);
      for (const tab of item.payload.tabs) sourceRenderer.bridge.delete(tab.id);
      testContext.assert.deepEqual([...sourceRenderer.centerTabs], []);
      testContext.assert.deepEqual([...sourceRenderer.fileDrafts], []);
      testContext.assert.deepEqual([...sourceRenderer.session], []);
      testContext.assert.deepEqual([...sourceRenderer.bridge], []);
      trace.push("source reversible removal");
      testContext.assert.equal(testContext.hooks.tabTransfers.journalOpened(sourceCtx, token, "source"), true);
      sourceRemovedResult = testContext.hooks.tabTransfers.sourceRemoved(
        sourceCtx,
        token,
        { ok: true, sourceEmpty: false },
      );
      return;
    }
    if (channel !== "tab-transfer:committed") return;
    sourceRenderer.reversible = null;
    testContext.assert.deepEqual([...sourceRenderer.centerTabs], []);
    testContext.assert.deepEqual([...sourceRenderer.fileDrafts], []);
    testContext.assert.deepEqual([...sourceRenderer.session], []);
    testContext.assert.deepEqual([...sourceRenderer.bridge], []);
    testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, token).status, "committed");
    testContext.assert.deepEqual(testContext.loadTransferDecision(token).finalizedRoles, [
      { role: "destination", windowId: destinationCtx.id },
    ]);
    testContext.assert.equal(
      testContext.hooks.tabTransfers.journalFinalized(sourceCtx, token, "source"),
      true,
    );
    trace.push("source durable finalize");
  };
  testContext.assert.equal(testContext.hooks.tabTransfers.destinationReady(destinationCtx, token, true), true);
  trace.push("destination-ready");
  testContext.flushRendererQueue();
  testContext.assert.equal(sourceRemovedResult, true);
  testContext.assert.equal(testContext.hooks.tabTransfers.activeTransfers.has(token), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("success-a"), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.isLocked("success-b"), false);
  testContext.assert.equal(testContext.clock.pendingIds().includes(expirationTimer), false);
  testContext.assert.equal(first.record.ownerId, destinationCtx.id);
  testContext.assert.equal(second.record.ownerId, destinationCtx.id);
  testContext.assert.equal(first.record.findRequestId, null);
  testContext.assert.deepEqual(first.nativeCalls.stopFind, ["clearSelection"]);
  testContext.assert.equal(first.record.pipLayoutZoom, null);
  testContext.assert.equal(first.record.pendingTransferZoomRestore, false);
  testContext.assert.equal(first.nativeCalls.zoom.at(-1), 1);
  testContext.assert.equal(testContext.hooks.tabTransfers.inspect(destinationCtx, token), null);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.accept(destinationCtx, token, { kind: "strip-end" }),
    null,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.destinationReady(destinationCtx, token, true), false);
  testContext.assert.equal(
    testContext.hooks.tabTransfers.sourceRemoved(
      sourceCtx,
      token,
      { ok: true, sourceEmpty: false },
    ),
    false,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, token), false);
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, token), null);
  testContext.assert.equal(testContext.loadTransferDecision(token), null);
  testContext.assert.deepEqual(trace, [
    "destination validation",
    "native destination ownership",
    "destination provisional insertion",
    "destination-ready",
    "source reversible removal",
    "main committed",
    "destination durable finalize",
    "source durable finalize",
  ]);
  testContext.assert.equal(first.closeCallCount(), 0);
  testContext.assert.equal(second.closeCallCount(), 0);
}
return { checkSuccessfulTransferAndDurableCommit };
};
