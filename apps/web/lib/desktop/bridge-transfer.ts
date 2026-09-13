// Desktop bridge transfer responsibilities.
import { sessionHistory } from "../tabs/session-tab-history";
/**
 * Desktop bridge — typed accessor for the Electron preload API
 * (`window.openprogramDesktop`) plus the renderer-side bookkeeping the
 * contract leaves to us:
 *
 *  - which native web views this renderer has created (the bridge has
 *    no list call, so destroying views after their tab closes works
 *    off a local set);
 *  - the app-menu DOM CustomEvents ("op-desktop-new-tab" /
 *    "op-desktop-close-tab") wired into the center-tabs store.
 *
 * Absent bridge (plain browser) ⇒ every helper is a no-op and
 * desktopBridge() returns null; callers keep their web fallbacks.
 */
import { insertTransferredTabs, rebaseCenterTabsPayload, removeTransferredTabs, replaceCenterTabsPayload, snapshotCenterTabsPayload, useCenterTabs, validateTransferredTabs } from "@/lib/tabs/center-tabs-store";
import { applySessionTransfer, snapshotSessionTransfer } from "@/lib/session-store";
import { applyFileDraftSnapshot, snapshotFileDrafts } from "@/lib/files/files-shared";
import { registerPendingTransfer, unregisterPendingTransfer } from "@/lib/tabs/pending-transfer-projection";
import { deleteTransferJournal, finalizeTransferJournal, readTransferJournal, rebaseSessionSnapshot, recoverTransferJournalEntry, transferJournalStorageKey, writeTransferJournal } from "@/lib/tabs/tab-transfer-journal";
import "@/lib/net/ws-events";
import { sessionDraftStorageKey } from "@/lib/chat/session-draft-persistence";
import type { ChatTransferState, DesktopTransferPayload, SerializedWebViewBookkeeping, SessionTransferSnapshot, TabDropPlacement, TransferJournalEntry, TransferMainStatus, TransferRecoveryHandlers } from "@/lib/tabs/tab-transfer-journal";
import { dragCoordinator } from "@/lib/tabs/tab-drag-coordinator";
import type { TabDragSubject, TabDropIntent } from "@/lib/tabs/tab-drag-coordinator";
import { useSessionStore } from "@/lib/session-store";
import { fileDraftKey, fileDrafts } from "@/lib/files/files-shared";
import { draftChannelChoiceFor, draftChannelChoiceHost } from "@/lib/runtime-bridge/draft-channel-choice";
import type { DesktopTransferReceipt } from "@/lib/desktop/desktop-transfer-types";
import { invalidateWebTabGeometry, liveViewIds, readyWebTabIds, scheduleVisibleWebBoundsFlush, transferLockedIds, visibleWebBounds, webTabGeometryRevisions, webTabReadyWaiters } from "./bridge-state";
import { type DesktopBridge } from "./bridge-api";



// ------------------------------------------------------------- tab transfer
//
// Renderer side of the cross-window transfer transaction. Order is the
// contract (see the multiwindow plan's non-negotiable invariants):
//   destination: journal write+read-back → journalOpened → {persist:false}
//                mutations → destinationReady;
//   source:      journal → journalOpened → {persist:false} removal →
//                sourceRemoved; commit persists via finalizeTransferJournal;
//   undo:        forget bridge bookkeeping FIRST, then revert transient
//                store/session state, then acknowledge destinationUndone.

export interface AcceptedTransfer {
  token: string;
  insertedIds: string[];
  journal: TransferJournalEntry;
  inMemoryBridgeRecovery: SerializedWebViewBookkeeping;
}

export const acceptedTransfers = new Map<string, AcceptedTransfer>();

/** In-memory ready-waiter snapshots per source-side token (not journaled —
 *  a reloaded renderer recreates waiters through the ensure path). */
export const sourceWaiterRecovery = new Map<
  string,
  Map<string, Set<(ready: boolean) => void> | undefined>
>();

export function transferWebIds(payload: DesktopTransferPayload): string[] {
  return payload.tabs.flatMap((tab) => (tab.kind === "web" ? [tab.id] : []));
}

export function serializeWebViewBookkeeping(): SerializedWebViewBookkeeping {
  return {
    liveIds: [...liveViewIds],
    readyIds: [...readyWebTabIds],
    visibleBounds: Array.from(visibleWebBounds, ([id, bounds]) => ({
      id,
      bounds: { ...bounds },
    })),
  };
}

