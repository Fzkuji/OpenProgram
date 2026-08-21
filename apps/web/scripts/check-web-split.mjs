import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

import { readCenterTabStripSource } from "./center-tab-strip-source.mjs";

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
    // Extensionless relative imports between source modules (Node needs the
    // extension; TypeScript and the Next build resolve them on their own).
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      // Append to href, not to pathname: pathname is percent-encoded and
      // re-parsing it against the same base double-encodes any space in the
      // repo path.
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
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
  splitRatio: 0.45,
};
const emptySessionSnapshot = {
  activeChatKey: null,
  currentSessionId: null,
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
const sessionDraftPersistence = await import("../lib/session-draft-persistence.ts");
const pendingProjection = await import("../lib/pending-transfer-projection.ts");

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
globalThis.localStorage.setItem = () => {};
assert.equal(writeTransferJournal(journalEntry("silent"), "journal-fail"), false);
assert.deepEqual(readTransferJournal("journal-fail"), { version: 1, entries: {} });
globalThis.localStorage.setItem = originalSetItem;

function recoveryHarness() {
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
    journalEntry("destination-staged-explicit-window"),
    "destination-staged",
    recovery.handlers,
    "recovery-window",
  ), true);
  assert.deepEqual(recovery.calls, [
    ["tabs", "s:local_one", false],
    ["session", "local_one", false],
    ["files", 0],
    ["bridge", 0],
    ["accepted", "destination-staged-explicit-window"],
  ]);
  assert.equal(
    pendingProjection.pendingTransfer(
      "destination-staged-explicit-window",
      "recovery-window",
    )?.token,
    "destination-staged-explicit-window",
  );
  assert.equal(
    pendingProjection.pendingTransfer("destination-staged-explicit-window", "main"),
    undefined,
  );
  pendingProjection.unregisterPendingTransfer(
    "destination-staged-explicit-window",
    "recovery-window",
  );
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
  pendingProjection.unregisterPendingTransfer("awaiting-source", "main");
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
    return originalApplyTabs(payload, options);
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

{
  const token = "finalize-phase-failure";
  const id = "journal-phase-failure";
  assert.equal(writeTransferJournal(journalEntry(token), id), true);
  globalThis.localStorage.setItem = (key, value) => {
    if (key === `openprogram.tabTransferJournal:${id}`) {
      throw new Error("journal phase quota");
    }
    originalSetItem(key, value);
  };
  assert.equal(finalizeTransferJournal(
    token,
    "commit",
    recoveryHarness().handlers,
    id,
  ), false);
  assert.equal(readTransferJournal(id).entries[token].phase, "staged");
  assert.equal(
    pendingProjection.pendingTransfer(token, id)?.token,
    token,
    "a phase-write failure must keep projecting the entry until recovery",
  );
  globalThis.localStorage.setItem = originalSetItem;
  pendingProjection.unregisterPendingTransfer(token, id);
  assert.equal(deleteTransferJournal(token, id), true);
}

for (const [status, role, missing] of [
  ["committed", "destination", "deleteJournal"],
  ["rolled-back", "source", "clearAccepted"],
  ["destination-staged", "destination", "rebuildAccepted"],
  ["awaiting-source", "source", "resumeSourceRemoved"],
]) {
  const recovery = recoveryHarness();
  delete recovery.handlers[missing];
  const entry = journalEntry(`missing-${missing}`, role);
  assert.equal(writeTransferJournal(entry, "missing-callbacks"), true);
  assert.equal(recoverTransferJournalEntry(entry, status, recovery.handlers), false);
  assert.deepEqual(recovery.calls, []);
  assert.ok(readTransferJournal("missing-callbacks").entries[entry.token]);
  assert.equal(
    pendingProjection.pendingTransfer(entry.token, "main")?.token,
    entry.token,
    "an unresolved recovery must keep projecting its provisional delta out",
  );
  pendingProjection.unregisterPendingTransfer(entry.token, "main");
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
channelDrafts.setDraftChannelChoice(secondarySessionModule.draftChoiceHost(), "local_one", {
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
  composerDrafts: {},
  composerSettingsBySession: {},
  pendingProjectsByChat: {},
});
secondarySessionModule.draftChoiceHost().__pendingChannelChoices = {};
secondarySessionModule.draftChoiceHost()._pendingChannelChoice = null;
secondarySessionModule.applySessionTransfer(sessionSnapshot, { persist: false });
assert.deepEqual(
  {
    activeChatKey: secondarySession.getState().activeChatKey,
    currentSessionId: secondarySession.getState().currentSessionId,
    composerDrafts: secondarySession.getState().composerDrafts,
    composerSettingsBySession: secondarySession.getState().composerSettingsBySession,
    pendingProjectsByChat: secondarySession.getState().pendingProjectsByChat,
    draftChannelChoices: secondarySessionModule.draftChoiceHost().__pendingChannelChoices,
  },
  {
    activeChatKey: originalSession.activeChatKey,
    currentSessionId: originalSession.currentSessionId,
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
  channelDrafts.draftChannelChoiceFor(secondarySessionModule.draftChoiceHost(), "local_one"),
  { channel: "slack", account_id: "team" },
);

const withoutKey = (map, key) => Object.fromEntries(
  Object.entries(map).filter(([candidate]) => candidate !== key),
);
values.clear();
globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "transfer-a" };
const sourceTransferModule = await import("../lib/session-store/index.ts?task3-transfer-a");
const sourceTransfer = sourceTransferModule.useSessionStore;
sourceTransfer.getState().setCurrentDraft("local_move");
sourceTransfer.getState().setComposerInput("move me");
sourceTransfer.getState().setComposerSettings({ tools: false, thinking: "medium" });
sourceTransfer.getState().setPendingProject("local_move", "project-move");
channelDrafts.setDraftChannelChoice(secondarySessionModule.draftChoiceHost(), "local_move", {
  channel: "discord",
  account_id: "move-account",
});
const sourceBeforeCommit = sourceTransferModule.snapshotSessionTransfer(["local_move"]);
sourceTransferModule.applySessionTransfer({
  ...sourceBeforeCommit,
  activeChatKey: null,
  currentSessionId: null,
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
const destinationTransferModule = await import("../lib/session-store/index.ts?task3-transfer-b");
const destinationBeforeCommit = destinationTransferModule.snapshotSessionTransfer([
  "local_move",
]);
destinationTransferModule.applySessionTransfer({
  ...destinationBeforeCommit,
  activeChatKey: "local_move",
  currentSessionId: null,
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
assert.deepEqual(
  sessionDraftPersistence.readSessionDraftState().draftChannelChoices.local_move,
  { channel: "discord", account_id: "move-account" },
);

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-a" };
const rollbackSourceModule = await import("../lib/session-store/index.ts?task3-rollback-a");
rollbackSourceModule.useSessionStore.getState().setCurrentDraft("local_keep");
rollbackSourceModule.useSessionStore.getState().setComposerInput("keep me");
const rollbackSourceBefore = rollbackSourceModule.snapshotSessionTransfer(["local_keep"]);
rollbackSourceModule.applySessionTransfer({
  ...rollbackSourceBefore,
  activeChatKey: null,
  composerDrafts: {},
}, { persist: false });
rollbackSourceModule.applySessionTransfer(rollbackSourceBefore, { persist: true });

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "rollback-b" };
const rollbackDestinationModule = await import(
  "../lib/session-store/index.ts?task3-rollback-b"
);
const rollbackDestinationBefore = rollbackDestinationModule.snapshotSessionTransfer([
  "local_keep",
]);
rollbackDestinationModule.applySessionTransfer({
  ...rollbackDestinationBefore,
  activeChatKey: "local_keep",
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
  splitRatio: 0.45,
});
assert.equal(secondaryTabsModule.persistCurrentCenterTabsPayload(), true);
const secondaryTabBytes = values.get("centerTabs:secondary");
secondarySessionModule.persistCurrentSessionTransfer(["local_one"]);
const effectSessionBytes = values.get("openprogram.sessionDraftState:secondary");
const effectSessionSnapshot = secondarySessionModule.snapshotSessionTransfer([
  "local_one",
]);
const validatedDestination = secondaryTabsModule.validateTransferredTabs(
  transferPayload,
  { kind: "strip-end" },
);
assert.equal(validatedDestination.ok, true);
const orderedDestinationEntry = journalEntry("destination-store-stage");
orderedDestinationEntry.beforeCenterTabs = {
  version: 2,
  tabs: secondaryTabs.getState().tabs,
  activeId: secondaryTabs.getState().activeId,
  groups: secondaryTabs.getState().groups,
  splitWebTabId: secondaryTabs.getState().splitWebTabId,
  splitRatio: secondaryTabs.getState().splitRatio,
};
orderedDestinationEntry.afterCenterTabs = validatedDestination.after;
orderedDestinationEntry.beforeSession = effectSessionSnapshot;
orderedDestinationEntry.afterSession = effectSessionSnapshot;

const beforeFailedStage = structuredClone(secondaryTabs.getState().tabs);
let rejectedStages = 0;
globalThis.localStorage.setItem = (key, value) => {
  if (key === "openprogram.tabTransferJournal:secondary") throw new Error("quota");
  originalSetItem(key, value);
};
assert.equal(stageTransferMutation(
  { ...orderedDestinationEntry, token: "destination-throw" },
  () => secondaryTabsModule.insertTransferredTabs(
    transferPayload,
    { kind: "strip-end" },
    { persist: false },
  ),
  () => { rejectedStages += 1; },
  "secondary",
), false);
assert.deepEqual(secondaryTabs.getState().tabs, beforeFailedStage);
globalThis.localStorage.setItem = (key, value) => {
  if (key !== "openprogram.tabTransferJournal:secondary") {
    originalSetItem(key, value);
  }
};
assert.equal(stageTransferMutation(
  { ...orderedDestinationEntry, token: "destination-silent" },
  () => secondaryTabsModule.insertTransferredTabs(
    transferPayload,
    { kind: "strip-end" },
    { persist: false },
  ),
  () => { rejectedStages += 1; },
  "secondary",
), false);
assert.deepEqual(secondaryTabs.getState().tabs, beforeFailedStage);
assert.equal(rejectedStages, 2);
globalThis.localStorage.setItem = originalSetItem;

let inserted;
assert.equal(stageTransferMutation(
  orderedDestinationEntry,
  () => {
    assert.ok(readTransferJournal("secondary").entries[orderedDestinationEntry.token]);
    inserted = secondaryTabsModule.insertTransferredTabs(
      transferPayload,
      { kind: "strip-end" },
      { persist: false },
    );
    return inserted.ok;
  },
  () => { throw new Error("unexpected destination rejection"); },
  "secondary",
), true);
assert.equal(inserted.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:local_one",
]);
assert.equal(values.get("centerTabs:secondary"), secondaryTabBytes);
assert.deepEqual(
  readTransferJournal("secondary").entries["destination-store-stage"]
    .afterCenterTabs,
  inserted.after,
);
assert.equal(
  readTransferJournal("source").entries["destination-store-stage"],
  undefined,
);

secondaryTabs.getState().openSessionTab("existing", "Effect write");
secondarySession.getState().setCurrentDraft("local_one");
channelDrafts.setDraftChannelChoice(secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "effect-channel",
  account_id: "effect-account",
});
const effectProjectedCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(effectProjectedCenter.tabs.map((tab) => tab.id), ["s:existing"]);
assert.equal(effectProjectedCenter.tabs[0].title, "Effect write");
assert.equal(
  values.get("openprogram.sessionDraftState:secondary"),
  effectSessionBytes,
  "affected path/channel effects must remain projected out",
);
pendingProjection.unregisterPendingTransfer(
  orderedDestinationEntry.token,
  "secondary",
);
assert.equal(deleteTransferJournal(orderedDestinationEntry.token, "secondary"), true);
secondaryTabsModule.replaceCenterTabsPayload(inserted.after, { persist: false });
secondarySessionModule.applySessionTransfer(effectSessionSnapshot, { persist: false });

const orderedSourceEntry = {
  ...orderedDestinationEntry,
  token: "source-store-stage",
  role: "source",
  beforeCenterTabs: inserted.after,
  afterCenterTabs: inserted.before,
};
let removed;
assert.equal(stageTransferMutation(
  orderedSourceEntry,
  () => {
    assert.ok(readTransferJournal("secondary").entries[orderedSourceEntry.token]);
    removed = secondaryTabsModule.removeTransferredTabs(
      ["s:local_one"],
      { persist: false },
    );
    return removed.ok;
  },
  () => { throw new Error("unexpected source rejection"); },
  "secondary",
), true);
assert.equal(removed.ok, true);
assert.equal(removed.empty, false);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), ["s:existing"]);
secondaryTabs.getState().openSessionTab("user-source", "User source");
const sourceProjectedCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(sourceProjectedCenter.tabs.map((tab) => tab.id), [
  "s:existing",
  "s:local_one",
  "s:user-source",
]);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:user-source",
]);
pendingProjection.unregisterPendingTransfer(orderedSourceEntry.token, "secondary");
assert.equal(deleteTransferJournal(orderedSourceEntry.token, "secondary"), true);
assert.equal(secondaryTabsModule.replaceCenterTabsPayload(removed.after, {
  persist: true,
}), true);
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
assert.equal(groupedMerge.ok, false);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
]);
assert.deepEqual(secondaryTabs.getState().groups, []);
const segmentPayload = {
  ...transferPayload,
  tabs: [{ id: "w:segment-b", kind: "web", title: "B", url: "https://b.test/" }],
  source: {
    windowId: "source",
    kind: "segment",
    groupId: "g:segment",
    memberIndex: 1,
    memberIds: ["w:segment-a", "w:segment-b", "w:segment-c"],
    visibleIds: ["w:segment-b", "w:segment-c"],
    focusedId: "w:segment-b",
  },
  chats: [],
};
secondaryTabsModule.replaceCenterTabsPayload({
  version: 2,
  tabs: [{ id: "s:target", kind: "session", title: "Target", sessionId: "target" }],
  activeId: "s:target",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
}, { persist: false });
const segmentPlacements = [
  { kind: "strip-end" },
  { kind: "before", targetTabId: "s:target" },
  { kind: "after", targetTabId: "s:target" },
  { kind: "merge", targetTabId: "s:target" },
];
for (const placement of segmentPlacements) {
  assert.equal(
    secondaryTabsModule.validateTransferredTabs(segmentPayload, placement).ok,
    true,
    `a one-tab segment must be valid for ${placement.kind}`,
  );
}
for (const count of [2, 4]) {
  const names = ["b", "c", "d", "e"].slice(0, count);
  const invalidSegment = {
    ...segmentPayload,
    tabs: names.map((name) => ({
      id: `w:segment-${name}`,
      kind: "web",
      title: name.toUpperCase(),
      url: `https://${name}.segment.test/`,
    })),
    source: {
      ...segmentPayload.source,
      memberIds: ["w:segment-a", ...names.map((name) => `w:segment-${name}`), "w:segment-f"],
    },
  };
  for (const placement of segmentPlacements) {
    assert.deepEqual(
      secondaryTabsModule.validateTransferredTabs(invalidSegment, placement),
      { ok: false, reason: "invalid" },
      `${count}-tab segments must be invalid for ${placement.kind}`,
    );
  }
}
const segmentValidation = secondaryTabsModule.validateTransferredTabs(
  segmentPayload,
  { kind: "strip-end" },
);
assert.equal(segmentValidation.ok, true);
assert.deepEqual(segmentValidation.after.groups, []);
assert.equal(secondaryTabsModule.insertTransferredTabs(
  segmentPayload,
  { kind: "strip-end" },
  { persist: false },
).ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:target",
  "w:segment-b",
]);

