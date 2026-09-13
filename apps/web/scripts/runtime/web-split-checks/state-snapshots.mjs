// state snapshots: original sequential assertions and shared fixtures.
export async function run(testContext) {


testContext.registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../../lib/session-store/index.ts", testContext.sourceUrl).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../../${specifier.slice(2)}.ts`, testContext.sourceUrl).href,
        shortCircuit: true,
      };
    }
    // Extensionless relative imports between source modules (Node needs the
    // extension; TypeScript and the Next build resolve them on their own).
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      // Append to href, not to pathname: pathname is percent-encoded and
      // re-parsing it against the same base double-encodes any space in the
      // repo path.
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = testContext.existsSync(testContext.fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});


(testContext.values = new Map());

globalThis.window = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  location: { pathname: "/chat" },
};

globalThis.localStorage = {
  getItem: (key) => testContext.values.get(key) ?? null,
  setItem: (key, value) => testContext.values.set(key, String(value)),
  removeItem: (key) => testContext.values.delete(key),
};


(testContext.emptyCenterPayload = {
  version: 2,
  tabs: [],
  activeId: null,
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

(testContext.emptySessionSnapshot = {
  activeChatKey: null,
  currentSessionId: null,
  composerDrafts: {},
  composerSettingsBySession: {},
  pendingProjectsByChat: {},
  draftChannelChoices: {},
});

(testContext.emptyBridgeSnapshot = { liveIds: [], readyIds: [], visibleBounds: [] });

(testContext.transferPayload = {
  tabs: [{
    id: "s:local_one",
    kind: "session",
    title: "Draft",
    sessionId: "local_one",
    draft: true,
  }],
  source: { windowId: "source", kind: "tab" },
  fileDrafts: [],
  chats: [{ chatKey: "local_one", composerDraft: "hello", wasActive: true }],
});

(testContext.journalEntry = (token, role = "destination") => ({
  version: 1,
  token,
  role,
  phase: "staged",
  payload: testContext.transferPayload,
  placement: { kind: "strip-end" },
  beforeCenterTabs: testContext.emptyCenterPayload,
  afterCenterTabs: {
    ...testContext.emptyCenterPayload,
    tabs: testContext.transferPayload.tabs,
    activeId: "s:local_one",
  },
  beforeSession: testContext.emptySessionSnapshot,
  afterSession: {
    ...testContext.emptySessionSnapshot,
    activeChatKey: "local_one",
    composerDrafts: { local_one: "hello" },
  },
  beforeFileDrafts: [],
  afterFileDrafts: [],
  beforeBridge: testContext.emptyBridgeSnapshot,
  afterBridge: testContext.emptyBridgeSnapshot,
}));


({
  deleteTransferJournal: testContext.deleteTransferJournal,
  finalizeTransferJournal: testContext.finalizeTransferJournal,
  recoverTransferJournalEntry: testContext.recoverTransferJournalEntry,
  readTransferJournal: testContext.readTransferJournal,
  stageTransferMutation: testContext.stageTransferMutation,
  updateTransferJournal: testContext.updateTransferJournal,
  writeTransferJournal: testContext.writeTransferJournal,
} = await import("../../../lib/tabs/tab-transfer-journal.ts"));

(testContext.sessionDraftPersistence = await import("../../../lib/chat/session-draft-persistence.ts"));

(testContext.pendingProjection = await import("../../../lib/tabs/pending-transfer-projection.ts"));


testContext.assert.equal(testContext.writeTransferJournal(testContext.journalEntry("one"), "journal-a"), true);

testContext.assert.equal(testContext.writeTransferJournal(testContext.journalEntry("two", "source"), "journal-a"), true);

testContext.assert.deepEqual(Object.keys(testContext.readTransferJournal("journal-a").entries), ["one", "two"]);

testContext.assert.deepEqual(testContext.readTransferJournal("journal-b"), { version: 1, entries: {} });

testContext.assert.equal(testContext.updateTransferJournal("one", { phase: "committing" }, "journal-a"), true);

testContext.assert.equal(testContext.readTransferJournal("journal-a").entries.one.phase, "committing");

testContext.assert.equal(testContext.deleteTransferJournal("one", "journal-a"), true);

testContext.assert.deepEqual(Object.keys(testContext.readTransferJournal("journal-a").entries), ["two"]);

testContext.values.set("openprogram.tabTransferJournal:corrupt", "{bad-json");

testContext.assert.deepEqual(testContext.readTransferJournal("corrupt"), { version: 1, entries: {} });


(testContext.originalSetItem = globalThis.localStorage.setItem);

globalThis.localStorage.setItem = () => { throw new Error("quota"); };

testContext.assert.equal(testContext.writeTransferJournal(testContext.journalEntry("quota"), "journal-fail"), false);

testContext.assert.deepEqual(testContext.readTransferJournal("journal-fail"), { version: 1, entries: {} });

globalThis.localStorage.setItem = () => {};

testContext.assert.equal(testContext.writeTransferJournal(testContext.journalEntry("silent"), "journal-fail"), false);

testContext.assert.deepEqual(testContext.readTransferJournal("journal-fail"), { version: 1, entries: {} });

globalThis.localStorage.setItem = testContext.originalSetItem;


{
  const recovery = testContext.recoveryHarness();
  testContext.assert.equal(testContext.recoverTransferJournalEntry(
    testContext.journalEntry("committed"),
    "committed",
    recovery.handlers,
  ), true);
  testContext.assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", true],
    ["session", "local_one", true],
    ["files", 0],
    ["bridge", 0],
    ["clear", "committed"],
    ["delete", "committed"],
  ]);
}


