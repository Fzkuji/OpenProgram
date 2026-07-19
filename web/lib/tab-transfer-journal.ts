import type { DesktopWebTabBounds as Bounds } from "@/lib/desktop-bridge";
import type { PendingChannelChoice } from "@/lib/runtime-bridge/draft-channel-choice";
import type { ComposerSettings } from "@/lib/session-store/types";
import type { FileDraft } from "@/lib/state/files-shared";
import type {
  CenterTab,
  CenterTabsPersistedPayload,
} from "@/lib/state/center-tabs-store";

export interface TransferSourcePosition {
  windowId: string;
  kind: "tab" | "segment" | "group";
  groupId?: string;
  memberIndex?: number;
  memberIds?: string[];
  visibleIds?: string[];
  focusedId?: string;
}

export interface ChatTransferState {
  chatKey: string;
  composerDraft?: string;
  composerSettings?: ComposerSettings;
  pendingProjectId?: string;
  draftChannelChoice?: PendingChannelChoice;
  wasActive: boolean;
  activeComposerInput?: string;
  activeComposerSettings?: ComposerSettings;
}

export interface DesktopTransferPayload {
  tabs: CenterTab[];
  source: TransferSourcePosition;
  fileDrafts: Array<{ key: string; value: FileDraft }>;
  chats: ChatTransferState[];
}

export type TabDropPlacement =
  | { kind: "before"; targetTabId: string }
  | { kind: "merge"; targetTabId: string; groupId?: string; memberIndex?: number }
  | { kind: "after"; targetTabId: string }
  | { kind: "strip-end" };

export interface SessionTransferSnapshot {
  activeChatKey: string | null;
  currentSessionId: string | null;
  composerInput: string;
  composerSettings: ComposerSettings;
  composerDrafts: Record<string, string>;
  composerSettingsBySession: Record<string, ComposerSettings>;
  pendingProjectsByChat: Record<string, string>;
  draftChannelChoices: Record<string, PendingChannelChoice>;
}

export interface SerializedWebViewBookkeeping {
  liveIds: string[];
  readyIds: string[];
  visibleBounds: Array<{ id: string; bounds: Bounds }>;
}

export interface TransferJournalEntry {
  version: 1;
  token: string;
  role: "source" | "destination";
  phase: "staged" | "committing" | "rolling-back";
  payload: DesktopTransferPayload;
  placement?: TabDropPlacement;
  beforeCenterTabs: CenterTabsPersistedPayload;
  afterCenterTabs: CenterTabsPersistedPayload;
  beforeSession: SessionTransferSnapshot;
  afterSession: SessionTransferSnapshot;
  beforeFileDrafts: Array<{ key: string; existed: boolean; value?: FileDraft }>;
  afterFileDrafts: Array<{ key: string; existed: boolean; value?: FileDraft }>;
  beforeBridge: SerializedWebViewBookkeeping;
  afterBridge: SerializedWebViewBookkeeping;
}

export interface TransferJournalFile {
  version: 1;
  entries: Record<string, TransferJournalEntry>;
}

const emptyJournal = (): TransferJournalFile => ({ version: 1, entries: {} });

function windowId(): string {
  if (typeof window === "undefined") return "main";
  const bridge = (window as unknown as {
    openprogramDesktop?: { isDesktop?: boolean; windowId?: string };
  }).openprogramDesktop;
  return bridge?.isDesktop && bridge.windowId ? bridge.windowId : "main";
}

export function transferJournalStorageKey(id = windowId()): string {
  return `openprogram.tabTransferJournal:${id}`;
}

export function readTransferJournal(id = windowId()): TransferJournalFile {
  if (typeof window === "undefined") return emptyJournal();
  try {
    const parsed = JSON.parse(
      localStorage.getItem(transferJournalStorageKey(id)) ?? "null",
    ) as Partial<TransferJournalFile> | null;
    if (
      parsed?.version !== 1
      || !parsed.entries
      || typeof parsed.entries !== "object"
      || Array.isArray(parsed.entries)
    ) return emptyJournal();
    return { version: 1, entries: parsed.entries };
  } catch {
    return emptyJournal();
  }
}

function replaceJournal(journal: TransferJournalFile, id: string): boolean {
  if (typeof window === "undefined") return false;
  const key = transferJournalStorageKey(id);
  try {
    const serialized = JSON.stringify(journal);
    localStorage.setItem(key, serialized);
    return localStorage.getItem(key) === serialized;
  } catch {
    return false;
  }
}

