import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../lib/session-store/index.ts", import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const values = new Map();
globalThis.window = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  location: { pathname: "/chat" },
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};

const emptyCenterPayload = {
  version: 2,
  tabs: [],
  activeId: null,
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.44,
};
const emptySessionSnapshot = {
  activeChatKey: null,
  currentSessionId: null,
  composerInput: "",
  composerSettings: {
    thinking: "",
    tools: true,
    webSearch: false,
    fast: false,
    unattended: false,
    permission_mode: "",
  },
  composerDrafts: {},
  composerSettingsBySession: {},
  pendingProjectsByChat: {},
  draftChannelChoices: {},
};
const emptyBridgeSnapshot = { liveIds: [], readyIds: [], visibleBounds: [] };
const transferPayload = {
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
};
const journalEntry = (token, role = "destination") => ({
  version: 1,
  token,
  role,
  phase: "staged",
  payload: transferPayload,
  placement: { kind: "strip-end" },
  beforeCenterTabs: emptyCenterPayload,
  afterCenterTabs: {
    ...emptyCenterPayload,
    tabs: transferPayload.tabs,
    activeId: "s:local_one",
  },
  beforeSession: emptySessionSnapshot,
  afterSession: {
    ...emptySessionSnapshot,
    activeChatKey: "local_one",
    composerInput: "hello",
    composerDrafts: { local_one: "hello" },
  },
  beforeFileDrafts: [],
  afterFileDrafts: [],
  beforeBridge: emptyBridgeSnapshot,
  afterBridge: emptyBridgeSnapshot,
});

const {
  deleteTransferJournal,
  finalizeTransferJournal,
  recoverTransferJournalEntry,
  readTransferJournal,
  stageTransferMutation,
  updateTransferJournal,
  writeTransferJournal,
} = await import("../lib/tab-transfer-journal.ts");

assert.equal(writeTransferJournal(journalEntry("one"), "journal-a"), true);
assert.equal(writeTransferJournal(journalEntry("two", "source"), "journal-a"), true);
assert.deepEqual(Object.keys(readTransferJournal("journal-a").entries), ["one", "two"]);
assert.deepEqual(readTransferJournal("journal-b"), { version: 1, entries: {} });
assert.equal(updateTransferJournal("one", { phase: "committing" }, "journal-a"), true);
assert.equal(readTransferJournal("journal-a").entries.one.phase, "committing");
assert.equal(deleteTransferJournal("one", "journal-a"), true);
assert.deepEqual(Object.keys(readTransferJournal("journal-a").entries), ["two"]);
values.set("openprogram.tabTransferJournal:corrupt", "{bad-json");
assert.deepEqual(readTransferJournal("corrupt"), { version: 1, entries: {} });

const originalSetItem = globalThis.localStorage.setItem;
globalThis.localStorage.setItem = () => { throw new Error("quota"); };
assert.equal(writeTransferJournal(journalEntry("quota"), "journal-fail"), false);
assert.deepEqual(readTransferJournal("journal-fail"), { version: 1, entries: {} });
let failedMutations = 0;
let rejectedStages = 0;
assert.equal(stageTransferMutation(
  journalEntry("quota-stage"),
  () => { failedMutations += 1; },
  () => { rejectedStages += 1; },
  "journal-fail",
), false);
assert.equal(failedMutations, 0);
assert.equal(rejectedStages, 1);
globalThis.localStorage.setItem = () => {};
assert.equal(writeTransferJournal(journalEntry("silent"), "journal-fail"), false);
assert.deepEqual(readTransferJournal("journal-fail"), { version: 1, entries: {} });
assert.equal(stageTransferMutation(
  journalEntry("silent-stage"),
  () => { failedMutations += 1; },
  () => { rejectedStages += 1; },
  "journal-fail",
), false);
assert.equal(failedMutations, 0);
assert.equal(rejectedStages, 2);
globalThis.localStorage.setItem = originalSetItem;

let mutationSawJournal = false;
assert.equal(stageTransferMutation(
  journalEntry("ordered-stage"),
  () => {
    mutationSawJournal = !!readTransferJournal("journal-order")
      .entries["ordered-stage"];
  },
  () => { throw new Error("unexpected rejection"); },
  "journal-order",
), true);
assert.equal(mutationSawJournal, true);

function recoveryHarness() {
  const calls = [];
  return {
    calls,
    handlers: {
      applyCenterTabs(payload, options) {
        calls.push(["tabs", payload.activeId, options.persist]);
      },
      applySession(snapshot, options) {
        calls.push(["session", snapshot.activeChatKey, options.persist]);
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
}

{
  const recovery = recoveryHarness();
  assert.equal(recoverTransferJournalEntry(
    journalEntry("committed"),
    "committed",
    recovery.handlers,
  ), true);
  assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", true],
    ["session", "local_one", true],
    ["files", 0],
    ["bridge", 0],
    ["clear", "committed"],
    ["delete", "committed"],
  ]);
}

{
  const recovery = recoveryHarness();
  assert.equal(recoverTransferJournalEntry(
    journalEntry("destination-staged"),
    "destination-staged",
    recovery.handlers,
  ), true);
  assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", false],
    ["session", "local_one", false],
    ["files", 0],
    ["bridge", 0],
    ["accepted", "destination-staged"],
  ]);
}

{
  const recovery = recoveryHarness();
  const entry = journalEntry("awaiting-source", "source");
  assert.equal(recoverTransferJournalEntry(
    entry,
    "awaiting-source",
    recovery.handlers,
  ), true);
  assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", false],
    ["session", "local_one", false],
    ["files", 0],
    ["bridge", 0],
    ["sourceRemoved", "awaiting-source"],
  ]);
}