{
  const recovery = testContext.recoveryHarness();
  testContext.assert.equal(testContext.recoverTransferJournalEntry(
    testContext.journalEntry("destination-staged-explicit-window"),
    "destination-staged",
    recovery.handlers,
    "recovery-window",
  ), true);
  testContext.assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", false],
    ["session", "local_one", false],
    ["files", 0],
    ["bridge", 0],
    ["accepted", "destination-staged-explicit-window"],
  ]);
  testContext.assert.equal(
    testContext.pendingProjection.pendingTransfer(
      "destination-staged-explicit-window",
      "recovery-window",
    )?.token,
    "destination-staged-explicit-window",
  );
  testContext.assert.equal(
    testContext.pendingProjection.pendingTransfer("destination-staged-explicit-window", "main"),
    undefined,
  );
  testContext.pendingProjection.unregisterPendingTransfer(
    "destination-staged-explicit-window",
    "recovery-window",
  );
}


{
  const recovery = testContext.recoveryHarness();
  const entry = testContext.journalEntry("awaiting-source", "source");
  testContext.assert.equal(testContext.recoverTransferJournalEntry(
    entry,
    "awaiting-source",
    recovery.handlers,
  ), true);
  testContext.assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", false],
    ["session", "local_one", false],
    ["files", 0],
    ["bridge", 0],
    ["sourceRemoved", "awaiting-source"],
  ]);
  testContext.pendingProjection.unregisterPendingTransfer("awaiting-source", "main");
}


for (const status of ["prepared", "rolled-back", "stale"]) {
  const recovery = testContext.recoveryHarness();
  const entry = testContext.journalEntry(`before-${status}`, "source");
  testContext.assert.equal(testContext.recoverTransferJournalEntry(entry, status, recovery.handlers), true);
  testContext.assert.deepEqual(recovery.calls, [
    ["tabs", null, true],
    ["session", null, true],
    ["files", 0],
    ["bridge", 0],
    ["clear", `before-${status}`],
    ["delete", `before-${status}`],
  ]);
}


for (const outcome of ["commit", "rollback"]) {
  const token = `finalize-${outcome}`;
  const id = `journal-${outcome}`;
  testContext.assert.equal(testContext.writeTransferJournal(testContext.journalEntry(token), id), true);
  const recovery = testContext.recoveryHarness();
  delete recovery.handlers.deleteJournal;
  const phaseSeen = [];
  const originalApplyTabs = recovery.handlers.applyCenterTabs;
  recovery.handlers.applyCenterTabs = (payload, options) => {
    phaseSeen.push(testContext.readTransferJournal(id).entries[token]?.phase);
    return originalApplyTabs(payload, options);
  };
  testContext.assert.equal(testContext.finalizeTransferJournal(
    token,
    outcome,
    recovery.handlers,
    id,
  ), true);
  testContext.assert.deepEqual(phaseSeen, [outcome === "commit" ? "committing" : "rolling-back"]);
  testContext.assert.equal(testContext.readTransferJournal(id).entries[token], undefined);
  testContext.assert.deepEqual(recovery.calls.slice(0, 4), [
    ["tabs", outcome === "commit" ? "s:local_one" : null, true],
    ["session", outcome === "commit" ? "local_one" : null, true],
    ["files", 0],
    ["bridge", 0],
  ]);
}


