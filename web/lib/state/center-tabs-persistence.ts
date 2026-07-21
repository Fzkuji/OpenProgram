/**
 * Center-tab persistence + payload normalization.
 *
 * Pure data-transform layer split out of center-tabs-store.ts: reads/writes
 * the localStorage payload, migrates the legacy shape, and normalizes/rebases
 * the persisted payload. No zustand, no store instance — only localStorage,
 * the layout helpers, and the deterministic id helpers.
 */
import { pendingTransfers } from "@/lib/pending-transfer-projection";
import {
  findCenterTabGroup,
  focusCenterTabGroupMember,
  groupCenterTabs,
  normalizeCenterTabLayout,
} from "@/lib/state/center-tab-groups";
import type { CenterTabGroup } from "@/lib/state/center-tab-groups";
import type { TransferJournalEntry } from "@/lib/tab-transfer-journal";
import { nextDraftSessionId, sessionTabId } from "@/lib/state/center-tab-ids";
import type { CenterTab } from "@/lib/state/center-tabs-store";

/** Legacy singleton id, kept only to migrate persisted pre-multi-draft tabs. */
export const DRAFT_SESSION_TAB_ID = "s:draft";
export function draftTab(sessionId = nextDraftSessionId()): CenterTab {
  return {
    id: sessionTabId(sessionId),
    kind: "session",
    title: "",
    sessionId,
    draft: true,
  };
}

const LEGACY_TABS_STORAGE_KEY = "centerTabs";
const LEGACY_SPLIT_STORAGE_KEY = "openprogram.webSplit";
const DEFAULT_SPLIT_RATIO = 0.44;
const MIN_SPLIT_RATIO = 0.30;
const MAX_SPLIT_RATIO = 0.70;
export const clampSplitRatio = (ratio: number) =>
  Math.min(MAX_SPLIT_RATIO, Math.max(MIN_SPLIT_RATIO, ratio));

interface LegacyPersistedTabs {
  tabs: CenterTab[];
  activeId: string | null;
}

interface LegacyPersistedSplit {
  tabId: string | null;
  ratio: number;
}

export interface CenterTabsPersistedPayload {
  version: 2;
  tabs: CenterTab[];
  activeId: string | null;
  groups: CenterTabGroup[];
  splitWebTabId: string | null;
  splitRatio: number;
}

export type CenterTabsPersistedState = Omit<CenterTabsPersistedPayload, "version">;

export function desktopWindowId(): string | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as unknown as {
    openprogramDesktop?: { isDesktop?: boolean; windowId?: string };
  }).openprogramDesktop;
  if (!bridge?.isDesktop) return null;
  return typeof bridge.windowId === "string" && bridge.windowId
    ? bridge.windowId
    : "main";
}

export function centerTabsStorageKey(windowId = desktopWindowId()): string {
  return windowId ? `centerTabs:${windowId}` : LEGACY_TABS_STORAGE_KEY;
}

export function splitGroupId(sessionId: string, webId: string): string {
  return `g:split:${sessionId}:${webId}`;
}

export function replaceGroupTabId(
  groups: readonly CenterTabGroup[],
  oldId: string,
  newId: string,
): CenterTabGroup[] {
  return groups.map((group) => ({
    ...group,
    memberIds: group.memberIds.map((id) => id === oldId ? newId : id),
    visibleIds: group.visibleIds.map((id) => id === oldId ? newId : id),
    focusedId: group.focusedId === oldId ? newId : group.focusedId,
  }));
}

export function orderTabs(tabs: readonly CenterTab[], tabIds: readonly string[]): CenterTab[] {
  const byId = new Map<string, CenterTab>();
  for (const tab of tabs) {
    if (!byId.has(tab.id)) byId.set(tab.id, tab);
  }
  return tabIds.flatMap((id) => {
    const tab = byId.get(id);
    return tab ? [tab] : [];
  });
}