for (const status of ["prepared", "rolled-back", "stale"]) {
  const recovery = recoveryHarness();
  const entry = journalEntry(`before-${status}`, "source");
  assert.equal(recoverTransferJournalEntry(entry, status, recovery.handlers), true);
  assert.deepEqual(recovery.calls, [
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
  assert.equal(writeTransferJournal(journalEntry(token), id), true);
  const recovery = recoveryHarness();
  delete recovery.handlers.deleteJournal;
  const phaseSeen = [];
  const originalApplyTabs = recovery.handlers.applyCenterTabs;
  recovery.handlers.applyCenterTabs = (payload, options) => {
    phaseSeen.push(readTransferJournal(id).entries[token]?.phase);
    originalApplyTabs(payload, options);
  };
  assert.equal(finalizeTransferJournal(
    token,
    outcome,
    recovery.handlers,
    id,
  ), true);
  assert.deepEqual(phaseSeen, [outcome === "commit" ? "committing" : "rolling-back"]);
  assert.equal(readTransferJournal(id).entries[token], undefined);
  assert.deepEqual(recovery.calls.slice(0, 4), [
    ["tabs", outcome === "commit" ? "s:local_one" : null, true],
    ["session", outcome === "commit" ? "local_one" : null, true],
    ["files", 0],
    ["bridge", 0],
  ]);
}

values.clear();
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };
values.set("composerDrafts", JSON.stringify({ v: 1, drafts: { legacy: "text" } }));
values.set("composerSettings", JSON.stringify({
  v: 1,
  map: { legacy: { ...emptySessionSnapshot.composerSettings, tools: false } },
}));
const mainSessionModule = await import("../lib/session-store/index.ts?task3-main");
const mainSession = mainSessionModule.useSessionStore;
assert.equal(mainSession.getState().composerDrafts.legacy, "text");
assert.equal(mainSession.getState().composerSettingsBySession.legacy.tools, false);
assert.equal(values.has("composerDrafts"), false);
assert.equal(values.has("composerSettings"), false);
assert.ok(values.has("openprogram.sessionDraftState:main"));

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "secondary" };
const secondarySessionModule = await import("../lib/session-store/index.ts?task3-secondary");
const secondarySession = secondarySessionModule.useSessionStore;
assert.deepEqual(secondarySession.getState().composerDrafts, {});
secondarySession.getState().setCurrentDraft("local_one");
secondarySession.getState().setComposerInput("secondary text");
secondarySession.getState().setComposerSettings({ tools: false, thinking: "high" });
secondarySession.getState().setPendingProject("local_one", "project-one");
const channelDrafts = await import("../lib/runtime-bridge/draft-channel-choice.ts");
channelDrafts.setDraftChannelChoice(globalThis.window, "local_one", {
  channel: "slack",
  account_id: "team",
});
const secondaryDraftBytes = values.get("openprogram.sessionDraftState:secondary");
assert.ok(secondaryDraftBytes);

const originalSession = secondarySession.getState();
const sessionSnapshot = secondarySessionModule.snapshotSessionTransfer(["local_one"]);
secondarySession.setState({
  activeChatKey: null,
  currentSessionId: null,
  composerInput: "",
  composerSettings: emptySessionSnapshot.composerSettings,
  composerDrafts: {},
  composerSettingsBySession: {},
  pendingProjectsByChat: {},
});
globalThis.window.__pendingChannelChoices = {};
globalThis.window._pendingChannelChoice = null;
secondarySessionModule.applySessionTransfer(sessionSnapshot, { persist: false });
assert.deepEqual(
  {
    activeChatKey: secondarySession.getState().activeChatKey,
    currentSessionId: secondarySession.getState().currentSessionId,
    composerInput: secondarySession.getState().composerInput,
    composerSettings: secondarySession.getState().composerSettings,
    composerDrafts: secondarySession.getState().composerDrafts,
    composerSettingsBySession: secondarySession.getState().composerSettingsBySession,
    pendingProjectsByChat: secondarySession.getState().pendingProjectsByChat,
    draftChannelChoices: globalThis.window.__pendingChannelChoices,
  },
  {
    activeChatKey: originalSession.activeChatKey,
    currentSessionId: originalSession.currentSessionId,
    composerInput: originalSession.composerInput,
    composerSettings: originalSession.composerSettings,
    composerDrafts: originalSession.composerDrafts,
    composerSettingsBySession: originalSession.composerSettingsBySession,
    pendingProjectsByChat: originalSession.pendingProjectsByChat,
    draftChannelChoices: { local_one: { channel: "slack", account_id: "team" } },
  },
);
assert.equal(
  values.get("openprogram.sessionDraftState:secondary"),
  secondaryDraftBytes,
  "persist:false session restore must leave durable bytes untouched",
);

secondarySessionModule.applySessionTransfer(sessionSnapshot, { persist: true });
const restoredSecondarySession = await import("../lib/session-store/index.ts?task3-secondary-restored");
assert.equal(restoredSecondarySession.useSessionStore.getState().composerDrafts.local_one, "secondary text");
assert.equal(
  restoredSecondarySession.useSessionStore.getState().pendingProjectsByChat.local_one,
  "project-one",
);
assert.deepEqual(
  channelDrafts.draftChannelChoiceFor(globalThis.window, "local_one"),
  { channel: "slack", account_id: "team" },
);