{
  const token = "finalize-phase-failure";
  const id = "journal-phase-failure";
  testContext.assert.equal(testContext.writeTransferJournal(testContext.journalEntry(token), id), true);
  globalThis.localStorage.setItem = (key, value) => {
    if (key === `openprogram.tabTransferJournal:${id}`) {
      throw new Error("journal phase quota");
    }
    testContext.originalSetItem(key, value);
  };
  testContext.assert.equal(testContext.finalizeTransferJournal(
    token,
    "commit",
    testContext.recoveryHarness().handlers,
    id,
  ), false);
  testContext.assert.equal(testContext.readTransferJournal(id).entries[token].phase, "staged");
  testContext.assert.equal(
    testContext.pendingProjection.pendingTransfer(token, id)?.token,
    token,
    "a phase-write failure must keep projecting the entry until recovery",
  );
  globalThis.localStorage.setItem = testContext.originalSetItem;
  testContext.pendingProjection.unregisterPendingTransfer(token, id);
  testContext.assert.equal(testContext.deleteTransferJournal(token, id), true);
}


for (const [status, role, missing] of [
  ["committed", "destination", "deleteJournal"],
  ["rolled-back", "source", "clearAccepted"],
  ["destination-staged", "destination", "rebuildAccepted"],
  ["awaiting-source", "source", "resumeSourceRemoved"],
]) {
  const recovery = testContext.recoveryHarness();
  delete recovery.handlers[missing];
  const entry = testContext.journalEntry(`missing-${missing}`, role);
  testContext.assert.equal(testContext.writeTransferJournal(entry, "missing-callbacks"), true);
  testContext.assert.equal(testContext.recoverTransferJournalEntry(entry, status, recovery.handlers), false);
  testContext.assert.deepEqual(recovery.calls, []);
  testContext.assert.ok(testContext.readTransferJournal("missing-callbacks").entries[entry.token]);
  testContext.assert.equal(
    testContext.pendingProjection.pendingTransfer(entry.token, "main")?.token,
    entry.token,
    "an unresolved recovery must keep projecting its provisional delta out",
  );
  testContext.pendingProjection.unregisterPendingTransfer(entry.token, "main");
}


testContext.values.clear();

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };

testContext.values.set("composerDrafts", JSON.stringify({ v: 1, drafts: { legacy: "text" } }));

testContext.values.set("composerSettings", JSON.stringify({
  v: 1,
  map: { legacy: { ...testContext.emptySessionSnapshot.composerSettings, tools: false } },
}));

(testContext.mainSessionModule = await import("../../../lib/session-store/index.ts?task3-main"));

(testContext.mainSession = testContext.mainSessionModule.useSessionStore);

testContext.assert.equal(testContext.mainSession.getState().composerDrafts.legacy, "text");

testContext.assert.equal(testContext.mainSession.getState().composerSettingsBySession.legacy.tools, false);

testContext.assert.equal(testContext.values.has("composerDrafts"), false);

testContext.assert.equal(testContext.values.has("composerSettings"), false);

testContext.assert.ok(testContext.values.has("openprogram.sessionDraftState:main"));


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "secondary" };

(testContext.secondarySessionModule = await import("../../../lib/session-store/index.ts?task3-secondary"));

(testContext.secondarySession = testContext.secondarySessionModule.useSessionStore);

testContext.assert.deepEqual(testContext.secondarySession.getState().composerDrafts, {});

testContext.secondarySession.getState().setCurrentDraft("local_one");

testContext.secondarySession.getState().setComposerInput("secondary text");

