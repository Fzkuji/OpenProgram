// navigation checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkLoadView() {
  const win = testContext.fakeWindow(1);
  const ctx = testContext.registerContext("load-window", win);

  // A request for the committed URL must replace a different pending URL.
  const competing = testContext.controlledRecord("tab-competing", "https://example.com/one");
  testContext.addRecord(ctx, competing);
  const first = testContext.hooks.loadView(competing.record, "https://example.com/two");
  const second = testContext.hooks.loadView(competing.record, "https://example.com/one");
  testContext.assert.notStrictEqual(first, second);
  testContext.assert.deepEqual(competing.calls, [
    "https://example.com/two",
    "https://example.com/one",
  ]);
  testContext.assert.strictEqual(competing.record.navigation.promise, second);

  let secondSettled = false;
  void second.then(() => { secondSettled = true; });
  competing.controls[0].resolve();
  testContext.assert.strictEqual(await first, competing.record);
  await Promise.resolve();
  testContext.assert.equal(secondSettled, false);
  testContext.assert.strictEqual(competing.record.navigation.promise, second);
  competing.controls[1].resolve();
  testContext.assert.strictEqual(await second, competing.record);
  testContext.assert.equal(competing.record.navigation, null);

  // Repeating the same pending URL shares one native load.
  const duplicate = testContext.controlledRecord("tab-duplicate", "https://example.com/one");
  testContext.addRecord(ctx, duplicate);
  const original = testContext.hooks.loadView(duplicate.record, "https://example.com/two");
  const repeated = testContext.hooks.loadView(duplicate.record, "https://example.com/two");
  testContext.assert.strictEqual(repeated, original);
  testContext.assert.deepEqual(duplicate.calls, ["https://example.com/two"]);
  duplicate.controls[0].resolve();
  testContext.assert.strictEqual(await repeated, duplicate.record);

  // Native reload/history invalidates a pending load before the next activation.
  const interrupted = testContext.controlledRecord(
    "tab-interrupted",
    "https://example.com/one",
    true,
  );
  testContext.addRecord(ctx, interrupted);
  const replaced = testContext.hooks.loadView(interrupted.record, "https://example.com/one");
  testContext.hooks.runNativeNavigation(ctx, "tab-interrupted", (wc) => wc.reload());
  testContext.assert.equal(interrupted.record.navigation, null);
  const replacement = testContext.hooks.loadView(
    interrupted.record,
    "https://example.com/one",
  );
  testContext.assert.notStrictEqual(replaced, replacement);
  testContext.assert.deepEqual(interrupted.calls, [
    "https://example.com/one",
    "https://example.com/one",
  ]);
  interrupted.controls[0].resolve();
  testContext.assert.strictEqual(await replaced, interrupted.record);
  testContext.assert.strictEqual(interrupted.record.navigation.promise, replacement);
  interrupted.controls[1].resolve();
  testContext.assert.strictEqual(await replacement, interrupted.record);
  testContext.hooks.runNativeNavigation(ctx, "tab-interrupted", (wc) => wc.stop());
  testContext.assert.equal(interrupted.nativeCalls.stop, 1);

  // A stable committed URL uses the zero-navigation path.
  const stable = testContext.controlledRecord("tab-stable", "https://example.com/one");
  testContext.addRecord(ctx, stable);
  testContext.assert.strictEqual(
    await testContext.hooks.loadView(stable.record, "https://example.com/one"),
    stable.record,
  );
  testContext.assert.deepEqual(stable.calls, []);
}

