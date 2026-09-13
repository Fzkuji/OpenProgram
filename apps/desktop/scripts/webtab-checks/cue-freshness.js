// cue freshness checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
function humanInputMessages(win) {
  return win.sent
    .filter(([channel]) => channel === "webtab:human-input")
    .map(([, payload]) => payload);
}

async function flushAsync() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

async function checkBackgroundPreview() {
  testContext.hooks.registerWebTabIpc();
  const winA = testContext.fakeWindow(811);
  const winB = testContext.fakeWindow(812);
  const ctxA = testContext.registerContext("preview-a", winA);
  const ctxB = testContext.registerContext("preview-b", winB);
  const record = testContext.hooks.ensureView(ctxA, "mirror-page", "https://example.com/mirror");
  const controlled = testContext.generatedNativeRecords.at(-1);
  controlled.controls[0].resolve();
  await record.navigation?.promise;
  testContext.hooks.syncVisibleViews(ctxA, [{
    id: "mirror-page",
    bounds: { x: 8, y: 9, width: 640, height: 480 },
  }]);
  testContext.hooks.hideView(ctxA, "mirror-page");
  const visibility = controlled.visibility.slice();
  const currentBounds = controlled.currentBounds();
  const zoom = controlled.nativeCalls.zoom.slice();

  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:preview")({ sender: winA.webContents }, "mirror-page"),
    null,
    "hidden preview is denied without allowBackground",
  );
  testContext.assert.equal(controlled.executeJavaScriptCalls.length, 0);

  const preview = testContext.plain(await testContext.ipcHandlers.get("webtab:preview")(
    { sender: winA.webContents },
    "mirror-page",
    true,
  ));
  testContext.assert.equal(preview.tab_id, "mirror-page");
  testContext.assert.equal(preview.target_id, `${controlled.record.id}-target`);
  testContext.assert.equal(preview.preview.visible_text_excerpt, "excerpt");
  testContext.assert.equal(controlled.executeJavaScriptCalls.at(-1)[1], true);
  testContext.assert.deepEqual(controlled.currentBounds(), currentBounds);
  testContext.assert.deepEqual(controlled.visibility, visibility);
  testContext.assert.deepEqual(controlled.nativeCalls.zoom, zoom);
  testContext.assert.deepEqual(controlled.focusCalls, []);
  testContext.assert.equal(ctxA.visibleViewIds.has("mirror-page"), false);

  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:preview")({ sender: winB.webContents }, "mirror-page", true),
    null,
    "foreign window cannot preview an unowned view",
  );

  controlled.delayExecuteJavaScript();
  const pendingDestroyed = testContext.ipcHandlers.get("webtab:preview")(
    { sender: winA.webContents },
    "mirror-page",
    true,
  );
  testContext.hooks.destroyView(ctxA, "mirror-page");
  controlled.completeExecuteJavaScript();
  testContext.assert.equal(await pendingDestroyed, null, "destroyed view rejects in-flight background preview");

  const live = testContext.hooks.ensureView(ctxA, "transfer-preview", "https://example.com/xfer");
  const transferred = testContext.generatedNativeRecords.at(-1);
  transferred.controls[0].resolve();
  transferred.delayExecuteJavaScript();
  const dest = testContext.registerContext("preview-dest", testContext.fakeWindow(813));
  const pendingTransfer = testContext.ipcHandlers.get("webtab:preview")(
    { sender: winA.webContents },
    "transfer-preview",
    true,
  );
  testContext.hooks.reparentRecords(ctxA, dest, [live]);
  transferred.completeExecuteJavaScript();
  testContext.assert.equal(await pendingTransfer, null, "transferred view rejects in-flight background preview");
  testContext.assert.deepEqual(transferred.focusCalls, []);

  testContext.hooks.windows.delete(ctxA.id);
  testContext.hooks.windows.delete(ctxB.id);
  testContext.hooks.windows.delete(dest.id);
}

async function checkActionCueFreshness() {
  testContext.hooks.registerWebTabIpc();
  const win = testContext.fakeWindow(821);
  const ctx = testContext.registerContext("cue-fresh", win);
  testContext.hooks.ensureView(ctx, "cue-page", "https://example.com/cue");
  const controlled = testContext.generatedNativeRecords.at(-1);
  controlled.controls[0].resolve();
  testContext.hooks.syncVisibleViews(ctx, [{
    id: "cue-page",
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  }]);
  const marker = { x: 10, y: 10, width: 800, height: 600, sequence: 4 };
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", marker),
    true,
  );
  const liveCue = ctx.views.get("cue-page");
  const highlights = () => (liveCue.actionCueWindow ? 1 : 0);
  const firstHighlights = highlights();
  testContext.clock.advance(2000);
  await flushAsync();
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", marker),
    false,
    "equal sequence must not recreate a timed-out cue",
  );
  testContext.assert.equal(highlights(), firstHighlights);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", {
      ...marker,
      sequence: 5,
    }),
    true,
  );
  testContext.clock.advance(2000);
  await flushAsync();
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", {
      ...marker,
      sequence: 1,
      generation: 2,
    }),
    true,
    "a newer resource generation may restart sequence on the same Page",
  );
  testContext.clock.advance(2000);
  await flushAsync();
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", {
      ...marker,
      sequence: 9,
      generation: 1,
    }),
    false,
    "stale generation is rejected even with a higher sequence",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", {
      ...marker,
      sequence: 1,
      generation: 2,
    }),
    false,
    "duplicate generation+sequence after timeout must not recreate the cue",
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("webtab:show-action")({ sender: win.webContents }, "cue-page", {
      ...marker,
      sequence: 2,
      generation: 2,
    }),
    true,
  );
  testContext.hooks.destroyView(ctx, "cue-page");
  testContext.hooks.windows.delete(ctx.id);
}

