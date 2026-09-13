export function installFixtures(testContext) {


testContext.recoveryHarness = function recoveryHarness() {
  const calls = [];
  return {
    calls,
    handlers: {
      applyCenterTabs(payload, options) {
        calls.push(["tabs", payload.activeId, options.persist]);
        return true;
      },
      applySession(snapshot, options) {
        calls.push(["session", snapshot.activeChatKey, options.persist]);
        return true;
      },
      applyFileDrafts(snapshot) {
        calls.push(["files", snapshot.length]);
      },
      applyBridge(snapshot) {
        calls.push(["bridge", snapshot.liveIds.length]);
      },
      rebuildAccepted(entry) { calls.push(["accepted", entry.token]); },
      resumeSourceRemoved(entry) { calls.push(["sourceRemoved", entry.token]); },
      clearAccepted(token) { calls.push(["clear", token]); },
      deleteJournal(token) { calls.push(["delete", token]); return true; },
    },
  };
};


testContext.stageDestinationEntry = function stageDestinationEntry(token, sessionId, chatKey) {
  const payload = {
    ...testContext.transferPayload,
    tabs: [{
      id: `s:${sessionId}`,
      kind: "session",
      title: sessionId,
      sessionId,
      draft: true,
    }],
    chats: chatKey
      ? [{ chatKey, composerDraft: `${chatKey} draft`, wasActive: false }]
      : [],
  };
  const beforeCenter = testContext.secondaryTabsModule.snapshotCenterTabsPayload();
  const beforeSession = testContext.secondarySessionModule.snapshotSessionTransfer([]);
  const validated = testContext.secondaryTabsModule.validateTransferredTabs(
    payload,
    { kind: "strip-end" },
  );
  testContext.assert.equal(validated.ok, true);
  const afterSession = chatKey
    ? {
      ...beforeSession,
      composerDrafts: {
        ...beforeSession.composerDrafts,
        [chatKey]: `${chatKey} draft`,
      },
    }
    : beforeSession;
  const entry = {
    ...testContext.journalEntry(token),
    payload,
    beforeCenterTabs: beforeCenter,
    afterCenterTabs: validated.after,
    beforeSession,
    afterSession,
  };
  testContext.assert.equal(testContext.stageTransferMutation(
    entry,
    () => {
      const center = testContext.secondaryTabsModule.insertTransferredTabs(
        payload,
        { kind: "strip-end" },
        { persist: false },
      );
      const session = testContext.secondarySessionModule.applySessionTransfer(
        afterSession,
        { persist: false },
      );
      return center.ok && session;
    },
    () => { throw new Error(`unexpected stage rejection for ${token}`); },
    "secondary",
  ), true);
  return entry;
};


// Crash recovery: a committed journal converges by re-applying only its
// own delta onto the rehydrated state; user edits made after the crash
// snapshot survive, and recovery is idempotent.
testContext.simulateRendererCrash = function simulateRendererCrash(token) {
  testContext.pendingProjection.unregisterPendingTransfer(token, "secondary");
  testContext.secondaryTabsModule.replaceCenterTabsPayload(
    JSON.parse(testContext.values.get("centerTabs:secondary")),
    { persist: false },
  );
  const persisted = JSON.parse(testContext.values.get("openprogram.sessionDraftState:secondary"));
  testContext.secondarySessionModule.applySessionTransfer({
    ...testContext.emptySessionSnapshot,
    composerDrafts: persisted.composerDrafts,
    composerSettingsBySession: persisted.composerSettingsBySession,
    pendingProjectsByChat: persisted.pendingProjectsByChat,
    draftChannelChoices: persisted.draftChannelChoices,
  }, { persist: false });
};


testContext.createTimingHarness = function createTimingHarness() {
  let nextId = 1;
  const frames = new Map();
  const timers = new Map();
  const runFirst = (queue) => {
    const next = queue.entries().next();
    if (next.done) return false;
    const [id, callback] = next.value;
    queue.delete(id);
    callback();
    return true;
  };
  return {
    frames,
    timers,
    deps: {
      requestFrame(callback) {
        const id = nextId++;
        frames.set(id, callback);
        return id;
      },
      cancelFrame(id) {
        frames.delete(id);
      },
      setTimer(callback, delay) {
        testContext.assert.equal(delay, 0);
        const id = nextId++;
        timers.set(id, callback);
        return id;
      },
      clearTimer(id) {
        timers.delete(id);
      },
    },
    runFrame: () => runFirst(frames),
    runTimer: () => runFirst(timers),
  };
};


testContext.makeTransferBridge = function makeTransferBridge(payload, overrides = {}) {
  const calls = [];
  const record = (name, impl) => async (...args) => {
    calls.push([name, ...args]);
    return impl ? impl(...args) : true;
  };
  const tabTransfer = {
    prepare: (value) => { calls.push(["prepare", value]); return "prepared-token"; },
    inspect: record("inspect", (token) => ({
      token,
      status: "prepared",
      sourceId: "source",
      payload,
    })),
    accept: record("accept", (token, placement) => ({
      token,
      status: "destination-staged",
      sourceId: "source",
      destinationId: "main",
      payload,
      placement,
      recordIds: [],
    })),
    reject: record("reject", (_token, reason, duplicateId) => ({ reason, duplicateId })),
    status: record("status", () => null),
    journalOpened: record("journalOpened"),
    journalFinalized: record("journalFinalized"),
    destinationReady: record("destinationReady"),
    sourceRemoved: record("sourceRemoved"),
    destinationUndone: record("destinationUndone"),
    cancel: record("cancel"),
    detach: record("detach", () => null),
    claimPending: record("claimPending", () => null),
    pendingTerminal: record("pendingTerminal", () => []),
    onRemoveSource: () => () => {},
    onUndoDestination: () => () => {},
    onCommitted: () => () => {},
    onRejected: () => () => {},
    onRolledBack: () => () => {},
    onFinalizeOrphaned: () => () => {},
    ...overrides,
  };
  return {
    bridge: {
      isDesktop: true,
      windowId: "main",
      openExternal: () => {},
      webTab: {
        ensure: () => {}, navigate: () => {}, activate: async () => null,
        setBounds: () => {}, show: () => {}, hide: () => {},
        syncVisible: () => {}, destroy: () => {}, reload: () => {},
        goBack: () => {}, goForward: () => {}, onState: () => () => {},
      },
      tabTransfer,
    },
    calls,
  };
};
}
