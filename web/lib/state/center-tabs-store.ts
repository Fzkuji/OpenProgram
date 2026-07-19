/**
 * Center tab container state — the browser-model tab strip that owns
 * the CENTER of the app: session tabs (💬, bookmarks over the single
 * live chat surface), file tabs (📄, per project+path), and one
 * reusable new-tab page.
 *
 * Deterministic ids make focus-or-create trivial:
 *   session  →  "s:<sessionId>"   (drafts pre-allocate a local_* id)
 *   file     →  "f:<projectId>:<path>"
 *   web      →  "w:<url>"         (id is fixed at open; in-pane
 *                                  navigation updates url, not id)
 *   ntp      →  "ntp"
 *
 * Navigation side effects (router.push on session-tab activation) are
 * NOT here — they live in <CenterTabStrip/>, which also syncs the
 * session store's currentSessionId / titles into this store.
 */
import { create } from "zustand";
import {
  findCenterTabGroup,
  focusCenterTabGroupMember,
  groupCenterTabs,
  moveCenterTab,
  moveCenterTabGroup,
  moveCenterTabGroupMember,
  normalizeCenterTabLayout,
  ungroupCenterTab,
} from "@/lib/state/center-tab-groups";
import type {
  CenterTabGroup,
  CenterTabLayout,
} from "@/lib/state/center-tab-groups";

export type CenterTabKind = "session" | "file" | "web" | "ntp";

export interface CenterTab {
  id: string;
  kind: CenterTabKind;
  /** Session tabs: conversation title (may lag; synced from the
   *  session store). File tabs: basename. Web tabs: hostname until
   *  updateWebTab sets a real title. NTP: unused (i18n label). */
  title: string;
  /** Session tabs only. Drafts use a provisional local_* id. */
  sessionId?: string;
  /** Session tabs only — true until the first server acknowledgement. */
  draft?: boolean;
  /** File tabs only. */
  projectId?: string;
  /** File tabs only — project-relative, "/"-separated. */
  path?: string;
  /** Web tabs only — current http(s) URL (may drift from the id
   *  after in-pane navigation). */
  url?: string;
  /** Unsaved-changes marker — strip shows ● instead of ✕. Set via
   *  setTabDirty by whoever owns the tab's content (file editor). */
  dirty?: boolean;
}

/** Legacy singleton id, kept only to migrate persisted pre-multi-draft tabs. */
export const DRAFT_SESSION_TAB_ID = "s:draft";
/** New-tab 页不再是单例（Chrome 行为：＋ 想开几个开几个），每个实例一个
 *  唯一 id。时间戳 + 自增序号，避免与持久化恢复的旧 id 撞车。 */
let ntpSeq = 0;
function nextNtpId(): string {
  return `ntp:${Date.now().toString(36)}:${(ntpSeq++).toString(36)}`;
}

function nextDraftSessionId(): string {
  return `local_${crypto.randomUUID()}`;
}

function draftTab(sessionId = nextDraftSessionId()): CenterTab {
  return {
    id: sessionTabId(sessionId),
    kind: "session",
    title: "",
    sessionId,
    draft: true,
  };
}

export function sessionTabId(sessionId: string): string {
  return `s:${sessionId}`;
}
export function fileTabId(projectId: string, path: string): string {
  return `f:${projectId}:${path}`;
}
export function webTabId(url: string): string {
  return `w:${url}`;
}

/** Normalize user input into a browsable http(s) URL: trims, prefixes
 *  bare domains with https://, and rejects every other scheme
 *  (javascript:, data:, file:, …). Returns null when not navigable. */
/** Chrome 地址栏（omnibox）语义：像 URL 的输入按 URL 打开，其余一律
 *  转搜索，绝不静默失败。此前 "bilibili"（无点）会拼成 https://bilibili
 *  → DNS 白屏，含空格的中文词直接被忽略——两种都表现为"浏览器打不开"。
 *  返回 null 仅当输入为空。 */