export function normalizeCenterTabsPayload(
  input: Partial<CenterTabsPersistedPayload>,
  clearDirty = false,
): CenterTabsPersistedPayload {
  const sourceTabs = Array.isArray(input.tabs) ? input.tabs : [];
  const tabs = sourceTabs.map((tab) => {
    if (tab.id === DRAFT_SESSION_TAB_ID) return draftTab();
    return clearDirty && tab.dirty ? { ...tab, dirty: false } : tab;
  });
  let layout = normalizeCenterTabLayout({
    tabIds: tabs.map((tab) => tab.id),
    groups: Array.isArray(input.groups) ? input.groups : [],
  });
  const activeId = layout.tabIds.includes(input.activeId ?? "")
    ? input.activeId ?? null
    : layout.tabIds[0] ?? null;
  let splitWebTabId = typeof input.splitWebTabId === "string" &&
      tabs.some((tab) => tab.id === input.splitWebTabId && tab.kind === "web")
    ? input.splitWebTabId
    : null;

  const activeTab = tabs.find((tab) => tab.id === activeId);
  if (activeTab?.kind === "session" && splitWebTabId) {
    const splitId = splitWebTabId;
    const activeGroup = findCenterTabGroup(layout.groups, activeTab.id);
    const memberIndex = activeGroup
      ? activeGroup.memberIds
          .filter((id) => id !== splitId)
          .indexOf(activeTab.id) + 1
      : 1;
    const grouped = groupCenterTabs(
      layout,
      splitId,
      activeTab.id,
      memberIndex,
      splitGroupId(activeTab.id, splitId),
    );
    if (grouped.accepted) {
      const splitGroup = findCenterTabGroup(grouped.layout.groups, activeTab.id);
      layout = splitGroup
        ? normalizeCenterTabLayout({
            ...grouped.layout,
            groups: grouped.layout.groups.map((group) => group.id === splitGroup.id
              ? {
                  ...group,
                  visibleIds: [activeTab.id, splitId],
                  focusedId: activeTab.id,
                }
              : group),
          })
        : grouped.layout;
    } else {
      splitWebTabId = null;
    }
  }

  const activeGroup = activeId
    ? findCenterTabGroup(layout.groups, activeId)
    : undefined;
  if (activeGroup && activeId && activeGroup.focusedId !== activeId) {
    layout = focusCenterTabGroupMember(layout, activeGroup.id, activeId);
  }
  const normalizedTabs = orderTabs(tabs, layout.tabIds);
  return {
    version: 2,
    tabs: normalizedTabs,
    activeId,
    groups: layout.groups,
    splitWebTabId,
    splitRatio:
      typeof input.splitRatio === "number" && Number.isFinite(input.splitRatio)
        ? clampSplitRatio(input.splitRatio)
        : DEFAULT_SPLIT_RATIO,
  };
}

function insertByReference(
  ids: string[],
  id: string,
  reference: readonly string[],
): void {
  const referenceIndex = reference.indexOf(id);
  for (let index = referenceIndex - 1; index >= 0; index -= 1) {
    const previousIndex = ids.indexOf(reference[index]);
    if (previousIndex >= 0) {
      ids.splice(previousIndex + 1, 0, id);
      return;
    }
  }
  for (let index = referenceIndex + 1; index < reference.length; index += 1) {
    const nextIndex = ids.indexOf(reference[index]);
    if (nextIndex >= 0) {
      ids.splice(nextIndex, 0, id);
      return;
    }
  }
  ids.push(id);
}

function normalizedRebasedGroup(
  group: CenterTabGroup,
  memberIds: string[],
  visibleIds: string[],
  focusedId: string | undefined,
): CenterTabGroup | null {
  if (memberIds.length < 2) return null;
  const visible = visibleIds.filter((id, index) =>
    memberIds.includes(id) && visibleIds.indexOf(id) === index).slice(0, 2);
  return {
    ...group,
    memberIds,
    visibleIds: visible,
    focusedId: focusedId && memberIds.includes(focusedId)
      ? focusedId
      : visible[0] ?? memberIds[0],
  };
}

export function rebaseCenterTabsPayload(
  current: CenterTabsPersistedPayload,
  entry: TransferJournalEntry,
  targetName: "before" | "after",
): CenterTabsPersistedPayload {
  const target = targetName === "before"
    ? entry.beforeCenterTabs
    : entry.afterCenterTabs;
  const opposite = targetName === "before"
    ? entry.afterCenterTabs
    : entry.beforeCenterTabs;
  const affected = new Set(entry.payload.tabs.map((tab) => tab.id));
  const targetTabs = new Map(target.tabs.map((tab) => [tab.id, tab]));
  const targetIds = target.tabs.map((tab) => tab.id);
  const tabs = current.tabs.filter((tab) =>
    !affected.has(tab.id) || targetTabs.has(tab.id));
  const tabIds = tabs.map((tab) => tab.id);
  for (const id of targetIds) {
    if (!affected.has(id) || tabIds.includes(id)) continue;
    const tab = targetTabs.get(id);
    if (!tab) continue;
    insertByReference(tabIds, id, targetIds);
    const at = tabIds.indexOf(id);
    tabs.splice(at, 0, structuredClone(tab));
  }

  let groups = current.groups.flatMap((group) => {
    const memberIds = group.memberIds.filter((id) => !affected.has(id));
    const visibleIds = group.visibleIds.filter((id) => !affected.has(id));
    const normalized = normalizedRebasedGroup(
      group,
      memberIds,
      visibleIds,
      group.focusedId,
    );
    return normalized ? [normalized] : [];
  });
  for (const targetGroup of target.groups) {
    const affectedMembers = targetGroup.memberIds.filter((id) =>
      affected.has(id) && tabIds.includes(id));
    if (affectedMembers.length === 0) continue;
    const groupIndex = groups.findIndex((group) => group.id === targetGroup.id);
    const existing = groupIndex >= 0 ? groups[groupIndex] : null;
    const groupedElsewhere = new Set(groups.flatMap((group) => group.memberIds));
    const memberIds = existing
      ? [...existing.memberIds]
      : targetGroup.memberIds.filter((id) =>
        !affected.has(id) && tabIds.includes(id) && !groupedElsewhere.has(id));
    for (const id of targetGroup.memberIds) {
      if (!affected.has(id) || memberIds.includes(id) || !tabIds.includes(id)) continue;
      insertByReference(memberIds, id, targetGroup.memberIds);
    }
    const visibleIds = existing
      ? [...existing.visibleIds]
      : targetGroup.visibleIds.filter((id) => memberIds.includes(id));
    for (const id of targetGroup.visibleIds) {
      if (affected.has(id) && memberIds.includes(id) && !visibleIds.includes(id)) {
        visibleIds.push(id);
      }
    }
    const normalized = normalizedRebasedGroup(
      targetGroup,
      memberIds,
      visibleIds,
      existing?.focusedId ?? targetGroup.focusedId,
    );
    if (!normalized) continue;
    if (groupIndex >= 0) groups[groupIndex] = normalized;
    else groups = [...groups, normalized];
  }

  const hasTab = (id: string | null): boolean =>
    id !== null && tabIds.includes(id);
  let activeId = current.activeId;
  if (current.activeId === opposite.activeId) activeId = target.activeId;
  if (!hasTab(activeId)) activeId = hasTab(target.activeId) ? target.activeId : null;
  let splitWebTabId = current.splitWebTabId;
  if (current.splitWebTabId === opposite.splitWebTabId) {
    splitWebTabId = target.splitWebTabId;
  }
  if (!hasTab(splitWebTabId)) splitWebTabId = null;
  const splitRatio = current.splitRatio === opposite.splitRatio
    ? target.splitRatio
    : current.splitRatio;

  return normalizeCenterTabsPayload({
    version: 2,
    tabs: orderTabs(tabs, tabIds),
    activeId,
    groups,
    splitWebTabId,
    splitRatio,
  });
}