export function applyWebViewBookkeeping(
  snapshot: SerializedWebViewBookkeeping,
  bridge?: DesktopBridge | null,
): void {
  liveViewIds.clear();
  readyWebTabIds.clear();
  visibleWebBounds.clear();
  webTabGeometryRevisions.clear();
  for (const id of snapshot.liveIds) liveViewIds.add(id);
  for (const id of snapshot.readyIds) readyWebTabIds.add(id);
  for (const { id, bounds } of snapshot.visibleBounds) {
    visibleWebBounds.set(id, { ...bounds });
    invalidateWebTabGeometry(bridge, id);
  }
  if (bridge) scheduleVisibleWebBoundsFlush(bridge);
}

export function forgetTransferredWebView(
  bridge: DesktopBridge | null,
  id: string,
): void {
  liveViewIds.delete(id);
  readyWebTabIds.delete(id);
  webTabGeometryRevisions.delete(id);
  if (visibleWebBounds.delete(id) && bridge) {
    scheduleVisibleWebBoundsFlush(bridge);
  }
}

export function unlockTransfer(payload: DesktopTransferPayload): void {
  for (const id of transferWebIds(payload)) transferLockedIds.delete(id);
}

export function destinationSessionAfter(
  before: SessionTransferSnapshot,
  payload: DesktopTransferPayload,
): SessionTransferSnapshot {
  const after = structuredClone(before);
  for (const chat of payload.chats) {
    if (chat.composerDraft !== undefined) {
      after.composerDrafts[chat.chatKey] = chat.composerDraft;
    }
    if (chat.composerSettings) {
      after.composerSettingsBySession[chat.chatKey] = chat.composerSettings;
    }
    if (chat.pendingProjectId) {
      after.pendingProjectsByChat[chat.chatKey] = chat.pendingProjectId;
    }
    if (chat.draftChannelChoice) {
      after.draftChannelChoices[chat.chatKey] = chat.draftChannelChoice;
    }
    if (chat.wasActive) {
      const tab = payload.tabs.find((item) => item.sessionId === chat.chatKey);
      after.activeChatKey = chat.chatKey;
      after.currentSessionId = tab?.draft ? null : chat.chatKey;
      // The focused chat's live draft and settings are no longer separate
      // fields — they ARE `composerDrafts[activeChatKey]` /
      // `composerSettingsBySession[activeChatKey]`, already written above.
    }
  }
  return after;
}

export function sourceSessionAfter(
  before: SessionTransferSnapshot,
  payload: DesktopTransferPayload,
  afterCenter: ReturnType<typeof snapshotCenterTabsPayload>,
): SessionTransferSnapshot {
  const after = structuredClone(before);
  const moved = new Set<string>();
  for (const chat of payload.chats) {
    moved.add(chat.chatKey);
    if (afterCenter.tabs.some(tab => tab.kind === "session"
      && sessionHistory(tab).entries.some(entry => entry.sessionId === chat.chatKey))) continue;
    delete after.composerDrafts[chat.chatKey];
    delete after.composerSettingsBySession[chat.chatKey];
    delete after.pendingProjectsByChat[chat.chatKey];
    delete after.draftChannelChoices[chat.chatKey];
  }
  if (before.activeChatKey && moved.has(before.activeChatKey)) {
    const active = afterCenter.tabs.find((tab) => tab.id === afterCenter.activeId);
    const key = active?.kind === "session" ? active.sessionId ?? null : null;
    after.activeChatKey = key;
    after.currentSessionId = active?.kind === "session" && !active.draft
      ? key
      : null;
    // Nothing else to move: the newly focused chat's draft and settings are
    // whatever its entries in the keyed maps already say.
  }
  return after;
}