const withoutKey = (map, key) => Object.fromEntries(
  Object.entries(map).filter(([candidate]) => candidate !== key),
);
values.clear();
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-a" };
globalThis.window.__pendingChannelChoices = {};
globalThis.window._pendingChannelChoice = null;
const sourceTransferModule = await import("../lib/session-store/index.ts?task3-transfer-a");
const sourceTransfer = sourceTransferModule.useSessionStore;
sourceTransfer.getState().setCurrentDraft("local_move");
sourceTransfer.getState().setComposerInput("move me");
sourceTransfer.getState().setComposerSettings({ tools: false, thinking: "medium" });
sourceTransfer.getState().setPendingProject("local_move", "project-move");
channelDrafts.setDraftChannelChoice(globalThis.window, "local_move", {
  channel: "discord",
  account_id: "move-account",
});
const sourceBeforeCommit = sourceTransferModule.snapshotSessionTransfer(["local_move"]);
sourceTransferModule.applySessionTransfer({
  ...sourceBeforeCommit,
  activeChatKey: null,
  currentSessionId: null,
  composerInput: "",
  composerSettings: emptySessionSnapshot.composerSettings,
  composerDrafts: withoutKey(sourceBeforeCommit.composerDrafts, "local_move"),
  composerSettingsBySession: withoutKey(
    sourceBeforeCommit.composerSettingsBySession,
    "local_move",
  ),
  pendingProjectsByChat: withoutKey(
    sourceBeforeCommit.pendingProjectsByChat,
    "local_move",
  ),
  draftChannelChoices: withoutKey(
    sourceBeforeCommit.draftChannelChoices,
    "local_move",
  ),
}, { persist: true });

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-b" };
globalThis.window.__pendingChannelChoices = {};
globalThis.window._pendingChannelChoice = null;
const destinationTransferModule = await import("../lib/session-store/index.ts?task3-transfer-b");
const destinationBeforeCommit = destinationTransferModule.snapshotSessionTransfer([
  "local_move",
]);
destinationTransferModule.applySessionTransfer({
  ...destinationBeforeCommit,
  activeChatKey: "local_move",
  currentSessionId: null,
  composerInput: sourceBeforeCommit.composerInput,
  composerSettings: sourceBeforeCommit.composerSettings,
  composerDrafts: {
    ...destinationBeforeCommit.composerDrafts,
    local_move: sourceBeforeCommit.composerDrafts.local_move,
  },
  composerSettingsBySession: {
    ...destinationBeforeCommit.composerSettingsBySession,
    local_move: sourceBeforeCommit.composerSettingsBySession.local_move,
  },
  pendingProjectsByChat: {
    ...destinationBeforeCommit.pendingProjectsByChat,
    local_move: sourceBeforeCommit.pendingProjectsByChat.local_move,
  },
  draftChannelChoices: {
    ...destinationBeforeCommit.draftChannelChoices,
    local_move: sourceBeforeCommit.draftChannelChoices.local_move,
  },
}, { persist: true });

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-a" };
const reloadedSource = await import("../lib/session-store/index.ts?task3-transfer-a-reload");
assert.equal(reloadedSource.useSessionStore.getState().composerDrafts.local_move, undefined);
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-b" };
const reloadedDestination = await import(
  "../lib/session-store/index.ts?task3-transfer-b-reload"
);
assert.equal(
  reloadedDestination.useSessionStore.getState().composerDrafts.local_move,
  "move me",
);
assert.equal(
  reloadedDestination.useSessionStore.getState().pendingProjectsByChat.local_move,
  "project-move",
);
const sessionDraftPersistence = await import("../lib/session-draft-persistence.ts");
assert.deepEqual(
  sessionDraftPersistence.readSessionDraftState().draftChannelChoices.local_move,
  { channel: "discord", account_id: "move-account" },
);

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-a" };
globalThis.window.__pendingChannelChoices = {};
globalThis.window._pendingChannelChoice = null;
const rollbackSourceModule = await import("../lib/session-store/index.ts?task3-rollback-a");
rollbackSourceModule.useSessionStore.getState().setCurrentDraft("local_keep");
rollbackSourceModule.useSessionStore.getState().setComposerInput("keep me");
const rollbackSourceBefore = rollbackSourceModule.snapshotSessionTransfer(["local_keep"]);
rollbackSourceModule.applySessionTransfer({
  ...rollbackSourceBefore,
  activeChatKey: null,
  composerInput: "",
  composerDrafts: {},
}, { persist: false });
rollbackSourceModule.applySessionTransfer(rollbackSourceBefore, { persist: true });

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-b" };
globalThis.window.__pendingChannelChoices = {};
globalThis.window._pendingChannelChoice = null;
const rollbackDestinationModule = await import(
  "../lib/session-store/index.ts?task3-rollback-b"
);
const rollbackDestinationBefore = rollbackDestinationModule.snapshotSessionTransfer([
  "local_keep",
]);
rollbackDestinationModule.applySessionTransfer({
  ...rollbackDestinationBefore,
  activeChatKey: "local_keep",
  composerInput: "keep me",
  composerDrafts: { local_keep: "keep me" },
}, { persist: false });
rollbackDestinationModule.applySessionTransfer(
  rollbackDestinationBefore,
  { persist: true },
);
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-a" };
const reloadedRollbackSource = await import(
  "../lib/session-store/index.ts?task3-rollback-a-reload"
);
assert.equal(
  reloadedRollbackSource.useSessionStore.getState().composerDrafts.local_keep,
  "keep me",
);
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-b" };
const reloadedRollbackDestination = await import(
  "../lib/session-store/index.ts?task3-rollback-b-reload"
);
assert.equal(
  reloadedRollbackDestination.useSessionStore.getState().composerDrafts.local_keep,
  undefined,
);

values.clear();
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };
values.set("centerTabs", JSON.stringify({
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
const mainTabsModule = await import("../lib/state/center-tabs-store.ts?task3-main-tabs");
assert.ok(values.has("centerTabs:main"));
assert.equal(mainTabsModule.useCenterTabs.getState().groups[0].id, "g:legacy");

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "secondary" };
const secondaryTabsModule = await import("../lib/state/center-tabs-store.ts?task3-secondary-tabs");
const secondaryTabs = secondaryTabsModule.useCenterTabs;
assert.deepEqual(secondaryTabs.getState().tabs, []);
secondaryTabs.setState({
  tabs: [{ id: "s:existing", kind: "session", title: "Existing", sessionId: "existing" }],
  activeId: "s:existing",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.44,
});
secondaryTabsModule.persistCurrentCenterTabsPayload();
const secondaryTabBytes = values.get("centerTabs:secondary");
const inserted = secondaryTabsModule.insertTransferredTabs(
  transferPayload,
  { kind: "strip-end" },
  { persist: false },
);
assert.equal(inserted.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:local_one",
]);
assert.equal(values.get("centerTabs:secondary"), secondaryTabBytes);
const stagedDestinationJournal = journalEntry("destination-store-stage");
stagedDestinationJournal.beforeCenterTabs = inserted.before;
stagedDestinationJournal.afterCenterTabs = inserted.after;
assert.equal(writeTransferJournal(
  stagedDestinationJournal,
  "secondary",
), true);
assert.deepEqual(
  readTransferJournal("secondary").entries["destination-store-stage"]
    .afterCenterTabs,
  inserted.after,
);
assert.equal(
  readTransferJournal("source").entries["destination-store-stage"],
  undefined,
);

const removed = secondaryTabsModule.removeTransferredTabs(
  ["s:local_one"],
  { persist: false },
);
assert.equal(removed.ok, true);
assert.equal(removed.empty, false);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), ["s:existing"]);
assert.equal(values.get("centerTabs:secondary"), secondaryTabBytes);
const realTransferPayload = {
  ...transferPayload,
  tabs: [{ id: "s:real", kind: "session", title: "Real", sessionId: "real" }],
  source: { windowId: "source", kind: "tab" },
  chats: [{ chatKey: "real", wasActive: false }],
};
assert.equal(secondaryTabsModule.insertTransferredTabs(
  realTransferPayload,
  { kind: "strip-end" },
  { persist: false },
).ok, true);
assert.equal(secondaryTabsModule.removeTransferredTabs(
  ["s:real"],
  { persist: false },
).ok, true);
assert.equal(
  secondaryTabsModule.sessionAckIsActive("real"),
  true,
  "transfer removal must not create a close tombstone",
);
assert.equal(values.get("centerTabs:secondary"), secondaryTabBytes);