export function persistCenterTabsPayload(
  state: CenterTabsPersistedState,
): boolean {
  if (typeof window === "undefined") return false;
  try {
    const key = centerTabsStorageKey();
    let projected = normalizeCenterTabsPayload({
      version: 2,
      tabs: state.tabs,
      activeId: state.activeId,
      groups: state.groups,
      splitWebTabId: state.splitWebTabId,
      splitRatio: state.splitRatio,
    });
    for (const entry of pendingTransfers().reverse()) {
      projected = rebaseCenterTabsPayload(projected, entry, "before");
    }
    const serialized = JSON.stringify(projected);
    localStorage.setItem(key, serialized);
    return localStorage.getItem(key) === serialized;
  } catch {
    return false;
  }
}

function parsePayload(raw: string | null): Partial<CenterTabsPersistedPayload> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<CenterTabsPersistedPayload>;
    return Array.isArray(parsed.tabs) ? parsed : null;
  } catch {
    return null;
  }
}

function readLegacySplit(): LegacyPersistedSplit {
  try {
    const raw = localStorage.getItem(LEGACY_SPLIT_STORAGE_KEY);
    if (!raw) return { tabId: null, ratio: DEFAULT_SPLIT_RATIO };
    const parsed = JSON.parse(raw) as Partial<LegacyPersistedSplit>;
    return {
      tabId: typeof parsed.tabId === "string" ? parsed.tabId : null,
      ratio: typeof parsed.ratio === "number" && Number.isFinite(parsed.ratio)
        ? clampSplitRatio(parsed.ratio)
        : DEFAULT_SPLIT_RATIO,
    };
  } catch {
    return { tabId: null, ratio: DEFAULT_SPLIT_RATIO };
  }
}

export function readCenterTabsPayload(): CenterTabsPersistedPayload {
  const empty = normalizeCenterTabsPayload({});
  if (typeof window === "undefined") return empty;
  const windowId = desktopWindowId();
  const key = centerTabsStorageKey(windowId);
  const current = parsePayload(localStorage.getItem(key));
  if (current?.version === 2) return normalizeCenterTabsPayload(current, true);

  // The unkeyed browser payload is also the legacy source. Only the main
  // desktop window may claim it; secondary windows must start isolated.
  const legacy = windowId && windowId !== "main"
    ? null
    : current ?? parsePayload(localStorage.getItem(LEGACY_TABS_STORAGE_KEY));
  if (!legacy) return empty;
  const split = readLegacySplit();
  const migrated = normalizeCenterTabsPayload({
    tabs: (legacy as LegacyPersistedTabs).tabs,
    activeId: (legacy as LegacyPersistedTabs).activeId,
    groups: Array.isArray(legacy.groups) ? legacy.groups : [],
    splitWebTabId: legacy.splitWebTabId ?? split.tabId,
    splitRatio: legacy.splitRatio ?? split.ratio,
  }, true);
  persistCenterTabsPayload(migrated);
  return migrated;
}

export function persistedState(payload: CenterTabsPersistedPayload): CenterTabsPersistedState {
  return {
    tabs: payload.tabs,
    activeId: payload.activeId,
    groups: payload.groups,
    splitWebTabId: payload.splitWebTabId,
    splitRatio: payload.splitRatio,
  };
}