export function normalizeWebUrl(input: string): string | null {
  const raw = input.trim();
  if (!raw) return null;
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
    try {
      const u = new URL(raw);
      if ((u.protocol === "http:" || u.protocol === "https:") && u.hostname) {
        return u.href;
      }
    } catch {
      /* 有 scheme 但解析不了 → 当搜索词 */
    }
    return webSearchUrl(raw);
  }
  // 无 scheme：无空格且主机段像域名（带点 / localhost / IPv6）才按 URL
  const hostish = raw.split("/")[0];
  const urlLike =
    !/\s/.test(raw) &&
    (hostish.includes(".") ||
      /^localhost(:\d+)?$/i.test(hostish) ||
      /^\[[0-9a-f:]+\]/i.test(hostish));
  if (urlLike) {
    try {
      const u = new URL(`https://${raw}`);
      if (u.hostname) return u.href;
    } catch {
      /* fall through to search */
    }
  }
  return webSearchUrl(raw);
}

function webSearchUrl(query: string): string {
  return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url;
  }
}

const LEGACY_TABS_STORAGE_KEY = "centerTabs";
const LEGACY_SPLIT_STORAGE_KEY = "openprogram.webSplit";
const DEFAULT_SPLIT_RATIO = 0.44;
const MIN_SPLIT_RATIO = 0.30;
const MAX_SPLIT_RATIO = 0.70;
const clampSplitRatio = (ratio: number) =>
  Math.min(MAX_SPLIT_RATIO, Math.max(MIN_SPLIT_RATIO, ratio));

// A missing non-local tab normally means a legacy caller that predates the
// center-tab UI, so its ACK is still allowed to activate the session. Preserve
// the one distinction the tab array cannot retain after removal: the user
// explicitly closed this session. Tombstones live only for this page lifetime
// and are cleared by an explicit reopen.
const closedSessionAckTombstones = new Set<string>();

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

type CenterTabsPersistedState = Omit<CenterTabsPersistedPayload, "version">;

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

function centerTabsStorageKey(windowId = desktopWindowId()): string {
  return windowId ? `centerTabs:${windowId}` : LEGACY_TABS_STORAGE_KEY;
}

function splitGroupId(sessionId: string, webId: string): string {
  return `g:split:${sessionId}:${webId}`;
}

function replaceGroupTabId(
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

function orderTabs(tabs: readonly CenterTab[], tabIds: readonly string[]): CenterTab[] {
  const byId = new Map<string, CenterTab>();
  for (const tab of tabs) {
    if (!byId.has(tab.id)) byId.set(tab.id, tab);
  }
  return tabIds.flatMap((id) => {
    const tab = byId.get(id);
    return tab ? [tab] : [];
  });
}

function normalizeCenterTabsPayload(
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

function persistCenterTabsPayload(
  state: Pick<CenterTabsState, "tabs" | "activeId" | "groups" | "splitWebTabId" | "splitRatio">,
): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(centerTabsStorageKey(), JSON.stringify({
      version: 2,
      tabs: state.tabs,
      activeId: state.activeId,
      groups: state.groups,
      splitWebTabId: state.splitWebTabId,
      splitRatio: state.splitRatio,
    } satisfies CenterTabsPersistedPayload));
  } catch {
    /* quota / private mode — workspace still works, just doesn't restore */
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

function readCenterTabsPayload(): CenterTabsPersistedPayload {
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
    groups: [],
    splitWebTabId: split.tabId,
    splitRatio: split.ratio,
  }, true);
  persistCenterTabsPayload(migrated);
  return migrated;
}

