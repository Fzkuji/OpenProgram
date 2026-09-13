// transfer fixtures checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
function eventFor(win) {
  return { sender: win.webContents, returnValue: undefined };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function webTransferPayload(ids, kind = ids.length > 1 ? "group" : "tab") {
  const source = {
    windowId: "spoofed-renderer-window",
    kind,
  };
  if (kind === "group") {
    source.groupId = `group:${ids.join(":")}`;
    source.memberIds = [...ids];
    source.visibleIds = ids.slice(0, 2);
    source.focusedId = ids[0];
  }
  if (kind === "segment") {
    source.groupId = `group:${ids[0]}`;
    source.memberIndex = 0;
    source.memberIds = [ids[0], `${ids[0]}:peer`];
    source.visibleIds = [...source.memberIds];
    source.focusedId = ids[0];
  }
  return {
    tabs: ids.map((id) => ({
      id,
      kind: "web",
      title: `Title ${id}`,
      url: `https://example.com/${id}`,
    })),
    source,
    fileDrafts: [],
    chats: [],
  };
}

function attachControlledRecord(ctx, controlled, bounds, visible = true) {
  const record = testContext.addRecord(ctx, controlled);
  ctx.win.contentView.addChildView(record.view);
  record.view.setBounds(bounds);
  record.view.setVisible(visible);
  if (visible) ctx.visibleViewIds.add(record.id);
  return record;
}

function prepareThroughIpc(win, payload) {
  const event = eventFor(win);
  testContext.ipcListeners.get("tab-transfer:prepare")(event, payload);
  return event.returnValue;
}

function transferDecisionFile() {
  return testContext.path.join(testContext.transferUserData, "tab-transfers.json");
}

function loadTransferDecision(token) {
  const { loadTransferDecisions } = testContext.require("../tab-transfer-store.js");
  return loadTransferDecisions(transferDecisionFile()).decisions[token] ?? null;
}

function installOneShotRenameFailure() {
  const original = testContext.fs.renameSync;
  let failed = false;
  testContext.fs.renameSync = function failOnce(...args) {
    if (!failed) {
      failed = true;
      throw new Error("injected durable acknowledgement failure");
    }
    return original.apply(this, args);
  };
  return () => { testContext.fs.renameSync = original; };
}

function installCommittedDecisionWriteFailure(token) {
  const originalRename = testContext.fs.renameSync;
  let failures = 0;
  testContext.fs.renameSync = function failCommittedWrites(...args) {
    let committedWrite = false;
    try {
      const parsed = JSON.parse(testContext.fs.readFileSync(args[0], "utf8"));
      committedWrite = parsed?.decisions?.[token]?.status === "committed";
    } catch (_error) {
      /* non-store renames are irrelevant to this injection */
    }
    if (committedWrite) {
      failures += 1;
      throw new Error("injected committed decision write failure");
    }
    return originalRename.apply(this, args);
  };
  return {
    restore() { testContext.fs.renameSync = originalRename; },
    failures() { return failures; },
  };
}

function installReadFailureAt(failAt) {
  const original = testContext.fs.readFileSync;
  let calls = 0;
  let triggered = false;
  testContext.fs.readFileSync = function failReadAt(...args) {
    calls += 1;
    if (calls === failAt) {
      triggered = true;
      throw new Error(`injected read failure at call ${failAt}`);
    }
    return original.apply(this, args);
  };
  return {
    restore() { testContext.fs.readFileSync = original; },
    triggered() { return triggered; },
    calls() { return calls; },
  };
}

function installReadFailureWhenDecisionMissing(token) {
  const original = testContext.fs.readFileSync;
  const decisionPath = testContext.path.resolve(transferDecisionFile());
  let triggered = false;
  testContext.fs.readFileSync = function failAfterDurableDelete(...args) {
    const result = original.apply(this, args);
    if (testContext.path.resolve(String(args[0])) !== decisionPath) return result;
    try {
      const parsed = JSON.parse(testContext.Buffer.isBuffer(result) ? result.toString("utf8") : result);
      if (!parsed?.decisions?.[token]) {
        triggered = true;
        throw new Error("injected read failure after durable decision deletion");
      }
    } catch (error) {
      if (triggered) throw error;
    }
    return result;
  };
  return {
    restore() { testContext.fs.readFileSync = original; },
    triggered() { return triggered; },
  };
}

function installAmbiguousCommitFailure(token, { blockReconcileReads = false } = {}) {
  const originalFsync = testContext.fs.fsyncSync;
  const originalRename = testContext.fs.renameSync;
  const originalRead = testContext.fs.readFileSync;
  let fsyncCalls = 0;
  let renameCalls = 0;
  let ambiguous = false;
  let sawCommitted = false;
  let reconcileReadFailures = 0;

  testContext.fs.fsyncSync = function failDirectorySync(...args) {
    fsyncCalls += 1;
    if (testContext.process.platform !== "win32" && fsyncCalls === 2) {
      throw new Error("injected directory fsync failure after committed rename");
    }
    return originalFsync.apply(this, args);
  };
  testContext.fs.renameSync = function failPriorRestore(...args) {
    renameCalls += 1;
    if (testContext.process.platform === "win32" && renameCalls === 1) {
      const result = originalRename.apply(this, args);
      throw new Error("injected metadata failure after committed rename");
    }
    if (renameCalls === 2) {
      ambiguous = true;
      throw new Error("injected prior-decision restore failure");
    }
    return originalRename.apply(this, args);
  };
  testContext.fs.readFileSync = function observeCommitted(...args) {
    if (ambiguous && blockReconcileReads) {
      reconcileReadFailures += 1;
      throw new Error("injected indeterminate commit reconciliation read failure");
    }
    const result = originalRead.apply(this, args);
    if (ambiguous) {
      try {
        const parsed = JSON.parse(testContext.Buffer.isBuffer(result) ? result.toString("utf8") : result);
        if (parsed?.decisions?.[token]?.status === "committed") sawCommitted = true;
      } catch (_error) {
        /* non-JSON reads are irrelevant to this injection */
      }
    }
    return result;
  };

  return {
    restore() {
      testContext.fs.fsyncSync = originalFsync;
      testContext.fs.renameSync = originalRename;
      testContext.fs.readFileSync = originalRead;
    },
    releaseReconcileReads() { blockReconcileReads = false; },
    sawCommitted() { return sawCommitted; },
    reconcileReadFailures() { return reconcileReadFailures; },
  };
}
return { eventFor, plain, webTransferPayload, attachControlledRecord, prepareThroughIpc, transferDecisionFile, loadTransferDecision, installOneShotRenameFailure, installCommittedDecisionWriteFailure, installReadFailureAt, installReadFailureWhenDecisionMissing, installAmbiguousCommitFailure };
};