const groupedPayload = {
  ...transferPayload,
  tabs: [
    { id: "w:left", kind: "web", title: "Left", url: "https://left.test/" },
    { id: "w:right", kind: "web", title: "Right", url: "https://right.test/" },
  ],
  source: {
    windowId: "source",
    kind: "group",
    groupId: "g:source",
    memberIndex: 0,
    memberIds: ["w:left", "w:right"],
    visibleIds: ["w:left", "w:right"],
    focusedId: "w:right",
  },
};
const grouped = secondaryTabsModule.insertTransferredTabs(
  groupedPayload,
  { kind: "before", targetTabId: "s:existing" },
  { persist: false },
);
assert.equal(grouped.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "w:left",
  "w:right",
  "s:existing",
]);
assert.deepEqual(secondaryTabs.getState().groups[0], {
  id: "g:source",
  memberIds: ["w:left", "w:right"],
  visibleIds: ["w:left", "w:right"],
  focusedId: "w:right",
});
assert.equal(groupedPayload.source.memberIndex, 0);

const duplicateBefore = structuredClone(secondaryTabs.getState().tabs);
assert.deepEqual(
  secondaryTabsModule.validateTransferredTabs(
    {
      ...transferPayload,
      tabs: [groupedPayload.tabs[0]],
      source: { windowId: "source", kind: "tab" },
    },
    { kind: "strip-end" },
  ),
  { ok: false, reason: "duplicate", duplicateId: "w:left" },
);
assert.deepEqual(secondaryTabs.getState().tabs, duplicateBefore);

const fourthMember = secondaryTabsModule.validateTransferredTabs(
  {
    ...groupedPayload,
    tabs: [
      { id: "w:three", kind: "web", title: "Three", url: "https://three.test/" },
      { id: "w:four", kind: "web", title: "Four", url: "https://four.test/" },
    ],
    source: {
      ...groupedPayload.source,
      groupId: "g:other",
      memberIds: ["w:three", "w:four"],
      visibleIds: ["w:three", "w:four"],
      focusedId: "w:four",
    },
  },
  { kind: "merge", targetTabId: "w:left", memberIndex: 1 },
);
assert.deepEqual(fourthMember, { ok: false, reason: "group-full" });

const groupedRemoval = secondaryTabsModule.removeTransferredTabs(
  ["w:left", "w:right"],
  { persist: false },
);
assert.equal(groupedRemoval.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), ["s:existing"]);

const groupedAfter = secondaryTabsModule.insertTransferredTabs(
  groupedPayload,
  { kind: "after", targetTabId: "s:existing" },
  { persist: false },
);
assert.equal(groupedAfter.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "w:left",
  "w:right",
]);
secondaryTabsModule.replaceCenterTabsPayload(groupedAfter.before, { persist: false });
const groupedMerge = secondaryTabsModule.insertTransferredTabs(
  groupedPayload,
  { kind: "merge", targetTabId: "s:existing", memberIndex: 1 },
  { persist: false },
);
assert.equal(groupedMerge.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "w:left",
  "w:right",
]);
assert.deepEqual(secondaryTabs.getState().groups[0], {
  id: "g:source",
  memberIds: ["s:existing", "w:left", "w:right"],
  visibleIds: ["w:left", "w:right"],
  focusedId: "w:right",
});
assert.equal(secondaryTabsModule.removeTransferredTabs(
  ["w:left"],
  { persist: false },
).ok, false, "a group cannot be partially removed");

const {
  applyFileDraftSnapshot,
  fileDrafts,
  snapshotFileDrafts,
} = await import("../lib/state/files-shared.ts");
const fileDraft = { draft: "edited", baselineContent: "base", baselineMtime: 4 };
fileDrafts.set("project:file.txt", fileDraft);
const fileSnapshot = snapshotFileDrafts(["project:file.txt", "project:missing.txt"]);
fileDrafts.delete("project:file.txt");
fileDrafts.set("project:missing.txt", fileDraft);
applyFileDraftSnapshot(fileSnapshot);
assert.deepEqual(fileDrafts.get("project:file.txt"), fileDraft);
assert.equal(fileDrafts.has("project:missing.txt"), false);

delete globalThis.window.openprogramDesktop;
values.clear();

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const {
  SPLIT_CHAT_MIN_WIDTH,
  SPLIT_DIVIDER_WIDTH,
  SPLIT_WEB_MIN_WIDTH,
  clampSplitRatioForWidth,
  createSplitLayoutMeasureScheduler,
  isSplitLayoutAvailable,
} = await import("../lib/split-layout.ts");
const {
  registerVisibleWebTabBounds,
  removeVisibleWebTabBounds,
  isDesktopSplitLayoutAvailable,
  isWebTabReady,
  restorePriorActiveTabAfterFailedWebOpen,
  setDesktopSplitLayoutAvailable,
  setWebTabReady,
  visibleWebTab,
  waitForWebTabReady,
} = await import("../lib/desktop-bridge.ts");
const {
  focusCenterTabGroupMember,
  resolveCenterTabPanes,
} = await import("../lib/state/center-tab-groups.ts");