export interface CenterTabsState {
  tabs: CenterTab[];
  activeId: string | null;
  groups: CenterTabGroup[];
  splitWebTabId: string | null;
  splitRatio: number;
  setActive: (id: string) => void;
  moveTab: (id: string, beforeId: string | null) => void;
  moveGroup: (groupId: string, beforeId: string | null) => void;
  moveGroupMember: (groupId: string, memberId: string, toIndex: number) => void;
  groupTab: (
    sourceId: string,
    targetId: string,
    memberIndex: number,
    groupId?: string,
  ) => boolean;
  ungroupTab: (id: string, beforeId?: string | null) => void;
  focusGroupMember: (groupId: string, memberId: string) => void;
  /** Focus-or-create the tab for a live session. Browser semantics:
   *  if the ACTIVE tab is the draft chat or the new-tab page, the
   *  session "navigates" that tab (replaces it in place) instead of
   *  opening a new one. */
  openSessionTab: (sessionId: string, title: string) => void;
  /** Create a distinct draft. An active NTP is replaced in place;
   *  otherwise the draft is appended. Returns its provisional id. */
  openDraftSessionTab: () => string;
  /** NTP New session: replace only the active NTP with a distinct draft. */
  claimDraftSessionTab: () => string;
  /** First acknowledgement: keep the same tab/id and clear draft state. */
  markSessionReady: (sessionId: string) => void;
  openFileTab: (projectId: string, path: string) => void;
  /** Focus-or-create a web tab for `url` (must already be a valid
   *  http(s) URL — run user input through normalizeWebUrl first). */
  openWebTab: (url: string) => void;
  /** Appends or reuses a web tab for the split pane without changing focus. */
  openWebTabInSplit: (url: string) => string;
  setSplitWebTab: (id: string | null) => void;
  setSplitRatio: (ratio: number) => void;
  /** Update a web tab's url/title in place (address-bar navigation,
   *  later title reporting from the sidecar browser). Id stays fixed. */
  updateWebTab: (id: string, patch: { url?: string; title?: string }) => void;
  /** Unsaved-changes marker groundwork — content owners call this;
   *  the strip renders ● instead of ✕ while dirty. */
  setTabDirty: (id: string, dirty: boolean) => void;
  /** Retarget a file tab after its file was renamed/moved on disk:
   *  new deterministic id + title (basename), order and active state
   *  preserved. If a tab already exists at the new id, the stale tab
   *  closes instead (focus moves to the survivor if it was active). */
  retargetFileTab: (oldId: string, newProjectId: string, newPath: string) => void;
  /** Single-instance new-tab page — reused if already open. */
  openNewTabPage: () => void;
  /** Close a tab; closing the active one activates the right
   *  neighbor, else the left. Never leaves zero tabs (falls back to
   *  the new-tab page). */
  closeTab: (id: string) => void;
  renameSessionTab: (sessionId: string, title: string) => void;
}

function persistedState(payload: CenterTabsPersistedPayload): CenterTabsPersistedState {
  return {
    tabs: payload.tabs,
    activeId: payload.activeId,
    groups: payload.groups,
    splitWebTabId: payload.splitWebTabId,
    splitRatio: payload.splitRatio,
  };
}

function commitCenterTabsState(
  state: CenterTabsState,
  patch: Partial<CenterTabsPersistedState>,
): CenterTabsPersistedState {
  const payload = normalizeCenterTabsPayload({
    tabs: patch.tabs ?? state.tabs,
    activeId: patch.activeId === undefined ? state.activeId : patch.activeId,
    groups: patch.groups ?? state.groups,
    splitWebTabId: patch.splitWebTabId === undefined
      ? state.splitWebTabId
      : patch.splitWebTabId,
    splitRatio: patch.splitRatio ?? state.splitRatio,
  });
  persistCenterTabsPayload(payload);
  return persistedState(payload);
}

function tabsForLayout(
  tabs: readonly CenterTab[],
  layout: CenterTabLayout,
): CenterTab[] {
  return orderTabs(tabs, layout.tabIds);
}

function detachesSplitPair(state: CenterTabsState, tabId: string): boolean {
  const splitId = state.splitWebTabId;
  if (!splitId) return false;
  if (tabId === splitId) return true;
  const tab = state.tabs.find((candidate) => candidate.id === tabId);
  return tabId === state.activeId && tab?.kind === "session" &&
    !!findCenterTabGroup(state.groups, tabId)?.memberIds.includes(splitId);
}

