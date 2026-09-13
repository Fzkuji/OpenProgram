// browser data checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkBrowserDataClear() {
  testContext.assert.deepEqual(
    testContext.plain(await testContext.ipcHandlers.get("browser-data:clear")(
      { sender: {} },
      { history: true, cookies: true },
    )),
    { ok: false },
  );
  const win = testContext.fakeWindow(930);
  const ctx = testContext.registerContext("browser-data-window", win);
  testContext.assert.deepEqual(
    testContext.plain(await testContext.ipcHandlers.get("browser-data:clear")(
      { sender: win.webContents },
      { history: false, cookies: true },
    )),
    { ok: true },
  );
  testContext.assert.deepEqual(testContext.clearedStorageRequests.map(testContext.plain), [{ storages: ["cookies"] }]);
  testContext.hooks.windows.delete(ctx.id);
}

async function checkBrowserImportCancellation() {
  const ownerWin = testContext.fakeWindow(931);
  const otherWin = testContext.fakeWindow(932);
  const owner = testContext.registerContext("browser-import-owner", ownerWin);
  const other = testContext.registerContext("browser-import-other", otherWin);
  let observedSignal = null;
  testContext.fakeBrowserImportRunner = (_request, { signal }) => new Promise((resolve, reject) => {
    observedSignal = signal;
    signal.addEventListener("abort", () => {
      reject(Object.assign(new Error("cancelled"), { code: "import_cancelled" }));
    }, { once: true });
  });
  const run = testContext.ipcHandlers.get("browser-import:run");
  const cancel = testContext.ipcHandlers.get("browser-import:cancel");
  testContext.assert.equal(typeof cancel, "function", "browser import cancel IPC must be registered");
  testContext.assert.deepEqual(
    testContext.plain(await run({ sender: {} }, { requestId: "unauthorized", browserId: "chrome", profileId: "Default", items: ["history"] })),
    { ok: false, error: "unauthorized" },
  );
  const pending = run(
    { sender: ownerWin.webContents },
    { requestId: "request-owner", browserId: "chrome", profileId: "Default", items: ["history"] },
  );
  await Promise.resolve();
  testContext.assert.equal(observedSignal?.aborted, false);
  testContext.assert.deepEqual(
    testContext.plain(await run(
      { sender: otherWin.webContents },
      { requestId: "request-other", browserId: "chrome", profileId: "Default", items: ["history"] },
    )),
    { ok: false, error: "import_busy" },
  );
  testContext.assert.equal(cancel({ sender: otherWin.webContents }, "request-owner"), false);
  testContext.assert.equal(cancel({ sender: ownerWin.webContents }, "wrong-request"), false);
  testContext.assert.equal(cancel({ sender: ownerWin.webContents }, "request-owner"), true);
  testContext.assert.deepEqual(testContext.plain(await pending), { ok: false, error: "import_cancelled" });
  testContext.assert.equal(observedSignal.aborted, true);

  testContext.fakeBrowserImportRunner = async () => ({
    source: { browserId: "chrome", profileId: "Default", label: "Chrome · Default" },
    history: [],
    bookmarks: [],
    cookies: { imported: 0, failed: 0 },
  });
  testContext.assert.equal((await run(
    { sender: ownerWin.webContents },
    { requestId: "request-next", browserId: "chrome", profileId: "Default", items: ["history"] },
  )).ok, true, "cancelled import must release the global lease");

  testContext.fakeBrowserImportRunner = (_request, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {
      reject(Object.assign(new Error("cancelled"), { code: "import_cancelled" }));
    }, { once: true });
  });
  const closing = run(
    { sender: ownerWin.webContents },
    { requestId: "request-closing", browserId: "chrome", profileId: "Default", items: ["history"] },
  );
  await Promise.resolve();
  testContext.hooks.cleanupWindowContext(owner);
  testContext.assert.deepEqual(testContext.plain(await closing), { ok: false, error: "import_cancelled" });
  testContext.hooks.windows.delete(other.id);
}