async function checkVisibleCollectionAndActivation() {
  const win = testContext.fakeWindow(2);
  const ctx = testContext.registerContext("visible-window", win);
  const a = testContext.controlledRecord("a");
  const b = testContext.controlledRecord("b");
  testContext.addRecord(ctx, a);
  testContext.addRecord(ctx, b);
  const boundsA = { x: 10, y: 20, width: 300, height: 400 };
  const boundsB = { x: 310, y: 20, width: 320, height: 400 };

  testContext.assert.equal(
    testContext.hooks.syncVisibleViews(ctx, [
      { id: "a", bounds: boundsA },
      { id: "b", bounds: boundsB },
    ]),
    true,
  );
  testContext.assert.deepEqual([...ctx.visibleViewIds].sort(), ["a", "b"]);
  testContext.assert.equal(a.visibility.at(-1), true);
  testContext.assert.equal(b.visibility.at(-1), true);
  testContext.assert.deepEqual(a.currentBounds(), boundsA);
  testContext.assert.deepEqual(b.currentBounds(), boundsB);
  testContext.assert.ok(a.currentBounds().width > 0 && b.currentBounds().width > 0);

  testContext.assert.equal(testContext.hooks.syncVisibleViews(ctx, [{ id: "b", bounds: boundsB }]), true);
  testContext.assert.deepEqual([...ctx.visibleViewIds], ["b"]);
  testContext.assert.equal(a.visibility.at(-1), false);
  testContext.assert.equal(b.visibility.at(-1), true);

  // Compatibility wrappers operate on a copied collection, not a singleton.
  testContext.assert.equal(testContext.hooks.showView(ctx, "a"), true);
  testContext.assert.deepEqual([...ctx.visibleViewIds].sort(), ["a", "b"]);
  testContext.assert.equal(testContext.hooks.hideView(ctx, "a"), true);
  testContext.assert.deepEqual([...ctx.visibleViewIds], ["b"]);
  testContext.assert.equal(b.visibility.at(-1), true);

  // An agent-bound activation validates the current layout without revealing
  // a tab that became hidden after the renderer issued its command.
  const hiddenVisibilityCalls = a.visibility.length;
  const hiddenTargetCalls = a.targetCallCount();
  testContext.assert.equal(await testContext.hooks.activateView(ctx, "a", "", true), null);
  testContext.assert.deepEqual([...ctx.visibleViewIds], ["b"]);
  testContext.assert.equal(a.visibility.length, hiddenVisibilityCalls);
  testContext.assert.equal(a.targetCallCount(), hiddenTargetCalls);

  // Resolving a background Page for web_use must not reveal it or alter
  // the renderer's visible collection.
  const backgroundVisibilityCalls = a.visibility.length;
  const backgroundTargetCalls = a.targetCallCount();
  testContext.assert.equal(await testContext.hooks.resolveView(ctx, "a"), "a-target");
  testContext.assert.deepEqual([...ctx.visibleViewIds], ["b"]);
  testContext.assert.equal(a.visibility.length, backgroundVisibilityCalls);
  testContext.assert.equal(a.targetCallCount(), backgroundTargetCalls + 1);

  testContext.assert.equal(
    await testContext.hooks.previewView(ctx, "a"),
    null,
    "preview stays visible-only by default",
  );
  testContext.assert.equal(await testContext.hooks.previewView(ctx, "a", false), null);
  testContext.assert.equal(a.executeJavaScriptCalls.length, 0);
  testContext.assert.equal(a.visibility.length, backgroundVisibilityCalls);

  // Page inventory must read the native Page's current URL/title. The tabs
  // store may still contain the pre-navigation metadata for a background Page.
  const live = testContext.controlledRecord("live-title", "https://live.example/path");
  testContext.addRecord(ctx, live);
  testContext.assert.deepEqual(
    testContext.plain(await testContext.hooks.inspectView(ctx, "live-title")),
    {
      target_id: "live-title-target",
      url: "https://live.example/path",
      title: "live-title",
    },
  );
  testContext.assert.deepEqual([...ctx.visibleViewIds], ["b"]);

  // Ownership validation happens before any visibility mutation.
  const foreign = testContext.controlledRecord("foreign");
  foreign.record.ownerId = "another-window";
  ctx.views.set("foreign", foreign.record);
  const beforeA = a.visibility.length;
  const beforeB = b.visibility.length;
  testContext.assert.equal(
    testContext.hooks.syncVisibleViews(ctx, [
      { id: "a", bounds: boundsA },
      { id: "foreign", bounds: boundsB },
    ]),
    false,
  );
  testContext.assert.equal(a.visibility.length, beforeA);
  testContext.assert.equal(b.visibility.length, beforeB);
  testContext.assert.deepEqual([...ctx.visibleViewIds], ["b"]);

  // Hiding during navigation prevents a stale activation target from winning.
  const activation = testContext.hooks.activateView(ctx, "a", "https://example.com/a");
  testContext.hooks.hideView(ctx, "a");
  a.controls[0].resolve();
  testContext.assert.equal(await activation, null);
  testContext.assert.equal(a.targetCallCount(), backgroundTargetCalls + 1);

  const active = testContext.hooks.activateView(ctx, "a", "https://example.com/a2");
  a.controls[1].resolve();
  testContext.assert.equal(await active, "a-target");
  testContext.assert.equal(a.targetCallCount(), backgroundTargetCalls + 2);

  // A completed navigation cannot return a target after ownership moves.
  const moved = testContext.controlledRecord("moved");
  testContext.addRecord(ctx, moved);
  const destination = testContext.registerContext("visible-destination", testContext.fakeWindow(20));
  const movingActivation = testContext.hooks.activateView(
    ctx,
    "moved",
    "https://example.com/moved",
  );
  ctx.views.delete("moved");
  moved.record.ownerId = destination.id;
  destination.views.set("moved", moved.record);
  moved.controls[0].resolve();
  testContext.assert.equal(await movingActivation, null);
  testContext.assert.equal(moved.targetCallCount(), 0);
}