testContext.secondarySession.getState().setComposerSettings({ tools: false, thinking: "high" });

testContext.secondarySession.getState().setPendingProject("local_one", "project-one");

(testContext.channelDrafts = await import("../../../lib/runtime-bridge/draft-channel-choice.ts"));

testContext.channelDrafts.setDraftChannelChoice(testContext.secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "slack",
  account_id: "team",
});

(testContext.secondaryDraftBytes = testContext.values.get("openprogram.sessionDraftState:secondary"));

testContext.assert.ok(testContext.secondaryDraftBytes);


(testContext.originalSession = testContext.secondarySession.getState());

(testContext.sessionSnapshot = testContext.secondarySessionModule.snapshotSessionTransfer(["local_one"]));

testContext.secondarySession.setState({
  activeChatKey: null,
  currentSessionId: null,
  composerDrafts: {},
  composerSettingsBySession: {},
  pendingProjectsByChat: {},
});

testContext.secondarySessionModule.draftChoiceHost().__pendingChannelChoices = {};

testContext.secondarySessionModule.draftChoiceHost()._pendingChannelChoice = null;

testContext.secondarySessionModule.applySessionTransfer(testContext.sessionSnapshot, { persist: false });

testContext.assert.deepEqual(
  {
    activeChatKey: testContext.secondarySession.getState().activeChatKey,
    currentSessionId: testContext.secondarySession.getState().currentSessionId,
    composerDrafts: testContext.secondarySession.getState().composerDrafts,
    composerSettingsBySession: testContext.secondarySession.getState().composerSettingsBySession,
    pendingProjectsByChat: testContext.secondarySession.getState().pendingProjectsByChat,
    draftChannelChoices: testContext.secondarySessionModule.draftChoiceHost().__pendingChannelChoices,
  },
  {
    activeChatKey: testContext.originalSession.activeChatKey,
    currentSessionId: testContext.originalSession.currentSessionId,
    composerDrafts: testContext.originalSession.composerDrafts,
    composerSettingsBySession: testContext.originalSession.composerSettingsBySession,
    pendingProjectsByChat: testContext.originalSession.pendingProjectsByChat,
    draftChannelChoices: { local_one: { channel: "slack", account_id: "team" } },
  },
);

testContext.assert.equal(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
  testContext.secondaryDraftBytes,
  "persist:false session restore must leave durable bytes untouched",
);


testContext.secondarySessionModule.applySessionTransfer(testContext.sessionSnapshot, { persist: true });

(testContext.restoredSecondarySession = await import("../../../lib/session-store/index.ts?task3-secondary-restored"));

testContext.assert.equal(testContext.restoredSecondarySession.useSessionStore.getState().composerDrafts.local_one, "secondary text");

testContext.assert.equal(
  testContext.restoredSecondarySession.useSessionStore.getState().pendingProjectsByChat.local_one,
  "project-one",
);

testContext.assert.deepEqual(
  testContext.channelDrafts.draftChannelChoiceFor(testContext.secondarySessionModule.draftChoiceHost(), "local_one"),
  { channel: "slack", account_id: "team" },
);


(testContext.withoutKey = (map, key) => Object.fromEntries(
  Object.entries(map).filter(([candidate]) => candidate !== key),
));

testContext.values.clear();

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-a" };

(testContext.sourceTransferModule = await import("../../../lib/session-store/index.ts?task3-transfer-a"));

(testContext.sourceTransfer = testContext.sourceTransferModule.useSessionStore);

testContext.sourceTransfer.getState().setCurrentDraft("local_move");

testContext.sourceTransfer.getState().setComposerInput("move me");

testContext.sourceTransfer.getState().setComposerSettings({ tools: false, thinking: "medium" });

testContext.sourceTransfer.getState().setPendingProject("local_move", "project-move");

testContext.channelDrafts.setDraftChannelChoice(testContext.secondarySessionModule.draftChoiceHost(), "local_move", {
  channel: "discord",
  account_id: "move-account",
});

(testContext.sourceBeforeCommit = testContext.sourceTransferModule.snapshotSessionTransfer(["local_move"]));

