// downloads checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
async function checkDownloadsLifecycle() {
  const outsideDirectory = testContext.fs.mkdtempSync(testContext.path.join(testContext.os.tmpdir(), "openprogram-download-escape-"));
  const outsideFile = testContext.path.join(outsideDirectory, "outside.pdf");
  testContext.fs.writeFileSync(outsideFile, "outside");
  const symlinkPath = testContext.path.join(testContext.transferUserData, "escape.pdf");
  const sameRootTarget = testContext.path.join(testContext.transferUserData, "target.pdf");
  const sameRootSymlink = testContext.path.join(testContext.transferUserData, "alias.pdf");
  testContext.fs.writeFileSync(sameRootTarget, "target");
  let symlinkSupported = true;
  try {
    testContext.fs.symlinkSync(outsideFile, symlinkPath);
    testContext.fs.symlinkSync(sameRootTarget, sameRootSymlink);
  } catch (error) {
    if (!new Set(["EPERM", "EACCES", "ENOTSUP"]).has(error?.code)) throw error;
    symlinkSupported = false;
    try { testContext.fs.unlinkSync(symlinkPath); } catch (_cleanupError) { /* not created */ }
    try { testContext.fs.unlinkSync(sameRootSymlink); } catch (_cleanupError) { /* not created */ }
  }
  const validShape = (id, filePath, overrides = {}) => ({
    id,
    filename: testContext.path.basename(filePath),
    path: filePath,
    url: "https://downloads.example/file.pdf",
    state: "completed",
    receivedBytes: 1,
    totalBytes: 1,
    startedAt: 1,
    updatedAt: 1,
    ...overrides,
  });
  testContext.fs.writeFileSync(testContext.path.join(testContext.transferUserData, "downloads.json"), JSON.stringify({
    version: 1,
    entries: [
      validShape("parent", testContext.path.join(testContext.transferUserData, "..", "parent.pdf")),
      validShape("prefix", `${testContext.transferUserData}-outside/prefix.pdf`),
      ...(symlinkSupported ? [
        validShape("symlink", symlinkPath),
        validShape("same-root-symlink", sameRootSymlink),
      ] : []),
      validShape("bad-type", testContext.path.join(testContext.transferUserData, "bad.pdf"), { url: 42 }),
      validShape("pending", testContext.path.join(testContext.transferUserData, "pending.pdf"), {
        state: "progressing",
        receivedBytes: 0,
        totalBytes: 100,
      }),
    ],
  }));
  const targetSession = new testContext.EventEmitter();
  testContext.hooks.registerDownloads(targetSession);
  testContext.assert.deepEqual(
    [...testContext.hooks.downloads.values()].map((entry) => [entry.id, entry.state]),
    [["pending", "interrupted"]],
    "safe pending records recover as interrupted while malformed paths are rejected",
  );
  testContext.hooks.downloads.clear();
  if (testContext.process.platform === "darwin" || testContext.process.platform === "win32") {
    testContext.assert.equal(
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "Report.pdf")),
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "report.pdf")),
    );
    testContext.assert.equal(
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "re\u0301sume.pdf")),
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "résume.pdf")),
    );
    testContext.assert.equal(
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "straße.pdf")),
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "STRASSE.pdf")),
    );
    testContext.assert.equal(
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "Σ.pdf")),
      testContext.hooks.downloadReservationKey(testContext.path.join(testContext.transferUserData, "ς.pdf")),
    );
  }
  const win = testContext.fakeWindow(42);
  testContext.registerContext("downloads-window", win);
  const secondWindow = testContext.fakeWindow(43);
  testContext.registerContext("downloads-window-second", secondWindow);
  const item = new testContext.EventEmitter();
  let received = 0;
  let savePath = "";
  let cancelled = false;
  item.getFilename = () => "report.pdf";
  item.getURL = () => "https://downloads.example/report.pdf";
  item.getTotalBytes = () => 100;
  item.getReceivedBytes = () => received;
  item.setSavePath = (value) => { savePath = value; };
  item.cancel = () => { cancelled = true; };

  targetSession.emit("will-download", {}, item);
  const [id] = [...testContext.hooks.downloads.keys()];
  testContext.assert.ok(id);
  testContext.assert.equal(testContext.path.basename(savePath), "report.pdf");
  testContext.assert.equal(testContext.hooks.downloads.get(id).state, "progressing");
  testContext.assert.equal(win.sent.at(-1)[0], "downloads:changed");
  item.emit("updated", {}, "interrupted");
  testContext.assert.equal(testContext.hooks.downloads.get(id).state, "interrupted");

  const second = new testContext.EventEmitter();
  let secondPath = "";
  second.getFilename = item.getFilename;
  second.getURL = () => "https://downloads.example/report-copy.pdf";
  second.getTotalBytes = () => 10;
  second.getReceivedBytes = () => 0;
  second.setSavePath = (value) => { secondPath = value; };
  second.cancel = () => {};
  targetSession.emit("will-download", {}, second);
  testContext.assert.equal(
    testContext.path.basename(secondPath),
    "report (1).pdf",
    "an interrupted item remains path-active until done",
  );
  const secondId = [...testContext.hooks.downloads.keys()].find((value) => value !== id);

  if (testContext.process.platform === "darwin" || testContext.process.platform === "win32") {
    const unicodeFirst = new testContext.EventEmitter();
    let unicodeFirstPath = "";
    unicodeFirst.getFilename = () => "straße.pdf";
    unicodeFirst.getURL = () => "https://downloads.example/strasse.pdf";
    unicodeFirst.getTotalBytes = () => 1;
    unicodeFirst.getReceivedBytes = () => 0;
    unicodeFirst.setSavePath = (value) => { unicodeFirstPath = value; };
    unicodeFirst.cancel = () => {};
    targetSession.emit("will-download", {}, unicodeFirst);

    const unicodeSecond = new testContext.EventEmitter();
    let unicodeSecondPath = "";
    unicodeSecond.getFilename = () => "STRASSE.pdf";
    unicodeSecond.getURL = () => "https://downloads.example/STRASSE.pdf";
    unicodeSecond.getTotalBytes = () => 1;
    unicodeSecond.getReceivedBytes = () => 0;
    unicodeSecond.setSavePath = (value) => { unicodeSecondPath = value; };
    unicodeSecond.cancel = () => {};
    targetSession.emit("will-download", {}, unicodeSecond);
    testContext.assert.equal(testContext.path.basename(unicodeFirstPath), "straße.pdf");
    testContext.assert.equal(
      testContext.path.basename(unicodeSecondPath),
      "STRASSE (1).pdf",
      "concurrent downloads use the target filesystem's Unicode case folding",
    );
    unicodeFirst.emit("done", {}, "cancelled");
    unicodeSecond.emit("done", {}, "cancelled");
  }

  const event = { sender: win.webContents };
  if (symlinkSupported) {
    testContext.hooks.downloads.set("runtime-symlink", validShape("runtime-symlink", sameRootSymlink));
    testContext.assert.equal(await testContext.ipcHandlers.get("downloads:open")(event, "runtime-symlink"), false);
    testContext.assert.equal(testContext.ipcHandlers.get("downloads:show")(event, "runtime-symlink"), false);
    testContext.hooks.downloads.delete("runtime-symlink");
  }
  testContext.assert.equal(testContext.ipcHandlers.get("downloads:clear")(event), true);
  testContext.assert.equal(testContext.hooks.downloads.has(id), true, "clear must preserve every active item");
  testContext.assert.equal(await testContext.ipcHandlers.get("downloads:cancel")(event, id), true);
  testContext.assert.equal(cancelled, true);
  received = 50;
  item.emit("updated", {}, "progressing");
  testContext.assert.equal(testContext.hooks.downloads.get(id).receivedBytes, 50);
  received = 100;
  item.emit("done", {}, "completed");
  testContext.assert.equal(testContext.hooks.downloads.get(id).state, "completed");
  testContext.fs.writeFileSync(savePath, "downloaded");
  testContext.assert.equal(await testContext.ipcHandlers.get("downloads:open")(event, id), true);
  testContext.assert.equal(testContext.ipcHandlers.get("downloads:show")(event, id), true);
  testContext.assert.equal(testContext.openedDownloadPaths.at(-1), savePath);
  testContext.assert.equal(testContext.shownDownloadPaths.at(-1), savePath);
  testContext.assert.equal((await testContext.ipcHandlers.get("downloads:list")(event, { query: "report.pdf" })).length, 1);
  second.emit("done", {}, "cancelled");
  testContext.assert.equal(testContext.hooks.downloads.get(secondId).state, "cancelled");
  const entriesBeforeFailedClear = [...testContext.hooks.downloads.keys()];
  const originalWriteFileSync = testContext.fs.writeFileSync;
  testContext.fs.writeFileSync = () => { throw new Error("injected downloads save failure"); };
  try {
    testContext.assert.equal(testContext.ipcHandlers.get("downloads:clear")(event), false);
  } finally {
    testContext.fs.writeFileSync = originalWriteFileSync;
  }
  testContext.assert.deepEqual(
    [...testContext.hooks.downloads.keys()],
    entriesBeforeFailedClear,
    "failed persistence must restore the in-memory download list",
  );
  testContext.assert.equal(testContext.ipcHandlers.get("downloads:clear")(event), true);
  testContext.assert.equal(testContext.hooks.downloads.has(id), false);
  testContext.assert.deepEqual(testContext.plain(secondWindow.sent.at(-1)), ["downloads:changed", null]);
  testContext.fs.rmSync(outsideDirectory, { recursive: true, force: true });
}
return { checkDownloadsLifecycle };
};