async function checkConfirmedDestroyHandler() {
  testContext.hooks.registerWebTabIpc();
  const ownerWin = testContext.fakeWindow(7001);
  const foreignWin = testContext.fakeWindow(7002);
  const owner = testContext.registerContext("confirmed-owner", ownerWin);
  const foreign = testContext.registerContext("confirmed-foreign", foreignWin);
  const record = testContext.controlledRecord("confirmed-page");
  record.record.ownerId = owner.id;
  owner.views.set(record.record.id, record.record);
  const event = { sender: ownerWin.webContents };
  record.setCloseFailure(true);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:destroy-confirmed")(event, record.record.id), false);
  testContext.assert.equal(owner.views.has(record.record.id), true, "failed close keeps the record");
  record.setCloseFailure(false);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:destroy-confirmed")(event, record.record.id), true);
  testContext.assert.equal(owner.views.has(record.record.id), false, "successful close removes the record");
  const foreignRecord = testContext.controlledRecord("foreign-confirmed-page");
  foreignRecord.record.ownerId = foreign.id;
  foreign.views.set(foreignRecord.record.id, foreignRecord.record);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:destroy-confirmed")(event, foreignRecord.record.id), false);
  testContext.assert.equal(foreign.views.has(foreignRecord.record.id), true, "other owner is rejected");
}