export function writeTransferJournal(
  entry: TransferJournalEntry,
  id = windowId(),
): boolean {
  const journal = readTransferJournal(id);
  const next = {
    version: 1 as const,
    entries: { ...journal.entries, [entry.token]: entry },
  };
  return replaceJournal(next, id)
    && readTransferJournal(id).entries[entry.token]?.token === entry.token;
}

export function updateTransferJournal(
  token: string,
  patch: Partial<TransferJournalEntry>,
  id = windowId(),
): boolean {
  const journal = readTransferJournal(id);
  const current = journal.entries[token];
  if (!current) return false;
  return writeTransferJournal({ ...current, ...patch, token }, id);
}

export function deleteTransferJournal(
  token: string,
  id = windowId(),
): boolean {
  const journal = readTransferJournal(id);
  if (!journal.entries[token]) return true;
  const entries = { ...journal.entries };
  delete entries[token];
  return replaceJournal({ version: 1, entries }, id)
    && !readTransferJournal(id).entries[token];
}

export function stageTransferMutation(
  entry: TransferJournalEntry,
  mutate: () => void,
  reject: () => void,
  id = windowId(),
): boolean {
  if (!writeTransferJournal(entry, id)) {
    reject();
    return false;
  }
  try {
    mutate();
    return true;
  } catch {
    reject();
    return false;
  }
}

export type TransferMainStatus =
  | "committed"
  | "awaiting-source"
  | "destination-staged"
  | "prepared"
  | "rolled-back"
  | "stale";

export interface TransferRecoveryHandlers {
  applyCenterTabs(
    payload: CenterTabsPersistedPayload,
    options: { persist: boolean },
  ): void;
  applySession(
    snapshot: SessionTransferSnapshot,
    options: { persist: boolean },
  ): void;
  applyFileDrafts(
    snapshot: Array<{ key: string; existed: boolean; value?: FileDraft }>,
  ): void;
  applyBridge(snapshot: SerializedWebViewBookkeeping): void;
  rebuildAccepted?(entry: TransferJournalEntry): void;
  resumeSourceRemoved?(entry: TransferJournalEntry): void;
  clearAccepted?(token: string): void;
  deleteJournal?(token: string): boolean;
}

function applyRecoverySnapshot(
  entry: TransferJournalEntry,
  target: "before" | "after",
  persist: boolean,
  handlers: TransferRecoveryHandlers,
): void {
  handlers.applyCenterTabs(
    target === "after" ? entry.afterCenterTabs : entry.beforeCenterTabs,
    { persist },
  );
  handlers.applySession(
    target === "after" ? entry.afterSession : entry.beforeSession,
    { persist },
  );
  handlers.applyFileDrafts(
    target === "after" ? entry.afterFileDrafts : entry.beforeFileDrafts,
  );
  handlers.applyBridge(
    target === "after" ? entry.afterBridge : entry.beforeBridge,
  );
}

export function recoverTransferJournalEntry(
  entry: TransferJournalEntry,
  status: TransferMainStatus,
  handlers: TransferRecoveryHandlers,
): boolean {
  if (status === "committed") {
    applyRecoverySnapshot(entry, "after", true, handlers);
    handlers.clearAccepted?.(entry.token);
    return handlers.deleteJournal?.(entry.token) ?? true;
  }
  if (
    entry.role === "destination"
    && (status === "destination-staged" || status === "awaiting-source")
  ) {
    applyRecoverySnapshot(entry, "after", false, handlers);
    handlers.rebuildAccepted?.(entry);
    return true;
  }
  if (entry.role === "source" && status === "awaiting-source") {
    applyRecoverySnapshot(entry, "after", false, handlers);
    handlers.resumeSourceRemoved?.(entry);
    return true;
  }
  applyRecoverySnapshot(entry, "before", true, handlers);
  handlers.clearAccepted?.(entry.token);
  return handlers.deleteJournal?.(entry.token) ?? true;
}

export function finalizeTransferJournal(
  token: string,
  outcome: "commit" | "rollback",
  handlers: TransferRecoveryHandlers,
  id = windowId(),
): boolean {
  const entry = readTransferJournal(id).entries[token];
  if (!entry) return false;
  const phase = outcome === "commit" ? "committing" : "rolling-back";
  if (!updateTransferJournal(token, { phase }, id)) return false;
  try {
    applyRecoverySnapshot(
      { ...entry, phase },
      outcome === "commit" ? "after" : "before",
      true,
      handlers,
    );
    handlers.clearAccepted?.(token);
    return handlers.deleteJournal
      ? handlers.deleteJournal(token)
      : deleteTransferJournal(token, id);
  } catch {
    return false;
  }
}