secondaryTabsModule.replaceCenterTabsPayload({
  version: 2,
  tabs: [
    { id: "w:segment-a", kind: "web", title: "A", url: "https://a.test/" },
    { id: "w:segment-b", kind: "web", title: "B", url: "https://b.test/" },
    { id: "w:segment-c", kind: "web", title: "C", url: "https://c.test/" },
  ],
  activeId: "w:segment-b",
  groups: [{
    id: "g:segment",
    memberIds: ["w:segment-a", "w:segment-b", "w:segment-c"],
    visibleIds: ["w:segment-b", "w:segment-c"],
    focusedId: "w:segment-b",
  }],
  splitWebTabId: null,
  splitRatio: 0.45,
}, { persist: false });
const segmentRemoval = secondaryTabsModule.removeTransferredTabs(
  ["w:segment-b"],
  { persist: false },
);
assert.equal(segmentRemoval.ok, true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "w:segment-a",
  "w:segment-c",
]);
assert.deepEqual(secondaryTabs.getState().groups, []);

const oversizedGroupPayload = {
  ...groupedPayload,
  tabs: ["one", "two", "three", "four"].map((name) => ({
    id: `w:oversized-${name}`,
    kind: "web",
    title: name,
    url: `https://${name}.oversized.test/`,
  })),
  source: {
    ...groupedPayload.source,
    groupId: "g:oversized",
    memberIds: [
      "w:oversized-one",
      "w:oversized-two",
      "w:oversized-three",
      "w:oversized-four",
    ],
    visibleIds: ["w:oversized-one", "w:oversized-two"],
    focusedId: "w:oversized-one",
  },
};
const beforeOversized = structuredClone(secondaryTabs.getState().tabs);
assert.deepEqual(
  secondaryTabsModule.validateTransferredTabs(
    oversizedGroupPayload,
    { kind: "strip-end" },
  ),
  { ok: false, reason: "group-full" },
);
assert.equal(secondaryTabsModule.insertTransferredTabs(
  oversizedGroupPayload,
  { kind: "strip-end" },
  { persist: false },
).ok, false);
assert.deepEqual(secondaryTabs.getState().tabs, beforeOversized);

const {
  applyFileDraftSnapshot,
  fileDrafts,
  snapshotFileDrafts,
} = await import("../lib/state/files-shared.ts");
const actualRecoveryHandlers = {
  applyCenterTabs: (payload, options) =>
    secondaryTabsModule.replaceCenterTabsPayload(payload, options),
  applySession: (snapshot, options) =>
    secondarySessionModule.applySessionTransfer(snapshot, options),
  applyFileDrafts: applyFileDraftSnapshot,
  applyBridge: () => {},
  rebuildAccepted: () => {},
  resumeSourceRemoved: () => {},
  clearAccepted: () => {},
  deleteJournal: (token) => deleteTransferJournal(token, "secondary"),
  snapshotCenterTabs: () => secondaryTabsModule.snapshotCenterTabsPayload(),
  snapshotSession: () => secondarySessionModule.snapshotSessionTransfer([]),
};

const projectionPayload = {
  ...transferPayload,
  tabs: [{
    id: "s:moving",
    kind: "session",
    title: "Moving",
    sessionId: "moving",
    draft: true,
  }],
  chats: [{ chatKey: "moving", composerDraft: "transferred", wasActive: true }],
};
const projectionBeforeCenter = {
  version: 2,
  tabs: [{ id: "s:existing", kind: "session", title: "Existing", sessionId: "existing" }],
  activeId: "s:existing",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
};
secondaryTabsModule.replaceCenterTabsPayload(projectionBeforeCenter, { persist: false });
secondarySessionModule.applySessionTransfer(effectSessionSnapshot, { persist: false });
assert.equal(secondaryTabsModule.persistCurrentCenterTabsPayload(), true);
assert.equal(secondarySessionModule.persistCurrentSessionTransfer(["local_one"]), true);
const projectionAfterCenter = secondaryTabsModule.validateTransferredTabs(
  projectionPayload,
  { kind: "strip-end" },
);
assert.equal(projectionAfterCenter.ok, true);
const projectionAfterSession = {
  ...effectSessionSnapshot,
  activeChatKey: "moving",
  currentSessionId: null,
  composerDrafts: {
    ...effectSessionSnapshot.composerDrafts,
    moving: "transferred",
  },
};
const projectionEntry = {
  ...orderedDestinationEntry,
  token: "projection-unrelated",
  payload: projectionPayload,
  beforeCenterTabs: projectionBeforeCenter,
  afterCenterTabs: projectionAfterCenter.after,
  beforeSession: effectSessionSnapshot,
  afterSession: projectionAfterSession,
};
assert.equal(stageTransferMutation(
  projectionEntry,
  () => {
    const center = secondaryTabsModule.insertTransferredTabs(
      projectionPayload,
      { kind: "strip-end" },
      { persist: false },
    );
    const session = secondarySessionModule.applySessionTransfer(
      projectionAfterSession,
      { persist: false },
    );
    return center.ok && session;
  },
  () => { throw new Error("unexpected projection stage rejection"); },
  "secondary",
), true);
secondaryTabs.getState().openSessionTab("user", "User");
secondarySession.getState().setCurrentDraft("user");
secondarySession.getState().setComposerInput("user edit");
const projectedCenterBytes = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(
  projectedCenterBytes.tabs.map((tab) => tab.id),
  ["s:existing", "s:user"],
  "ordinary center persistence must exclude only the pending destination tab",
);
assert.equal(projectedCenterBytes.activeId, "s:user");
const projectedSessionBytes = JSON.parse(
  values.get("openprogram.sessionDraftState:secondary"),
);
assert.equal(projectedSessionBytes.composerDrafts.user, "user edit");
assert.equal(projectedSessionBytes.composerDrafts.moving, undefined);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:moving",
  "s:user",
]);
assert.equal(secondarySession.getState().composerDrafts.user, "user edit");
assert.equal(finalizeTransferJournal(
  projectionEntry.token,
  "commit",
  actualRecoveryHandlers,
  "secondary",
), true);
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), [
  "s:existing",
  "s:moving",
  "s:user",
]);
assert.equal(secondaryTabs.getState().activeId, "s:user");
assert.equal(secondarySession.getState().composerDrafts.user, "user edit");
const committedProjectionCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(committedProjectionCenter.tabs.map((tab) => tab.id), [
  "s:existing",
  "s:moving",
  "s:user",
]);
assert.equal(committedProjectionCenter.activeId, "s:user");
const committedProjectionSession = JSON.parse(
  values.get("openprogram.sessionDraftState:secondary"),
);
assert.equal(committedProjectionSession.composerDrafts.moving, "transferred");
assert.equal(committedProjectionSession.composerDrafts.user, "user edit");
assert.equal(
  pendingProjection.pendingTransfer(projectionEntry.token, "secondary"),
  undefined,
);

function stageDestinationEntry(token, sessionId, chatKey) {
  const payload = {
    ...transferPayload,
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
  const beforeCenter = secondaryTabsModule.snapshotCenterTabsPayload();
  const beforeSession = secondarySessionModule.snapshotSessionTransfer([]);
  const validated = secondaryTabsModule.validateTransferredTabs(
    payload,
    { kind: "strip-end" },
  );
  assert.equal(validated.ok, true);
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
    ...journalEntry(token),
    payload,
    beforeCenterTabs: beforeCenter,
    afterCenterTabs: validated.after,
    beforeSession,
    afterSession,
  };
  assert.equal(stageTransferMutation(
    entry,
    () => {
      const center = secondaryTabsModule.insertTransferredTabs(
        payload,
        { kind: "strip-end" },
        { persist: false },
      );
      const session = secondarySessionModule.applySessionTransfer(
        afterSession,
        { persist: false },
      );
      return center.ok && session;
    },
    () => { throw new Error(`unexpected stage rejection for ${token}`); },
    "secondary",
  ), true);
  return entry;
}

