// Fixed viewport screenshot lifecycle through the production IPC boundary.
module.exports = function createChecks(t) {
  async function checkPipCapture() {
    await checkPipBeforeFirstCommit();
    await checkNavigationGeneration();
    t.hooks.registerWebTabIpc();
    const win = t.fakeWindow(845);
    const ctx = t.registerContext("pip-capture", win);
    const record = t.hooks.ensureView(ctx, "pip-capture-page", "https://example.com/pip");
    const c = t.generatedNativeRecords.at(-1);
    c.controls[0].resolve();
    await record.navigation?.promise;
    const event = { sender: win.webContents };
    const capture = () => t.ipcHandlers.get("webtab:capture")(event, record.id);
    const resize = (width) => {
      t.hooks.setPipZoom(ctx, record.id, width, width * 9 / 16);
      t.hooks.syncVisibleViews(ctx, [{ id: record.id, bounds: { x: 10, y: 20, width, height: width * 9 / 16 } }]);
    };
    const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
    t.hooks.setPipZoom(ctx, record.id, 255, 146);
    t.hooks.syncVisibleViews(ctx, [{ id: record.id, bounds: { x: 10, y: 20, width: 255, height: 146 } }]);
    t.assert.deepEqual(t.plain(record.view.getBounds()), { x: 10, y: 20, width: 255, height: 146 },
      "the native page fills the preview whatever its shape");
    const filled = c.nativeCalls.emulation.at(-1);
    t.assert.equal(filled.scale, 255 / 1920, "the page keeps its 1920 layout width");
    t.assert.deepEqual(t.plain(filled.viewSize), { width: 1920, height: 1099 }, "the layout height follows the preview's shape");
    t.assert.ok(Math.abs(filled.scale * filled.viewSize.height - 146) < 1, "rounding leaves less than one native pixel");
    resize(240);
    t.assert.equal(c.nativeCalls.radius.at(-1), 10, "PiP rounds the native surface");
    win.webContents.getZoomFactor = () => 1.5;
    resize(240);
    t.assert.equal(c.nativeCalls.radius.at(-1), 15, "native radius follows host UI zoom, not page scale");
    win.webContents.getZoomFactor = () => 1;
    resize(240);
    const radiusCalls = c.nativeCalls.radius.length;
    const emulationCount = c.nativeCalls.emulation.length;
    const zoomCount = c.nativeCalls.zoom.length;
    for (let x = 11; x <= 70; x++) {
      t.hooks.syncVisibleViews(ctx, [{ id: record.id, bounds: { x, y: 20, width: 240, height: 135 } }]);
    }
    t.assert.equal(c.nativeCalls.emulation.length, emulationCount, "60 position-only moves must not reset emulation");
    t.assert.equal(c.nativeCalls.zoom.length, zoomCount, "moving must not reset page zoom");
    const boundsCount = c.boundsCalls.length;
    for (let i = 0; i < 60; i++) {
      t.hooks.syncVisibleViews(ctx, [{ id: record.id, bounds: { x: 70, y: 20, width: 240, height: 135 } }]);
    }
    t.assert.equal(c.boundsCalls.length, boundsCount, "identical geometry must not update native bounds");
    t.assert.equal(c.nativeCalls.emulation.length, emulationCount);
    t.assert.equal(c.nativeCalls.insertedCSS.length, 1, "moving must not reinsert preview CSS");
    t.assert.equal(c.nativeCalls.radius.length, radiusCalls, "moves and identical bounds do not update corner clipping");
    resize(480);
    t.assert.equal(c.nativeCalls.emulation.length, emulationCount + 1, "one emulation update per changed scale");
    resize(240);
    t.assert.equal((await t.hooks.inspectView(ctx, record.id)).input_scale, 0.125);
    const nativeCaptures = c.nativeCalls.capturePage;
    const beforePresentation = c.nativeCalls.emulation.length;
    const beforeDebugger = c.debuggerCommands.length;
    await t.ipcHandlers.get("webtab:capture")(event, record.id, "presentation");
    t.assert.equal(c.nativeCalls.capturePage, nativeCaptures + 1, "gesture captures use the already rendered presentation");
    t.assert.equal(c.nativeCalls.emulation.length, beforePresentation, "gesture captures never reconfigure emulation");
    t.assert.equal(c.debuggerCommands.length, beforeDebugger, "gesture captures never attach CDP");
    c.delayDebuggerMethod("Page.captureScreenshot");
    c.delayDebuggerMethod("Target.getTargetInfo");
    const resolving = t.hooks.resolveView(ctx, record.id);
    const first = capture();
    const second = capture();
    await flush();
    t.assert.equal(c.debuggerCommands.filter(x => x.method === "Page.captureScreenshot").length, 1);
    t.assert.deepEqual(t.plain(c.debuggerCommands.find(x => x.method === "Page.captureScreenshot").params.clip), {
      x: 0, y: 0, width: 1920, height: 1080, scale: 0.5,
    });
    c.completeDebuggerMethod("Page.captureScreenshot");
    t.assert.equal(await first, "data:image/png;base64,PIP_CSS_PIXELS");
    t.assert.equal(await second, "data:image/png;base64,PIP_CSS_PIXELS");
    t.assert.equal(c.isDebuggerAttached(), true, "resolve still holds the shared debugger");
    const reapplied = c.nativeCalls.emulation.length;
    c.completeDebuggerMethod("Target.getTargetInfo");
    await resolving;
    t.assert.equal(c.isDebuggerAttached(), false);
    t.assert.ok(c.nativeCalls.emulation.length > reapplied, "the final detach reapplies the current viewport");
    t.assert.equal(c.nativeCalls.emulation.at(-1).scale, 0.125);

    for (const change of ["resize", "exit", "navigation", "ownership"]) {
      resize(240);
      c.delayDebuggerMethod("Page.captureScreenshot");
      const pending = capture();
      await flush();
      if (change === "resize") resize(960);
      if (change === "exit") t.hooks.setPipZoom(ctx, record.id, null);
      if (change === "navigation") record.navigation = { promise: Promise.resolve() };
      if (change === "ownership") record.ownerId = "another-window";
      c.completeDebuggerMethod("Page.captureScreenshot");
      t.assert.equal(await pending, null, `${change} invalidates a captured image`);
      t.assert.ok(c.debuggerCommands.some(x => x.method === "Emulation.clearDeviceMetricsOverride"));
      t.assert.equal(c.isDebuggerAttached(), false);
      if (change === "exit") t.assert.equal(record.pipLayoutZoom, null);
      if (change === "resize") t.assert.equal(c.nativeCalls.emulation.at(-1).scale, 0.5);
      record.ownerId = ctx.id;
      record.navigation = null;
    }
    resize(240);
    const send = record.view.webContents.debugger.sendCommand;
    record.view.webContents.debugger.sendCommand = (method, params) => method === "Page.captureScreenshot"
      ? Promise.reject(new Error("injected capture failure")) : send(method, params);
    t.assert.equal(await capture(), null);
    t.assert.equal(c.debuggerCommands.at(-1).method, "Emulation.clearDeviceMetricsOverride");
    t.assert.equal(c.nativeCalls.emulation.at(-1).scale, 0.125);
    t.assert.equal(c.isDebuggerAttached(), false);
    // Pending styles from either side of navigation cannot survive a preview exit.
    t.hooks.setPipZoom(ctx, record.id, null);
    await flush();
    t.assert.ok(c.nativeCalls.removedCSS.includes(c.nativeCalls.insertedCSS[0].key));
    t.assert.equal(c.nativeCalls.activeCSS.length, 0, "normal tabs must recover their original root scrollbar styling");
    t.assert.equal(c.nativeCalls.radius.at(-1), 0, "ordinary tabs restore square native corners");
    const restoredRadiusCalls = c.nativeCalls.radius.length;
    t.hooks.setPipZoom(ctx, record.id, null);
    t.assert.equal(c.nativeCalls.radius.length, restoredRadiusCalls, "repeated restore does not update corner clipping");
    const pendingStyles = [];
    record.view.webContents.insertCSS = () => new Promise(resolve => pendingStyles.push(resolve));
    resize(240);
    const beforeNavigation = c.nativeCalls.emulation.length;
    record.view.webContents.emit("did-navigate");
    t.assert.equal(c.nativeCalls.emulation.length, beforeNavigation + 1, "navigation forces restore even at the same scale");
    t.assert.equal(pendingStyles.length, 2);
    t.hooks.setPipZoom(ctx, record.id, null);
    pendingStyles[0]("old-document-style");
    pendingStyles[1]("new-document-style");
    await flush();
    t.assert.ok(c.nativeCalls.removedCSS.includes("old-document-style"));
    t.assert.ok(c.nativeCalls.removedCSS.includes("new-document-style"));
    t.assert.equal(record.pipScrollbarStyle, null);
    t.hooks.destroyView(ctx, record.id);
    t.hooks.windows.delete(ctx.id);
  }
  // A pane mounts (ensure, then setPipZoom(null)) and a restored preview sizes
  // its page before the page's first document commits. Electron's device
  // emulation dereferences the missing frame view there, which crashed the
  // App on launch; it waits for the commit, and for a reload after a crash.
  async function checkPipBeforeFirstCommit() {
    t.hooks.registerWebTabIpc();
    const win = t.fakeWindow(847);
    const ctx = t.registerContext("pip-before-commit", win);
    const record = t.hooks.ensureView(ctx, "pip-before-commit-page", "https://example.com/slow");
    const c = t.generatedNativeRecords.at(-1);
    try {
      const dead = t.deadFrameEmulation.length;
      t.assert.equal(t.hooks.setPipZoom(ctx, record.id, null), true);
      t.assert.equal(c.nativeCalls.disableEmulation, 0, "a page with no document has no emulation to clear");
      t.assert.equal(t.hooks.setPipZoom(ctx, record.id, 480, 270), true);
      t.hooks.syncVisibleViews(ctx, [{ id: record.id, bounds: { x: 0, y: 0, width: 640, height: 360 } }]);
      t.assert.equal(c.nativeCalls.emulation.length, 0, "no emulation before the first commit");
      t.assert.equal(t.deadFrameEmulation.length, dead);
      t.assert.equal(record.pipLayoutZoom, 640 / 1920, "the preview geometry is kept for the commit");
      c.controls[0].resolve();
      c.emitWebContents("did-navigate");
      await record.navigation?.promise;
      t.assert.equal(c.nativeCalls.emulation.at(-1).scale, 640 / 1920, "the commit applies the latest preview viewport");
      c.setRendererCrashed(true);
      const live = c.nativeCalls.emulation.length;
      t.hooks.syncVisibleViews(ctx, [{ id: record.id, bounds: { x: 0, y: 0, width: 960, height: 540 } }]);
      record.view.webContents.debugger.detach();
      t.assert.equal(t.hooks.setPipZoom(ctx, record.id, null), true);
      t.assert.equal(c.nativeCalls.emulation.length, live, "a dead renderer gets no emulation");
      t.assert.equal(c.nativeCalls.disableEmulation, 0);
      t.assert.equal(t.deadFrameEmulation.length, dead);
      c.setRendererCrashed(false);
      t.assert.equal(t.hooks.setPipZoom(ctx, record.id, 480, 270), true);
      t.assert.equal(c.nativeCalls.emulation.at(-1).scale, 0.25, "a live page is emulated at once");
    } finally {
      t.hooks.destroyView(ctx, record.id);
      t.hooks.windows.delete(ctx.id);
    }
  }
  async function checkNavigationGeneration() {
    t.hooks.registerWebTabIpc();
    const win = t.fakeWindow(846);
    const ctx = t.registerContext("capture-generation", win);
    const record = t.hooks.ensureView(ctx, "capture-generation-page", "https://example.com/same");
    const c = t.generatedNativeRecords.at(-1);
    c.controls[0].resolve();
    await record.navigation?.promise;
    const url = record.view.webContents.getURL();
    const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
    const start = (same = false, main = true) => c.emitWebContents("did-start-navigation",
      { url, isSameDocument: same, isMainFrame: main }, url, same, main, 1, main ? 1 : 2);
    const commit = (same = false, main = true) => same
      ? c.emitWebContents("did-navigate-in-page", {}, url, main, 1, main ? 1 : 2)
      : c.emitWebContents("did-frame-navigate", {}, url, 200, "OK", main, 1, main ? 1 : 2);
    try {
      t.hooks.setPipZoom(ctx, record.id, 240, 135);
      for (const mode of ["presentation", undefined]) {
        const capture = () => t.ipcHandlers.get("webtab:capture")({ sender: win.webContents }, record.id, mode);
        const delay = () => mode ? c.delayCapturePage() : c.delayDebuggerMethod("Page.captureScreenshot");
        const complete = () => mode ? c.completeCapturePage() : c.completeDebuggerMethod("Page.captureScreenshot");
        for (const change of ["reload", "capture-after-start", "in-page", "subframe", "aborted", "ownership"]) {
          if (change === "capture-after-start") start();
          delay();
          const pending = capture();
          await flush();
          t.assert.equal(record.navigation, null, "native navigation is independent of loadURL promise identity");
          if (change === "ownership") record.ownerId = "other-window";
          else {
            const same = change === "in-page";
            const main = change !== "subframe";
            if (change !== "capture-after-start") start(same, main);
            if (change === "aborted") c.emitWebContents("did-fail-load", {}, -3, "ERR_ABORTED", url, true, 1, 1);
            else commit(same, main);
          }
          complete();
          t.assert.equal(await pending, null, `${mode || "CDP"}: ${change} must reject the obsolete frame`);
          record.ownerId = ctx.id;
          t.assert.equal(record.navigation, null);
          t.assert.equal(record.view.webContents.getURL(), url, "same URL must not revive capture identity");
          t.assert.ok(await capture(), "a fresh capture succeeds after the obsolete pipeline settles");
        }
        delay();
        const moved = capture();
        await flush();
        const bounds = record.view.getBounds();
        record.view.setBounds({ ...bounds, x: bounds.x + 10 });
        complete();
        t.assert.equal(Boolean(await moved), Boolean(mode), "native movement remains valid; CDP retains its existing bounds guard");
      }
      c.delayCapturePage();
      const destroyed = t.ipcHandlers.get("webtab:capture")({ sender: win.webContents }, record.id, "presentation");
      c.emitWebContents("destroyed");
      c.completeCapturePage();
      t.assert.equal(await destroyed, null);
    } finally {
      t.hooks.destroyView(ctx, record.id);
      t.hooks.windows.delete(ctx.id);
    }
  }
  return { checkPipCapture };
};