export function transferRecoveryHandlers(
  bridge: DesktopBridge | null,
): TransferRecoveryHandlers {
  return {
    applyCenterTabs: (payload, options) =>
      replaceCenterTabsPayload(payload, options),
    applySession: (snapshot, options) => applySessionTransfer(snapshot, options),
    applyFileDrafts: applyFileDraftSnapshot,
    applyBridge: (snapshot) => applyWebViewBookkeeping(snapshot, bridge),
    rebuildAccepted: (entry) => {
      for (const id of transferWebIds(entry.payload)) transferLockedIds.add(id);
      acceptedTransfers.set(entry.token, {
        token: entry.token,
        insertedIds: entry.payload.tabs.map((tab) => tab.id),
        journal: entry,
        inMemoryBridgeRecovery: entry.beforeBridge,
      });
    },
    resumeSourceRemoved: (entry) => {
      const transfer = bridge?.tabTransfer;
      if (!transfer) return false;
      for (const id of transferWebIds(entry.payload)) transferLockedIds.add(id);
      void transfer
        .sourceRemoved(entry.token, true, entry.afterCenterTabs.tabs.length === 0)
        .then((ok) => {
          if (ok) return finalizeSourceCommit(bridge!, entry.token);
          restoreSourceBefore(bridge!, entry);
          return undefined;
        })
        .catch(() => restoreSourceBefore(bridge!, entry));
    },
    clearAccepted: (token) => {
      const accepted = acceptedTransfers.get(token);
      if (accepted) unlockTransfer(accepted.journal.payload);
      acceptedTransfers.delete(token);
      const journaled = readTransferJournal().entries[token];
      if (journaled) unlockTransfer(journaled.payload);
      sourceWaiterRecovery.delete(token);
    },
    deleteJournal: (token) => deleteTransferJournal(token),
    snapshotCenterTabs: snapshotCenterTabsPayload,
    snapshotSession: () => snapshotSessionTransfer([]),
  };
}

/** Fixed destination-undo order: bridge bookkeeping first, then the
 *  transient store/session/file-draft state, then accepted-map cleanup.
 *  The journal itself outlives this call — rolled-back deletes it. */
export function undoDestinationLocal(
  bridge: DesktopBridge | null,
  entry: TransferJournalEntry,
): void {
  for (const id of transferWebIds(entry.payload)) {
    forgetTransferredWebView(bridge, id);
  }
  replaceCenterTabsPayload(
    rebaseCenterTabsPayload(snapshotCenterTabsPayload(), entry, "before"),
    { persist: false },
  );
  applySessionTransfer(
    rebaseSessionSnapshot(snapshotSessionTransfer([]), entry, "before"),
    { persist: false },
  );
  applyFileDraftSnapshot(entry.beforeFileDrafts);
  acceptedTransfers.delete(entry.token);
  unlockTransfer(entry.payload);
}

/** Same 25/50/25 drop intent, expressed as a main-process placement. */
export function placementForDropIntent(intent: TabDropIntent): TabDropPlacement {
  if (intent.mode === "merge") {
    const placement: TabDropPlacement = {
      kind: "merge",
      targetTabId: intent.targetTabId,
    };
    if (intent.groupId !== undefined) placement.groupId = intent.groupId;
    if (intent.memberIndex !== undefined) placement.memberIndex = intent.memberIndex;
    return placement;
  }
  return { kind: intent.mode, targetTabId: intent.targetTabId };
}

/** Pointer-down payload for tabTransfer.prepare — the dragged tabs plus
 *  every piece of chat/file draft state they own in this window. */
export function buildTransferPayload(
  subject: TabDragSubject,
  windowId: string,
): DesktopTransferPayload | null {
  const centerTabs = useCenterTabs.getState().tabs;
  const tabs = [];
  for (const tabId of subject.tabIds) {
    const tab = centerTabs.find((candidate) => candidate.id === tabId);
    if (!tab) return null;
    tabs.push(structuredClone(tab));
  }
  const session = useSessionStore.getState();
  const host = draftChannelChoiceHost;
  const chats: ChatTransferState[] = [];
  const payloadFileDrafts: DesktopTransferPayload["fileDrafts"] = [];
  for (const tab of tabs) {
    if (tab.kind === "session" && tab.sessionId) {
      for (const entry of sessionHistory(tab).entries) {
        const chatKey = entry.sessionId;
        if (!chatKey || chats.some(chat => chat.chatKey === chatKey)) continue;
        const wasActive = tab.sessionId === chatKey && session.activeChatKey === chatKey;
        const chat: ChatTransferState = { chatKey, wasActive };
        if (session.composerDrafts[chatKey] !== undefined) {
          chat.composerDraft = session.composerDrafts[chatKey];
        }
        if (session.composerSettingsBySession[chatKey]) {
          chat.composerSettings = structuredClone(
            session.composerSettingsBySession[chatKey],
          );
        }
        if (session.pendingProjectsByChat[chatKey]) {
          chat.pendingProjectId = session.pendingProjectsByChat[chatKey];
        }
        const choice = draftChannelChoiceFor(host, chatKey);
        if (choice) chat.draftChannelChoice = structuredClone(choice);
        chats.push(chat);
      }
    } else if (tab.kind === "file" && tab.projectId && tab.path) {
      const key = fileDraftKey(tab.projectId, tab.path);
      const value = fileDrafts.get(key);
      if (value) payloadFileDrafts.push({ key, value: structuredClone(value) });
    }
  }
  const source: DesktopTransferPayload["source"] = {
    windowId,
    kind: subject.kind,
  };
  if (subject.kind !== "tab") {
    source.groupId = subject.sourceGroup.id;
    source.memberIds = [...subject.sourceGroup.memberIds];
    source.visibleIds = [...subject.sourceGroup.visibleIds];
    if (subject.sourceGroup.focusedId !== undefined) {
      source.focusedId = subject.sourceGroup.focusedId;
    }
  }
  if (subject.kind === "segment") source.memberIndex = subject.memberIndex;
  return { tabs, source, fileDrafts: payloadFileDrafts, chats };
}