// Two concurrent tokens: each commit/rollback leaves the other's staged
// delta and the user's interleaved edits intact.
const concurrentBase = {
  version: 2,
  tabs: [{ id: "s:existing", kind: "session", title: "Existing", sessionId: "existing" }],
  activeId: "s:existing",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
};
secondaryTabsModule.replaceCenterTabsPayload(concurrentBase, { persist: true });
secondarySessionModule.applySessionTransfer(effectSessionSnapshot, { persist: true });
const entryA = stageDestinationEntry("concurrent-a", "alpha", "alpha");
const entryB = stageDestinationEntry("concurrent-b", "beta");
secondaryTabs.getState().openSessionTab("user-two", "User two");
secondarySession.getState().setCurrentDraft("user-two");
secondarySession.getState().setComposerInput("user two edit");
const concurrentPendingCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(
  concurrentPendingCenter.tabs.map((tab) => tab.id),
  ["s:existing", "s:user-two"],
  "both pending tokens must stay projected out of persistence",
);
const concurrentPendingSession = JSON.parse(
  values.get("openprogram.sessionDraftState:secondary"),
);
assert.equal(concurrentPendingSession.composerDrafts.alpha, undefined);
assert.equal(concurrentPendingSession.composerDrafts["user-two"], "user two edit");
assert.equal(finalizeTransferJournal(
  entryA.token,
  "rollback",
  actualRecoveryHandlers,
  "secondary",
), true);
assert.deepEqual(
  secondaryTabs.getState().tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two"],
  "rolling back A must keep B's staged tab and the user's new tab",
);
assert.equal(secondarySession.getState().composerDrafts.alpha, undefined);
assert.equal(secondarySession.getState().composerDrafts["user-two"], "user two edit");
const afterRollbackCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(
  afterRollbackCenter.tabs.map((tab) => tab.id),
  ["s:existing", "s:user-two"],
  "still-pending token B must remain projected out after A rolls back",
);
assert.equal(finalizeTransferJournal(
  entryB.token,
  "commit",
  actualRecoveryHandlers,
  "secondary",
), true);
assert.deepEqual(
  secondaryTabs.getState().tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two"],
);
const afterCommitCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(
  afterCommitCenter.tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two"],
);
assert.equal(afterCommitCenter.activeId, "s:user-two");
assert.deepEqual(pendingProjection.pendingTransfers("secondary"), []);
assert.deepEqual(readTransferJournal("secondary").entries, {});

// Crash recovery: a committed journal converges by re-applying only its
// own delta onto the rehydrated state; user edits made after the crash
// snapshot survive, and recovery is idempotent.
function simulateRendererCrash(token) {
  pendingProjection.unregisterPendingTransfer(token, "secondary");
  secondaryTabsModule.replaceCenterTabsPayload(
    JSON.parse(values.get("centerTabs:secondary")),
    { persist: false },
  );
  const persisted = JSON.parse(values.get("openprogram.sessionDraftState:secondary"));
  secondarySessionModule.applySessionTransfer({
    ...emptySessionSnapshot,
    composerDrafts: persisted.composerDrafts,
    composerSettingsBySession: persisted.composerSettingsBySession,
    pendingProjectsByChat: persisted.pendingProjectsByChat,
    draftChannelChoices: persisted.draftChannelChoices,
  }, { persist: false });
}
const entryC = stageDestinationEntry("crash-commit", "gamma", "gamma");
secondaryTabs.getState().openSessionTab("user-three", "User three");
simulateRendererCrash(entryC.token);
assert.deepEqual(
  secondaryTabs.getState().tabs.map((tab) => tab.id),
  ["s:existing", "s:beta", "s:user-two", "s:user-three"],
);
const journaledC = readTransferJournal("secondary").entries[entryC.token];
assert.equal(recoverTransferJournalEntry(
  journaledC,
  "committed",
  actualRecoveryHandlers,
  "secondary",
), true);
const crashCommittedTabs = ["s:existing", "s:beta", "s:user-two", "s:gamma", "s:user-three"];
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), crashCommittedTabs);
assert.equal(secondarySession.getState().composerDrafts.gamma, "gamma draft");
assert.equal(secondarySession.getState().composerDrafts["user-two"], "user two edit");
const crashCommittedCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(crashCommittedCenter.tabs.map((tab) => tab.id), crashCommittedTabs);
assert.equal(readTransferJournal("secondary").entries[entryC.token], undefined);
assert.equal(recoverTransferJournalEntry(
  journaledC,
  "committed",
  actualRecoveryHandlers,
  "secondary",
), true, "committed recovery must be idempotent");
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), crashCommittedTabs);
assert.deepEqual(pendingProjection.pendingTransfers("secondary"), []);

// Crash recovery of a rolled-back journal drops only its own delta.
const entryD = stageDestinationEntry("crash-rollback", "delta", "delta");
secondaryTabs.getState().openSessionTab("user-four", "User four");
simulateRendererCrash(entryD.token);
const journaledD = readTransferJournal("secondary").entries[entryD.token];
assert.equal(recoverTransferJournalEntry(
  journaledD,
  "rolled-back",
  actualRecoveryHandlers,
  "secondary",
), true);
const crashRolledBackTabs = [...crashCommittedTabs, "s:user-four"];
assert.deepEqual(secondaryTabs.getState().tabs.map((tab) => tab.id), crashRolledBackTabs);
assert.equal(secondarySession.getState().composerDrafts.delta, undefined);
assert.equal(secondarySession.getState().composerDrafts.gamma, "gamma draft");
const crashRolledBackCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(
  crashRolledBackCenter.tabs.map((tab) => tab.id),
  crashRolledBackTabs,
);
assert.deepEqual(pendingProjection.pendingTransfers("secondary"), []);
assert.deepEqual(readTransferJournal("secondary").entries, {});

secondaryTabsModule.replaceCenterTabsPayload(
  orderedDestinationEntry.beforeCenterTabs,
  { persist: false },
);
secondarySessionModule.applySessionTransfer(effectSessionSnapshot, { persist: false });
const startupEntry = {
  ...orderedDestinationEntry,
  token: "startup-destination-staged",
};
assert.equal(writeTransferJournal(startupEntry, "secondary"), true);
assert.equal(recoverTransferJournalEntry(
  startupEntry,
  "destination-staged",
  actualRecoveryHandlers,
), true);
secondaryTabs.getState().openSessionTab("existing", "Startup effect write");
secondarySession.getState().setCurrentDraft("local_one");
channelDrafts.setDraftChannelChoice(secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "startup-effect",
  account_id: "startup-effect",
});
const startupProjectedCenter = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(startupProjectedCenter.tabs.map((tab) => tab.id), ["s:existing"]);
assert.equal(startupProjectedCenter.tabs[0].title, "Startup effect write");
assert.equal(
  values.get("openprogram.sessionDraftState:secondary"),
  effectSessionBytes,
  "startup recovery must project affected session writes out",
);
pendingProjection.unregisterPendingTransfer(startupEntry.token, "secondary");
assert.equal(deleteTransferJournal(startupEntry.token, "secondary"), true);
secondaryTabsModule.replaceCenterTabsPayload(startupEntry.afterCenterTabs, {
  persist: false,
});
secondarySessionModule.applySessionTransfer(startupEntry.afterSession, {
  persist: false,
});

const losslessCommitEntry = {
  ...startupEntry,
  token: "lossless-commit",
};
assert.equal(stageTransferMutation(
  losslessCommitEntry,
  () => secondaryTabsModule.replaceCenterTabsPayload(
    losslessCommitEntry.afterCenterTabs,
    { persist: false },
  ),
  () => { throw new Error("unexpected lossless commit rejection"); },
  "secondary",
), true);
values.set("openprogram.sessionDraftState:secondary", "{stale-session-bytes");
globalThis.localStorage.setItem = (key, value) => {
  if (key === "centerTabs:secondary") throw new Error("center quota");
  originalSetItem(key, value);
};
assert.equal(finalizeTransferJournal(
  losslessCommitEntry.token,
  "commit",
  actualRecoveryHandlers,
  "secondary",
), false);
assert.equal(
  readTransferJournal("secondary").entries[losslessCommitEntry.token].phase,
  "committing",
);
globalThis.localStorage.setItem = originalSetItem;
secondaryTabs.getState().openSessionTab("existing", "Failed commit effect");
secondarySession.getState().setCurrentDraft("local_one");
channelDrafts.setDraftChannelChoice(secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "failed-commit-effect",
  account_id: "failed-commit-effect",
});
const failedCommitProjection = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(failedCommitProjection.tabs.map((tab) => tab.id), ["s:existing"]);
assert.equal(failedCommitProjection.tabs[0].title, "Failed commit effect");
assert.equal(
  values.get("openprogram.sessionDraftState:secondary"),
  effectSessionBytes,
  "a failed commit must keep projecting its affected session state out",
);
assert.equal(finalizeTransferJournal(
  losslessCommitEntry.token,
  "commit",
  actualRecoveryHandlers,
  "secondary",
), true);
assert.equal(
  readTransferJournal("secondary").entries[losslessCommitEntry.token],
  undefined,
);

const losslessRollbackEntry = {
  ...startupEntry,
  token: "lossless-rollback",
};
assert.equal(stageTransferMutation(
  losslessRollbackEntry,
  () => secondaryTabsModule.replaceCenterTabsPayload(
    losslessRollbackEntry.afterCenterTabs,
    { persist: false },
  ),
  () => { throw new Error("unexpected lossless rollback rejection"); },
  "secondary",
), true);
values.set("openprogram.sessionDraftState:secondary", "{stale-session-bytes");
globalThis.localStorage.setItem = (key, value) => {
  if (key !== "openprogram.sessionDraftState:secondary") {
    originalSetItem(key, value);
  }
};
assert.equal(finalizeTransferJournal(
  losslessRollbackEntry.token,
  "rollback",
  actualRecoveryHandlers,
  "secondary",
), false);
assert.equal(
  readTransferJournal("secondary").entries[losslessRollbackEntry.token].phase,
  "rolling-back",
);
globalThis.localStorage.setItem = originalSetItem;
secondaryTabs.getState().openSessionTab("existing", "Failed rollback effect");
secondarySession.getState().setCurrentDraft("local_one");
channelDrafts.setDraftChannelChoice(secondarySessionModule.draftChoiceHost(), "local_one", {
  channel: "failed-rollback-effect",
  account_id: "failed-rollback-effect",
});
const failedRollbackProjection = JSON.parse(values.get("centerTabs:secondary"));
assert.deepEqual(failedRollbackProjection.tabs.map((tab) => tab.id), ["s:existing"]);
assert.equal(failedRollbackProjection.tabs[0].title, "Failed rollback effect");
assert.equal(
  values.get("openprogram.sessionDraftState:secondary"),
  effectSessionBytes,
  "a failed rollback must keep projecting its affected session state out",
);
assert.equal(finalizeTransferJournal(
  losslessRollbackEntry.token,
  "rollback",
  actualRecoveryHandlers,
  "secondary",
), true);
assert.equal(
  readTransferJournal("secondary").entries[losslessRollbackEntry.token],
  undefined,
);

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
  browserPageInventory,
  removeVisibleWebTabBounds,
  isDesktopSplitLayoutAvailable,
  isWebTabReady,
  restorePriorActiveTabAfterFailedWebOpen,
  setDesktopSplitLayoutAvailable,
  setWebTabReady,
  visibleWebTab,
  waitForWebTabReady,
} = await import("../lib/desktop-bridge.ts");
const { isWebTabOccluded, measureWebTabBounds } = await import("../lib/web-tab-bounds.ts");
const {
  focusCenterTabGroupMember,
  resolveCenterTabPanes,
} = await import("../lib/state/center-tab-groups.ts");

