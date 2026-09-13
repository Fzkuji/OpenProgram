// control overlay checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkNativeControlOverlayAndCueReplay() {
  testContext.hooks.registerWebTabIpc();
  const win = testContext.fakeWindow(901);
  const ctx = testContext.registerContext("overlay-sec", win);
  testContext.hooks.ensureView(ctx, "live-page", "https://example.com/live");
  const pageHarness = testContext.generatedNativeRecords.at(-1);
  pageHarness.controls[0].resolve();
  const live = ctx.views.get("live-page");
  testContext.hooks.syncVisibleViews(ctx, [{
    id: "live-page",
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  }]);
  const event = { sender: win.webContents };
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")(event, "live-page", {
      x: 40, y: 80, width: 800, height: 600, sequence: 1, generation: 1, resourceId: "page-a",
    }),
    true,
  );
  const firstSeq = live.pendingCuePayload.playSeq;
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")(event, "live-page", {
      x: 40, y: 80, width: 800, height: 600, sequence: 2, generation: 1, resourceId: "page-a",
    }),
    true,
  );
  testContext.assert.equal(live.pendingCuePayload.playSeq, 2);
  testContext.assert.notEqual(live.pendingCuePayload.playSeq, firstSeq);

  testContext.ipcListeners.get("webtab:control-overlay")(event, "live-page", {
    resourceId: "page-a",
    generation: 1,
    conversationSessionId: "a",
    controlState: "active",
    showActions: true,
    connected: true,
    status: "Active",
    pauseLabel: "I will operate",
    showLabel: "Show actions",
    historyLabel: "Operation history",
    pauseDisabled: false,
    resumeDisabled: true,
    showTakeover: true,
    historyItems: [{ id: "op-1", label: "click · acknowledged", disabled: true }],
  });
  const overlayView = live.controlOverlayView;
  testContext.assert.ok(overlayView, "control overlay attaches to the registry record");
  const overlayHarness = testContext.generatedNativeRecords.find((item) => item.record.view === overlayView);
  overlayHarness.controls[0].resolve();
  await testContext.flushAsync();
  const overlaySender = overlayView.webContents;
  testContext.ipcListeners.get("webtab:control-overlay-ready")({ sender: overlaySender });
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: overlaySender },
    { type: "layout", collapsed: false, width: 280, height: 44 },
  );
  testContext.assert.equal(live.controlCollapsed, false);
  testContext.assert.ok(
    overlayView.getBounds().width >= 200,
    "expanded control overlay must grow beyond the collapsed 36px host view",
  );
  const expandedWidth = overlayView.getBounds().width;
  testContext.assert.ok(expandedWidth <= 360, "expanded overlay stays a compact row");
  const boundsBeforeRepeat = overlayHarness.boundsCalls.length;
  for (let i = 0; i < 8; i++) {
    testContext.ipcListeners.get("webtab:control-overlay-event")(
      { sender: overlaySender },
      { type: "layout", collapsed: false, width: 280, height: 44 },
    );
  }
  testContext.assert.equal(overlayView.getBounds().width, expandedWidth);
  testContext.assert.equal(
    overlayHarness.boundsCalls.length,
    boundsBeforeRepeat,
    "identical layout acks must not resize the host view",
  );
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: overlaySender },
    { type: "move", id: "page-a", generation: 1, dx: -20, dy: -10 },
  );
  testContext.assert.ok(live.controlLeft < 800);
  const forwarded = win.sent.filter((item) => item[0] === "webtab:control-overlay-event").length;
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: live.actionCueWindow.webContents },
    { type: "pause", id: "page-a", generation: 1 },
  );
  testContext.assert.equal(
    win.sent.filter((item) => item[0] === "webtab:control-overlay-event").length,
    forwarded,
    "cue window must not invoke privileged control actions",
  );
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: overlaySender },
    { type: "pause", id: "page-a", generation: 1 },
  );
  testContext.assert.ok(win.sent.some((item) => item[0] === "webtab:control-overlay-event" && item[1].type === "pause"));
  const beforeStale = win.sent.filter((item) => item[0] === "webtab:control-overlay-event").length;
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: overlaySender },
    { type: "resume", id: "page-a", generation: 99 },
  );
  const afterStale = win.sent.filter((item) => item[0] === "webtab:control-overlay-event");
  testContext.assert.equal(afterStale.length, beforeStale + 1);
  testContext.assert.equal(afterStale.at(-1)[1].type, "stale");
  testContext.assert.equal(afterStale.some((item) => item[1].type === "resume" && item[1].generation === 99), false);
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: overlaySender },
    { type: "reveal", id: "page-a", generation: 1 },
  );
  testContext.assert.ok(win.sent.some((item) => item[0] === "webtab:control-overlay-event" && item[1].type === "reveal"));
  testContext.ipcListeners.get("webtab:control-overlay-event")(
    { sender: overlaySender },
    { type: "history", id: "page-a", generation: 1 },
  );
  testContext.assert.ok(testContext.menuPopupOptions.length >= 1, "history is handled natively on the host window");
  testContext.hooks.hideView(ctx, "live-page");
  testContext.assert.ok(overlayHarness.visibility.includes(false), "hiding the page must hide the control overlay");
  testContext.hooks.destroyView(ctx, "live-page");
  testContext.assert.equal(live.actionCueWindow, null);
  testContext.assert.equal(live.controlOverlayView, null);
  testContext.hooks.windows.delete(ctx.id);
}
return { checkNativeControlOverlayAndCueReplay };
};