/** Destination staging. Returns true when destinationReady(true) was sent. */
export async function stageIncomingTransfer(
  bridge: DesktopBridge,
  token: string,
  placement: TabDropPlacement,
): Promise<boolean> {
  const transfer = bridge.tabTransfer;
  const inspected = await transfer.inspect(token);
  if (!inspected) return false;
  const validated = validateTransferredTabs(inspected.payload, placement);
  if (!validated.ok) {
    if (validated.reason === "duplicate" && validated.duplicateId) {
      useCenterTabs.getState().setActive(validated.duplicateId);
      await transfer.reject(token, "duplicate", validated.duplicateId);
    } else if (validated.reason === "group-full") {
      await transfer.reject(token, "group-full");
    }
    // "invalid" has no reject channel; the prepared token expires in main.
    return false;
  }
  const accepted = await transfer.accept(token, placement);
  if (!accepted) return false;
  const payload = accepted.payload;
  const webIds = transferWebIds(payload);
  for (const id of webIds) transferLockedIds.add(id);

  const beforeBridge = serializeWebViewBookkeeping();
  const beforeSession = snapshotSessionTransfer(
    payload.chats.map((chat) => chat.chatKey),
  );
  const afterSession = destinationSessionAfter(beforeSession, payload);
  const draftKeys = payload.fileDrafts.map((draft) => draft.key);
  const entry: TransferJournalEntry = {
    version: 1,
    token,
    role: "destination",
    phase: "staged",
    payload,
    placement: accepted.placement,
    beforeCenterTabs: snapshotCenterTabsPayload(),
    afterCenterTabs: validated.after,
    beforeSession,
    afterSession,
    beforeFileDrafts: snapshotFileDrafts(draftKeys),
    afterFileDrafts: payload.fileDrafts.map(({ key, value }) => ({
      key,
      existed: true,
      value,
    })),
    beforeBridge,
    afterBridge: {
      ...beforeBridge,
      liveIds: [...new Set([...beforeBridge.liveIds, ...webIds])],
    },
  };

  const fail = async () => {
    undoDestinationLocal(bridge, entry);
    unregisterPendingTransfer(token);
    deleteTransferJournal(token);
    await transfer.destinationReady(token, false);
    return false;
  };
  if (!writeTransferJournal(entry)) {
    unlockTransfer(payload);
    await transfer.destinationReady(token, false);
    return false;
  }
  registerPendingTransfer(entry);
  if (!(await transfer.journalOpened(token, "destination"))) return fail();
  try {
    const inserted = insertTransferredTabs(payload, accepted.placement, {
      persist: false,
    });
    if (!inserted.ok) return fail();
    if (!applySessionTransfer(afterSession, { persist: false })) return fail();
    applyFileDraftSnapshot(entry.afterFileDrafts);
    for (const id of webIds) liveViewIds.add(id);
  } catch {
    return fail();
  }
  acceptedTransfers.set(token, {
    token,
    insertedIds: payload.tabs.map((tab) => tab.id),
    journal: entry,
    inMemoryBridgeRecovery: beforeBridge,
  });
  return transfer.destinationReady(token, true);
}