assert.equal(SPLIT_CHAT_MIN_WIDTH, 360);
assert.equal(SPLIT_WEB_MIN_WIDTH, 480);
assert.equal(SPLIT_DIVIDER_WIDTH, 6);
const rect = (left, top, width, height) => ({
  left,
  top,
  right: left + width,
  bottom: top + height,
  width,
  height,
});
const webBody = { x: 600, y: 180, width: 500, height: 620 };
assert.equal(
  isWebTabOccluded(webBody, [{ getBoundingClientRect: () => rect(150, 900, 420, 360) }]),
  false,
  "a project menu confined to the chat pane must not hide the split WebTab",
);
assert.equal(
  isWebTabOccluded(webBody, [{ getBoundingClientRect: () => rect(900, 240, 300, 220) }]),
  true,
  "an overlay intersecting the native page body must hide it",
);
assert.equal(
  isWebTabOccluded(webBody, [{ getBoundingClientRect: () => rect(100, 300, 500, 200) }]),
  false,
  "edge contact without positive overlap must not hide the WebTab",
);
assert.equal(
  isWebTabOccluded(webBody, [
    { getBoundingClientRect: () => rect(40, 240, 500, 300) },
    { getBoundingClientRect: () => rect(0, 0, 1200, 900) },
  ]),
  true,
  "a modal backdrop must occlude the WebTab even when dialog content is elsewhere",
);
const leftWebBody = { x: 520, y: 180, width: 420, height: 620 };
const rightWebBody = { x: 946, y: 180, width: 420, height: 620 };
const paneMenu = [{ getBoundingClientRect: () => rect(700, 260, 180, 240) }];
assert.equal(isWebTabOccluded(leftWebBody, paneMenu), true);
assert.equal(
  isWebTabOccluded(rightWebBody, paneMenu),
  false,
  "each visible WebTab must evaluate the same menu against its own body bounds",
);
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
assert.equal(sessionPanes.length, 2, "two sessions split into two panes");
assert.deepEqual(
  sessionPanes.map((pane) => pane.kind),
  ["peer", "peer"],
  "a chat+chat split is symmetric — neither side takes the singleton shell",
);
assert.equal(sessionPanes[0].tabId, "s:a");
assert.equal(sessionPanes[1].tabId, "s:b");

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