testContext.sourceTransferModule.applySessionTransfer({
  ...testContext.sourceBeforeCommit,
  activeChatKey: null,
  currentSessionId: null,
  composerDrafts: testContext.withoutKey(testContext.sourceBeforeCommit.composerDrafts, "local_move"),
  composerSettingsBySession: testContext.withoutKey(
    testContext.sourceBeforeCommit.composerSettingsBySession,
    "local_move",
  ),
  pendingProjectsByChat: testContext.withoutKey(
    testContext.sourceBeforeCommit.pendingProjectsByChat,
    "local_move",
  ),
  draftChannelChoices: testContext.withoutKey(
    testContext.sourceBeforeCommit.draftChannelChoices,
    "local_move",
  ),
}, { persist: true });


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-b" };

(testContext.destinationTransferModule = await import("../../../lib/session-store/index.ts?task3-transfer-b"));

(testContext.destinationBeforeCommit = testContext.destinationTransferModule.snapshotSessionTransfer([
  "local_move",
]));

testContext.destinationTransferModule.applySessionTransfer({
  ...testContext.destinationBeforeCommit,
  activeChatKey: "local_move",
  currentSessionId: null,
  composerDrafts: {
    ...testContext.destinationBeforeCommit.composerDrafts,
    local_move: testContext.sourceBeforeCommit.composerDrafts.local_move,
  },
  composerSettingsBySession: {
    ...testContext.destinationBeforeCommit.composerSettingsBySession,
    local_move: testContext.sourceBeforeCommit.composerSettingsBySession.local_move,
  },
  pendingProjectsByChat: {
    ...testContext.destinationBeforeCommit.pendingProjectsByChat,
    local_move: testContext.sourceBeforeCommit.pendingProjectsByChat.local_move,
  },
  draftChannelChoices: {
    ...testContext.destinationBeforeCommit.draftChannelChoices,
    local_move: testContext.sourceBeforeCommit.draftChannelChoices.local_move,
  },
}, { persist: true });


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-a" };

(testContext.reloadedSource = await import("../../../lib/session-store/index.ts?task3-transfer-a-reload"));

testContext.assert.equal(testContext.reloadedSource.useSessionStore.getState().composerDrafts.local_move, undefined);

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-b" };

(testContext.reloadedDestination = await import(
  "../../../lib/session-store/index.ts?task3-transfer-b-reload"
));

testContext.assert.equal(
  testContext.reloadedDestination.useSessionStore.getState().composerDrafts.local_move,
  "move me",
);

testContext.assert.equal(
  testContext.reloadedDestination.useSessionStore.getState().pendingProjectsByChat.local_move,
  "project-move",
);

testContext.assert.deepEqual(
  testContext.sessionDraftPersistence.readSessionDraftState().draftChannelChoices.local_move,
  { channel: "discord", account_id: "move-account" },
);


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-a" };

(testContext.rollbackSourceModule = await import("../../../lib/session-store/index.ts?task3-rollback-a"));

testContext.rollbackSourceModule.useSessionStore.getState().setCurrentDraft("local_keep");

testContext.rollbackSourceModule.useSessionStore.getState().setComposerInput("keep me");

(testContext.rollbackSourceBefore = testContext.rollbackSourceModule.snapshotSessionTransfer(["local_keep"]));

testContext.rollbackSourceModule.applySessionTransfer({
  ...testContext.rollbackSourceBefore,
  activeChatKey: null,
  composerDrafts: {},
}, { persist: false });

testContext.rollbackSourceModule.applySessionTransfer(testContext.rollbackSourceBefore, { persist: true });


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-b" };

(testContext.rollbackDestinationModule = await import(
  "../../../lib/session-store/index.ts?task3-rollback-b"
));

(testContext.rollbackDestinationBefore = testContext.rollbackDestinationModule.snapshotSessionTransfer([
  "local_keep",
]));

testContext.rollbackDestinationModule.applySessionTransfer({
  ...testContext.rollbackDestinationBefore,
  activeChatKey: "local_keep",
  composerDrafts: { local_keep: "keep me" },
}, { persist: false });

testContext.rollbackDestinationModule.applySessionTransfer(
  testContext.rollbackDestinationBefore,
  { persist: true },
);

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-a" };