async function checkSenderOwnership() {
  testContext.hooks.registerWebTabIpc();
  const winA = testContext.fakeWindow(3);
  const winB = testContext.fakeWindow(4);
  const ctxA = testContext.registerContext("window-a", winA);
  const ctxB = testContext.registerContext("window-b", winB);
  const frame = {};
  winA.webContents.mainFrame = frame;
  winA.webContents.getURL = () => "http://127.0.0.1:18100/chat";
  const nativeEvent = { sender: winA.webContents, senderFrame: frame };
  testContext.assert.strictEqual(testContext.hooks.nativeMenuOwner(nativeEvent), ctxA);
  testContext.assert.equal(testContext.hooks.nativeMenuOwner({ ...nativeEvent, senderFrame: {} }), null);
  testContext.assert.equal(testContext.hooks.nativeMenuOwner({ sender: winA.webContents }), null);
  winA.webContents.getURL = () => "https://example.com/";
  testContext.assert.equal(testContext.hooks.nativeMenuOwner(nativeEvent), null);
  testContext.assert.throws(() => testContext.ipcHandlers.get("native-menu:popup")(nativeEvent, {}), /Unauthorized/);
  winA.webContents.getURL = () => "http://127.0.0.1:18100/chat";
  const a = testContext.controlledRecord("owned-a");
  const b = testContext.controlledRecord("owned-b");
  testContext.addRecord(ctxA, a);
  testContext.addRecord(ctxB, b);
  const initialBounds = { x: 1, y: 2, width: 200, height: 100 };
  testContext.hooks.syncVisibleViews(ctxA, [{ id: "owned-a", bounds: initialBounds }]);
  testContext.hooks.syncVisibleViews(ctxB, [{ id: "owned-b", bounds: initialBounds }]);

  // State events follow the record's current owner instead of a fixed window.
  testContext.hooks.sendState(a.record);
  testContext.assert.equal(winA.sent.at(-1)[0], "webtab:state");
  ctxA.views.delete("owned-a");
  a.record.ownerId = ctxB.id;
  ctxB.views.set("owned-a", a.record);
  testContext.hooks.sendState(a.record);
  testContext.assert.equal(winB.sent.at(-1)[0], "webtab:state");
  ctxB.views.delete("owned-a");
  a.record.ownerId = ctxA.id;
  ctxA.views.set("owned-a", a.record);

  const eventB = { sender: winB.webContents };
  // A stale cross-window reference must make every view mutation a no-op.
  ctxB.views.set("owned-a", a.record);
  testContext.ipcListeners.get("webtab:navigate")(
    eventB,
    "owned-a",
    "https://example.com/blocked",
  );
  testContext.ipcListeners.get("webtab:reload")(eventB, "owned-a");
  testContext.ipcListeners.get("webtab:stop")(eventB, "owned-a");
  testContext.ipcListeners.get("webtab:go-back")(eventB, "owned-a");
  testContext.ipcListeners.get("webtab:go-forward")(eventB, "owned-a");
  testContext.ipcListeners.get("webtab:find")(eventB, "owned-a", "blocked", {});
  testContext.ipcListeners.get("webtab:stop-find")(eventB, "owned-a", "clearSelection");
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:zoom")(eventB, "owned-a", "in"), null);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:print")(eventB, "owned-a"), false);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:capture")(eventB, "owned-a"), null);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")(eventB, "owned-a", {
      x: 1,
      y: 1,
      width: 200,
      height: 100,
      sequence: 1,
    }),
    false,
  );
  testContext.ipcListeners.get("webtab:set-pip-zoom")(eventB, "owned-a", 640);
  testContext.ipcListeners.get("webtab:set-bounds")(
    eventB,
    "owned-a",
    { x: 9, y: 9, width: 999, height: 999 },
  );
  testContext.ipcListeners.get("webtab:hide")(eventB, "owned-a");
  testContext.ipcListeners.get("webtab:show")(eventB, "owned-a");
  testContext.ipcListeners.get("webtab:sync-visible")(eventB, [
    { id: "owned-a", bounds: initialBounds },
  ]);
  testContext.ipcListeners.get("webtab:destroy")(eventB, "owned-a");
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:activate")(
      eventB,
      "owned-a",
      "https://example.com/blocked",
    ),
    null,
  );
  ctxB.views.delete("owned-a");
  testContext.assert.deepEqual(a.calls, []);
  testContext.assert.deepEqual(a.nativeCalls, {
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
  testContext.assert.deepEqual(a.currentBounds(), initialBounds);
  testContext.assert.equal(a.visibility.at(-1), true);
  testContext.assert.equal(a.closeCallCount(), 0);
  testContext.assert.equal(ctxA.views.has("owned-a"), true);
  testContext.assert.deepEqual([...ctxB.visibleViewIds], ["owned-b"]);

  const eventA = { sender: winA.webContents };
  testContext.ipcListeners.get("webtab:find")(eventA, "owned-a", "needle", {
    forward: false,
    findNext: true,
  });
  testContext.ipcListeners.get("webtab:find")(eventA, "owned-a", "needle", {
    forward: true,
    findNext: false,
  });
  testContext.ipcListeners.get("webtab:stop-find")(eventA, "owned-a", "clearSelection");
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:zoom")(eventA, "owned-a", "in"), 110);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:zoom")(eventA, "owned-a", "out"), 100);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:zoom")(eventA, "owned-a", "reset"), 100);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a"), true);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:capture")(eventA, "owned-a"),
    "data:image/png;base64,TEST",
  );
  testContext.assert.equal(a.nativeCalls.capturePage, 1);
  const staleCapture = testContext.controlledRecord("stale-capture");
  testContext.addRecord(ctxA, staleCapture);
  staleCapture.delayCapturePage();
  const pendingCapture = testContext.ipcHandlers.get("webtab:capture")(
    eventA, "stale-capture",
  );
  ctxA.views.delete("stale-capture");
  testContext.addRecord(ctxA, testContext.controlledRecord("stale-capture"));
  staleCapture.completeCapturePage();
  testContext.assert.equal(
    await pendingCapture,
    null,
    "capture completion must reject pixels from a replaced native Page",
  );
  ctxA.views.delete("stale-capture");
  testContext.assert.deepEqual(testContext.plain(a.nativeCalls.find), [
    ["needle", { forward: false, findNext: true }],
    ["needle", { forward: true, findNext: false }],
  ]);
  testContext.assert.deepEqual(a.nativeCalls.stopFind, ["clearSelection"]);
  testContext.assert.deepEqual(a.nativeCalls.zoom, [1.1, 1, 1]);
  testContext.ipcListeners.get("webtab:set-pip-zoom")(eventA, "owned-a", 960);
  testContext.assert.equal(a.nativeCalls.zoom.at(-1), 0.5);
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:zoom")(eventA, "owned-a", "in"), 110);
  testContext.assert.equal(a.nativeCalls.zoom.at(-1), 0.5);
  testContext.ipcListeners.get("webtab:set-pip-zoom")(eventA, "owned-a", 240);
  testContext.assert.equal(a.nativeCalls.zoom.at(-1), 0.25);
  testContext.ipcListeners.get("webtab:set-pip-zoom")(eventA, "owned-a", 2000);
  testContext.assert.equal(a.nativeCalls.zoom.at(-1), 1);
  testContext.ipcListeners.get("webtab:set-pip-zoom")(eventA, "owned-a", null);
  testContext.assert.equal(a.nativeCalls.zoom.at(-1), 1.1);

  const freshZoomRecord = testContext.hooks.ensureView(
    ctxA,
    "fresh-zoom",
    "https://fresh-zoom.example/",
  );
  const freshZoom = testContext.generatedNativeRecords.at(-1);
  freshZoom.record.view.webContents.setZoomFactor(0.25);
  testContext.hooks.setPipZoom(ctxA, "fresh-zoom", null);
  testContext.assert.equal(freshZoom.nativeCalls.zoom.at(-1), 1);
  freshZoom.record.view.webContents.setZoomFactor(0.25);
  freshZoom.controls[0].resolve();
  freshZoom.emitWebContents("did-navigate");
  testContext.assert.equal(
    freshZoom.nativeCalls.zoom.at(-1),
    1,
    "the target host commit must reapply a pending ordinary-pane zoom reset",
  );
  await freshZoomRecord.navigation?.promise;
  testContext.hooks.destroyView(ctxA, "fresh-zoom");
  testContext.assert.deepEqual(testContext.plain(a.nativeCalls.print), [{ silent: false, printBackground: true }]);
  testContext.assert.deepEqual(a.nativeCalls.printToPDF, []);
  testContext.assert.deepEqual(testContext.shownPrintSaveDialogs, []);

  a.setPrintResult(false, "Print job canceled");
  const dialogsBeforeSystemCancel = testContext.shownPrintSaveDialogs.length;
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a"), false);
  testContext.assert.equal(testContext.shownPrintSaveDialogs.length, dialogsBeforeSystemCancel);
  testContext.assert.deepEqual(a.nativeCalls.printToPDF, []);

  a.setPrintResult(false);
  const savedPdfPath = testContext.path.join(testContext.transferUserData, "owned-a.pdf");
  testContext.nextPrintSaveDialogResult = { canceled: false, filePath: savedPdfPath };
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a"), true);
  testContext.assert.equal(testContext.fs.readFileSync(savedPdfPath, "utf8"), "%PDF-openprogram-test");
  testContext.assert.equal(testContext.shownPrintSaveDialogs.at(-1).win, winA);
  testContext.assert.equal(testContext.shownPrintSaveDialogs.at(-1).options.defaultPath, savedPdfPath);
  testContext.assert.deepEqual(testContext.plain(a.nativeCalls.printToPDF.at(-1)), {
    printBackground: true,
    preferCSSPageSize: true,
  });

  const cancelledPdfPath = testContext.path.join(testContext.transferUserData, "cancelled.pdf");
  testContext.nextPrintSaveDialogResult = { canceled: true, filePath: cancelledPdfPath };
  const pdfCallsBeforeCancel = a.nativeCalls.printToPDF.length;
  testContext.assert.equal(await testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a"), false);
  testContext.assert.equal(testContext.fs.existsSync(cancelledPdfPath), false);
  testContext.assert.equal(a.nativeCalls.printToPDF.length, pdfCallsBeforeCancel);

  const ownershipLostPdfPath = testContext.path.join(testContext.transferUserData, "ownership-lost.pdf");
  testContext.nextPrintSaveDialogResult = { canceled: false, filePath: ownershipLostPdfPath };
  a.delayPrintPdf();
  const pdfCallsBeforeOwnershipLoss = a.nativeCalls.printToPDF.length;
  const ownershipLostPrint = testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a");
  await new Promise((resolve) => testContext.setImmediate(resolve));
  testContext.assert.equal(a.nativeCalls.printToPDF.length, pdfCallsBeforeOwnershipLoss + 1);
  a.record.ownerId = ctxB.id;
  a.completePrintPdf();
  testContext.assert.equal(await ownershipLostPrint, false);
  testContext.assert.equal(testContext.fs.existsSync(ownershipLostPdfPath), false);
  a.record.ownerId = ctxA.id;

  const preservedPdfPath = testContext.path.join(testContext.transferUserData, "preserved.pdf");
  testContext.fs.writeFileSync(preservedPdfPath, "existing-user-file");
  testContext.nextPrintSaveDialogResult = { canceled: false, filePath: preservedPdfPath };
  const originalWriteFile = testContext.fs.promises.writeFile;
  testContext.fs.promises.writeFile = async (filePath, data, options) => {
    await originalWriteFile(filePath, data.subarray(0, 5), options);
    throw new Error("injected partial PDF write failure");
  };
  try {
    testContext.assert.equal(await testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a"), false);
  } finally {
    testContext.fs.promises.writeFile = originalWriteFile;
  }
  testContext.assert.equal(testContext.fs.readFileSync(preservedPdfPath, "utf8"), "existing-user-file");
  testContext.assert.deepEqual(
    testContext.fs.readdirSync(testContext.transferUserData).filter((name) => name.startsWith(".preserved.pdf.")),
    [],
  );

  a.setPrintResult(true);
  a.delayPrintCallback();
  const destroyedPrint = testContext.ipcHandlers.get("webtab:print")(eventA, "owned-a");
  a.emitWebContents("destroyed");
  testContext.assert.equal(await destroyedPrint, false);
  a.completePrint(true);

  testContext.ipcListeners.get("webtab:destroy")({ sender: winA.webContents }, "owned-a");
  testContext.assert.equal(a.closeCallCount(), 1);
  testContext.assert.equal(ctxA.views.has("owned-a"), false);
  testContext.assert.equal(ctxA.visibleViewIds.has("owned-a"), false);

  testContext.ipcListeners.get("webtab:ensure")(eventB, "fresh-b", "");
  testContext.assert.equal(ctxB.views.get("fresh-b").ownerId, ctxB.id);
  testContext.assert.equal(ctxA.views.has("fresh-b"), false);
  const fresh = ctxB.views.get("fresh-b").view.webContents;
  testContext.ipcListeners.get("webtab:find")(eventB, "fresh-b", "old", { findNext: true });
  testContext.ipcListeners.get("webtab:find")(eventB, "fresh-b", "new", { findNext: true });
  testContext.assert.equal(ctxB.views.get("fresh-b").findRequestId, 2);
  const sentBeforeStaleFind = winB.sent.length;
  fresh.emit("found-in-page", {}, {
    requestId: 1,
    activeMatchOrdinal: 1,
    matches: 1,
    finalUpdate: true,
  });
  testContext.assert.equal(winB.sent.length, sentBeforeStaleFind, "stale find results must be ignored");
  fresh.emit("found-in-page", {}, {
    requestId: 2,
    activeMatchOrdinal: 2,
    matches: 5,
    finalUpdate: true,
  });
  testContext.assert.deepEqual(testContext.plain(winB.sent.at(-1)), [
    "webtab:find-result",
    { id: "fresh-b", activeMatchOrdinal: 2, matches: 5, finalUpdate: true },
  ]);
  let shortcutPrevented = false;
  fresh.emit("before-input-event", {
    preventDefault() { shortcutPrevented = true; },
  }, { type: "keyDown", meta: true, key: "f" });
  testContext.assert.equal(shortcutPrevented, true);
  testContext.assert.deepEqual(testContext.plain(winB.sent.at(-1)), [
    "webtab:command",
    { id: "fresh-b", command: "find" },
  ]);
}
return { checkLoadView, checkVisibleCollectionAndActivation, checkConfirmedDestroyHandler, checkSenderOwnership };
};