async function checkActionCueWorkerIncarnation() {
  testContext.hooks.registerWebTabIpc();
  const win = testContext.fakeWindow(833);
  const ctx = testContext.registerContext("cue-incarnation", win);
  testContext.hooks.ensureView(ctx, "retained-page", "https://example.com/retained");
  const controlled = testContext.generatedNativeRecords.at(-1);
  controlled.controls[0].resolve();
  testContext.hooks.syncVisibleViews(ctx, [{
    id: "retained-page",
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  }]);
  const event = { sender: win.webContents };
  const show = (extra) => testContext.ipcHandlers.get("webtab:show-action")(event, "retained-page", {
    x: 10,
    y: 10,
    width: 800,
    height: 600,
    ...extra,
  });
  const clear = () => testContext.ipcHandlers.get("webtab:show-action")(event, "retained-page", null);

  testContext.assert.equal(await show({ resourceId: "page:old:1", generation: 5, sequence: 12 }), true);
  testContext.assert.equal(await clear(), true, "disconnect null clears the visual cue");
  testContext.assert.equal(
    await show({ resourceId: "page:old:1", generation: 1, sequence: 2 }),
    false,
    "same incarnation cannot restart generation after null",
  );
  testContext.assert.equal(
    await show({ resourceId: "page:new:1", generation: 1, sequence: 2 }),
    true,
    "a new backend resourceId on the retained Page may restart generation",
  );
  testContext.assert.equal(
    await show({ resourceId: "page:old:1", generation: 5, sequence: 13 }),
    false,
    "obsolete incarnation must not erase the newer cue",
  );
  testContext.assert.equal(
    await show({ resourceId: "page:new:1", generation: 1, sequence: 2 }),
    false,
    "duplicate sequence in the new incarnation is stale",
  );
  testContext.assert.equal(await show({ resourceId: "page:new:1", generation: 1, sequence: 3 }), true);
  testContext.assert.equal(await clear(), true);
  testContext.assert.equal(
    await show({ resourceId: "page:new:1", generation: 1, sequence: 3 }),
    false,
    "null must not reset freshness for stale replay after human yield",
  );

  const first = show({ resourceId: "page:newer:1", generation: 1, sequence: 1 });
  const second = show({ resourceId: "page:newer:1", generation: 1, sequence: 2 });
  const overlapping = [await first, await second];
  testContext.assert.equal(overlapping[1], true, "overlapping higher sequence in the new incarnation must complete");
  const pendingNull = clear();
  testContext.assert.equal(await pendingNull, true);
  await flushAsync();
  testContext.assert.equal(controlled.isDebuggerAttached(), false);

  testContext.hooks.destroyView(ctx, "retained-page");
  testContext.hooks.windows.delete(ctx.id);
}

async function checkOverlappingActionCues() {
  testContext.hooks.registerWebTabIpc();
  const win = testContext.fakeWindow(832);
  const ctx = testContext.registerContext("cue-overlap", win);
  testContext.hooks.ensureView(ctx, "overlap-page", "https://example.com/overlap");
  const controlled = testContext.generatedNativeRecords.at(-1);
  controlled.controls[0].resolve();
  testContext.hooks.syncVisibleViews(ctx, [{
    id: "overlap-page",
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  }]);
  const event = { sender: win.webContents };
  const marker = { x: 10, y: 10, width: 800, height: 600 };
  const show = (sequence) => testContext.ipcHandlers.get("webtab:show-action")(
    event,
    "overlap-page",
    { ...marker, sequence },
  );

  const first = show(1);
  const second = show(2);
  const overlapping = [await first, await second];
  testContext.assert.equal(
    overlapping[1],
    true,
    "a later in-flight higher sequence must complete",
  );
  testContext.assert.equal(controlled.isDebuggerAttached(), false);
  testContext.assert.ok(ctx.views.get("overlap-page").actionCueWindow, "newer overlapping cue must paint a host overlay window");

  const replacement = show(3);
  const cleared = testContext.ipcHandlers.get("webtab:show-action")(event, "overlap-page", null);
  testContext.assert.equal(await cleared, true, "explicit null must clear the host overlay");
  await replacement;
  await flushAsync();
  testContext.assert.equal(
    controlled.isDebuggerAttached(),
    false,
    "later null must not attach a page debugger",
  );

  testContext.hooks.destroyView(ctx, "overlap-page");
  testContext.hooks.windows.delete(ctx.id);
}
return { humanInputMessages, flushAsync, checkBackgroundPreview, checkActionCueFreshness, checkActionCueWorkerIncarnation, checkOverlappingActionCues };
};