(testContext.reloadedRollbackSource = await import(
  "../../../lib/session-store/index.ts?task3-rollback-a-reload"
));

testContext.assert.equal(
  testContext.reloadedRollbackSource.useSessionStore.getState().composerDrafts.local_keep,
  "keep me",
);

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-b" };

(testContext.reloadedRollbackDestination = await import(
  "../../../lib/session-store/index.ts?task3-rollback-b-reload"
));

testContext.assert.equal(
  testContext.reloadedRollbackDestination.useSessionStore.getState().composerDrafts.local_keep,
  undefined,
);


testContext.values.clear();

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };

testContext.values.set("centerTabs", JSON.stringify({
  version: 2,
  tabs: [
    { id: "s:a", kind: "session", title: "A", sessionId: "a" },
    { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
  ],
  activeId: "s:a",
  groups: [{
    id: "g:legacy",
    memberIds: ["s:a", "w:one"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "s:a",
  }],
  splitWebTabId: "w:one",
  splitRatio: 0.5,
}));

(testContext.mainTabsModule = await import("../../../lib/tabs/center-tabs-store.ts?task3-main-tabs"));

testContext.assert.ok(testContext.values.has("centerTabs:main"));

testContext.assert.equal(testContext.mainTabsModule.useCenterTabs.getState().groups[0].id, "g:legacy");


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "secondary" };

(testContext.secondaryTabsModule = await import("../../../lib/tabs/center-tabs-store.ts?task3-secondary-tabs"));

(testContext.secondaryTabs = testContext.secondaryTabsModule.useCenterTabs);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs, []);

testContext.secondaryTabs.setState({
  tabs: [{ id: "s:existing", kind: "session", title: "Existing", sessionId: "existing" }],
  activeId: "s:existing",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.assert.equal(testContext.secondaryTabsModule.persistCurrentCenterTabsPayload(), true);

(testContext.secondaryTabBytes = testContext.values.get("centerTabs:secondary"));

testContext.secondarySessionModule.persistCurrentSessionTransfer(["local_one"]);

(testContext.effectSessionBytes = testContext.values.get("openprogram.sessionDraftState:secondary"));

(testContext.effectSessionSnapshot = testContext.secondarySessionModule.snapshotSessionTransfer([
  "local_one",
]));

(testContext.validatedDestination = testContext.secondaryTabsModule.validateTransferredTabs(
  testContext.transferPayload,
  { kind: "strip-end" },
));

testContext.assert.equal(testContext.validatedDestination.ok, true);

(testContext.orderedDestinationEntry = testContext.journalEntry("destination-store-stage"));

testContext.orderedDestinationEntry.beforeCenterTabs = {
  version: 2,
  tabs: testContext.secondaryTabs.getState().tabs,
  activeId: testContext.secondaryTabs.getState().activeId,
  groups: testContext.secondaryTabs.getState().groups,
  splitWebTabId: testContext.secondaryTabs.getState().splitWebTabId,
  splitRatio: testContext.secondaryTabs.getState().splitRatio,
};

testContext.orderedDestinationEntry.afterCenterTabs = testContext.validatedDestination.after;

testContext.orderedDestinationEntry.beforeSession = testContext.effectSessionSnapshot;

testContext.orderedDestinationEntry.afterSession = testContext.effectSessionSnapshot;


(testContext.beforeFailedStage = structuredClone(testContext.secondaryTabs.getState().tabs));

(testContext.rejectedStages = 0);

globalThis.localStorage.setItem = (key, value) => {
  if (key === "openprogram.tabTransferJournal:secondary") throw new Error("quota");
  testContext.originalSetItem(key, value);
};

testContext.assert.equal(testContext.stageTransferMutation(
  { ...testContext.orderedDestinationEntry, token: "destination-throw" },
  () => testContext.secondaryTabsModule.insertTransferredTabs(
    testContext.transferPayload,
    { kind: "strip-end" },
    { persist: false },
  ),
  () => { testContext.rejectedStages += 1; },
  "secondary",
), false);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs, testContext.beforeFailedStage);

globalThis.localStorage.setItem = (key, value) => {
  if (key !== "openprogram.tabTransferJournal:secondary") {
    testContext.originalSetItem(key, value);
  }
};

testContext.assert.equal(testContext.stageTransferMutation(
  { ...testContext.orderedDestinationEntry, token: "destination-silent" },
  () => testContext.secondaryTabsModule.insertTransferredTabs(
    testContext.transferPayload,
    { kind: "strip-end" },
    { persist: false },
  ),
  () => { testContext.rejectedStages += 1; },
  "secondary",
), false);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs, testContext.beforeFailedStage);