export function restoreSourceBefore(
  bridge: DesktopBridge,
  entry: TransferJournalEntry,
): void {
  replaceCenterTabsPayload(
    rebaseCenterTabsPayload(snapshotCenterTabsPayload(), entry, "before"),
    { persist: false },
  );
  applySessionTransfer(
    rebaseSessionSnapshot(snapshotSessionTransfer([]), entry, "before"),
    { persist: false },
  );
  applyFileDraftSnapshot(entry.beforeFileDrafts);
  applyWebViewBookkeeping(entry.beforeBridge, bridge);
  const waiters = sourceWaiterRecovery.get(entry.token);
  if (waiters) {
    for (const [id, set] of waiters) {
      if (set) webTabReadyWaiters.set(id, set);
    }
    sourceWaiterRecovery.delete(entry.token);
  }
  unlockTransfer(entry.payload);
  // The journal entry stays: main's rolled-back event finalizes it.
}

export async function finalizeSourceCommit(
  bridge: DesktopBridge,
  token: string,
): Promise<void> {
  if (finalizeTransferJournal(token, "commit", transferRecoveryHandlers(bridge))) {
    await bridge.tabTransfer.journalFinalized(token, "source");
  }
}

/** Source side of remove-source. */
export async function handleRemoveSource(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const transfer = bridge.tabTransfer;
  const payload = detail.payload;
  const token = detail.token;
  if (!payload || !token) return;
  const ids = payload.tabs.map((tab) => tab.id);
  const webIds = transferWebIds(payload);
  for (const id of webIds) transferLockedIds.add(id);

  const beforeBridge = serializeWebViewBookkeeping();
  const beforeSession = snapshotSessionTransfer(
    payload.chats.map((chat) => chat.chatKey),
  );
  // Dry-run the removal to learn the post-removal payload for the journal,
  // then revert; the journal must be durable before the real mutation.
  const removal = removeTransferredTabs(ids, { persist: false, expectedTabs: payload.tabs });
  if (!removal.ok) {
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  replaceCenterTabsPayload(removal.before, { persist: false });
  const afterSession = sourceSessionAfter(beforeSession, payload, removal.after);
  const draftKeys = payload.fileDrafts.map((draft) => draft.key);
  const entry: TransferJournalEntry = {
    version: 1,
    token,
    role: "source",
    phase: "staged",
    payload,
    beforeCenterTabs: removal.before,
    afterCenterTabs: removal.after,
    beforeSession,
    afterSession,
    beforeFileDrafts: snapshotFileDrafts(draftKeys),
    afterFileDrafts: draftKeys.map((key) => ({ key, existed: false })),
    beforeBridge,
    afterBridge: {
      liveIds: beforeBridge.liveIds.filter((id) => !webIds.includes(id)),
      readyIds: beforeBridge.readyIds.filter((id) => !webIds.includes(id)),
      visibleBounds: beforeBridge.visibleBounds.filter(
        (item) => !webIds.includes(item.id),
      ),
    },
  };
  if (!writeTransferJournal(entry)) {
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  registerPendingTransfer(entry);
  if (!(await transfer.journalOpened(token, "source"))) {
    unregisterPendingTransfer(token);
    deleteTransferJournal(token);
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  // A navigation during journalOpened invalidates the prepared snapshot.
  // No source mutation has occurred: discard the staged journal instead of
  // restoring its old before-image over the user's newer navigation.
  const currentRemoval = removeTransferredTabs(ids, { persist: false, expectedTabs: payload.tabs });
  if (!currentRemoval.ok) {
    unregisterPendingTransfer(token);
    deleteTransferJournal(token);
    replaceCenterTabsPayload(snapshotCenterTabsPayload(), { persist: true });
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  sourceWaiterRecovery.set(
    token,
    new Map(webIds.map((id) => [id, webTabReadyWaiters.get(id)])),
  );
  let removed = false;
  try {
    removed = applySessionTransfer(afterSession, { persist: false });
    if (removed) {
      applyFileDraftSnapshot(entry.afterFileDrafts);
      for (const id of webIds) forgetTransferredWebView(bridge, id);
    }
  } catch {
    removed = false;
  }
  if (!removed) {
    restoreSourceBefore(bridge, entry);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  const acknowledged = await transfer
    .sourceRemoved(token, true, removal.empty)
    .catch(() => false);
  if (!acknowledged) {
    // Stale/raced acknowledgement: restore locally right away.
    restoreSourceBefore(bridge, entry);
    return;
  }
  await finalizeSourceCommit(bridge, token);
}

/** Destination side of undo-destination. */
export async function handleUndoDestination(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const token = detail.token;
  const entry = acceptedTransfers.get(token)?.journal
    ?? readTransferJournal().entries[token];
  if (entry) undoDestinationLocal(bridge, entry);
  let cleaned = true;
  if (detail.discardWindowState) {
    const keys = [
      `centerTabs:${bridge.windowId}`,
      sessionDraftStorageKey(bridge.windowId),
      transferJournalStorageKey(bridge.windowId),
    ];
    try {
      for (const key of keys) localStorage.removeItem(key);
      cleaned = keys.every((key) => localStorage.getItem(key) === null);
    } catch {
      cleaned = false;
    }
  }
  await bridge.tabTransfer.destinationUndone(token, cleaned);
}

export function transferRole(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): "source" | "destination" {
  return detail.sourceId === bridge.windowId ? "source" : "destination";
}

export async function handleTransferCommitted(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const handlers = transferRecoveryHandlers(bridge);
  const role = transferRole(bridge, detail);
  if (finalizeTransferJournal(detail.token, "commit", handlers)) {
    await bridge.tabTransfer.journalFinalized(detail.token, role);
  }
  handlers.clearAccepted?.(detail.token);
}

export async function handleTransferRolledBack(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const handlers = transferRecoveryHandlers(bridge);
  const role = transferRole(bridge, detail);
  const finalized = finalizeTransferJournal(detail.token, "rollback", handlers);
  handlers.clearAccepted?.(detail.token);
  // The acknowledgement is mandatory for both roles — a source whose
  // journal never existed (pre-journal rollback) still acknowledges;
  // main ignores roles it never recorded.
  if (finalized || role === "source") {
    await bridge.tabTransfer.journalFinalized(detail.token, role);
  }
}

export function handleTransferRejected(detail: DesktopTransferReceipt): void {
  const prepared = dragCoordinator.current();
  if (prepared?.transferToken === detail.token) dragCoordinator.cancel();
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("op-tab-transfer-rejected", { detail }),
    );
  }
}

export async function handleFinalizeOrphaned(
  bridge: DesktopBridge,
  detail: {
    token: string;
    status: "committed" | "rolled-back";
    role: "source" | "destination";
    windowId: string;
    orphaned: boolean;
    discardWindowState?: boolean;
  },
): Promise<void> {
  if (!detail.orphaned) return;
  if (!finalizeOrphanTransferJournal(
    detail.token,
    detail.status,
    detail.windowId,
    detail.discardWindowState,
  )) return;
  await bridge.tabTransfer.journalFinalized(
    detail.token,
    detail.role,
    detail.windowId,
  );
}

export let transferHandlersInstalled = false;

/** Wire the transfer event channels. Idempotent per renderer. Returns a
 *  cleanup that cancels a still-prepared drag token and unsubscribes; it
 *  intentionally leaves unresolved journal entries for startup recovery. */
export function installTabTransferHandlers(bridge: DesktopBridge): () => void {
  const transfer = bridge.tabTransfer;
  if (!transfer || transferHandlersInstalled) return () => {};
  transferHandlersInstalled = true;
  const subscriptions = [
    transfer.onRemoveSource((detail) => {
      void handleRemoveSource(bridge, detail);
    }),
    transfer.onUndoDestination((detail) => {
      void handleUndoDestination(bridge, detail);
    }),
    transfer.onCommitted((detail) => {
      void handleTransferCommitted(bridge, detail);
    }),
    transfer.onRolledBack((detail) => {
      void handleTransferRolledBack(bridge, detail);
    }),
    transfer.onRejected((detail) => handleTransferRejected(detail)),
    transfer.onFinalizeOrphaned((detail) =>
      handleFinalizeOrphaned(bridge, detail)),
    // Pointer-driven cross-window drop: the source window delivered a
    // prepared token here; stage it at the end of this window's strip.
    transfer.onStageIncoming?.((detail) => {
      void stageIncomingTransfer(bridge, detail.token, { kind: "strip-end" });
    }) ?? (() => {}),
  ];
  return () => {
    transferHandlersInstalled = false;
    const prepared = dragCoordinator.current();
    if (prepared?.transferToken && !prepared.committed) {
      void transfer.cancel(prepared.transferToken);
      dragCoordinator.cancel();
    }
    for (const unsubscribe of subscriptions) unsubscribe();
  };
}

// --------------------------------------------------------- startup recovery

/** Finalize a destroyed participant's keyed journal on its behalf: apply the
 *  durable decision's outcome directly to the owner window's normal storage
 *  keys, then delete that keyed journal. Returns false when nothing durable
 *  could be written (the ack must then wait for the next recovery pass). */
export function finalizeOrphanTransferJournal(
  token: string,
  status: "committed" | "rolled-back",
  ownerWindowId: string,
  discardWindowState = false,
): boolean {
  if (status === "rolled-back" && discardWindowState) {
    const keys = [
      `centerTabs:${ownerWindowId}`,
      sessionDraftStorageKey(ownerWindowId),
      transferJournalStorageKey(ownerWindowId),
    ];
    try {
      for (const key of keys) localStorage.removeItem(key);
      return keys.every((key) => localStorage.getItem(key) === null);
    } catch {
      return false;
    }
  }
  const entry = readTransferJournal(ownerWindowId).entries[token];
  if (!entry) return true; // journal already cleaned — ack is idempotent
  const target = status === "committed" ? "after" : "before";
  const center = target === "after" ? entry.afterCenterTabs : entry.beforeCenterTabs;
  const session = target === "after" ? entry.afterSession : entry.beforeSession;
  try {
    localStorage.setItem(`centerTabs:${ownerWindowId}`, JSON.stringify(center));
    localStorage.setItem(
      sessionDraftStorageKey(ownerWindowId),
      JSON.stringify({
        version: 1,
        composerDrafts: session.composerDrafts,
        composerSettingsBySession: session.composerSettingsBySession,
        pendingProjectsByChat: session.pendingProjectsByChat,
        draftChannelChoices: session.draftChannelChoices,
      }),
    );
  } catch {
    return false;
  }
  return deleteTransferJournal(token, ownerWindowId);
}

/** Renderer startup recovery, in the plan's fixed order: resolve every
 *  journal entry against main's token status, acknowledge terminal/orphan
 *  decisions, reconcile ordinary native views, then pull a detached
 *  window's pending token. */
export async function recoverPendingTabTransfers(
  bridge: DesktopBridge,
  reconcile?: () => void,
): Promise<void> {
  const transfer = bridge.tabTransfer;
  if (!transfer) {
    reconcile?.();
    return;
  }
  const handlers = transferRecoveryHandlers(bridge);
  const journaledTokens = new Set(Object.keys(readTransferJournal().entries));
  for (const token of journaledTokens) {
    const entry = readTransferJournal().entries[token];
    if (!entry) continue;
    let status: TransferMainStatus = "stale";
    try {
      const receipt = await transfer.status(token);
      if (receipt) status = receipt.status as TransferMainStatus;
    } catch {
      /* unreachable main — resolve as stale */
    }
    recoverTransferJournalEntry(entry, status, handlers);
  }
  try {
    for (const item of await transfer.pendingTerminal(bridge.windowId)) {
      if (item.orphaned) {
        if (!finalizeOrphanTransferJournal(
          item.token,
          item.status,
          item.windowId,
          item.discardWindowState,
        )) {
          continue;
        }
      } else if (readTransferJournal().entries[item.token]) {
        // Own journal still live (pre-commit): its normal commit/rollback
        // handler performs the mandatory journalFinalized ack.
        continue;
      }
      // Own role with a cleared journal: journals are deleted only after
      // their outcome persisted, so committed storage already matches the
      // decision and the ack is idempotent.
      await transfer.journalFinalized(item.token, item.role, item.windowId);
    }
  } catch {
    /* decision store unreadable — re-query on the next recovery pass */
  }
  reconcile?.();
  try {
    const token = await transfer.claimPending(bridge.windowId);
    if (token && !journaledTokens.has(token)) {
      await stageIncomingTransfer(bridge, token, { kind: "strip-end", consumePlaceholder: true });
    }
    // A token already represented by a recovered journal entry resumes
    // through that entry (idempotent) — staging again would double-insert.
  } catch {
    /* main's expiry/rollback closes the hidden window */
  }
}