async function checkCrossWindowHoverCue() {
  for (const id of [...testContext.hooks.windows.keys()]) testContext.hooks.windows.delete(id);
  const srcWin = testContext.fakeWindow(910);
  const winB = testContext.fakeWindow(911);
  const winC = testContext.fakeWindow(912);
  for (const w of [srcWin, winB, winC]) w.show();
  srcWin.bounds = { x: 0, y: 0, width: 100, height: 100 };
  winB.bounds = { x: 200, y: 0, width: 100, height: 100 };
  winC.bounds = { x: 400, y: 0, width: 100, height: 100 };
  const src = testContext.registerContext("hover-src", srcWin);
  testContext.registerContext("hover-b", winB);
  testContext.registerContext("hover-c", winC);
  const atCursor = testContext.ipcHandlers.get("tab-transfer:window-at-cursor");
  const ev = { sender: srcWin.webContents };
  const sentTo = (win, chan) =>
    win.sent.some(([c]) => c === chan);

  // Cursor over the SOURCE window: no target, and the source never lights up.
  testContext.fakeElectron.cursorPoint = { x: 50, y: 50 };
  testContext.assert.equal(await atCursor(ev), null, "source under cursor is not a target");
  testContext.assert.equal(sentTo(srcWin, "tab-transfer:hover-enter"), false,
    "the source window must never highlight itself");

  // Cursor over window B: B gets hover-enter.
  testContext.fakeElectron.cursorPoint = { x: 250, y: 50 };
  testContext.assert.equal(await atCursor(ev), "hover-b");
  testContext.assert.ok(sentTo(winB, "tab-transfer:hover-enter"), "B must get hover-enter");

  // Cursor moves to window C: B gets hover-leave, C gets hover-enter.
  winB.sent.length = 0;
  testContext.fakeElectron.cursorPoint = { x: 450, y: 50 };
  testContext.assert.equal(await atCursor(ev), "hover-c");
  testContext.assert.ok(sentTo(winB, "tab-transfer:hover-leave"), "B must get hover-leave when cursor leaves it");
  testContext.assert.ok(sentTo(winC, "tab-transfer:hover-enter"), "C must get hover-enter");

  // Adaptive: cursor moves to EMPTY desktop (over no window) → the lit window
  // (C) must clear immediately, never latch. Then back over C → C re-lights.
  winC.sent.length = 0;
  testContext.fakeElectron.cursorPoint = { x: 900, y: 900 };
  testContext.assert.equal(await atCursor(ev), null, "empty desktop is not a target");
  testContext.assert.ok(sentTo(winC, "tab-transfer:hover-leave"),
    "cursor over empty desktop must clear the previously-lit window (no stuck highlight)");
  winC.sent.length = 0;
  testContext.fakeElectron.cursorPoint = { x: 450, y: 50 };
  testContext.assert.equal(await atCursor(ev), "hover-c");
  testContext.assert.ok(sentTo(winC, "tab-transfer:hover-enter"), "moving back over C must re-light it");

  // Drag ends via detach: the highlighted window (C) gets hover-leave.
  winC.sent.length = 0;
  await testContext.ipcHandlers.get("tab-transfer:detach")(ev, "tok-none").catch(() => {});
  testContext.assert.ok(sentTo(winC, "tab-transfer:hover-leave"),
    "drag end (detach) must clear the current highlight — no stuck window");

  for (const id of ["hover-src", "hover-b", "hover-c"]) testContext.hooks.windows.delete(id);
}

async function checkCrossWindowHoverZOrder() {
  for (const id of [...testContext.hooks.windows.keys()]) testContext.hooks.windows.delete(id);
  const srcWin = testContext.fakeWindow(920);
  const lowWin = testContext.fakeWindow(921); // registered first → wins on map order
  const topWin = testContext.fakeWindow(922); // registered later, but focused → on top
  for (const w of [srcWin, lowWin, topWin]) w.show();
  // src off to the side; low and top fully overlap the same region.
  srcWin.bounds = { x: 0, y: 0, width: 100, height: 100 };
  lowWin.bounds = { x: 200, y: 0, width: 100, height: 100 };
  topWin.bounds = { x: 200, y: 0, width: 100, height: 100 };
  const src = testContext.registerContext("z-src", srcWin);
  testContext.registerContext("z-low", lowWin);
  testContext.registerContext("z-top", topWin);
  const atCursor = testContext.ipcHandlers.get("tab-transfer:window-at-cursor");
  const ev = { sender: srcWin.webContents };

  testContext.fakeElectron.cursorPoint = { x: 250, y: 50 };
  // Without a focus signal the two are tied — map order would answer, and does.
  testContext.focusedWindow = null;
  testContext.assert.equal(await atCursor(ev), "z-low", "tie falls back to deterministic map order");
  // Focus the later-registered (visually top) window: it must now win the overlap.
  testContext.focusedWindow = topWin;
  testContext.assert.equal(
    await atCursor(ev),
    "z-top",
    "the focused (topmost) window wins the overlap, never the occluded map-order one",
  );
  // Even when the SOURCE window overlaps and is focused, it is still excluded —
  // the hit resolves to one of the other windows, never the source.
  srcWin.bounds = { x: 200, y: 0, width: 100, height: 100 };
  testContext.focusedWindow = srcWin;
  testContext.assert.ok(
    ["z-low", "z-top"].includes(await atCursor(ev)),
    "the drag source is excluded from the hit test even when topmost under the cursor",
  );

  for (const id of ["z-src", "z-low", "z-top"]) testContext.hooks.windows.delete(id);
  testContext.focusedWindow = null;
}
return { checkBrowserDataClear, checkBrowserImportCancellation, checkCrossWindowHoverCue, checkCrossWindowHoverZOrder };
};
