// terminal checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
function checkContextMenuEndAlignment() {
  const anchor = {
    align: "end",
    right: 600,
    x: 0,
    y: 40,
    winW: 800,
    winH: 600,
  };
  testContext.assert.equal(testContext.hooks.contextMenuRequestedX(anchor, 224), 376);
  testContext.assert.equal(testContext.hooks.clampContextMenuPanel(anchor, 224, 120).x, 376);
  testContext.assert.equal(
    testContext.hooks.clampContextMenuPanel(anchor, 300, 120).x,
    300,
    "a measured width change keeps the context-menu right edge on its trigger",
  );
}

async function checkTerminalProcessIdentity() {
  const gracefulKills = testContext.process.platform === "win32" ? [undefined] : ["SIGTERM"];
  const escalatedKills = testContext.process.platform === "win32"
    ? [undefined, undefined]
    : ["SIGTERM", "SIGKILL"];
  const start = testContext.ipcHandlers.get("terminal:start");
  const write = testContext.ipcListeners.get("terminal:write");
  const resize = testContext.ipcListeners.get("terminal:resize");
  const stop = testContext.ipcListeners.get("terminal:stop");
  testContext.assert.equal(typeof start, "function", "terminal start handler is registered");
  testContext.assert.equal(typeof write, "function", "terminal write handler is registered");
  testContext.assert.equal(typeof resize, "function", "terminal resize handler is registered");
  testContext.assert.equal(typeof stop, "function", "terminal stop handler is registered");

  const unauthorizedSender = new testContext.EventEmitter();
  unauthorizedSender.id = 7000;
  unauthorizedSender.isDestroyed = () => false;
  unauthorizedSender.send = () => {};
  const spawnedBeforeUnauthorized = testContext.spawnedPtys.length;
  const unauthorized = await start({ sender: unauthorizedSender }, {
    id: "terminal", cwd: testContext.transferUserData, preset: "shell", cols: 80, rows: 24,
  });
  testContext.assert.equal(unauthorized.ok, false);
  testContext.assert.equal(unauthorized.error, "unauthorized_sender");
  testContext.assert.equal(testContext.spawnedPtys.length, spawnedBeforeUnauthorized);

  const terminalWindow = testContext.fakeWindow(7001);
  testContext.registerContext("terminal-owner", terminalWindow);
  const sender = terminalWindow.webContents;
  const senderEvents = new testContext.EventEmitter();
  sender.id = 7001;
  sender.isDestroyed = () => false;
  sender.once = senderEvents.once.bind(senderEvents);
  sender.emit = senderEvents.emit.bind(senderEvents);
  sender.listenerCount = senderEvents.listenerCount.bind(senderEvents);
  sender.getURL = () => "http://127.0.0.1:18100/chat";
  const sent = [];
  sender.send = (channel, payload) => sent.push([channel, payload]);
  const event = { sender };
  const claudeId = "terminal:terminal-owner:claude";
  const shellId = "terminal:terminal-owner:shell";

  sender.getURL = () => "http://127.0.0.1:18101/chat";
  const wrongOrigin = await start(event, {
    id: shellId, cwd: testContext.transferUserData, preset: "shell", cols: 80, rows: 24,
  });
  testContext.assert.equal(wrongOrigin.error, "unauthorized_sender");
  sender.getURL = () => "http://127.0.0.1:18100/chat";

  const invalid = await start(event, {
    id: claudeId, cwd: testContext.transferUserData, preset: "unknown", cols: 80, rows: 24,
  });
  testContext.assert.equal(invalid.ok, false);
  testContext.assert.equal(invalid.error, "invalid_preset");
  const invalidId = await start(event, {
    id: "claude", cwd: testContext.transferUserData, preset: "claude", cols: 80, rows: 24,
  });
  testContext.assert.equal(invalidId.error, "invalid_terminal");
  testContext.assert.equal(
    (await start(event, { id: claudeId, cwd: testContext.transferUserData, preset: "claude", cols: 90, rows: 31 })).ok,
    true,
  );
  const first = testContext.spawnedPtys.at(-1);
  testContext.assert.deepEqual(
    Array.from(first.args),
    testContext.process.platform === "win32"
      ? ["-NoLogo", "-NoExit", "-Command", "claude"]
      : ["-l", "-i", "-c", "exec claude"],
  );
  testContext.assert.equal(first.options.useConpty, testContext.process.platform === "win32");
  testContext.assert.equal(first.options.cwd, testContext.fs.realpathSync(testContext.transferUserData));
  testContext.assert.equal(first.options.name, "xterm-256color");
  testContext.assert.deepEqual([first.options.cols, first.options.rows], [90, 31]);
  testContext.assert.equal(sender.listenerCount("destroyed"), 1);

  first.emit("data", "Claude Code ready");
  testContext.assert.equal(sent.at(-1)[0], "terminal:data");
  testContext.assert.equal(sent.at(-1)[1].id, claudeId);
  testContext.assert.equal(sent.at(-1)[1].data, "Claude Code ready");
  testContext.assert.equal(
    (await start(event, { id: claudeId, cwd: testContext.transferUserData, preset: "claude", cols: 100, rows: 35 })).reused,
    true,
  );
  testContext.assert.strictEqual(testContext.spawnedPtys.at(-1), first, "remounting a live terminal must reuse its PTY");
  testContext.assert.deepEqual(first.resizes.at(-1), [100, 35]);
  testContext.assert.equal(sent.at(-1)[1].data, "Claude Code ready", "remount replays buffered output");
  write(event, claudeId, "hello\r");
  resize(event, claudeId, 120, 40);
  testContext.assert.deepEqual(first.writes, ["hello\r"]);
  testContext.assert.deepEqual(first.resizes, [[100, 35], [120, 40]]);
  write(event, claudeId, "你".repeat(22_000));
  testContext.assert.deepEqual(
    first.writes,
    ["hello\r"],
    "terminal input over 64 KiB in UTF-8 bytes must be rejected",
  );
  const boundaryInput = "a".repeat(65_536);
  write(event, claudeId, boundaryInput);
  testContext.assert.equal(first.writes.at(-1), boundaryInput, "exactly 64 KiB remains accepted");

  const timersBeforeReplace = new Set(testContext.clock.pendingIds());
  testContext.assert.equal(
    (await start(event, {
      id: claudeId,
      cwd: testContext.path.dirname(testContext.transferUserData),
      preset: "claude",
      cols: 80,
      rows: 24,
    })).ok,
    true,
  );
  const second = testContext.spawnedPtys.at(-1);
  const firstKillTimer = testContext.clock.pendingIds().find((id) => !timersBeforeReplace.has(id));
  testContext.assert.notEqual(firstKillTimer, undefined);
  testContext.assert.deepEqual(first.kills, gracefulKills);
  if (testContext.process.platform === "win32") {
    testContext.assert.deepEqual(testContext.processSignals, []);
  } else {
    testContext.assert.deepEqual(testContext.processSignals.slice(-2), [
      [-first.pid, "SIGTERM"],
      [first.pid + 1_000, "SIGTERM"],
    ]);
  }
  testContext.assert.equal(
    sender.listenerCount("destroyed"),
    1,
    "replacing or remounting terminals must not accumulate sender listeners",
  );
  const sentBeforeStale = sent.length;
  const signalsBeforeFirstExit = testContext.processSignals.length;
  first.emit("data", "stale");
  first.emit("exit", { exitCode: 0, signal: 0 });
  testContext.assert.equal(sent.length, sentBeforeStale, "replaced PTY cannot write or exit into its successor");
  testContext.assert.equal(
    testContext.clock.pendingIds().includes(firstKillTimer),
    false,
    "a PTY that exits after SIGTERM cancels its SIGKILL timer",
  );
  testContext.clock.runCleared(firstKillTimer);
  testContext.assert.deepEqual(first.kills, gracefulKills);
  testContext.assert.equal(
    testContext.processSignals.length,
    signalsBeforeFirstExit,
    "a cleared escalation callback cannot signal a reused process id",
  );
  const timersBeforeStop = new Set(testContext.clock.pendingIds());
  stop(event, claudeId);
  testContext.assert.deepEqual(second.kills, gracefulKills);
  const secondKillTimer = testContext.clock.pendingIds().find((id) => !timersBeforeStop.has(id));
  testContext.assert.notEqual(secondKillTimer, undefined);
  testContext.clock.runCleared(secondKillTimer);
  testContext.clock.clearTimeout(secondKillTimer);
  testContext.assert.deepEqual(second.kills, escalatedKills);
  if (testContext.process.platform === "win32") {
    testContext.assert.deepEqual(testContext.processSignals, []);
  } else {
    testContext.assert.deepEqual(testContext.processSignals.slice(-2), [
      [-second.pid, "SIGKILL"],
      [second.pid + 1_000, "SIGKILL"],
    ]);
  }
  second.emit("exit", { exitCode: 137, signal: 9 });

  await start(event, {
    id: shellId, cwd: testContext.transferUserData, preset: "shell", cols: 80, rows: 24,
  });
  const third = testContext.spawnedPtys.at(-1);
  const timersBeforeDestroy = new Set(testContext.clock.pendingIds());
  sender.emit("destroyed");
  testContext.assert.deepEqual(third.kills, gracefulKills, "renderer destruction kills all owned PTYs");
  const thirdKillTimer = testContext.clock.pendingIds().find((id) => !timersBeforeDestroy.has(id));
  testContext.assert.notEqual(thirdKillTimer, undefined);
  testContext.clock.runCleared(thirdKillTimer);
  testContext.clock.clearTimeout(thirdKillTimer);
  testContext.assert.deepEqual(third.kills, escalatedKills);
  if (testContext.process.platform === "win32") {
    testContext.assert.deepEqual(testContext.processSignals, []);
  } else {
    testContext.assert.deepEqual(testContext.processSignals.slice(-2), [
      [-third.pid, "SIGKILL"],
      [third.pid + 1_000, "SIGKILL"],
    ]);
  }
  third.emit("exit", { exitCode: 137, signal: 9 });
  if (testContext.process.platform === "win32") {
    testContext.assert.deepEqual(
      testContext.windowsTreeKills.slice(-6),
      [first.pid, second.pid, second.pid, third.pid, third.pid].slice(-6),
      "Windows stops each ConPTY process tree and retries during escalation",
    );
  }
}