testContext.assert.equal(testContext.rejectedStages, 2);

globalThis.localStorage.setItem = testContext.originalSetItem;


(testContext.inserted = undefined);

testContext.assert.equal(testContext.stageTransferMutation(
  testContext.orderedDestinationEntry,
  () => {
    testContext.assert.ok(testContext.readTransferJournal("secondary").entries[testContext.orderedDestinationEntry.token]);
    testContext.inserted = testContext.secondaryTabsModule.insertTransferredTabs(
      testContext.transferPayload,
      { kind: "strip-end" },
      { persist: false },
    );
    return testContext.inserted.ok;
  },
  () => { throw new Error("unexpected destination rejection"); },
  "secondary",
), true);

testContext.assert.equal(testContext.inserted.ok, true);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:local_one",
]);

testContext.assert.equal(testContext.values.get("centerTabs:secondary"), testContext.secondaryTabBytes);

testContext.assert.deepEqual(
  testContext.readTransferJournal("secondary").entries["destination-store-stage"]
    .afterCenterTabs,
  testContext.inserted.after,
);

testContext.assert.equal(
  testContext.readTransferJournal("source").entries["destination-store-stage"],
  undefined,
);


testContext.secondaryTabs.getState().renameSessionTab("existing", "Effect write");

testContext.secondarySession.getState().setCurrentDraft("local_one");

testContext.channelDrafts.setDraftChannelChoice(testContext.secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "effect-channel",
  account_id: "effect-account",
});

(testContext.effectProjectedCenter = JSON.parse(testContext.values.get("centerTabs:secondary")));

testContext.assert.deepEqual(testContext.effectProjectedCenter.tabs.map((tab) => tab.id), ["s:existing"]);

testContext.assert.equal(testContext.effectProjectedCenter.tabs[0].title, "Effect write");

testContext.assert.equal(
  testContext.values.get("openprogram.sessionDraftState:secondary"),
  testContext.effectSessionBytes,
  "affected path/channel effects must remain projected out",
);

testContext.pendingProjection.unregisterPendingTransfer(
  testContext.orderedDestinationEntry.token,
  "secondary",
);

testContext.assert.equal(testContext.deleteTransferJournal(testContext.orderedDestinationEntry.token, "secondary"), true);

testContext.secondaryTabsModule.replaceCenterTabsPayload(testContext.inserted.after, { persist: false });

testContext.secondarySessionModule.applySessionTransfer(testContext.effectSessionSnapshot, { persist: false });


(testContext.orderedSourceEntry = {
  ...testContext.orderedDestinationEntry,
  token: "source-store-stage",
  role: "source",
  beforeCenterTabs: testContext.inserted.after,
  afterCenterTabs: testContext.inserted.before,
});

(testContext.removed = undefined);

testContext.assert.equal(testContext.stageTransferMutation(
  testContext.orderedSourceEntry,
  () => {
    testContext.assert.ok(testContext.readTransferJournal("secondary").entries[testContext.orderedSourceEntry.token]);
    testContext.removed = testContext.secondaryTabsModule.removeTransferredTabs(
      ["s:local_one"],
      { persist: false },
    );
    return testContext.removed.ok;
  },
  () => { throw new Error("unexpected source rejection"); },
  "secondary",
), true);

testContext.assert.equal(testContext.removed.ok, true);

testContext.assert.equal(testContext.removed.empty, false);

testContext.assert.deepEqual(testContext.secondaryTabs.getState().tabs.map((tab) => tab.id), ["s:existing"]);

testContext.secondaryTabs.getState().openNewTabPage();

testContext.secondaryTabs.getState().openSessionTab("user-source", "User source");
}