const inventoryTabs = [
  { id: "w:google", kind: "web", title: "Google", url: "https://google.test/" },
  { id: "w:github", kind: "web", title: "GitHub", url: "https://github.test/" },
  { id: "w:bilibili", kind: "web", title: "Bilibili", url: "https://bilibili.test/" },
  { id: "w:youtube", kind: "web", title: "YouTube", url: "https://youtube.test/" },
];
useCenterTabs.setState({
  tabs: inventoryTabs,
  activeId: "w:youtube",
  groups: [{
    id: "g3",
    memberIds: ["w:bilibili", "w:youtube"],
    visibleIds: ["w:bilibili", "w:youtube"],
    focusedId: "w:youtube",
  }],
  splitRatio: 0.5,
});
const inventoryBridge = {
  windowId: "window-1",
  webTab: {
    inspect: async (id) => ({
      target_id: `target:${id}`,
      url: inventoryTabs.find((tab) => tab.id === id).url,
      title: inventoryTabs.find((tab) => tab.id === id).title,
    }),
    syncVisible: () => {},
  },
};
for (const tab of inventoryTabs) setWebTabReady(tab.id, true);
registerVisibleWebTabBounds(
  inventoryBridge,
  "w:bilibili",
  { x: 0, y: 0, width: 500, height: 700 },
);
registerVisibleWebTabBounds(
  inventoryBridge,
  "w:youtube",
  { x: 500, y: 0, width: 500, height: 700 },
);
await Promise.resolve();
const pageInventory = await browserPageInventory(inventoryBridge);
assert.equal(pageInventory.window_id, "window-1");
assert.equal(pageInventory.active_tab_entry_id, "group:g3");
assert.equal(pageInventory.focused_tab_id, "w:youtube");
assert.deepEqual(pageInventory.tab_entries, [
  { id: "tab:w:google", mode: "single", tab_ids: ["w:google"] },
  { id: "tab:w:github", mode: "single", tab_ids: ["w:github"] },
  {
    id: "group:g3",
    mode: "split",
    tab_ids: ["w:bilibili", "w:youtube"],
    split: {
      axis: "horizontal",
      ratio: 0.5,
      panes: [
        { pane_id: "pane:g3:0", order: 0, tab_id: "w:bilibili" },
        { pane_id: "pane:g3:1", order: 1, tab_id: "w:youtube" },
      ],
    },
  },
]);
assert.deepEqual(
  pageInventory.pages.map((page) => ({
    tab_id: page.tab_id,
    tab_entry_id: page.tab_entry_id,
    placement: page.placement,
    visible: page.visible,
    focused: page.focused,
  })),
  [
    { tab_id: "w:google", tab_entry_id: "tab:w:google", placement: { mode: "single" }, visible: false, focused: false },
    { tab_id: "w:github", tab_entry_id: "tab:w:github", placement: { mode: "single" }, visible: false, focused: false },
    { tab_id: "w:bilibili", tab_entry_id: "group:g3", placement: { mode: "split", pane_id: "pane:g3:0", order: 0 }, visible: true, focused: false },
    { tab_id: "w:youtube", tab_entry_id: "group:g3", placement: { mode: "split", pane_id: "pane:g3:1", order: 1 }, visible: true, focused: true },
  ],
);
const samePageInventory = await browserPageInventory(inventoryBridge);
assert.equal(
  samePageInventory.inventory_revision,
  pageInventory.inventory_revision,
  "an unchanged Page/layout snapshot keeps its inventory revision",
);
useCenterTabs.setState({ splitRatio: 0.6 });
const resizedPageInventory = await browserPageInventory(inventoryBridge);
assert.ok(
  resizedPageInventory.inventory_revision > pageInventory.inventory_revision,
  "a split ratio change advances the inventory revision",
);
assert.equal(resizedPageInventory.tab_entries[2].split.ratio, 0.6);
let releaseOldInventory;
const oldInventoryGate = new Promise((resolve) => { releaseOldInventory = resolve; });
let blockOldInventory = true;
const concurrentBridge = {
  ...inventoryBridge,
  webTab: {
    ...inventoryBridge.webTab,
    inspect: async (id) => {
      if (blockOldInventory) await oldInventoryGate;
      return inventoryBridge.webTab.inspect(id);
    },
  },
};
useCenterTabs.setState({ splitRatio: 0.55 });
const oldInventoryPromise = browserPageInventory(concurrentBridge);
await Promise.resolve();
blockOldInventory = false;
useCenterTabs.setState({ splitRatio: 0.65 });
const newConcurrentInventory = await browserPageInventory(concurrentBridge);
releaseOldInventory();
const oldConcurrentInventory = await oldInventoryPromise;
assert.equal(oldConcurrentInventory.tab_entries[2].split.ratio, 0.55);
assert.equal(newConcurrentInventory.tab_entries[2].split.ratio, 0.65);
assert.ok(
  oldConcurrentInventory.inventory_revision
    < newConcurrentInventory.inventory_revision,
  "an older blocked snapshot must keep an older inventory revision",
);
useCenterTabs.getState().moveGroupMember("g3", "w:bilibili", 1);
const reorderedPageInventory = await browserPageInventory(inventoryBridge);
assert.deepEqual(
  useCenterTabs.getState().groups[0].memberIds,
  ["w:youtube", "w:bilibili"],
);
assert.deepEqual(
  useCenterTabs.getState().groups[0].visibleIds,
  ["w:bilibili", "w:youtube"],
);
assert.deepEqual(
  reorderedPageInventory.tab_entries[2].split.panes,
  [
    { pane_id: "pane:g3:0", order: 0, tab_id: "w:bilibili" },
    { pane_id: "pane:g3:1", order: 1, tab_id: "w:youtube" },
  ],
  "Page inventory pane order must match the rendered visibleIds order",
);
assert.deepEqual(
  Object.fromEntries(reorderedPageInventory.pages.slice(2).map((page) => [
    page.tab_id,
    page.placement,
  ])),
  {
    "w:bilibili": { mode: "split", pane_id: "pane:g3:0", order: 0 },
    "w:youtube": { mode: "split", pane_id: "pane:g3:1", order: 1 },
  },
);
removeVisibleWebTabBounds(inventoryBridge, "w:bilibili");
removeVisibleWebTabBounds(inventoryBridge, "w:youtube");
for (const tab of inventoryTabs) setWebTabReady(tab.id, false);

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
assert.deepEqual(hiddenThird.memberIds, ["s:a", "w:one"]);
assert.equal(hiddenThirdPanes.some((pane) => pane.tabId === "w:two"), false);
const narrowPanes = resolveCenterTabPanes(
  undefined,
  paneTabs,
  "w:two",
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
  const boundsOne = measureWebTabBounds({
    getBoundingClientRect: () => ({
      left: 812.6333618164062,
      top: 79.99031066894531,
      width: 518.6390380859375,
      height: 696.86279296875,
    }),
  });
  const boundsTwo = { x: 506, y: 40, width: 600, height: 600 };
  assert.equal(
    isWebTabOccluded(boundsOne, [{
      getBoundingClientRect: () => ({ left: 560, top: 720, right: 740, bottom: 840, width: 180, height: 120 }),
    }]),
    false,
    "a composer popover outside the native Page must not hide it",
  );
  assert.equal(
    isWebTabOccluded(boundsOne, [{
      getBoundingClientRect: () => ({ left: 780, top: 200, right: 900, bottom: 360, width: 120, height: 160 }),
    }]),
    true,
    "an overlay intersecting the native Page must hide it",
  );
  registerVisibleWebTabBounds(bridge, "w:one", boundsOne);
  registerVisibleWebTabBounds(bridge, "w:two", boundsTwo);
  await Promise.resolve();
  assert.equal(syncCalls.length, 1, "one scheduled flush publishes both panes");
  assert.deepEqual(syncCalls[0], [
    { id: "w:one", bounds: boundsOne },
    { id: "w:two", bounds: boundsTwo },
  ]);
  assert.deepEqual(syncCalls[0][0].bounds, {
    x: 812.6333618164062,
    y: 79.99031066894531,
    width: 518.6390380859375,
    height: 696.86279296875,
  }, "renderer bridge must preserve fractional DOMRect bounds until main IPC");
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
const splitViewPickerSource = await readFile(
  new URL("../components/center-tabs/split-view-picker.tsx", import.meta.url),
  "utf8",
);
const appShellSource = await readFile(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);
// The strip is split across center-tab-strip.tsx and its submodules;
// readCenterTabStripSource concatenates them in source order.
const tabStripSource = readCenterTabStripSource(import.meta.url);
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

assert.match(webTabPaneSource, /\bHouse\b/, "browser toolbar Home icon missing");
assert.match(
  webTabPaneSource,
  /replaceWebTabWithNewTabPage\(tabId\)/,
  "browser toolbar must navigate the current web tab to the OpenProgram home page",
);
assert.match(webTabPaneSource, /text\("Home",\s*"主页"\)/);
assert.equal(
  (webTabPaneSource.match(/<HomeButton tabId=\{tabId\} \/>/g) ?? []).length,
  2,
  "desktop and iframe browser toolbars must both expose Home",
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
assert.match(desktopBridgeSource, /state\.ensureWebTab\(d\.url\)/);
assert.match(desktopBridgeSource, /useWebTabPip\.getState\(\)\.show\(id\)/);
assert.match(desktopBridgeSource, /waitForWebTabReady\(id, 2000\)/);
assert.match(desktopBridgeSource, /subscribeWebTabPopups\(bridge\)/);
assert.match(desktopBridgeSource, /state\.openPopupWebTab\(popup\.url, popup\.openerId\)/);
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
assert.match(appShellSource, /<WebTabPip \/>/);
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
  // No isDesktop gate: split is purely a measured-width decision now.
  /const panes = activeGroup && splitAvailable \? compoundPanes : focusedPanes;/,
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

assert.doesNotMatch(
  webTabPaneSource,
  /Open split view|Exit split view|SplitViewPicker|Columns2/,
  "split controls belong to the tab and pane layer, not the browser toolbar",
);
assert.doesNotMatch(webTabPaneSource, /setSplitWebTab\(/);
assert.doesNotMatch(webTabPaneSource, /openDraftSessionTab\(|newSession\(/);
assert.match(
  splitViewPickerSource,
  /const latestState = useCenterTabs\.getState\(\);[\s\S]*?splitCandidates\(\s*latestState\.tabs,\s*latestState\.groups,\s*subjectId,?\s*\)/,
  "a stale picker row must be revalidated against the latest store before grouping",
);
assert.match(splitViewPickerSource, /onClose\("escape"\)/);
assert.match(splitViewPickerSource, /onClose\("outside"\)/);
assert.match(splitViewPickerSource, /onClose\("close-button"\)/);
assert.match(
  splitViewPickerSource,
  /querySelector<HTMLButtonElement>\("\[data-split-option\]"\)[\s\S]*?\?\?[\s\S]*?querySelector<HTMLButtonElement>\("\[data-split-close\]"\)/,
  "a picker must focus its first option, falling back to Close only when empty",
);
assert.match(
  tabStripSource,
  /if \(reason !== "outside"\)[\s\S]*?returnFocusToMenuInvoker\(subject\)/,
  "outside dismissal must preserve the newly clicked element's focus",
);

assert.doesNotMatch(tabStripSource, /splitPinned|data-split-pinned/);
assert.match(tabStripSource, /active=\{tab\.id === activeId\}/);
assert.doesNotMatch(tabsCssSource, /\[data-split-pinned="true"\]/);
assert.match(baseCssSource, /\.center-split-divider\s*\{[^}]*width:\s*6px;/s);
assert.match(baseCssSource, /\.center-split-primary\s*\{[^}]*min-width:\s*360px;/s);
assert.match(
  baseCssSource,
  /\.center-split-primary\.center-pane-chat\s*\{[^}]*flex:\s*0\s+0\s+auto;/s,
  "a chat pane in the primary split slot must keep the ratio-controlled width",
);
assert.match(baseCssSource, /\.center-split-secondary\s*\{[^}]*min-width:\s*480px;/s);
assert.match(webTabPaneSource, /setWebTabReady/);
assert.doesNotMatch(webTabPaneSource, /bridge\.webTab\.(?:show|hide|setBounds)\(/);
assert.doesNotMatch(
  webTabPaneSource,
  /bridge\.webTab\.navigate\(tabId, viewUrlRef\.current\);/,
);
assert.match(webTabPaneSource, /const occluded = isWebTabOccluded\(/);
assert.match(
  webTabPaneSource,
  /document\.querySelectorAll\([\s\S]*?\[role="dialog"\], \[role="menu"\], \[role="listbox"\], \.branches-merge-modal-backdrop, \[data-native-view-occluder="true"\]/,
);
assert.match(
  webTabPaneSource,
  /const bounds = measureWebTabBounds\(el\);/,
);
assert.match(
  webTabPaneSource,
  /if \(occluded \|\| bounds\.width <= 0 \|\| bounds\.height <= 0\) \{\s*removeVisibleWebTabBounds\(bridge, tabId\);\s*setWebTabReady\(tabId, false\);/s,
);
assert.match(
  webTabPaneSource,
  /registerVisibleWebTabBounds\(bridge, tabId, bounds\);\s*setWebTabReady\(tabId, true\);/s,
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
const dialogSource = await readFile(
  new URL("../components/ui/dialog.tsx", import.meta.url),
  "utf8",
);
assert.match(
  dialogSource,
  /<DialogPrimitive\.Overlay[\s\S]*?data-native-view-occluder="true"/,
  "the full-window dialog backdrop must participate in native view occlusion",
);
const permissionMenuSource = await readFile(
  new URL("../components/chat/top-bar/permission-menu.tsx", import.meta.url),
  "utf8",
);
assert.match(
  permissionMenuSource,
  /bypassConfirm[\s\S]*?data-native-view-occluder="true"/,
  "the custom full-window permission confirmation must occlude native views",
);
const contextBadgeSource = await readFile(
  new URL("../components/chat/context-badge.tsx", import.meta.url),
  "utf8",
);
assert.match(
  contextBadgeSource,
  /透明全屏遮罩[\s\S]*?data-native-view-occluder="true"/,
  "the context panel click-catcher must not sit behind a native view",
);
const scopedDropOverlaySource = await readFile(
  new URL("../components/chat/composer/attach/scoped-drop-overlay.tsx", import.meta.url),
  "utf8",
);
assert.match(
  scopedDropOverlaySource,
  /<div[\s\S]*?data-native-view-occluder="true"[\s\S]*?\.\.\.style/,
  "the measured drop overlay must occlude only Web bodies it intersects",
);

useCenterTabs.setState({
  tabs: [{ id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" }],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});
const pipOnlyId = useCenterTabs.getState().ensureWebTab("https://pip.example/");
assert.equal(pipOnlyId, "w:https://pip.example/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.equal(useCenterTabs.getState().splitWebTabId, null);
assert.equal(useCenterTabs.getState().groups.length, 0);
assert.equal(
  useCenterTabs.getState().tabs.find((tab) => tab.id === pipOnlyId)?.url,
  "https://pip.example/",
);
assert.equal(
  useCenterTabs.getState().ensureWebTab("https://pip.example/"),
  pipOnlyId,
);
assert.equal(
  useCenterTabs.getState().tabs.filter((tab) => tab.kind === "web").length,
  1,
);
const { peekWebTabPipId, useWebTabPip } = await import("../lib/state/web-tab-pip-store.ts");
useWebTabPip.getState().show(pipOnlyId);
assert.equal(peekWebTabPipId(), pipOnlyId);
registerVisibleWebTabBounds(
  { webTab: { syncVisible() {} } },
  pipOnlyId,
  { x: 40, y: 40, width: 360, height: 188 },
);
setWebTabReady(pipOnlyId, true);
assert.equal(visibleWebTab()?.id, pipOnlyId);
useWebTabPip.getState().hide();
removeVisibleWebTabBounds({ webTab: { syncVisible() {} } }, pipOnlyId);
setWebTabReady(pipOnlyId, false);
assert.equal(peekWebTabPipId(), null);

const id = useCenterTabs.getState().openWebTabInSplit("https://example.com/");
assert.equal(id, "w:https://example.com/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.equal(useCenterTabs.getState().splitWebTabId, id);
assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:chat", id]);
assert.equal(JSON.parse(values.get("centerTabs")).splitWebTabId, id);
assert.equal(JSON.parse(values.get("centerTabs")).splitRatio, 0.45);

// A split group is one top-level tab. Activating another top-level tab must
// hide the whole split without moving its web member into the new tab.
useCenterTabs.setState((state) => ({
  tabs: [
    ...state.tabs,
    { id: "s:other", kind: "session", title: "Other", sessionId: "other" },
  ],
}));
const splitMembersBeforeSwitch = useCenterTabs.getState().groups[0].memberIds;
const tabOrderBeforeSwitch = useCenterTabs.getState().tabs.map((tab) => tab.id);
useCenterTabs.getState().setActive("s:other");
assert.equal(useCenterTabs.getState().activeId, "s:other");
assert.deepEqual(
  useCenterTabs.getState().groups[0].memberIds,
  splitMembersBeforeSwitch,
  "switching top-level tabs must not re-parent the split web view",
);
assert.deepEqual(
  useCenterTabs.getState().tabs.map((tab) => tab.id),
  tabOrderBeforeSwitch,
  "switching top-level tabs must not reorder split members",
);
assert.equal(
  useCenterTabs.getState().groups.some((group) =>
    group.memberIds.includes("s:other") && group.memberIds.includes(id)),
  false,
  "the browser must remain owned by its original composite tab",
);

// Explicitly opening a URL already owned by another composite selects that
// whole entry. It must not claim success while leaving its native view hidden.
const ownedId = useCenterTabs.getState().openWebTabInSplit(
  "https://example.com/",
);
assert.equal(ownedId, id);
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:chat", id]);

// Repeated agent opens reuse the browser pane owned by the active composite.
// A two-view composite must not append an invisible third member or orphan tab.
useCenterTabs.getState().setActive("s:chat");
const tabCountBeforeRepeatedOpen = useCenterTabs.getState().tabs.length;
const repeatedId = useCenterTabs.getState().openWebTabInSplit(
  "https://second.example/",
);
assert.equal(repeatedId, id, "the existing composite browser owns the new navigation");
assert.equal(useCenterTabs.getState().tabs.length, tabCountBeforeRepeatedOpen);
assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:chat", id]);
assert.equal(
  useCenterTabs.getState().tabs.find((tab) => tab.id === id)?.url,
  "https://second.example/",
);
useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id, kind: "web", title: "other.example", url: "https://other.example/" },
  ],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: id,
  splitRatio: 0.45,
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

// Browser Home replaces the current web tab with the Browser-only home page.
// The position and compound-tab membership stay intact, while
// the native web-view id disappears so the desktop bridge can destroy it.
const homeWebId = "w:https://home-target.test/";
useCenterTabs.setState({
  tabs: [
    { id: "s:home-chat", kind: "session", title: "Chat", sessionId: "home-chat" },
    { id: homeWebId, kind: "web", title: "Target", url: "https://home-target.test/" },
    { id: "f:after", kind: "file", title: "after.txt", projectId: "p", path: "after.txt" },
  ],
  activeId: homeWebId,
  groups: [{
    id: "g:home",
    memberIds: ["s:home-chat", homeWebId],
    visibleIds: ["s:home-chat", homeWebId],
    focusedId: homeWebId,
  }],
  splitWebTabId: homeWebId,
  splitRatio: 0.45,
});
useCenterTabs.getState().replaceWebTabWithNewTabPage(homeWebId);
const homeState = useCenterTabs.getState();
const homeTab = homeState.tabs[1];
assert.equal(homeTab.kind, "builtin", "Home must show the Browser home page");
assert.equal(homeTab.page, "browser");
assert.match(homeTab.id, /^browser:/, "Home must allocate a pane-local Browser home id");
assert.equal(homeState.activeId, homeTab.id, "the replacement remains active");
assert.equal(homeState.tabs[2].id, "f:after", "Home must preserve tab order");
assert.deepEqual(homeState.groups[0].memberIds, ["s:home-chat", homeTab.id]);
assert.deepEqual(homeState.groups[0].visibleIds, ["s:home-chat", homeTab.id]);
assert.equal(homeState.groups[0].focusedId, homeTab.id);
assert.equal(homeState.splitWebTabId, null, "the replaced web view must leave legacy split state");
assert.equal(homeState.tabs.some((tab) => tab.id === homeWebId), false);

// A Browser home in another tab must not steal the current pane or create a
// duplicate deterministic id.
useCenterTabs.setState({
  tabs: [
    { id: "browser:existing", kind: "builtin", title: "", page: "browser" },
    { id: "ntp:browser-choice", kind: "ntp", title: "" },
  ],
  activeId: "ntp:browser-choice",
  groups: [],
  splitWebTabId: null,
});
useCenterTabs.getState().openBuiltinTab("browser");
const browserChoiceState = useCenterTabs.getState();
assert.equal(browserChoiceState.tabs.length, 2);
assert.equal(browserChoiceState.tabs[0].id, "browser:existing");
assert.equal(browserChoiceState.tabs[1].kind, "builtin");
assert.equal(browserChoiceState.tabs[1].page, "browser");
assert.match(browserChoiceState.tabs[1].id, /^browser:/);
assert.notEqual(browserChoiceState.tabs[1].id, "browser:existing");
assert.equal(browserChoiceState.activeId, browserChoiceState.tabs[1].id);

useCenterTabs.setState({
  tabs: [
    { id: "browser:existing", kind: "builtin", title: "", page: "browser" },
    { id: "w:https://home-second.test/", kind: "web", title: "Second", url: "https://home-second.test/" },
  ],
  activeId: "w:https://home-second.test/",
  groups: [],
  splitWebTabId: null,
});
useCenterTabs.getState().replaceWebTabWithNewTabPage("w:https://home-second.test/");
const secondHomeState = useCenterTabs.getState();
assert.equal(secondHomeState.tabs.length, 2);
assert.equal(secondHomeState.tabs[0].id, "browser:existing");
assert.equal(secondHomeState.tabs[1].page, "browser");
assert.notEqual(secondHomeState.tabs[1].id, "browser:existing");
assert.equal(secondHomeState.activeId, secondHomeState.tabs[1].id);

useCenterTabs.setState(homeState);
const beforeWrongKind = JSON.stringify({
  tabs: homeState.tabs,
  activeId: homeState.activeId,
  groups: homeState.groups,
  splitWebTabId: homeState.splitWebTabId,
  splitRatio: homeState.splitRatio,
});
useCenterTabs.getState().replaceWebTabWithNewTabPage("s:home-chat");
assert.equal(
  JSON.stringify({
    tabs: useCenterTabs.getState().tabs,
    activeId: useCenterTabs.getState().activeId,
    groups: useCenterTabs.getState().groups,
    splitWebTabId: useCenterTabs.getState().splitWebTabId,
    splitRatio: useCenterTabs.getState().splitRatio,
  }),
  beforeWrongKind,
  "Home must ignore ids that do not belong to a web tab",
);

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

// ---------------------------------------------------------------- Task 5:
// desktop-bridge renderer staging / recovery / cleanup around the main
// transfer transaction. The bridge module shares the PLAIN (unqueried)
// center-tabs/session-store instances, so drive those here.

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };
const bridgeModule = await import("../lib/desktop-bridge.ts");
const plainTabsModule = await import("../lib/state/center-tabs-store.ts");
const plainSessionModule = await import("../lib/session-store/index.ts");
const plainTabs = plainTabsModule.useCenterTabs;

const popupOpenerGroup = {
  id: "g:popup-opener",
  memberIds: ["s:popup-chat", "w:opener"],
  visibleIds: ["s:popup-chat", "w:opener"],
  focusedId: "w:opener",
};
plainTabs.setState({
  tabs: [
    { id: "s:popup-chat", kind: "session", title: "Chat", sessionId: "popup-chat" },
    { id: "w:opener", kind: "web", title: "Opener", url: "https://opener.test/" },
  ],
  activeId: "w:opener",
  groups: [popupOpenerGroup],
  splitWebTabId: "w:opener",
  splitRatio: 0.45,
});
let popupCallback = null;
const popupBridge = {
  webTab: {
    onPopup(callback) {
      popupCallback = callback;
      return () => { popupCallback = null; };
    },
  },
};
const unsubscribePopup = bridgeModule.subscribeWebTabPopups(popupBridge);
const beforeRejectedPopup = plainTabsModule.snapshotCenterTabsPayload();
popupCallback({ openerId: "missing", url: "https://popup.test/" });
popupCallback({ openerId: "s:popup-chat", url: "https://popup.test/" });
assert.deepEqual(
  plainTabsModule.snapshotCenterTabsPayload(),
  beforeRejectedPopup,
  "missing and non-web openers must not create popup tabs",
);

popupCallback({ openerId: "w:opener", url: "https://popup.test/" });
const popupOne = plainTabs.getState().activeId;
popupCallback({ openerId: "w:opener", url: "https://popup.test/" });
const popupTwo = plainTabs.getState().activeId;
assert.notEqual(popupOne, popupTwo, "two popup requests must create distinct tabs");
assert.equal(
  plainTabs.getState().tabs.find((tab) => tab.id === popupOne)?.openerTabId,
  "w:opener",
  "popup tabs must retain their opener Page identity",
);
assert.equal(
  plainTabs.getState().tabs.find((tab) => tab.id === popupTwo)?.openerTabId,
  "w:opener",
);
assert.ok(plainTabs.getState().tabs.some((tab) => tab.id === "w:opener"));
assert.equal(plainTabs.getState().activeId, popupTwo);
assert.deepEqual(plainTabs.getState().groups, [popupOpenerGroup]);
assert.equal(plainTabs.getState().splitWebTabId, "w:opener");
assert.equal(
  plainTabs.getState().tabs.filter((tab) => tab.url === "https://popup.test/").length,
  2,
  "popup URLs must not reuse a normal deterministic web tab",
);
unsubscribePopup();
assert.equal(popupCallback, null);

const surfaceTabs = [
  { id: "s:surface-chat", kind: "session", title: "Chat", sessionId: "surface-chat" },
  { id: "w:surface-page", kind: "web", title: "Page", url: "https://surface.test/" },
];
plainTabs.setState({
  tabs: surfaceTabs,
  activeId: "s:surface-chat",
  groups: [{
    id: "g:surface-geometry",
    memberIds: ["s:surface-chat", "w:surface-page"],
    visibleIds: ["s:surface-chat", "w:surface-page"],
    focusedId: "s:surface-chat",
  }],
  splitWebTabId: null,
  splitRatio: 0.5,
});
const geometryBridge = {
  webTab: {
    syncVisible() {},
  },
};
bridgeModule.registerVisibleWebTabBounds(
  geometryBridge,
  "w:surface-page",
  { x: 600, y: 120, width: 500, height: 700 },
);
bridgeModule.setWebTabReady("w:surface-page", true);
const firstGeometryRevision = bridgeModule.surfaceRefForChat(
  "surface-chat",
  true,
)?.geometry_revision;
assert.ok(firstGeometryRevision > 0);
bridgeModule.registerVisibleWebTabBounds(
  geometryBridge,
  "w:surface-page",
  { x: 600, y: 120, width: 500, height: 700 },
);
assert.equal(
  bridgeModule.surfaceRefForChat("surface-chat", true)?.geometry_revision,
  firstGeometryRevision,
  "identical bounds must preserve geometry revision",
);
bridgeModule.registerVisibleWebTabBounds(
  geometryBridge,
  "w:surface-page",
  { x: 0, y: 120, width: 500, height: 700 },
);
assert.ok(
  bridgeModule.surfaceRefForChat("surface-chat", true)?.geometry_revision
    > firstGeometryRevision,
  "pane movement must advance geometry revision",
);
const previewStartRevision = bridgeModule.surfaceRefForChat(
  "surface-chat",
  true,
)?.geometry_revision;
let resolveDeferredPreview;
const deferredPreview = new Promise((resolve) => {
  resolveDeferredPreview = resolve;
});
bridgeModule.registerVisibleWebTabBounds(
  geometryBridge,
  "w:surface-page",
  { x: 0, y: 120, width: 520, height: 700 },
);
resolveDeferredPreview({ preview: { title: "stale" }, target_id: "target-1" });
const stalePreview = await deferredPreview.then((result) =>
  bridgeModule.finalizeWebTabPreview(
    "w:surface-page",
    previewStartRevision,
    result,
  ),
);
assert.equal(stalePreview.reason_code, "page_context_stale");
assert.equal("preview" in stalePreview, false);
assert.equal("target_id" in stalePreview, false);
const legacyPreview = bridgeModule.finalizeWebTabPreview(
  "w:surface-page",
  0,
  { preview: { title: "legacy" }, target_id: "target-legacy" },
);
assert.equal(legacyPreview.ok, true);
assert.equal(legacyPreview.preview.title, "legacy");
assert.equal(legacyPreview.target_id, "target-legacy");
let resolveDeferredActivation;
const deferredActivation = new Promise((resolve) => {
  resolveDeferredActivation = resolve;
});
bridgeModule.removeVisibleWebTabBounds(geometryBridge, "w:surface-page");
bridgeModule.setWebTabReady("w:surface-page", false);
assert.equal(
  bridgeModule.surfaceRefForChat("surface-chat", true),
  null,
  "the production hide lifecycle must remove the page from turn surfaces",
);
const occludedPreview = bridgeModule.finalizeWebTabPreview(
  "w:surface-page",
  0,
  { preview: { title: "occluded" }, target_id: "target-occluded" },
);
assert.equal(occludedPreview.reason_code, "page_context_stale");
assert.equal("preview" in occludedPreview, false);
assert.equal("target_id" in occludedPreview, false);
resolveDeferredActivation("target-occluded");
const occludedActivation = await deferredActivation.then((targetId) =>
  bridgeModule.finalizeBoundWebTabActivation(
    "w:surface-page",
    0,
    targetId,
  ),
);
assert.equal(occludedActivation.reason_code, "page_context_stale");
assert.equal("target_id" in occludedActivation, false);
bridgeModule.registerVisibleWebTabBounds(
  geometryBridge,
  "w:surface-page",
  { x: 0, y: 120, width: 520, height: 700 },
);
bridgeModule.setWebTabReady("w:surface-page", true);
for (const [visibleIds, expectedRegion] of [
  [["w:surface-page", "s:surface-chat"], "left"],
  [["s:surface-chat", "w:surface-page"], "right"],
]) {
  plainTabsModule.replaceCenterTabsPayload({
    version: 2,
    tabs: surfaceTabs,
    activeId: "s:surface-chat",
    groups: [{
      id: "g:surface",
      memberIds: visibleIds,
      visibleIds,
      focusedId: "s:surface-chat",
    }],
    splitWebTabId: null,
    splitRatio: 0.5,
  }, { persist: false });
  assert.equal(
    bridgeModule.surfaceRefForChat("surface-chat", true)?.region,
    expectedRegion,
  );
}
plainTabs.setState({
  tabs: surfaceTabs,
  activeId: "s:surface-chat",
  groups: [{
    id: "g:surface",
    memberIds: ["s:surface-chat", "w:surface-page"],
    visibleIds: ["w:surface-page"],
    focusedId: "w:surface-page",
  }],
  splitWebTabId: null,
  splitRatio: 0.5,
});
assert.equal(
  bridgeModule.surfaceRefForChat("surface-chat", true)?.region,
  "center",
);
plainTabs.setState({
  tabs: surfaceTabs,
  activeId: "s:surface-chat",
  groups: [],
  splitWebTabId: "w:surface-page",
  splitRatio: 0.5,
});
assert.equal(
  bridgeModule.surfaceRefForChat("surface-chat", true)?.region,
  "right",
);
plainTabsModule.replaceCenterTabsPayload({
  version: 2,
  tabs: surfaceTabs,
  activeId: "s:surface-chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.5,
}, { persist: false });
assert.equal(bridgeModule.surfaceRefForChat("surface-chat", true), null);
const hiddenLegacyPreview = bridgeModule.finalizeWebTabPreview(
  "w:surface-page",
  0,
  { preview: { title: "hidden" }, target_id: "target-hidden" },
);
assert.equal(hiddenLegacyPreview.reason_code, "page_context_stale");
assert.equal("preview" in hiddenLegacyPreview, false);
assert.equal("target_id" in hiddenLegacyPreview, false);

// Replacing the id is the cleanup signal used by the desktop bridge. Verify
// the existing reconciler destroys the native view once Home removes that id.
{
  const calls = [];
  const cleanupBridge = {
    webTab: {
      ensure: (...args) => calls.push(["ensure", ...args]),
      destroy: (...args) => calls.push(["destroy", ...args]),
      syncVisible: () => {},
    },
  };
  const cleanupId = "w:https://cleanup-home.test/";
  bridgeModule.ensureWebView(cleanupBridge, cleanupId, "https://cleanup-home.test/");
  plainTabs.setState({
    tabs: [{ id: cleanupId, kind: "web", title: "Cleanup", url: "https://cleanup-home.test/" }],
    activeId: cleanupId,
    groups: [],
    splitWebTabId: null,
  });
  plainTabs.getState().replaceWebTabWithNewTabPage(cleanupId);
  bridgeModule.destroyStaleWebViews(
    cleanupBridge,
    plainTabs.getState().tabs.map((tab) => tab.id),
  );
  assert.deepEqual(calls, [
    ["ensure", cleanupId, "https://cleanup-home.test/"],
    ["destroy", cleanupId],
  ]);
}

function makeTransferBridge(payload, overrides = {}) {
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
}

const t5Base = {
  version: 2,
  tabs: [{ id: "s:home", kind: "session", title: "Home", sessionId: "home" }],
  activeId: "s:home",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
};
const t5Payload = {
  tabs: [{ id: "w:moved", kind: "web", title: "Moved", url: "https://moved.test/" }],
  source: { windowId: "source", kind: "tab" },
  fileDrafts: [],
  chats: [],
};

// Destination staging: journal write + journalOpened strictly precede the
// {persist:false} mutation; committed storage bytes stay unchanged.
plainTabsModule.replaceCenterTabsPayload(t5Base, { persist: true });
plainSessionModule.applySessionTransfer(emptySessionSnapshot, { persist: true });
const committedCenterBytes = values.get("centerTabs:main");
const committedSessionBytes = values.get("openprogram.sessionDraftState:main");
{
  const journalOpenedObservations = [];
  const { bridge, calls } = makeTransferBridge(t5Payload, {
    journalOpened: async (token, role) => {
      journalOpenedObservations.push({
        role,
        journalHasToken: !!readTransferJournal("main").entries[token],
        storeMutated: plainTabs.getState().tabs.some((tab) => tab.id === "w:moved"),
      });
      return true;
    },
  });
  assert.equal(
    await bridgeModule.stageIncomingTransfer(bridge, "t5-stage", { kind: "strip-end" }),
    true,
  );
  assert.deepEqual(journalOpenedObservations, [{
    role: "destination",
    journalHasToken: true,
    storeMutated: false,
  }]);
  assert.deepEqual(
    plainTabs.getState().tabs.map((tab) => tab.id),
    ["s:home", "w:moved"],
  );
  assert.equal(values.get("centerTabs:main"), committedCenterBytes);
  assert.equal(values.get("openprogram.sessionDraftState:main"), committedSessionBytes);
  assert.ok(bridgeModule.acceptedTransfers.has("t5-stage"));
  assert.ok(
    bridgeModule.serializeWebViewBookkeeping().liveIds.includes("w:moved"),
  );
  assert.deepEqual(calls.at(-1), ["destinationReady", "t5-stage", true]);

  // Destination rollback: bridge bookkeeping is forgotten BEFORE the store
  // reverts, acceptedTransfers clears, destinationUndone acknowledges; the
  // journal survives until main reports rolled-back.
  let forgottenBeforeStoreRevert = null;
  const unsubscribe = plainTabs.subscribe(() => {
    if (forgottenBeforeStoreRevert === null) {
      forgottenBeforeStoreRevert = !bridgeModule
        .serializeWebViewBookkeeping().liveIds.includes("w:moved");
    }
  });
  await bridgeModule.handleUndoDestination(bridge, { token: "t5-stage" });
  unsubscribe();
  assert.equal(forgottenBeforeStoreRevert, true);
  assert.deepEqual(plainTabs.getState().tabs.map((tab) => tab.id), ["s:home"]);
  assert.equal(bridgeModule.acceptedTransfers.has("t5-stage"), false);
  assert.deepEqual(calls.at(-1), ["destinationUndone", "t5-stage", true]);
  assert.ok(readTransferJournal("main").entries["t5-stage"]);
  await bridgeModule.handleTransferRolledBack(bridge, {
    token: "t5-stage",
    sourceId: "source",
    destinationId: "main",
  });
  assert.equal(readTransferJournal("main").entries["t5-stage"], undefined);
  assert.deepEqual(
    calls.at(-1),
    ["journalFinalized", "t5-stage", "destination"],
  );
  assert.equal(values.get("centerTabs:main"), committedCenterBytes);
}

// A rolled-back tear-off window is destroyed after destinationUndone. Its
// window-keyed tab/session/journal records must be removed before that ack.
{
  const detachedId = "window-rollback-cleanup";
  const keys = [
    `centerTabs:${detachedId}`,
    `openprogram.sessionDraftState:${detachedId}`,
    `openprogram.tabTransferJournal:${detachedId}`,
  ];
  for (const key of keys) values.set(key, "temporary");
  const { bridge, calls } = makeTransferBridge(t5Payload);
  bridge.windowId = detachedId;
  await bridgeModule.handleUndoDestination(bridge, {
    token: "detached-cleanup",
    discardWindowState: true,
  });
  for (const key of keys) assert.equal(values.has(key), false);
  assert.deepEqual(calls.at(-1), ["destinationUndone", "detached-cleanup", true]);
}

// Destination commit: journal after* persists, then accepted state and the
// journal entry are deleted.
{
  const { bridge, calls } = makeTransferBridge(t5Payload);
  assert.equal(
    await bridgeModule.stageIncomingTransfer(bridge, "t5-commit", { kind: "strip-end" }),
    true,
  );
  await bridgeModule.handleTransferCommitted(bridge, {
    token: "t5-commit",
    sourceId: "source",
    destinationId: "main",
  });
  const persisted = JSON.parse(values.get("centerTabs:main"));
  assert.deepEqual(persisted.tabs.map((tab) => tab.id), ["s:home", "w:moved"]);
  assert.equal(bridgeModule.acceptedTransfers.has("t5-commit"), false);
  assert.equal(readTransferJournal("main").entries["t5-commit"], undefined);
  assert.deepEqual(calls.at(-1), ["journalFinalized", "t5-commit", "destination"]);
}

// Simulated reload: a destination-staged journal rebuilds acceptedTransfers
// through the recovery handlers.
{
  const { bridge } = makeTransferBridge(t5Payload);
  const reloadPayload = {
    ...t5Payload,
    tabs: [{ id: "w:reloaded", kind: "web", title: "Reloaded", url: "https://reloaded.test/" }],
  };
  const reloadBefore = plainTabsModule.snapshotCenterTabsPayload();
  const reloadValidated = plainTabsModule.validateTransferredTabs(
    reloadPayload,
    { kind: "strip-end" },
  );
  assert.equal(reloadValidated.ok, true);
  const staged = {
    ...journalEntry("t5-reload"),
    payload: reloadPayload,
    beforeCenterTabs: reloadBefore,
    afterCenterTabs: reloadValidated.after,
    beforeSession: plainSessionModule.snapshotSessionTransfer([]),
    afterSession: plainSessionModule.snapshotSessionTransfer([]),
  };
  assert.equal(recoverTransferJournalEntry(
    staged,
    "destination-staged",
    bridgeModule.transferRecoveryHandlers(bridge),
    "main",
  ), true);
  assert.ok(bridgeModule.acceptedTransfers.has("t5-reload"));
  assert.ok(plainTabs.getState().tabs.some((tab) => tab.id === "w:reloaded"));
  bridgeModule.transferRecoveryHandlers(bridge).clearAccepted("t5-reload");
  assert.equal(bridgeModule.acceptedTransfers.has("t5-reload"), false);
  pendingProjection.unregisterPendingTransfer("t5-reload", "main");
  plainTabsModule.replaceCenterTabsPayload(reloadBefore, { persist: true });
}

// Duplicate: activates the existing destination tab, rejects with the
// duplicate id, and never touches accept/journal.
{
  const duplicatePayload = {
    ...t5Payload,
    tabs: [{ id: "s:home", kind: "session", title: "Home", sessionId: "home" }],
  };
  const { bridge, calls } = makeTransferBridge(duplicatePayload);
  plainTabs.getState().openNewTabPage();
  assert.notEqual(plainTabs.getState().activeId, "s:home");
  assert.equal(
    await bridgeModule.stageIncomingTransfer(bridge, "t5-dup", { kind: "strip-end" }),
    false,
  );
  assert.equal(plainTabs.getState().activeId, "s:home");
  assert.deepEqual(calls.at(-1), ["reject", "t5-dup", "duplicate", "s:home"]);
  assert.equal(calls.some(([name]) => name === "accept"), false);
  assert.equal(readTransferJournal("main").entries["t5-dup"], undefined);
  plainTabs.getState().closeTab(plainTabs.getState().tabs.at(-1).id);
}

// Full group: reject("group-full") before any accept/journal/mutation.
{
  const fullGroupPayload = {
    tabs: ["one", "two", "three", "four"].map((name) => ({
      id: `w:t5-${name}`,
      kind: "web",
      title: name,
      url: `https://${name}.t5.test/`,
    })),
    source: {
      windowId: "source",
      kind: "group",
      groupId: "g:t5-full",
      memberIds: ["w:t5-one", "w:t5-two", "w:t5-three", "w:t5-four"],
      visibleIds: ["w:t5-one", "w:t5-two"],
      focusedId: "w:t5-one",
    },
    fileDrafts: [],
    chats: [],
  };
  const { bridge, calls } = makeTransferBridge(fullGroupPayload);
  assert.equal(
    await bridgeModule.stageIncomingTransfer(bridge, "t5-full", { kind: "strip-end" }),
    false,
  );
  assert.deepEqual(calls.at(-1), ["reject", "t5-full", "group-full"]);
  assert.equal(calls.some(([name]) => name === "accept"), false);
}

// Source removal: journal before mutation, {persist:false} removal, commit
// finalizes durably; a failed main acknowledgement restores before* state.
const t5SourcePayload = {
  tabs: [{
    id: "s:local_move",
    kind: "session",
    title: "Draft move",
    sessionId: "local_move",
    draft: true,
  }],
  source: { windowId: "main", kind: "tab" },
  fileDrafts: [],
  chats: [{ chatKey: "local_move", composerDraft: "moving text", wasActive: false }],
};
const t5SourceBase = {
  ...t5Base,
  tabs: [
    ...t5Base.tabs,
    {
      id: "s:local_move",
      kind: "session",
      title: "Draft move",
      sessionId: "local_move",
      draft: true,
    },
  ],
};
{
  plainTabsModule.replaceCenterTabsPayload(t5SourceBase, { persist: true });
  plainSessionModule.applySessionTransfer({
    ...emptySessionSnapshot,
    composerDrafts: { local_move: "moving text" },
  }, { persist: true });
  const sourceJournalObservations = [];
  const { bridge, calls } = makeTransferBridge(t5SourcePayload, {
    journalOpened: async (token, role) => {
      sourceJournalObservations.push({
        role,
        journalHasToken: !!readTransferJournal("main").entries[token],
        tabStillPresent: plainTabs.getState().tabs
          .some((tab) => tab.id === "s:local_move"),
      });
      return true;
    },
  });
  await bridgeModule.handleRemoveSource(bridge, {
    token: "t5-source",
    payload: t5SourcePayload,
  });
  assert.deepEqual(sourceJournalObservations, [{
    role: "source",
    journalHasToken: true,
    tabStillPresent: true,
  }]);
  assert.deepEqual(plainTabs.getState().tabs.map((tab) => tab.id), ["s:home"]);
  const persistedCenter = JSON.parse(values.get("centerTabs:main"));
  assert.deepEqual(persistedCenter.tabs.map((tab) => tab.id), ["s:home"]);
  const persistedSession = JSON.parse(
    values.get("openprogram.sessionDraftState:main"),
  );
  assert.equal(persistedSession.composerDrafts.local_move, undefined);
  assert.equal(readTransferJournal("main").entries["t5-source"], undefined);
  assert.deepEqual(
    calls.filter(([name]) => name === "sourceRemoved" || name === "journalFinalized"),
    [
      ["sourceRemoved", "t5-source", true, false],
      ["journalFinalized", "t5-source", "source"],
    ],
  );
}

// Source removal with a stale main acknowledgement restores the tab and
// keeps the journal for the rolled-back event to finalize.
{
  plainTabsModule.replaceCenterTabsPayload(t5SourceBase, { persist: true });
  plainSessionModule.applySessionTransfer({
    ...emptySessionSnapshot,
    composerDrafts: { local_move: "moving text" },
  }, { persist: true });
  const { bridge, calls } = makeTransferBridge(t5SourcePayload, {
    sourceRemoved: async (...args) => {
      calls.push(["sourceRemoved", ...args]);
      return false;
    },
  });
  await bridgeModule.handleRemoveSource(bridge, {
    token: "t5-source-stale",
    payload: t5SourcePayload,
  });
  assert.deepEqual(
    plainTabs.getState().tabs.map((tab) => tab.id),
    ["s:home", "s:local_move"],
    "a stale main response must restore the source tabs locally",
  );
  assert.ok(readTransferJournal("main").entries["t5-source-stale"]);
  assert.equal(
    calls.some(([name]) => name === "journalFinalized"),
    false,
  );
  await bridgeModule.handleTransferRolledBack(bridge, {
    token: "t5-source-stale",
    sourceId: "main",
    destinationId: "other",
  });
  assert.equal(readTransferJournal("main").entries["t5-source-stale"], undefined);
  assert.deepEqual(
    calls.at(-1),
    ["journalFinalized", "t5-source-stale", "source"],
  );
  assert.deepEqual(
    plainTabs.getState().tabs.map((tab) => tab.id),
    ["s:home", "s:local_move"],
  );
}

// Rejected: clears the prepared drag coordinator token.
{
  const { dragCoordinator } = await import("../lib/tab-drag-coordinator.ts");
  dragCoordinator.prepare({
    subject: { kind: "tab", tabIds: ["s:home"] },
    transferToken: "t5-rejected",
    started: false,
    cancelled: false,
    committed: false,
  });
  bridgeModule.handleTransferRejected({ token: "t5-rejected", reason: "duplicate" });
  assert.equal(dragCoordinator.current(), null);
}

// A live orphan-finalization notification must not wait for the next renderer
// restart. The surviving window acknowledges the destroyed destination role.
{
  let orphanHandler = null;
  const { bridge, calls } = makeTransferBridge(t5Payload, {
    onFinalizeOrphaned: (handler) => {
      orphanHandler = handler;
      return () => {};
    },
  });
  const cleanup = bridgeModule.installTabTransferHandlers(bridge);
  assert.equal(typeof orphanHandler, "function");
  await orphanHandler({
    token: "detached-orphan",
    status: "rolled-back",
    role: "destination",
    windowId: "window-destroyed",
    orphaned: true,
  });
  assert.deepEqual(
    calls.at(-1),
    ["journalFinalized", "detached-orphan", "destination", "window-destroyed"],
  );
  cleanup();
}

// A detached destination whose first cleanup failed must be discarded by the
// surviving renderer before it acknowledges the orphaned journal role.
{
  const ownerWindowId = "window-detached-cleanup";
  const token = "detached-cleanup-retry";
  assert.equal(writeTransferJournal({
    ...journalEntry(token),
    role: "destination",
  }, ownerWindowId), true);
  values.set(`centerTabs:${ownerWindowId}`, "stale-center");
  values.set(`openprogram.sessionDraftState:${ownerWindowId}`, "stale-session");
  assert.equal(
    bridgeModule.finalizeOrphanTransferJournal(
      token,
      "rolled-back",
      ownerWindowId,
      true,
    ),
    true,
  );
  assert.equal(values.has(`centerTabs:${ownerWindowId}`), false);
  assert.equal(
    values.has(`openprogram.sessionDraftState:${ownerWindowId}`),
    false,
  );
  assert.equal(
    values.has(`openprogram.tabTransferJournal:${ownerWindowId}`),
    false,
  );
}

// Structure gates: dragstart stays synchronous and never awaits before
// writing DataTransfer.
const stripSource = readCenterTabStripSource(import.meta.url);
assert.doesNotMatch(
  stripSource,
  /async function onDragStart|onDragStart\s*[=:]\s*async/,
  "onDragStart must not be async",
);
assert.doesNotMatch(
  stripSource,
  /await[^\n]*setData/,
  "DataTransfer.setData must not be awaited",
);

// Task 6: pointer-down payload building and shared drop-intent geometry.
{
  const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
  const { useSessionStore } = await import("../lib/session-store/index.ts");
  const filesShared = await import("../lib/state/files-shared.ts");
  useCenterTabs.setState({
    tabs: [
      {
        id: "s:chatA",
        kind: "session",
        sessionId: "chatA",
        title: "Chat A",
        draft: false,
      },
      {
        id: "f:readme",
        kind: "file",
        projectId: "proj",
        path: "README.md",
        title: "README.md",
      },
    ],
    groups: [],
    activeId: "s:chatA",
  });
  useSessionStore.setState({
    activeChatKey: "chatA",
    composerDrafts: { chatA: "draft text" },
    composerSettingsBySession: { chatA: { model: "m1" } },
    pendingProjectsByChat: { chatA: "proj" },
  });
  const channelHostModule = await import(
    "../lib/runtime-bridge/draft-channel-choice.ts"
  );
  channelHostModule.draftChannelChoiceHost.__pendingChannelChoices = {
    chatA: { channel: "web" },
  };
  const draftKey = filesShared.fileDraftKey("proj", "README.md");
  filesShared.fileDrafts.set(draftKey, { content: "edited", baseMtime: 1 });

  const group = {
    id: "g:one",
    memberIds: ["s:chatA", "f:readme"],
    visibleIds: ["s:chatA"],
    focusedId: "s:chatA",
  };
  const payload = bridgeModule.buildTransferPayload(
    { kind: "group", tabIds: ["s:chatA", "f:readme"], sourceGroup: group },
    "win-src",
  );
  assert.deepEqual(payload.tabs.map((tab) => tab.id), ["s:chatA", "f:readme"]);
  assert.deepEqual(payload.source, {
    windowId: "win-src",
    kind: "group",
    groupId: "g:one",
    memberIds: ["s:chatA", "f:readme"],
    visibleIds: ["s:chatA"],
    focusedId: "s:chatA",
  });
  assert.deepEqual(payload.fileDrafts, [
    { key: draftKey, value: { content: "edited", baseMtime: 1 } },
  ]);
  assert.deepEqual(payload.chats, [
    {
      chatKey: "chatA",
      wasActive: true,
      composerDraft: "draft text",
      composerSettings: { model: "m1" },
      pendingProjectId: "proj",
      draftChannelChoice: { channel: "web" },
    },
  ]);
  const segmentPayload = bridgeModule.buildTransferPayload(
    {
      kind: "segment",
      tabIds: ["f:readme"],
      sourceGroup: group,
      memberIndex: 1,
    },
    "win-src",
  );
  assert.equal(segmentPayload.source.memberIndex, 1);
  assert.equal(segmentPayload.chats.length, 0);
  assert.equal(
    bridgeModule.buildTransferPayload(
      { kind: "tab", tabIds: ["missing"] },
      "win-src",
    ),
    null,
    "an unknown tab id must not produce a partial payload",
  );
  delete window.__pendingChannelChoices;
  filesShared.fileDrafts.delete(draftKey);

  assert.deepEqual(
    bridgeModule.placementForDropIntent({ mode: "before", targetTabId: "x" }),
    { kind: "before", targetTabId: "x" },
  );
  assert.deepEqual(
    bridgeModule.placementForDropIntent({
      mode: "merge",
      targetTabId: "x",
      groupId: "g",
      memberIndex: 2,
    }),
    { kind: "merge", targetTabId: "x", groupId: "g", memberIndex: 2 },
  );
  assert.deepEqual(
    bridgeModule.placementForDropIntent({ mode: "after", targetTabId: "x" }),
    { kind: "after", targetTabId: "x" },
  );
}

// ---------------------------------------------------------------- Task 7:
// renderer startup recovery order — journal recovery, terminal/orphan acks,
// native reconciliation, then claimPending for a detached window.

// Fresh detached claim: pendingTerminal is consulted before claimPending,
// reconcile runs between them, and the claimed token stages at strip end.
{
  plainTabsModule.replaceCenterTabsPayload(t5Base, { persist: true });
  plainSessionModule.applySessionTransfer(emptySessionSnapshot, { persist: true });
  const { bridge, calls } = makeTransferBridge(t5Payload, {
    claimPending: async (windowId) => {
      calls.push(["claimPending", windowId]);
      return "t7-claim";
    },
  });
  let reconciledAt = -1;
  await bridgeModule.recoverPendingTabTransfers(bridge, () => {
    reconciledAt = calls.length;
  });
  const order = calls.map(([name]) => name);
  const terminalIndex = order.indexOf("pendingTerminal");
  const claimIndex = order.indexOf("claimPending");
  assert.ok(terminalIndex >= 0 && claimIndex > terminalIndex);
  assert.ok(
    reconciledAt > terminalIndex && reconciledAt <= claimIndex,
    "native reconciliation must run after terminal cleanup, before claim",
  );
  assert.ok(plainTabs.getState().tabs.some((tab) => tab.id === "w:moved"));
  assert.ok(bridgeModule.acceptedTransfers.has("t7-claim"));
  // startup staging leaves the destination journal pending until commit
  assert.ok(readTransferJournal("main").entries["t7-claim"]);
  await bridgeModule.handleTransferCommitted(bridge, {
    token: "t7-claim",
    sourceId: "source",
    destinationId: "main",
  });
  plainTabsModule.replaceCenterTabsPayload(t5Base, { persist: true });
}

// A token already represented by a recovered journal resumes idempotently:
// no second accept/insert, and its live pre-commit journal defers the
// journalFinalized ack to the normal commit/rollback path.
{
  plainTabsModule.replaceCenterTabsPayload(t5Base, { persist: true });
  const resumePayload = {
    ...t5Payload,
    tabs: [{ id: "w:resume", kind: "web", title: "Resume", url: "https://resume.test/" }],
  };
  const validated = plainTabsModule.validateTransferredTabs(
    resumePayload,
    { kind: "strip-end" },
  );
  assert.equal(validated.ok, true);
  assert.equal(writeTransferJournal({
    ...journalEntry("t7-resume"),
    payload: resumePayload,
    beforeCenterTabs: plainTabsModule.snapshotCenterTabsPayload(),
    afterCenterTabs: validated.after,
    beforeSession: plainSessionModule.snapshotSessionTransfer([]),
    afterSession: plainSessionModule.snapshotSessionTransfer([]),
  }, "main"), true);
  const { bridge, calls } = makeTransferBridge(resumePayload, {
    status: async () => ({
      status: "destination-staged",
      sourceId: "source",
      destinationId: "main",
    }),
    claimPending: async () => "t7-resume",
    pendingTerminal: async () => [{
      token: "t7-resume",
      status: "committed",
      role: "destination",
      windowId: "main",
      orphaned: false,
    }],
  });
  await bridgeModule.recoverPendingTabTransfers(bridge);
  assert.equal(calls.some(([name]) => name === "accept"), false);
  assert.equal(
    plainTabs.getState().tabs.filter((tab) => tab.id === "w:resume").length,
    1,
  );
  assert.ok(bridgeModule.acceptedTransfers.has("t7-resume"));
  assert.equal(
    calls.some(([name]) => name === "journalFinalized"),
    false,
    "a live pre-commit journal must not be acked during startup",
  );
  await bridgeModule.handleTransferCommitted(bridge, {
    token: "t7-resume",
    sourceId: "source",
    destinationId: "main",
  });
  plainTabsModule.replaceCenterTabsPayload(t5Base, { persist: true });
}

// Own terminal role whose journal was cleared before the crash: idempotent
// journalFinalized ack, no state change.
{
  const { bridge, calls } = makeTransferBridge(t5Payload, {
    pendingTerminal: async () => [{
      token: "t7-ack",
      status: "committed",
      role: "source",
      windowId: "main",
      orphaned: false,
    }],
  });
  const beforeBytes = values.get("centerTabs:main");
  await bridgeModule.recoverPendingTabTransfers(bridge);
  assert.deepEqual(
    calls.find(([name]) => name === "journalFinalized"),
    ["journalFinalized", "t7-ack", "source", "main"],
  );
  assert.equal(values.get("centerTabs:main"), beforeBytes);
}

// Orphan cleanup: apply the durable decision to the destroyed owner's keyed
// storage, delete that keyed journal, then ack the orphan role.
{
  const orphanEntry = {
    ...journalEntry("t7-orphan"),
    role: "destination",
  };
  assert.equal(writeTransferJournal(orphanEntry, "win-orphan"), true);
  const { bridge, calls } = makeTransferBridge(t5Payload, {
    pendingTerminal: async () => [{
      token: "t7-orphan",
      status: "committed",
      role: "destination",
      windowId: "win-orphan",
      orphaned: true,
    }],
  });
  await bridgeModule.recoverPendingTabTransfers(bridge);
  assert.deepEqual(
    JSON.parse(values.get("centerTabs:win-orphan")),
    orphanEntry.afterCenterTabs,
  );
  assert.equal(
    JSON.parse(values.get("openprogram.sessionDraftState:win-orphan"))
      .composerDrafts.local_one,
    orphanEntry.afterSession.composerDrafts.local_one,
  );
  assert.deepEqual(readTransferJournal("win-orphan"), { version: 1, entries: {} });
  assert.deepEqual(
    calls.find(([name]) => name === "journalFinalized"),
    ["journalFinalized", "t7-orphan", "destination", "win-orphan"],
  );
  pendingProjection.unregisterPendingTransfer("t7-orphan", "win-orphan");
}

console.log("web-split checks passed");