assert.equal(SPLIT_CHAT_MIN_WIDTH, 360);
assert.equal(SPLIT_WEB_MIN_WIDTH, 480);
assert.equal(SPLIT_DIVIDER_WIDTH, 6);
const thresholdWidth = 846;
const thresholdRatio = SPLIT_CHAT_MIN_WIDTH / thresholdWidth;
assert.equal(clampSplitRatioForWidth(0.44, thresholdWidth), thresholdRatio);
assert.equal(clampSplitRatioForWidth(0.70, thresholdWidth), thresholdRatio);
assert.equal(clampSplitRatioForWidth(0.44, 1200), 0.44);
assert.equal(
  clampSplitRatioForWidth(0.70, 1200),
  (1200 - SPLIT_WEB_MIN_WIDTH - SPLIT_DIVIDER_WIDTH) / 1200,
);
const preferredRatio = 0.527651858567543;
assert.equal(isSplitLayoutAvailable(864), true);
assert.equal(clampSplitRatioForWidth(preferredRatio, 864), 0.4375);
assert.equal(
  clampSplitRatioForWidth(preferredRatio, 1200),
  preferredRatio,
  "restoring space must recompute from the preferred ratio",
);
assert.equal(isSplitLayoutAvailable(463), false);

const paneTabs = [
  { id: "s:a", kind: "session", title: "A", sessionId: "a" },
  { id: "s:b", kind: "session", title: "B", sessionId: "b" },
  { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
  { id: "w:two", kind: "web", title: "Two", url: "https://two.test/" },
];
const sessionPair = {
  id: "g:sessions",
  memberIds: ["s:a", "s:b"],
  visibleIds: ["s:a", "s:b"],
  focusedId: "s:b",
};
const sessionPanes = resolveCenterTabPanes(sessionPair, paneTabs, "s:b");
assert.equal(sessionPanes.length, 1, "two sessions share the singleton chat pane");
assert.equal(sessionPanes[0].kind, "session");
assert.equal(sessionPanes[0].activeTabId, "s:b");

const sessionWebPanes = resolveCenterTabPanes({
  ...sessionPair,
  memberIds: ["s:a", "w:one"],
  visibleIds: ["s:a", "w:one"],
  focusedId: "w:one",
}, paneTabs, "w:one");
assert.deepEqual(sessionWebPanes.map((pane) => pane.kind), ["session", "tab"]);

const webPairPanes = resolveCenterTabPanes({
  id: "g:webs",
  memberIds: ["w:one", "w:two"],
  visibleIds: ["w:one", "w:two"],
  focusedId: "w:two",
}, paneTabs, "w:two");
assert.deepEqual(webPairPanes.map((pane) => pane.tabId), ["w:one", "w:two"]);

const hiddenThird = focusCenterTabGroupMember({
  tabIds: paneTabs.map((tab) => tab.id),
  groups: [{
    id: "g:hidden",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "s:a",
  }],
}, "g:hidden", "w:two").groups[0];
const hiddenThirdPanes = resolveCenterTabPanes(hiddenThird, paneTabs, "w:two");
assert.equal(hiddenThirdPanes.length <= 2, true);
assert.equal(hiddenThirdPanes.some((pane) => pane.tabId === "w:two"), true);
const narrowPanes = resolveCenterTabPanes(
  undefined,
  paneTabs,
  hiddenThird.focusedId,
);
assert.deepEqual(narrowPanes, [{ key: "w:two", kind: "tab", tabId: "w:two" }]);

{
  const syncCalls = [];
  const singletonShowCalls = [];
  const bridge = {
    webTab: {
      syncVisible(items) { syncCalls.push(items); },
      show(id) { singletonShowCalls.push(id); },
    },
  };
  const boundsOne = { x: 0, y: 40, width: 500, height: 600 };
  const boundsTwo = { x: 506, y: 40, width: 600, height: 600 };
  registerVisibleWebTabBounds(bridge, "w:one", boundsOne);
  registerVisibleWebTabBounds(bridge, "w:two", boundsTwo);
  await Promise.resolve();
  assert.equal(syncCalls.length, 1, "one scheduled flush publishes both panes");
  assert.deepEqual(syncCalls[0], [
    { id: "w:one", bounds: boundsOne },
    { id: "w:two", bounds: boundsTwo },
  ]);
  removeVisibleWebTabBounds(bridge, "w:one");
  await Promise.resolve();
  assert.deepEqual(syncCalls.at(-1), [{ id: "w:two", bounds: boundsTwo }]);
  assert.deepEqual(
    singletonShowCalls,
    [],
    "two singleton show calls are not collection synchronization",
  );
  removeVisibleWebTabBounds(bridge, "w:two");
  await Promise.resolve();
}

function createTimingHarness() {
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
        assert.equal(delay, 0);
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
}

{
  const timing = createTimingHarness();
  const published = [];
  let width = 1200;
  const scheduler = createSplitLayoutMeasureScheduler(
    () => published.push(width),
    timing.deps,
  );
  scheduler.schedule();
  width = 864;
  assert.equal(timing.runTimer(), true);
  assert.deepEqual(published, [864]);
  assert.equal(
    timing.frames.size,
    0,
    "timer completion cancels the pending frame",
  );
}

{
  const timing = createTimingHarness();
  const published = [];
  let width = 1200;
  const scheduler = createSplitLayoutMeasureScheduler(
    () => published.push(width),
    timing.deps,
  );
  scheduler.schedule();
  const staleFrame = timing.frames.keys().next().value;
  const staleTimer = timing.timers.keys().next().value;
  width = 864;
  scheduler.schedule();
  assert.equal(timing.frames.has(staleFrame), false);
  assert.equal(timing.timers.has(staleTimer), false);
  assert.equal(timing.runTimer(), true);
  timing.runFrame();
  assert.deepEqual(
    published,
    [864],
    "continuous scheduling only publishes the latest width",
  );
}

{
  const timing = createTimingHarness();
  const published = [];
  const scheduler = createSplitLayoutMeasureScheduler(
    () => published.push(1200),
    timing.deps,
  );
  scheduler.schedule();
  assert.equal(timing.runFrame(), true);
  assert.equal(timing.runTimer(), false);
  assert.deepEqual(
    published,
    [1200],
    "frame completion cancels the timer fallback",
  );
}

{
  const timing = createTimingHarness();
  const published = [];
  const scheduler = createSplitLayoutMeasureScheduler(
    () => published.push(1200),
    timing.deps,
  );
  scheduler.schedule();
  scheduler.cancel();
  timing.runFrame();
  timing.runTimer();
  assert.deepEqual(published, [], "cleanup prevents pending measurements");
}

setWebTabReady("already-ready", true);
assert.equal(isWebTabReady("already-ready"), true);
assert.equal(await waitForWebTabReady("already-ready", 10), true);

const waiting = waitForWebTabReady("becomes-ready", 10);
setWebTabReady("becomes-ready", true);
assert.equal(await waiting, true);

assert.equal(await waitForWebTabReady("never-ready", 1), false);

const clearingWaiter = waitForWebTabReady("clear-while-waiting", 5);
setWebTabReady("clear-while-waiting", false);
assert.equal(await clearingWaiter, false);

setWebTabReady("clear-ready", true);
setWebTabReady("clear-ready", false);
assert.equal(isWebTabReady("clear-ready"), false);

setWebTabReady("w:one", true);
setWebTabReady("w:two", true);
useCenterTabs.setState({
  tabs: paneTabs,
  groups: [{
    id: "g:visible-webs",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["w:one", "w:two"],
    focusedId: "w:two",
  }],
  activeId: "w:two",
});
assert.equal(visibleWebTab()?.id, "w:two", "focused visible web wins");
useCenterTabs.setState({
  groups: [{
    id: "g:visible-webs",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "s:a",
  }],
  activeId: "s:a",
});
assert.equal(
  visibleWebTab()?.id,
  "w:one",
  "session focus selects the first resolved visible web pane",
);
assert.notEqual(
  visibleWebTab()?.id,
  "w:two",
  "a hidden group member must not be selected",
);
useCenterTabs.setState({ groups: [], activeId: "w:two" });
assert.equal(visibleWebTab()?.id, "w:two", "ungrouped active web is selected");

setDesktopSplitLayoutAvailable(true);
assert.equal(isDesktopSplitLayoutAvailable(), true);
setDesktopSplitLayoutAvailable(false);
assert.equal(isDesktopSplitLayoutAvailable(), false);

const priorSessionId = "s:prior";
const fallbackWebId = "w:https://fallback.example/";
const userSelectedId = "s:user-selected";
const rollbackTabs = [
  { id: priorSessionId, kind: "session", title: "Prior", sessionId: "prior" },
  {
    id: fallbackWebId,
    kind: "web",
    title: "fallback.example",
    url: "https://fallback.example/",
  },
  {
    id: userSelectedId,
    kind: "session",
    title: "User selected",
    sessionId: "user-selected",
  },
];
useCenterTabs.setState({ tabs: rollbackTabs, activeId: fallbackWebId });
restorePriorActiveTabAfterFailedWebOpen(priorSessionId, fallbackWebId);
assert.equal(
  useCenterTabs.getState().activeId,
  priorSessionId,
  "failed fallback restores the prior tab while the opened web tab is active",
);
useCenterTabs.setState({ tabs: rollbackTabs, activeId: userSelectedId });
restorePriorActiveTabAfterFailedWebOpen(priorSessionId, fallbackWebId);
assert.equal(
  useCenterTabs.getState().activeId,
  userSelectedId,
  "failed fallback must preserve a tab selected by the user during activation",
);
useCenterTabs.setState({ tabs: rollbackTabs, activeId: null });
restorePriorActiveTabAfterFailedWebOpen(priorSessionId, null);
assert.equal(
  useCenterTabs.getState().activeId,
  null,
  "a missing fallback web id must not restore the prior tab",
);

const webTabPaneSource = await readFile(
  new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url),
  "utf8",
);
const appShellSource = await readFile(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);
const tabStripSource = await readFile(
  new URL("../components/center-tabs/center-tab-strip.tsx", import.meta.url),
  "utf8",
);
const tabsCssSource = await readFile(
  new URL("../components/center-tabs/center-tabs.module.css", import.meta.url),
  "utf8",
);
const baseCssSource = await readFile(
  new URL("../app/styles/base.css", import.meta.url),
  "utf8",
);
const desktopBridgeSource = await readFile(
  new URL("../lib/desktop-bridge.ts", import.meta.url),
  "utf8",
);

