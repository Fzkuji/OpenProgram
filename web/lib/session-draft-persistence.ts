import type { PendingChannelChoice } from "@/lib/runtime-bridge/draft-channel-choice";
import type { ComposerSettings } from "@/lib/session-store/types";

export interface PersistedSessionDraftState {
  version: 1;
  composerDrafts: Record<string, string>;
  composerSettingsBySession: Record<string, ComposerSettings>;
  pendingProjectsByChat: Record<string, string>;
  draftChannelChoices: Record<string, PendingChannelChoice>;
}

const emptyState = (): PersistedSessionDraftState => ({
  version: 1,
  composerDrafts: {},
  composerSettingsBySession: {},
  pendingProjectsByChat: {},
  draftChannelChoices: {},
});

function desktopWindowId(): string | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as unknown as {
    openprogramDesktop?: { isDesktop?: boolean; windowId?: string };
  }).openprogramDesktop;
  if (!bridge?.isDesktop) return null;
  return typeof bridge.windowId === "string" && bridge.windowId
    ? bridge.windowId
    : "main";
}

export function sessionDraftStorageKey(windowId?: string | null): string {
  const resolvedWindowId = windowId || desktopWindowId() || "main";
  return `openprogram.sessionDraftState:${resolvedWindowId}`;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function parseState(raw: string | null): PersistedSessionDraftState | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedSessionDraftState>;
    if (parsed?.version !== 1) return null;
    return {
      version: 1,
      composerDrafts: record(parsed.composerDrafts) as Record<string, string>,
      composerSettingsBySession: record(
        parsed.composerSettingsBySession,
      ) as Record<string, ComposerSettings>,
      pendingProjectsByChat: record(
        parsed.pendingProjectsByChat,
      ) as Record<string, string>,
      draftChannelChoices: record(
        parsed.draftChannelChoices,
      ) as Record<string, PendingChannelChoice>,
    };
  } catch {
    return null;
  }
}

function readLegacyDrafts(): Record<string, string> {
  try {
    const parsed = JSON.parse(localStorage.getItem("composerDrafts") ?? "null");
    return parsed?.v === 1 ? record(parsed.drafts) as Record<string, string> : {};
  } catch {
    return {};
  }
}

function readLegacySettings(): Record<string, ComposerSettings> {
  try {
    const parsed = JSON.parse(localStorage.getItem("composerSettings") ?? "null");
    return parsed?.v === 1
      ? record(parsed.map) as Record<string, ComposerSettings>
      : {};
  } catch {
    return {};
  }
}

export function readSessionDraftState(): PersistedSessionDraftState {
  if (typeof window === "undefined") return emptyState();
  const key = sessionDraftStorageKey();
  const current = parseState(localStorage.getItem(key));
  if (current) {
    if ((desktopWindowId() ?? "main") === "main") {
      try {
        localStorage.removeItem("composerDrafts");
        localStorage.removeItem("composerSettings");
      } catch {
        /* retry on the next read */
      }
    }
    return current;
  }
  if ((desktopWindowId() ?? "main") !== "main") return emptyState();

  const composerDrafts = readLegacyDrafts();
  const composerSettingsBySession = readLegacySettings();
  if (
    Object.keys(composerDrafts).length === 0
    && Object.keys(composerSettingsBySession).length === 0
  ) return emptyState();

  const migrated = {
    ...emptyState(),
    composerDrafts,
    composerSettingsBySession,
  };
  const serialized = JSON.stringify(migrated);
  try {
    localStorage.setItem(key, serialized);
    if (localStorage.getItem(key) !== serialized) return migrated;
    localStorage.removeItem("composerDrafts");
    localStorage.removeItem("composerSettings");
  } catch {
    return migrated;
  }
  return migrated;
}

export function replaceSessionDraftState(
  state: PersistedSessionDraftState,
): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(sessionDraftStorageKey(), JSON.stringify({
      version: 1,
      composerDrafts: state.composerDrafts,
      composerSettingsBySession: state.composerSettingsBySession,
      pendingProjectsByChat: state.pendingProjectsByChat,
      draftChannelChoices: state.draftChannelChoices,
    } satisfies PersistedSessionDraftState));
  } catch {
    /* quota / private mode */
  }
}

export function updateSessionDraftState(
  update: (state: PersistedSessionDraftState) => PersistedSessionDraftState,
): PersistedSessionDraftState {
  const next = update(readSessionDraftState());
  replaceSessionDraftState(next);
  return next;
}
