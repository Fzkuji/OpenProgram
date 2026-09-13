// popups checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkPopupCreatesIndependentRendererTab() {
  const win = testContext.fakeWindow(41);
  const ctx = testContext.registerContext("popup-window", win);
  const opener = testContext.hooks.ensureView(
    ctx,
    "popup-opener",
    "https://opener.example/current",
  );
  const controlled = testContext.generatedNativeRecords.at(-1);
  testContext.assert.ok(opener, "opener view must be created");
  testContext.assert.ok(controlled, "opener native view must be observable");
  controlled.controls[0].resolve();
  await opener.navigation?.promise;
  controlled.emitWebContents("did-navigate");
  testContext.assert.equal(win.sent.at(-1)[0], "webtab:state");
  testContext.assert.equal(win.sent.at(-1)[1].id, "popup-opener");
  testContext.assert.equal(
    win.sent.some(([channel]) => channel === "webtab:popup"),
    false,
    "ordinary page navigation must update the existing WebTab without creating a popup",
  );

  const result = opener.view.webContents.windowOpen?.({
    url: "https://popup.example/path",
  });
  testContext.assert.deepEqual(testContext.plain(result), { action: "deny" });
  testContext.assert.deepEqual(testContext.plain(win.sent.at(-1)), [
    "webtab:popup",
    { openerId: "popup-opener", url: "https://popup.example/path" },
  ]);

  opener.view.webContents.windowOpen?.({ url: "http://popup.example/plain-http" });
  testContext.assert.deepEqual(testContext.plain(win.sent.at(-1)), [
    "webtab:popup",
    { openerId: "popup-opener", url: "http://popup.example/plain-http" },
  ]);
  const sentBeforeRejectedPopup = win.sent.length;
  for (const url of [
    "file:///Users/test/private.txt",
    "javascript:alert(1)",
    "data:text/html,private",
    "blob:https://popup.example/private",
    "mailto:test@example.com",
    "ftp://popup.example/private",
    "about:blank",
    "not a url",
  ]) {
    opener.view.webContents.windowOpen?.({ url });
  }
  testContext.assert.equal(
    win.sent.length,
    sentBeforeRejectedPopup,
    "popup egress must reject non-http(s) URLs",
  );

  controlled.setNavigationAvailability({ back: true, forward: true });
  const frame = { name: "popup-frame" };
  controlled.emitWebContents("context-menu", {}, {
    frame,
    menuSourceType: "mouse",
    linkURL: "https://popup.example/from-context-menu",
    selectionText: "",
    isEditable: false,
    editFlags: {},
  });
  testContext.assert.strictEqual(testContext.menuPopupOptions.at(-1)?.window, win);
  testContext.assert.strictEqual(testContext.menuPopupOptions.at(-1)?.frame, frame);
  testContext.assert.equal(testContext.menuPopupOptions.at(-1)?.sourceType, "mouse");
  const openLink = testContext.menuTemplate.find((item) => item.label === "Open Link in New Tab");
  const copyLink = testContext.menuTemplate.find((item) => item.label === "Copy Link Address");
  const back = testContext.menuTemplate.find((item) => item.label === "Back");
  const forward = testContext.menuTemplate.find((item) => item.label === "Forward");
  const reload = testContext.menuTemplate.find((item) => item.label === "Reload");
  testContext.assert.ok(openLink && copyLink && back && forward && reload,
    "link context menu must expose link and page navigation actions");
  testContext.assert.equal(back.enabled, true);
  testContext.assert.equal(forward.enabled, true);
  openLink.click();
  testContext.assert.deepEqual(testContext.plain(win.sent.at(-1)), [
    "webtab:popup",
    { openerId: "popup-opener", url: "https://popup.example/from-context-menu" },
  ]);
  copyLink.click();
  testContext.assert.equal(testContext.clipboardWrites.at(-1), "https://popup.example/from-context-menu");
  back.click();
  forward.click();
  reload.click();
  testContext.assert.deepEqual(
    testContext.plain({
      back: controlled.nativeCalls.back,
      forward: controlled.nativeCalls.forward,
      reload: controlled.nativeCalls.reload,
    }),
    { back: 1, forward: 1, reload: 1 },
  );

  controlled.emitWebContents("context-menu", {}, {
    frame,
    menuSourceType: "keyboard",
    linkURL: "",
    selectionText: "selected",
    isEditable: true,
    editFlags: {
      canUndo: true,
      canRedo: true,
      canCut: true,
      canCopy: true,
      canPaste: true,
      canSelectAll: true,
    },
  });
  const editActions = [
    ["Undo", "undo"],
    ["Redo", "redo"],
    ["Cut", "cut"],
    ["Copy", "copy"],
    ["Paste", "paste"],
    ["Select All", "selectAll"],
  ];
  const humanBeforeEdit = testContext.humanInputMessages(win).length;
  for (const [label] of editActions) {
    const item = testContext.menuTemplate.find((candidate) => candidate.label === label);
    testContext.assert.ok(item,
      `editable context menu must include ${label}`);
    testContext.assert.equal(item.enabled, true, `${label} must follow its true edit flag`);
    item.click();
  }
  testContext.assert.deepEqual(testContext.plain(controlled.editCalls), {
    undo: 1,
    redo: 1,
    cut: 1,
    copy: 1,
    paste: 1,
    selectAll: 1,
  });
  testContext.assert.deepEqual(
    testContext.plain(testContext.humanInputMessages(win).slice(humanBeforeEdit).map((item) => item.kind)),
    [],
    "Undo/Redo/Cut/Paste emit sanitized human input; Copy and Select All are passive",
  );

  const mixedEditFlags = {
    canUndo: false,
    canRedo: true,
    canCut: false,
    canCopy: true,
    canPaste: false,
    canSelectAll: true,
  };
  controlled.emitWebContents("context-menu", {}, {
    linkURL: "",
    selectionText: "selected",
    isEditable: true,
    editFlags: mixedEditFlags,
  });
  for (const [label, method] of editActions) {
    testContext.assert.equal(
      testContext.menuTemplate.find((item) => item.label === label)?.enabled,
      mixedEditFlags[`can${method[0].toUpperCase()}${method.slice(1)}`],
      `${label} must follow its false/mixed edit flag`,
    );
  }

  for (const linkURL of [
    "file:///Users/test/private.txt",
    "javascript:alert(1)",
    "data:text/html,private",
    "blob:https://popup.example/private",
    "mailto:test@example.com",
    "ftp://popup.example/private",
    "about:blank",
    "not a url",
  ]) {
    controlled.emitWebContents("context-menu", {}, {
      linkURL,
      selectionText: "",
      isEditable: false,
      editFlags: {},
    });
    testContext.assert.equal(
      testContext.menuTemplate.some((item) => item.label === "Open Link in New Tab"),
      false,
      `${linkURL} must not receive a new-tab action`,
    );
  }

  controlled.emitWebContents("context-menu", {}, {
    linkURL: "https://popup.example/stale",
    selectionText: "",
    isEditable: false,
    editFlags: {},
  });
  const staleOpenLink = testContext.menuTemplate.find((item) => item.label === "Open Link in New Tab");
  const staleCopyLink = testContext.menuTemplate.find((item) => item.label === "Copy Link Address");
  const staleReload = testContext.menuTemplate.find((item) => item.label === "Reload");
  const destinationWin = testContext.fakeWindow(42);
  const destinationCtx = testContext.registerContext("popup-destination", destinationWin);
  ctx.views.delete("popup-opener");
  opener.ownerId = destinationCtx.id;
  destinationCtx.views.set("popup-opener", opener);
  const beforeTransferredMenu = {
    sent: win.sent.length,
    clipboard: testContext.clipboardWrites.length,
    reload: controlled.nativeCalls.reload,
  };
  staleOpenLink.click();
  staleCopyLink.click();
  staleReload.click();
  testContext.assert.deepEqual(
    testContext.plain({
      sent: win.sent.length,
      clipboard: testContext.clipboardWrites.length,
      reload: controlled.nativeCalls.reload,
    }),
    beforeTransferredMenu,
    "a menu opened before owner transfer must be inert after the record moves",
  );

  destinationCtx.views.delete("popup-opener");
  opener.ownerId = ctx.id;
  ctx.views.set("popup-opener", opener);
  controlled.emitWebContents("context-menu", {}, {
    linkURL: "https://popup.example/destroyed",
    selectionText: "",
    isEditable: false,
    editFlags: {},
  });
  const destroyedActions = [
    testContext.menuTemplate.find((item) => item.label === "Open Link in New Tab"),
    testContext.menuTemplate.find((item) => item.label === "Copy Link Address"),
    testContext.menuTemplate.find((item) => item.label === "Reload"),
  ];
  const beforeDestroyedMenu = {
    sent: win.sent.length,
    clipboard: testContext.clipboardWrites.length,
    reload: controlled.nativeCalls.reload,
  };
  controlled.emitWebContents("destroyed");
  for (const action of destroyedActions) action.click();
  testContext.assert.deepEqual(
    testContext.plain({
      sent: win.sent.length,
      clipboard: testContext.clipboardWrites.length,
      reload: controlled.nativeCalls.reload,
    }),
    beforeDestroyedMenu,
    "menu actions must be inert after the exact WebContents is destroyed",
  );

  ctx.views.delete("popup-opener");
  const sentBeforeDetachedOpener = win.sent.length;
  opener.view.webContents.windowOpen?.({ url: "https://detached.example/" });
  testContext.assert.equal(
    win.sent.length,
    sentBeforeDetachedOpener,
    "a record no longer owned by this window must not emit popup IPC",
  );
  testContext.hooks.windows.delete(destinationCtx.id);
  testContext.hooks.contextsByBrowserWindowId.delete(destinationWin.id);
}
return { checkPopupCreatesIndependentRendererTab };
};