assert.match(desktopBridgeSource, /function visibleWebTab\(\)/);
assert.match(
  desktopBridgeSource,
  /resolveCenterTabPanes\(group, state\.tabs, state\.activeId\)/,
);
assert.match(
  desktopBridgeSource,
  /group\.focusedId/,
);
assert.match(desktopBridgeSource, /const visibleWebBounds = new Map/);
assert.match(desktopBridgeSource, /targetBridge\.webTab\.syncVisible/);
assert.match(desktopBridgeSource, /state\.openWebTabInSplit\(d\.url\)/);
assert.match(desktopBridgeSource, /waitForWebTabReady\(id, 2000\)/);
assert.match(
  desktopBridgeSource,
  /if \(!split && !routeVisible\) \{\s*const routed = showCenterSurface\(\);\s*if \(!routed\)/,
  "split opens and existing /s or /chat fallbacks must preserve their route",
);
assert.doesNotMatch(
  desktopBridgeSource,
  /openWebTab\(d\.url\);\s*const routed = showCenterSurface\(\);/,
  "webtab.command must not unconditionally navigate a session route to /chat",
);

assert.match(appShellSource, /splitRatio/);
assert.match(
  appShellSource,
  /findCenterTabGroup,[\s\S]*?resolveCenterTabPanes/,
  "AppShell must use the shared group and pane resolver",
);
assert.match(appShellSource, /const tabs = useCenterTabs\(\(s\) => s\.tabs\);/);
assert.match(appShellSource, /const groups = useCenterTabs\(\(s\) => s\.groups\);/);
assert.match(appShellSource, /const activeId = useCenterTabs\(\(s\) => s\.activeId\);/);
assert.match(appShellSource, /const activeGroup = activeId[\s\S]*?findCenterTabGroup\(groups, activeId\)/);
assert.match(
  appShellSource,
  /const splitAvailable = isSplitLayoutAvailable\(centerBodyWidth\);/,
  "split availability must use the measured center-body width",
);
assert.match(
  appShellSource,
  /const compoundPanes = resolveCenterTabPanes\(activeGroup, tabs, activeId\);/,
);
assert.match(
  appShellSource,
  /const focusedPanes = resolveCenterTabPanes\([\s\S]*?undefined,[\s\S]*?tabs,[\s\S]*?activeGroup\?\.focusedId \?\? activeId/,
  "narrow layout must resolve only the focused member",
);
assert.match(
  appShellSource,
  /const panes =\s*isDesktop && activeGroup && splitAvailable\s*\? compoundPanes\s*:\s*focusedPanes;/,
);
assert.match(
  appShellSource,
  /const showDivider = panes\.length === 2;/,
  "divider follows rendered panes, not visible member count",
);
assert.doesNotMatch(appShellSource, /visibleTabs\.length === 2/);
assert.equal(
  appShellSource.match(/<PageShell page="chat" \/>/g)?.length,
  1,
  "the chat shell must remain a mounted singleton",
);
assert.match(
  appShellSource,
  /const sessionPaneIndex = panes\.findIndex\(\(pane\) => pane\.kind === "session"\);/,
);
assert.match(appShellSource, /panes\.map\(\(pane, index\) =>/);
assert.match(appShellSource, /if \(pane\.kind === "session"\) return null;/);
assert.match(appShellSource, /tab\.kind === "web"[\s\S]*?<WebTabPane/);
assert.match(appShellSource, /"center-split-primary"/);
assert.match(appShellSource, /className="center-split-divider"/);
assert.match(appShellSource, /"center-split-secondary"/);
assert.match(appShellSource, /setDesktopSplitLayoutAvailable/);
assert.match(appShellSource, /clampSplitRatioForWidth/);
assert.match(appShellSource, /const effectiveSplitRatio = clampSplitRatioForWidth/);
assert.doesNotMatch(
  appShellSource,
  /if \(effectiveSplitRatio !== splitRatio\) setSplitRatio\(effectiveSplitRatio\);/,
  "container constraints must not overwrite the preferred split ratio",
);
const splitMeasureSource = appShellSource.slice(
  appShellSource.indexOf("const centerBodyRef"),
  appShellSource.indexOf("const splitAvailable"),
);
assert.match(
  splitMeasureSource,
  /createSplitLayoutMeasureScheduler/,
  "AppShell must use the behavior-tested measurement scheduler",
);
assert.match(
  splitMeasureSource,
  /new ResizeObserver\(measureScheduler\.schedule\)/,
);
assert.match(
  splitMeasureSource,
  /window\.addEventListener\("resize", measureScheduler\.schedule\)/,
);
assert.match(
  splitMeasureSource,
  /window\.removeEventListener\("resize", measureScheduler\.schedule\)/,
);
assert.match(splitMeasureSource, /node\.closest\("\.app"\)/);
assert.match(
  splitMeasureSource,
  /layoutRoot\?\.addEventListener\(\s*"transitionend",\s*measureScheduler\.schedule,?\s*\)/,
);
assert.match(
  splitMeasureSource,
  /layoutRoot\?\.removeEventListener\(\s*"transitionend",\s*measureScheduler\.schedule,?\s*\)/,
);
assert.match(splitMeasureSource, /measureScheduler\.cancel\(\)/);
assert.doesNotMatch(splitMeasureSource, /setInterval/);
assert.match(appShellSource, /width: `\$\{effectiveSplitRatio \* 100\}%`/);
assert.match(appShellSource, /aria-valuenow=\{Math\.round\(effectiveSplitRatio \* 100\)\}/);
assert.match(appShellSource, /onPointerDown=/);
assert.match(appShellSource, /onPointerMove=/);
assert.match(appShellSource, /role="separator"/);
assert.match(appShellSource, /aria-orientation="vertical"/);
assert.match(appShellSource, /"ArrowLeft"/);
assert.match(appShellSource, /"ArrowRight"/);
assert.match(appShellSource, /0\.02/);
assert.match(appShellSource, /sessionStore\.setCurrentConv\(sid\);/);
assert.match(appShellSource, /sessionStore\.setCurrentDraft\(active\.sessionId\);/);

const activeFocusEffect = tabStripSource.slice(
  tabStripSource.indexOf("// Active center-tab focus"),
  tabStripSource.indexOf("function onTabClick"),
);
assert.match(
  tabStripSource,
  /const \[sessionActivationRequest, setSessionActivationRequest\] = useState\(0\);/,
);
assert.match(activeFocusEffect, /useEffect\(\(\) =>/);
assert.match(activeFocusEffect, /activateSession\(tab\);/);
assert.match(activeFocusEffect, /\[activeId, sessionActivationRequest\]/);
const onTabClickSource = tabStripSource.slice(
  tabStripSource.indexOf("function onTabClick"),
  tabStripSource.indexOf("function onOpenNewTab"),
);
assert.match(onTabClickSource, /tab\.kind === "session" && tab\.id === activeId/);
assert.match(
  onTabClickSource,
  /setSessionActivationRequest\(\(request\) => request \+ 1\);/,
);
assert.doesNotMatch(onTabClickSource, /activateSession/);
const finishCloseSource = tabStripSource.slice(
  tabStripSource.indexOf("function finishClose"),
  tabStripSource.indexOf("function labelOf"),
);
assert.doesNotMatch(finishCloseSource, /activateSession/);

assert.match(webTabPaneSource, /text\("Open split view", "打开分屏"\)/);
assert.match(webTabPaneSource, /text\("Exit split view", "退出分屏"\)/);
assert.match(webTabPaneSource, /setSplitWebTab\(null\)/);
assert.match(webTabPaneSource, /setRightDockOpen\(false\)/);
assert.match(webTabPaneSource, /openDraftSessionTab\(\)/);
assert.match(webTabPaneSource, /\.newSession\?\.\(draftId\)/);
assert.match(
  webTabPaneSource,
  /const title = sessionState\.conversations\[routeSessionId\]\?\.title \?\? "";/,
);
assert.match(webTabPaneSource, /state\.openSessionTab\(routeSessionId, title\);/);
assert.match(
  webTabPaneSource,
  /if \(openedSession\) openedState\.setActive\(openedSession\.id\);/,
);
assert.match(
  webTabPaneSource,
  /if \(routeSession\) \{[\s\S]*?state\.setActive\(routeSession\.id\);[\s\S]*?\} else if \(routeSessionId\) \{[\s\S]*?state\.openSessionTab\(routeSessionId, title\);[\s\S]*?\} else if \(activeDraft\) \{[\s\S]*?state\.setActive\(activeDraft\.id\);[\s\S]*?\} else \{[\s\S]*?state\.openDraftSessionTab\(\)/,
);

assert.doesNotMatch(tabStripSource, /splitPinned|data-split-pinned/);
assert.match(tabStripSource, /active=\{tab\.id === activeId\}/);
assert.doesNotMatch(tabsCssSource, /\[data-split-pinned="true"\]/);
assert.match(baseCssSource, /\.center-split-divider\s*\{[^}]*width:\s*6px;/s);
assert.match(baseCssSource, /\.center-split-primary\s*\{[^}]*min-width:\s*360px;/s);
assert.match(baseCssSource, /\.center-split-secondary\s*\{[^}]*min-width:\s*480px;/s);
assert.match(webTabPaneSource, /setWebTabReady/);
assert.doesNotMatch(webTabPaneSource, /bridge\.webTab\.(?:show|hide|setBounds)\(/);
assert.doesNotMatch(
  webTabPaneSource,
  /bridge\.webTab\.navigate\(tabId, viewUrlRef\.current\);/,
);
assert.match(webTabPaneSource, /const occluded = !!document\.querySelector/);
assert.match(
  webTabPaneSource,
  /\[role="dialog"\], \.branches-merge-modal-backdrop, \[data-native-view-occluder="true"\]/,
);
assert.match(
  webTabPaneSource,
  /const roundedBounds = \{\s*x: Math\.round\(r\.left\),\s*y: Math\.round\(r\.top\),\s*width: Math\.round\(r\.width\),\s*height: Math\.round\(r\.height\),\s*\};/s,
);
assert.match(
  webTabPaneSource,
  /if \(occluded \|\| roundedBounds\.width <= 0 \|\| roundedBounds\.height <= 0\) \{\s*removeVisibleWebTabBounds\(bridge, tabId\);\s*setWebTabReady\(tabId, false\);/s,
);
assert.match(
  webTabPaneSource,
  /registerVisibleWebTabBounds\(bridge, tabId, roundedBounds\);\s*setWebTabReady\(tabId, true\);/s,
);
assert.match(
  webTabPaneSource,
  /return \(\) => \{[\s\S]*?removeVisibleWebTabBounds\(bridge, tabId\);/s,
);
assert.match(webTabPaneSource, /new MutationObserver\(report\)/);
assert.match(
  webTabPaneSource,
  /mo\.observe\(document\.body, \{ subtree: true, childList: true, attributes: true \}\);/,
);
assert.match(webTabPaneSource, /mo\.disconnect\(\);/);

const fileTilesSource = await readFile(
  new URL("../components/chat/composer/attach/file-tiles.tsx", import.meta.url),
  "utf8",
);
assert.match(fileTilesSource, /data-native-view-occluder="true"/);

useCenterTabs.setState({
  tabs: [{ id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" }],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.44,
});
const id = useCenterTabs.getState().openWebTabInSplit("https://example.com/");
assert.equal(id, "w:https://example.com/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.equal(useCenterTabs.getState().splitWebTabId, id);
assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:chat", id]);
assert.equal(JSON.parse(values.get("centerTabs")).splitWebTabId, id);
assert.equal(JSON.parse(values.get("centerTabs")).splitRatio, 0.44);
useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id, kind: "web", title: "other.example", url: "https://other.example/" },
  ],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: id,
  splitRatio: 0.44,
});
useCenterTabs.getState().openWebTabInSplit("https://example.com/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.deepEqual(useCenterTabs.getState().tabs.find((tab) => tab.id === id), {
  id,
  kind: "web",
  title: "example.com",
  url: "https://example.com/",
});
useCenterTabs.getState().setSplitRatio(5);
assert.equal(useCenterTabs.getState().splitRatio, 0.70);
assert.equal(JSON.parse(values.get("centerTabs")).splitWebTabId, id);
assert.equal(JSON.parse(values.get("centerTabs")).splitRatio, 0.70);
useCenterTabs.getState().setSplitWebTab(null);
assert.equal(useCenterTabs.getState().splitWebTabId, null);
useCenterTabs.getState().setSplitWebTab(id);
assert.equal(useCenterTabs.getState().splitWebTabId, id);
useCenterTabs.getState().closeTab(id);
assert.equal(useCenterTabs.getState().splitWebTabId, null);
assert.equal(JSON.parse(values.get("centerTabs")).splitWebTabId, null);
assert.equal(JSON.parse(values.get("centerTabs")).splitRatio, 0.70);

values.set("centerTabs", JSON.stringify({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id, kind: "web", title: "example.com", url: "https://example.com/" },
  ],
  activeId: "s:chat",
}));
values.set("openprogram.webSplit", JSON.stringify({ tabId: id, ratio: 5 }));
const { useCenterTabs: restoredSplit } = await import(
  "../lib/state/center-tabs-store.ts?restore-valid-split",
);
assert.equal(restoredSplit.getState().splitWebTabId, id);
assert.equal(restoredSplit.getState().splitRatio, 0.70);
assert.deepEqual(restoredSplit.getState().groups[0].memberIds, ["s:chat", id]);

values.set("centerTabs", JSON.stringify({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id, kind: "web", title: "example.com", url: "https://example.com/" },
  ],
  activeId: "s:chat",
}));
values.set("openprogram.webSplit", JSON.stringify({ tabId: "s:chat", ratio: 0 }));
const { useCenterTabs: restoredInvalidSplit } = await import(
  "../lib/state/center-tabs-store.ts?restore-invalid-split",
);
assert.equal(restoredInvalidSplit.getState().splitWebTabId, null);
assert.equal(restoredInvalidSplit.getState().splitRatio, 0.30);

console.log("web-split checks passed");