export const useCenterTabs = create<CenterTabsState>((set) => {
  const initial = readCenterTabsPayload();

  /** Focus tab `id` if present; otherwise insert `make()` — replacing
   *  the active tab when it's a New-tab page or one of `replaceable`
   *  (in-place browser navigation), else appending at the end.
   *
   *  New-tab 页永远原地变身（按 kind 判断，Chrome 语义：NTP 一旦导航就
   *  不存在了）——目标 tab 已在别处打开时也一样：聚焦目标并把当前 NTP
   *  移除，绝不把空 New tab 留在原地。 */
  function focusOrCreate(
    s: CenterTabsState,
    id: string,
    make: () => CenterTab,
    replaceable: string[],
  ): Partial<CenterTabsState> {
    const active = s.tabs.find((t) => t.id === s.activeId);
    const existing = s.tabs.find((t) => t.id === id);
    if (existing) {
      const tabs =
        active && active.kind === "ntp" && active.id !== id
          ? s.tabs.filter((t) => t.id !== active.id)
          : s.tabs;
      return commitCenterTabsState(s, { tabs, activeId: id });
    }
    const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
    let tabs: CenterTab[];
    if (
      activeIdx >= 0 &&
      (s.tabs[activeIdx].kind === "ntp" ||
        replaceable.includes(s.tabs[activeIdx].id))
    ) {
      tabs = s.tabs.map((t, i) => (i === activeIdx ? make() : t));
    } else {
      tabs = [...s.tabs, make()];
    }
    const replacedId = activeIdx >= 0 && tabs[activeIdx]?.id === id
      ? s.tabs[activeIdx].id
      : null;
    return commitCenterTabsState(s, {
      tabs,
      activeId: id,
      groups: replacedId && replacedId !== id
        ? replaceGroupTabId(s.groups, replacedId, id)
        : s.groups,
    });
  }

  return {
    tabs: initial.tabs,
    activeId: initial.activeId,
    groups: initial.groups,
    splitWebTabId: initial.splitWebTabId,
    splitRatio: initial.splitRatio,

    setActive: (id) =>
      set((s) => {
        if (!s.tabs.some((tab) => tab.id === id)) return {};
        const group = findCenterTabGroup(s.groups, id);
        if (s.activeId === id && (!group || group.focusedId === id)) return {};
        const layout = group
          ? focusCenterTabGroupMember({
              tabIds: s.tabs.map((tab) => tab.id),
              groups: s.groups,
            }, group.id, id)
          : { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups };
        return commitCenterTabsState(s, {
          activeId: id,
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      }),

    moveTab: (id, beforeId) =>
      set((s) => {
        const splitWebTabId = detachesSplitPair(s, id)
          ? null
          : s.splitWebTabId;
        const layout = moveCenterTab({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, id, beforeId);
        if (layout.tabIds.every((tabId, index) => tabId === s.tabs[index]?.id)
            && layout.groups === s.groups) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
          splitWebTabId,
        });
      }),

    moveGroup: (groupId, beforeId) =>
      set((s) => {
        const layout = moveCenterTabGroup({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, groupId, beforeId);
        if (layout.tabIds.every((tabId, index) => tabId === s.tabs[index]?.id)) {
          return {};
        }
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      }),

    moveGroupMember: (groupId, memberId, toIndex) =>
      set((s) => {
        const layout = moveCenterTabGroupMember({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, groupId, memberId, toIndex);
        const prior = findCenterTabGroup(s.groups, memberId);
        const next = findCenterTabGroup(layout.groups, memberId);
        if (!prior || !next || next.memberIds.every(
          (id, index) => id === prior.memberIds[index],
        )) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      }),

    groupTab: (sourceId, targetId, memberIndex, groupId) => {
      let accepted = false;
      set((s) => {
        const result = groupCenterTabs({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, sourceId, targetId, memberIndex, groupId ?? `g:${crypto.randomUUID()}`);
        accepted = result.accepted;
        if (!accepted) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, result.layout),
          groups: result.layout.groups,
        });
      });
      return accepted;
    },

    ungroupTab: (id, beforeId = null) =>
      set((s) => {
        const splitWebTabId = detachesSplitPair(s, id)
          ? null
          : s.splitWebTabId;
        const prior = findCenterTabGroup(s.groups, id);
        const layout = ungroupCenterTab({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, id, beforeId);
        if (!prior && beforeId === null) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
          splitWebTabId,
        });
      }),

    focusGroupMember: (groupId, memberId) =>
      set((s) => {
        const group = s.groups.find((candidate) => candidate.id === groupId);
        if (!group?.memberIds.includes(memberId)) return {};
        const layout = focusCenterTabGroupMember({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, groupId, memberId);
        return commitCenterTabsState(s, {
          activeId: memberId,
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      }),

    openSessionTab: (sessionId, title) =>
      set((s) => {
        closedSessionAckTombstones.delete(sessionId);
        const id = sessionTabId(sessionId);
        const existing = s.tabs.find((tab) => tab.id === id);
        if (!existing) {
          return focusOrCreate(
            s,
            id,
            () => ({ id, kind: "session", title, sessionId }),
            [],
          );
        }
        const tabs = s.tabs.map((tab) =>
          tab.id === id ? { ...tab, title, sessionId, draft: false } : tab,
        );
        return commitCenterTabsState(s, { tabs, activeId: id });
      }),

    openDraftSessionTab: () => {
      const tab = draftTab();
      set((s) => {
        const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
        const tabs =
          activeIdx >= 0 && s.tabs[activeIdx].kind === "ntp"
            ? s.tabs.map((item, i) => (i === activeIdx ? tab : item))
            : [...s.tabs, tab];
        const replacedId = activeIdx >= 0 && s.tabs[activeIdx].kind === "ntp"
          ? s.tabs[activeIdx].id
          : null;
        return commitCenterTabsState(s, {
          tabs,
          activeId: tab.id,
          groups: replacedId
            ? replaceGroupTabId(s.groups, replacedId, tab.id)
            : s.groups,
        });
      });
      return tab.sessionId!;
    },

    claimDraftSessionTab: () => {
      const tab = draftTab();
      set((s) => {
        const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
        if (activeIdx < 0) return {};
        const replacedId = s.tabs[activeIdx].id;
        const tabs = s.tabs.map((item, i) => (i === activeIdx ? tab : item));
        return commitCenterTabsState(s, {
          tabs,
          activeId: tab.id,
          groups: replaceGroupTabId(s.groups, replacedId, tab.id),
        });
      });
      return tab.sessionId!;
    },

    markSessionReady: (sessionId) =>
      set((s) => {
        const id = sessionTabId(sessionId);
        if (!s.tabs.some((tab) => tab.id === id && tab.draft)) return {};
        const tabs = s.tabs.map((tab) =>
          tab.id === id ? { ...tab, draft: false } : tab,
        );
        return commitCenterTabsState(s, { tabs });
      }),

    openFileTab: (projectId, path) =>
      set((s) =>
        focusOrCreate(
          s,
          fileTabId(projectId, path),
          () => ({
            id: fileTabId(projectId, path),
            kind: "file",
            title: path.split("/").pop() || path,
            projectId,
            path,
          }),
          [],
        ),
      ),

    openWebTab: (url) =>
      set((s) => {
        const id = webTabId(url);
        const existing = s.tabs.find((tab) => tab.id === id);
        if (!existing) {
          return focusOrCreate(
            s,
            id,
            () => ({ id, kind: "web", title: hostnameOf(url), url }),
            [],
          );
        }
        const active = s.tabs.find((tab) => tab.id === s.activeId);
        const consumeNtp = active?.kind === "ntp" && active.id !== id;
        const restoreUrl = existing.url !== url;
        if (!consumeNtp && !restoreUrl && s.activeId === id) return {};
        let tabs = consumeNtp
          ? s.tabs.filter((tab) => tab.id !== active.id)
          : s.tabs;
        if (restoreUrl) {
          tabs = tabs.map((tab) =>
            tab.id === id
              ? { ...tab, url, title: hostnameOf(url) }
              : tab,
          );
        }
        return commitCenterTabsState(s, { tabs, activeId: id });
      }),

    openWebTabInSplit: (url) => {
      const id = webTabId(url);
      set((s) => {
        const existing = s.tabs.find((tab) => tab.id === id);
        const tabs = !existing
          ? [...s.tabs, { id, kind: "web" as const, title: hostnameOf(url), url }]
          : existing.url !== url
            ? s.tabs.map((tab) =>
                tab.id === id ? { ...tab, url, title: hostnameOf(url) } : tab,
              )
            : s.tabs;
        return commitCenterTabsState(s, { tabs, splitWebTabId: id });
      });
      return id;
    },

    setSplitWebTab: (id) =>
      set((s) => {
        const tabId =
          id && s.tabs.some((tab) => tab.id === id && tab.kind === "web")
            ? id
            : null;
        if (tabId === null) {
          if (s.splitWebTabId === null) return {};
          const layout = ungroupCenterTab({
            tabIds: s.tabs.map((tab) => tab.id),
            groups: s.groups,
          }, s.splitWebTabId);
          return commitCenterTabsState(s, {
            tabs: tabsForLayout(s.tabs, layout),
            groups: layout.groups,
            splitWebTabId: null,
          });
        }
        const active = s.tabs.find((tab) => tab.id === s.activeId);
        const group = active?.kind === "session"
          ? findCenterTabGroup(s.groups, active.id)
          : undefined;
        if (tabId === s.splitWebTabId && group?.memberIds.includes(tabId)
            && group.visibleIds.includes(active!.id)
            && group.visibleIds.includes(tabId)) return {};
        return commitCenterTabsState(s, { splitWebTabId: tabId });
      }),

    setSplitRatio: (ratio) =>
      set((s) => {
        const splitRatio = clampSplitRatio(ratio);
        if (splitRatio === s.splitRatio) return {};
        return commitCenterTabsState(s, { splitRatio });
      }),

    updateWebTab: (id, patch) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === id && t.kind === "web");
        if (!tab) return {};
        const url = patch.url ?? tab.url;
        // Navigating to a new site resets a stale title to the new
        // hostname unless the caller supplies one.
        const title =
          patch.title ??
          (patch.url && patch.url !== tab.url ? hostnameOf(patch.url) : tab.title);
        if (url === tab.url && title === tab.title) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, url, title } : t));
        return commitCenterTabsState(s, { tabs });
      }),

    setTabDirty: (id, dirty) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === id);
        if (!tab || !!tab.dirty === dirty) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, dirty } : t));
        return commitCenterTabsState(s, { tabs });
      }),

    retargetFileTab: (oldId, newProjectId, newPath) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === oldId && t.kind === "file");
        if (!tab) return {};
        const newId = fileTabId(newProjectId, newPath);
        if (newId === oldId) return {};
        if (s.tabs.some((t) => t.id === newId)) {
          // Target already open — drop the stale tab; if it was the
          // active one, the surviving tab at the new path takes focus.
          const tabs = s.tabs.filter((t) => t.id !== oldId);
          return commitCenterTabsState(s, {
            tabs,
            activeId: s.activeId === oldId ? newId : s.activeId,
          });
        }
        const tabs = s.tabs.map((t) =>
          t.id === oldId
            ? {
                ...t,
                id: newId,
                projectId: newProjectId,
                path: newPath,
                title: newPath.split("/").pop() || newPath,
              }
            : t,
        );
        return commitCenterTabsState(s, {
          tabs,
          activeId: s.activeId === oldId ? newId : s.activeId,
          groups: replaceGroupTabId(s.groups, oldId, newId),
          splitWebTabId: s.splitWebTabId === oldId ? newId : s.splitWebTabId,
        });
      }),

    // ＋ 永远追加一个新的 New-tab 页并聚焦（Chrome：想开几个开几个，不做
    // 单例限制；也不走 focusOrCreate 的 NTP 原地替换——从 NTP 上点＋就该
    // 多出一个）。
    openNewTabPage: () =>
      set((s) => {
        const tab: CenterTab = { id: nextNtpId(), kind: "ntp", title: "" };
        return commitCenterTabsState(s, {
          tabs: [...s.tabs, tab],
          activeId: tab.id,
        });
      }),

    closeTab: (id) =>
      set((s) => {
        const idx = s.tabs.findIndex((t) => t.id === id);
        if (idx < 0) return {};
        const closingTab = s.tabs[idx];
        if (
          closingTab.kind === "session" &&
          closingTab.sessionId &&
          !closingTab.sessionId.startsWith("local_")
        ) {
          closedSessionAckTombstones.add(closingTab.sessionId);
        }
        let tabs = s.tabs.filter((t) => t.id !== id);
        let activeId = s.activeId;
        if (s.activeId === id) {
          activeId = (tabs[idx] ?? tabs[idx - 1])?.id ?? null;
        }
        if (tabs.length === 0) {
          // 关掉最后一个 tab → 兜底给一个新 New-tab 页（栏不能空）。
          const ntp: CenterTab = { id: nextNtpId(), kind: "ntp", title: "" };
          tabs = [ntp];
          activeId = ntp.id;
        }
        const splitWebTabId = s.splitWebTabId === id ? null : s.splitWebTabId;
        return commitCenterTabsState(s, { tabs, activeId, splitWebTabId });
      }),

    renameSessionTab: (sessionId, title) =>
      set((s) => {
        const id = sessionTabId(sessionId);
        if (!s.tabs.some((t) => t.id === id && t.title !== title)) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, title } : t));
        return commitCenterTabsState(s, { tabs });
      }),
  };
});

/** Whether an acknowledgement may activate its session. Missing tabs are
 *  legacy/non-tab callers; existing background tabs never take focus. */
export function sessionAckIsActive(sessionId: string): boolean {
  if (closedSessionAckTombstones.has(sessionId)) return false;
  const state = useCenterTabs.getState();
  const hasTab = state.tabs.some((tab) => tab.sessionId === sessionId);
  if (!hasTab && sessionId.startsWith("local_")) return false;
  return !hasTab || state.activeId === sessionTabId(sessionId);
}