async function checkFocusedRoutingAndCleanup() {
  const winA = testContext.fakeWindow(5);
  const winB = testContext.fakeWindow(6);
  const ctxA = testContext.registerContext("focus-a", winA);
  const ctxB = testContext.registerContext("focus-b", winB);

  testContext.focusedWindow = winA;
  testContext.assert.strictEqual(testContext.hooks.focusedContext(), ctxA);
  testContext.focusedWindow = winB;
  testContext.assert.strictEqual(testContext.hooks.focusedContext(), ctxB);
  testContext.focusedWindow = null;
  testContext.assert.strictEqual(testContext.hooks.focusedContext(), ctxB);

  const unrelated = testContext.fakeWindow(60);
  testContext.focusedWindow = unrelated;
  testContext.assert.strictEqual(testContext.hooks.focusedContext(), null);

  testContext.hooks.buildMenu();
  const fileMenu = testContext.menuTemplate.find((entry) => entry.label === "File");
  const newTab = fileMenu.submenu.find((entry) => entry.label === "New Tab");
  const closeTab = fileMenu.submenu.find((entry) => entry.label === "Close Tab");
  testContext.focusedWindow = winA;
  newTab.click();
  testContext.focusedWindow = winB;
  closeTab.click();
  testContext.focusedWindow = null;
  newTab.click();
  testContext.assert.deepEqual(winA.sent, [["menu:new-tab"]]);
  testContext.assert.deepEqual(winB.sent, [["menu:close-tab"], ["menu:new-tab"]]);

  winB.destroyed = true;
  const fallback = testContext.hooks.focusedContext();
  testContext.assert.ok(fallback === ctxA || fallback === null);
  testContext.assert.notStrictEqual(fallback, ctxB);

  const ctxC = await testContext.hooks.createWindow({ windowId: "cleanup-c" });
  const winC = ctxC.win;
  testContext.assert.deepEqual(
    Array.from(testContext.browserWindowOptions.at(-1).webPreferences.additionalArguments),
    ["--openprogram-window-id=cleanup-c"],
  );
  const mainCtx = await testContext.hooks.createWindow();
  testContext.assert.equal(mainCtx.id, "main");
  testContext.assert.deepEqual(
    Array.from(testContext.browserWindowOptions.at(-1).webPreferences.additionalArguments),
    ["--openprogram-window-id=main"],
  );
  mainCtx.win.listeners.get("closed")();
  const owned = testContext.controlledRecord("owned-c");
  const foreign = testContext.controlledRecord("foreign-c");
  testContext.addRecord(ctxC, owned);
  foreign.record.ownerId = "different-owner";
  ctxC.views.set("foreign-c", foreign.record);
  ctxC.visibleViewIds = new Set(["owned-c", "foreign-c"]);
  winC.listeners.get("closed")();
  testContext.assert.equal(owned.closeCallCount(), 1);
  testContext.assert.equal(foreign.closeCallCount(), 0);
  testContext.assert.equal(ctxC.views.size, 0);
  testContext.assert.equal(ctxC.visibleViewIds.size, 0);
  testContext.assert.equal(testContext.hooks.windows.has("cleanup-c"), false);
  testContext.assert.equal(testContext.hooks.contextsByBrowserWindowId.has(winC.id), false);
}
return { checkContextMenuEndAlignment, checkTerminalProcessIdentity, checkFocusedRoutingAndCleanup };
};
