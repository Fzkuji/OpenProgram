// human input checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkHumanInputYieldingAndActionCue() {
  testContext.hooks.registerWebTabIpc();
  const winA = testContext.fakeWindow(801);
  const winB = testContext.fakeWindow(802);
  const ctxA = testContext.registerContext("human-a", winA);
  const ctxB = testContext.registerContext("human-b", winB);
  const record = testContext.hooks.ensureView(ctxA, "live-page", "https://example.com/live");
  const controlled = testContext.generatedNativeRecords.at(-1);
  controlled.controls[0].resolve();
  await record.navigation?.promise;
  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "live-page",
    bounds: { x: 10, y: 20, width: 800, height: 600 },
  }]);

  let prevented = false;
  const mouseEvent = { preventDefault() { prevented = true; } };
  controlled.emitWebContents("before-mouse-event", mouseEvent, {
    type: "mouseDown",
    x: 40,
    y: 50,
    button: "left",
  });
  testContext.assert.equal(prevented, false, "yielding must not preventDefault native pointer input");
  testContext.assert.equal(testContext.humanInputMessages(winA).length, 0);
  testContext.assert.equal(testContext.humanInputMessages(winB).length, 0, "human input follows the exact owner window");

  prevented = false;
  controlled.emitWebContents("before-mouse-event", mouseEvent, {
    type: "mouseWheel",
    deltaY: 40,
  });
  testContext.assert.equal(prevented, false);
  const keyEvent = { preventDefault() { prevented = true; } };
  controlled.emitWebContents("before-input-event", keyEvent, {
    type: "keyDown",
    key: "a",
    code: "KeyA",
    shift: false,
    control: false,
    alt: false,
    meta: false,
  });
  testContext.assert.equal(prevented, false, "yielding must not steal page-operating keys");
  testContext.assert.deepEqual(
    testContext.plain(testContext.humanInputMessages(winA).slice(-2).map((item) => item.kind)),
    [],
  );
  testContext.assert.equal(testContext.humanInputMessages(winA).length, 0);

  const ignoredBefore = testContext.humanInputMessages(winA).length;
  for (const mouse of [
    { type: "mouseMove", x: 1, y: 1 },
    { type: "mouseEnter", x: 1, y: 1 },
    { type: "mouseLeave", x: 1, y: 1 },
    { type: "mouseUp", x: 1, y: 1 },
  ]) {
    controlled.emitWebContents("before-mouse-event", {
      preventDefault() { prevented = true; },
    }, mouse);
  }
  controlled.emitWebContents("before-input-event", { preventDefault() {} }, {
    type: "keyDown",
    key: "Tab",
    code: "Tab",
    shift: false,
    control: false,
    alt: false,
    meta: false,
  });
  controlled.emitWebContents("before-input-event", { preventDefault() {} }, {
    type: "keyDown",
    key: "Tab",
    code: "Tab",
    shift: true,
    control: false,
    alt: false,
    meta: false,
  });
  controlled.emitWebContents("before-input-event", { preventDefault() {} }, {
    type: "keyUp",
    key: "a",
    code: "KeyA",
  });
  controlled.emitWebContents("before-input-event", { preventDefault() {} }, {
    type: "keyDown",
    key: "Shift",
    code: "ShiftLeft",
    shift: true,
  });
  controlled.emitWebContents("before-input-event", { preventDefault() {} }, {
    type: "keyDown",
    key: "Meta",
    code: "MetaLeft",
    meta: true,
  });
  controlled.emitWebContents("input-event", {}, { type: "mouseDown", x: 9, y: 9 });
  testContext.assert.equal(
    testContext.humanInputMessages(winA).length,
    ignoredBefore,
    "hover/move/Tab/keyup/modifiers and CDP input-event must not yield",
  );

  const navigateBefore = testContext.humanInputMessages(winA).length;
  testContext.ipcListeners.get("webtab:reload")({ sender: winA.webContents }, "live-page");
  testContext.ipcListeners.get("webtab:go-back")({ sender: winA.webContents }, "live-page");
  testContext.ipcListeners.get("webtab:go-forward")({ sender: winA.webContents }, "live-page");
  testContext.ipcListeners.get("webtab:navigate")(
    { sender: winA.webContents },
    "live-page",
    "https://example.com/user",
  );
  testContext.assert.deepEqual(
    testContext.plain(testContext.humanInputMessages(winA).slice(navigateBefore).map((item) => item.kind)),
    [],
  );
  const afterUserNav = testContext.humanInputMessages(winA).length;
  testContext.ipcListeners.get("webtab:ensure")(
    { sender: winA.webContents },
    "live-page",
    "https://example.com/agent-ensure",
  );
  await testContext.ipcHandlers.get("webtab:resolve")({ sender: winA.webContents }, "live-page");
  await testContext.ipcHandlers.get("webtab:activate")({ sender: winA.webContents }, "live-page");
  testContext.assert.equal(
    testContext.humanInputMessages(winA).length,
    afterUserNav,
    "agent ensure/resolve/activate must not be human navigation",
  );

  controlled.setNavigationAvailability({ back: true, forward: false });
  controlled.emitWebContents("context-menu", {}, {
    selectionText: "",
    isEditable: false,
    editFlags: {},
  });
  testContext.menuTemplate.find((item) => item.label === "Back").click();
  testContext.assert.equal(testContext.humanInputMessages(winA).length, 0);

  const hiddenId = "hidden-page";
  testContext.hooks.ensureView(ctxA, hiddenId, "https://example.com/hidden");
  const hidden = testContext.generatedNativeRecords.at(-1);
  hidden.controls[0].resolve();
  testContext.assert.deepEqual(hidden.currentBounds(), { x: 0, y: 0, width: 1920, height: 1080 });
  const hiddenBounds = hidden.currentBounds();
  const hiddenVisibility = hidden.visibility.slice();
  const hiddenZoom = hidden.nativeCalls.zoom.slice();
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:capture")({ sender: winA.webContents }, hiddenId),
    "data:image/png;base64,TEST",
  );
  testContext.assert.equal(hidden.capturePageArgs.at(-1)[0], undefined);
  testContext.assert.equal(hidden.capturePageArgs.at(-1)[1].stayHidden, true);
  testContext.assert.deepEqual(hidden.currentBounds(), hiddenBounds);
  testContext.assert.deepEqual(hidden.visibility, hiddenVisibility);
  testContext.assert.deepEqual(hidden.nativeCalls.zoom, hiddenZoom);
  testContext.assert.deepEqual(hidden.focusCalls, []);
  testContext.assert.equal(ctxA.visibleViewIds.has(hiddenId), false);

  const visibleVisibility = controlled.visibility.slice();
  const visibleBounds = controlled.currentBounds();
  const visibleZoom = controlled.nativeCalls.zoom.slice();
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:capture")({ sender: winA.webContents }, "live-page"),
    "data:image/png;base64,TEST",
  );
  testContext.assert.equal(controlled.capturePageArgs.at(-1)[0], undefined);
  testContext.assert.equal(controlled.capturePageArgs.at(-1)[1].stayHidden, true);
  testContext.assert.deepEqual(controlled.currentBounds(), visibleBounds);
  testContext.assert.deepEqual(controlled.visibility, visibleVisibility);
  testContext.assert.deepEqual(controlled.nativeCalls.zoom, visibleZoom);
  testContext.assert.deepEqual(controlled.focusCalls, []);

  testContext.assert.equal(typeof testContext.ipcHandlers.get("webtab:show-action"), "function");
  const marker = { x: 40, y: 80, width: 800, height: 600, sequence: 11 };
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", marker),
    true,
  );
  testContext.assert.ok(record.actionCueWindow, "owned annotation must use a host overlay window");
  testContext.assert.deepEqual(
    testContext.plain(record.actionCueWindow.ignoreMouseCalls.at(-1)),
    [true, { forward: true }],
    "cue window must use BrowserWindow setIgnoreMouseEvents click-through",
  );
  testContext.assert.equal(record.actionCueWindow.focusable, false);
  testContext.assert.ok((record.actionCueWindow.showInactiveCalls || 0) >= 1, "cue must not steal focus");
  const cueBounds = record.actionCueWindow.getBounds();
  testContext.assert.ok(
    cueBounds.width <= 40 && cueBounds.height <= 40,
    "cue is a brief point mark, not the observed viewport rectangle",
  );
  record.view.webContents.setZoomFactor(2);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      x: 100,
      y: 100,
      width: 400,
      height: 300,
      sequence: 12,
      reducedMotion: true,
    }),
    true,
  );
  const zoomedPage = record.view.getBounds();
  const zoomedContent = winA.getContentBounds();
  const zoomedCue = record.actionCueWindow.getBounds();
  testContext.assert.equal(zoomedCue.width, 28);
  testContext.assert.equal(zoomedCue.height, 28);
  testContext.assert.equal(zoomedCue.x, zoomedContent.x + zoomedPage.x + 100 * 2 - 14);
  testContext.assert.equal(zoomedCue.y, zoomedContent.y + zoomedPage.y + 100 * 2 - 14);
  winA.setBounds({ x: 40, y: 50, width: 800, height: 600 });
  winA.listeners.get("move")();
  const movedCue = record.actionCueWindow.getBounds();
  const movedContent = winA.getContentBounds();
  testContext.assert.equal(movedCue.x, movedContent.x + zoomedPage.x + 100 * 2 - 14);
  testContext.assert.equal(movedCue.y, movedContent.y + zoomedPage.y + 100 * 2 - 14);
  record.view.webContents.setZoomFactor(1);
  winA.setBounds({ x: 0, y: 0, width: 800, height: 600 });
  testContext.assert.equal(controlled.isDebuggerAttached(), false);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winB.webContents }, "live-page", {
      ...marker,
      sequence: 12,
    }),
    false,
    "foreign window cannot annotate an unowned view",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      ...marker,
      sequence: 10,
    }),
    false,
    "stale sequence must be rejected",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      x: 900,
      y: 80,
      width: 800,
      height: 600,
      sequence: 13,
    }),
    false,
    "out-of-bounds cue must be rejected",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      x: 40,
      y: 80,
      width: 400,
      height: 300,
      sequence: 13,
    }),
    false,
    "viewport-size mismatch is stale geometry",
  );

  let trackedCue = null;
  const hideCount = () => {
    if (record.actionCueWindow) trackedCue = record.actionCueWindow;
    if (!trackedCue) return 0;
    return (trackedCue.hideCalls || 0) + (trackedCue.closeCalls || 0);
  };
  const hidesBeforeGeometry = hideCount();
  testContext.ipcListeners.get("webtab:set-bounds")(
    { sender: winA.webContents },
    "live-page",
    { x: 10, y: 20, width: 640, height: 480 },
  );
  await testContext.flushAsync();
  testContext.assert.ok(hideCount() > hidesBeforeGeometry, "geometry change must clear the action cue");

  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "live-page",
    bounds: { x: 10, y: 20, width: 800, height: 600 },
  }]);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      ...marker,
      sequence: 13,
    }),
    true,
  );
  const hidesBeforeHide = hideCount();
  testContext.hooks.hideView(ctxA, "live-page");
  await testContext.flushAsync();
  testContext.assert.ok(hideCount() > hidesBeforeHide, "hiding must clear the action cue");

  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "live-page",
    bounds: { x: 10, y: 20, width: 800, height: 600 },
  }]);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      ...marker,
      sequence: 14,
    }),
    true,
  );
  const hidesBeforeNav = hideCount();
  controlled.emitWebContents("did-navigate");
  await testContext.flushAsync();
  testContext.assert.ok(hideCount() > hidesBeforeNav, "navigation must clear the action cue");

  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      ...marker,
      sequence: 15,
    }),
    true,
  );
  const destWin = testContext.fakeWindow(803);
  const dest = testContext.registerContext("human-dest", destWin);
  const hidesBeforeTransfer = hideCount();
  testContext.hooks.reparentRecords(ctxA, dest, [record]);
  await testContext.flushAsync();
  testContext.assert.ok(hideCount() > hidesBeforeTransfer, "transfer must clear the action cue");
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      ...marker,
      sequence: 16,
    }),
    false,
    "source window cannot annotate a transferred view",
  );

  testContext.hooks.reparentRecords(dest, ctxA, [record]);
  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "live-page",
    bounds: { x: 10, y: 20, width: 800, height: 600 },
  }]);
  const closedBefore = testContext.humanInputMessages(winA).length;
  testContext.hooks.destroyView(ctxA, "live-page");
  prevented = false;
  controlled.emitWebContents("before-mouse-event", {
    preventDefault() { prevented = true; },
  }, { type: "mouseDown", x: 1, y: 1 });
  testContext.assert.equal(prevented, false);
  testContext.assert.equal(
    testContext.humanInputMessages(winA).length,
    closedBefore,
    "destroyed view must not notify human input",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "live-page", {
      ...marker,
      sequence: 17,
    }),
    false,
  );

  testContext.hooks.ensureView(ctxA, "no-overlay", "https://example.com/none");
  const unavailable = testContext.generatedNativeRecords.at(-1);
  unavailable.controls[0].resolve();
  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "no-overlay",
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  }]);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "no-overlay", {
      x: 10,
      y: 10,
      width: 800,
      height: 600,
      sequence: 1,
    }),
    true,
  );
  testContext.assert.equal(
    unavailable.isDebuggerAttached(),
    false,
    "click cue must not attach a page debugger session",
  );
  testContext.assert.equal(await testContext.hooks.resolveView(ctxA, "no-overlay"), `${unavailable.record.id}-target`);

  testContext.hooks.ensureView(ctxA, "timed-cue", "https://example.com/timed");
  const timed = testContext.generatedNativeRecords.at(-1);
  timed.controls[0].resolve();
  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "timed-cue",
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  }]);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: winA.webContents }, "timed-cue", {
      x: 10,
      y: 10,
      width: 800,
      height: 600,
      sequence: 1,
    }),
    true,
  );
  testContext.assert.equal(timed.isDebuggerAttached(), false);
  const targetBeforeTimeout = timed.targetCallCount();
  const resolveDuringCue = testContext.hooks.resolveView(ctxA, "timed-cue");
  testContext.assert.equal(await resolveDuringCue, `${timed.record.id}-target`);
  testContext.assert.equal(timed.isDebuggerAttached(), false, "cue overlay must not hold a page debugger session");
  testContext.assert.equal(timed.targetCallCount(), targetBeforeTimeout + 1);
  testContext.clock.advance(3000);
  await testContext.flushAsync();
  const timedLive = ctxA.views.get("timed-cue");
  testContext.assert.ok(timedLive.actionCueWindow);
  testContext.assert.ok((timedLive.actionCueWindow.hideCalls || 0) >= 1, "cue timeout must hide the host overlay");

  testContext.hooks.windows.delete(ctxA.id);
  testContext.hooks.windows.delete(ctxB.id);
  testContext.hooks.windows.delete(dest.id);
}
return { checkHumanInputYieldingAndActionCue };
};
