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
 *   builtin  →  "b:<page>"       (bookmarks / history — singleton per
 *                                 page, the Chrome chrome:// analogue)
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
  mergeCenterTabGroup,
  moveCenterTab,
  moveCenterTabGroup,
  moveCenterTabGroupMember,
  ungroupCenterTab,
} from "@/lib/state/center-tab-groups";
import type {
  CenterTabGroup,
  CenterTabLayout,
} from "@/lib/state/center-tab-groups";
import type {
  DesktopTransferPayload,
  TabDropPlacement,
  TransferJournalEntry,
} from "@/lib/tab-transfer-journal";
import {
  builtinTabId,
  fileTabId,
  hostnameOf,
  nextNtpId,
  sessionTabId,
  webTabId,
} from "@/lib/state/center-tab-ids";
// Re-exported for callers that still import these from this module.
export {
  builtinTabId,
  fileTabId,
  normalizeWebUrl,
  sessionTabId,
  webTabId,
} from "@/lib/state/center-tab-ids";
export type { BuiltinPage } from "@/lib/state/center-tab-ids";
import type { BuiltinPage } from "@/lib/state/center-tab-ids";
import {
  clampSplitRatio,
  desktopWindowId,
  draftTab,
  normalizeCenterTabsPayload,
  orderTabs,
  persistCenterTabsPayload,
  persistedState,
  readCenterTabsPayload,
  replaceGroupTabId,
} from "@/lib/state/center-tabs-persistence";
import type {
  CenterTabsPersistedPayload,
  CenterTabsPersistedState,
} from "@/lib/state/center-tabs-persistence";
// Re-exported for callers that still import these from this module.
export {
  DRAFT_SESSION_TAB_ID,
  rebaseCenterTabsPayload,
} from "@/lib/state/center-tabs-persistence";
export type {
  CenterTabsPersistedPayload,
} from "@/lib/state/center-tabs-persistence";

export type CenterTabKind = "session" | "file" | "web" | "ntp" | "builtin";

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
  /** Web tabs only — favicon URL reported by the desktop shell; the
   *  strip falls back to the Globe icon when absent or unloadable. */
  faviconUrl?: string;
  /** Builtin tabs only — which built-in page this tab shows. */
  page?: BuiltinPage;
  /** Unsaved-changes marker — strip shows ● instead of ✕. Set via
   *  setTabDirty by whoever owns the tab's content (file editor). */
  dirty?: boolean;
}

// A missing non-local tab normally means a legacy caller that predates the
// center-tab UI, so its ACK is still allowed to activate the session. Preserve
// the one distinction the tab array cannot retain after removal: the user
// explicitly closed this session. Tombstones live only for this page lifetime
// and are cleared by an explicit reopen.
const closedSessionAckTombstones = new Set<string>();

/** Ask the desktop shell to close THIS window (last tab closed → close window,
 *  Chrome parity). No-op off desktop or if the bridge lacks the method. */
function requestDesktopWindowClose(): void {
  if (typeof window === "undefined") return;
  const bridge = (window as unknown as {
    openprogramDesktop?: { closeWindow?: () => void };
  }).openprogramDesktop;
  bridge?.closeWindow?.();
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
  mergeGroup: (
    sourceGroupId: string,
    targetId: string,
    memberIndex: number,
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
  updateWebTab: (
    id: string,
    patch: { url?: string; title?: string; faviconUrl?: string },
  ) => void;
  /** Unsaved-changes marker groundwork — content owners call this;
   *  the strip renders ● instead of ✕ while dirty. */
  setTabDirty: (id: string, dirty: boolean) => void;
  /** Retarget a file tab after its file was renamed/moved on disk:
   *  new deterministic id + title (basename), order and active state
   *  preserved. If a tab already exists at the new id, the stale tab
   *  closes instead (focus moves to the survivor if it was active). */
  retargetFileTab: (oldId: string, newProjectId: string, newPath: string) => void;
  /** Focus-or-create the singleton tab for a built-in page. */
  openBuiltinTab: (page: BuiltinPage) => void;
  /** Single-instance new-tab page — reused if already open. */
  openNewTabPage: () => void;
  /** Close a tab; closing the active one activates the right
   *  neighbor, else the left. Never leaves zero tabs (falls back to
   *  the new-tab page). */
  closeTab: (id: string) => void;
  renameSessionTab: (sessionId: string, title: string) => void;
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

    mergeGroup: (sourceGroupId, targetId, memberIndex) => {
      let accepted = false;
      set((s) => {
        const currentLayout = {
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        };
        const result = mergeCenterTabGroup(
          currentLayout,
          sourceGroupId,
          targetId,
          memberIndex,
        );
        accepted = result.accepted;
        if (!accepted || result.layout === currentLayout) return {};
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

    // Deterministic id ⇒ focusOrCreate already enforces the singleton:
    // a second "open bookmarks" focuses the existing tab.
    openBuiltinTab: (page) =>
      set((s) =>
        focusOrCreate(
          s,
          builtinTabId(page),
          () => ({ id: builtinTabId(page), kind: "builtin", title: "", page }),
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
        // Navigating to a new site drops the old site's icon unless the
        // caller supplies one (main.js clears it on did-navigate too).
        const faviconUrl =
          patch.faviconUrl ??
          (patch.url && patch.url !== tab.url ? undefined : tab.faviconUrl);
        if (url === tab.url && title === tab.title && faviconUrl === tab.faviconUrl) {
          return {};
        }
        const tabs = s.tabs.map((t) =>
          t.id === id ? { ...t, url, title, faviconUrl } : t
        );
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
          if (desktopWindowId()) {
            // Chrome parity: closing a desktop window's last tab closes the
            // WINDOW (main handles "last window ⇒ stay open, don't quit").
            // Ask the shell to close; keep the strip non-empty meanwhile so
            // the brief render before the window goes away has a valid tab.
            requestDesktopWindowClose();
          }
          // Browser mode (and the transient desktop frame above) can't show an
          // empty strip — fall back to a fresh New-tab page.
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

export function snapshotCenterTabsPayload(): CenterTabsPersistedPayload {
  const state = useCenterTabs.getState();
  return normalizeCenterTabsPayload({
    tabs: state.tabs,
    activeId: state.activeId,
    groups: state.groups,
    splitWebTabId: state.splitWebTabId,
    splitRatio: state.splitRatio,
  });
}

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length
    && left.every((id, index) => id === right[index]);
}

function sourceGroup(
  payload: DesktopTransferPayload,
): CenterTabGroup | null {
  if (payload.source.kind === "tab") return null;
  const transferred = new Set(payload.tabs.map((tab) => tab.id));
  const sourceMemberIds = payload.source.memberIds
    ?? payload.tabs.map((tab) => tab.id);
  const memberIds = payload.source.kind === "segment"
    ? sourceMemberIds.filter((id) => transferred.has(id))
    : sourceMemberIds;
  if (memberIds.length < 2) return null;
  const visibleIds = (payload.source.visibleIds ?? memberIds.slice(0, 2))
    .filter((id) => transferred.has(id));
  return {
    id: payload.source.groupId ?? `g:transfer:${memberIds.join(":")}`,
    memberIds,
    visibleIds,
    focusedId: payload.source.focusedId ?? visibleIds[0] ?? memberIds[0],
  };
}

function transferredActiveId(payload: DesktopTransferPayload): string | null {
  const activeChat = payload.chats.find((chat) => chat.wasActive)?.chatKey;
  return payload.tabs.find((tab) => tab.sessionId === activeChat)?.id
    ?? payload.source.focusedId
    ?? null;
}

/**
 * A lone unmodified placeholder — an ntp "New tab" page or an empty draft
 * "New chat" session (draft:true, still title "", never navigated). Same
 * consumable predicate the store uses when navigating an active ntp/draft
 * replaces it in place rather than adding a tab.
 */
function isConsumablePlaceholder(tab: CenterTab): boolean {
  return tab.kind === "ntp" || (tab.kind === "session" && tab.draft === true);
}

function transferredPayloadAfter(
  before: CenterTabsPersistedPayload,
  payload: DesktopTransferPayload,
  placement: TabDropPlacement,
): CenterTabsPersistedPayload | null {
  // A tab delivered into a fresh window (its sole tab an empty placeholder)
  // must consume that placeholder, not sit beside it. Consume ONLY on a plain
  // strip-end append: the incoming tabs replace the placeholder outright and
  // the result is well-formed (delivered tabs inserted from an empty base, one
  // becomes active, no dangling group/target left behind). A positioned drop
  // (before/after/merge) targets the placeholder itself, so it must survive —
  // consuming it would leave the placement referencing a tab that no longer
  // exists, which is exactly the "delivered tab disappears" report.
  if (
    placement.kind === "strip-end" &&
    placement.consumePlaceholder === true &&
    before.tabs.length === 1 &&
    isConsumablePlaceholder(before.tabs[0])
  ) {
    before = { ...before, tabs: [], groups: [], activeId: null };
  }
  const transferIds = payload.tabs.map((tab) => tab.id);
  const allTabs = [...before.tabs, ...payload.tabs];
  const targetId = placement.kind === "strip-end" ? null : placement.targetTabId;
  const targetGroup = targetId ? findCenterTabGroup(before.groups, targetId) : undefined;
  let tabIds = before.tabs.map((tab) => tab.id);
  let groups = [...before.groups];

  if (placement.kind === "merge") {
    const targetMembers = targetGroup?.memberIds ?? [placement.targetTabId];
    const at = Math.max(0, Math.min(
      placement.memberIndex ?? targetMembers.indexOf(placement.targetTabId) + 1,
      targetMembers.length,
    ));
    const memberIds = [...targetMembers];
    memberIds.splice(at, 0, ...transferIds);
    const transferGroup = sourceGroup(payload);
    const visibleIds = transferGroup
      ? [
          ...transferGroup.visibleIds,
          ...(targetGroup?.visibleIds ?? [placement.targetTabId]),
        ]
          .filter((id, index, ids) => ids.indexOf(id) === index)
          .slice(0, 2)
      : [
          ...(targetGroup?.visibleIds ?? [placement.targetTabId]),
          ...transferIds,
        ]
          .filter((id, index, ids) => ids.indexOf(id) === index)
          .slice(0, 2);
    const group: CenterTabGroup = {
      id: placement.groupId
        ?? targetGroup?.id
        ?? transferGroup?.id
        ?? `g:transfer:${memberIds.join(":")}`,
      memberIds,
      visibleIds,
      focusedId: transferGroup?.focusedId ?? targetGroup?.focusedId ?? placement.targetTabId,
    };
    groups = targetGroup
      ? groups.map((candidate) => candidate.id === targetGroup.id ? group : candidate)
      : [...groups, group];
    const memberSet = new Set(targetMembers);
    const remaining = tabIds.filter((id) => !memberSet.has(id));
    const targetAt = Math.min(...targetMembers.map((id) => tabIds.indexOf(id)));
    tabIds = [
      ...remaining.slice(0, targetAt),
      ...memberIds,
      ...remaining.slice(targetAt),
    ];
  } else {
    let at = tabIds.length;
    if (targetId) {
      const targetMembers = targetGroup?.memberIds ?? [targetId];
      const positions = targetMembers.map((id) => tabIds.indexOf(id));
      at = placement.kind === "before"
        ? Math.min(...positions)
        : Math.max(...positions) + 1;
    }
    tabIds.splice(at, 0, ...transferIds);
    const group = sourceGroup(payload);
    if (group) groups.push(group);
  }

  return normalizeCenterTabsPayload({
    ...before,
    tabs: orderTabs(allTabs, tabIds),
    groups,
    activeId: transferredActiveId(payload) ?? before.activeId,
  });
}

export function validateTransferredTabs(
  payload: DesktopTransferPayload,
  placement: TabDropPlacement,
):
  | { ok: true; after: CenterTabsPersistedPayload }
  | { ok: false; reason: "duplicate" | "group-full" | "invalid"; duplicateId?: string } {
  const before = snapshotCenterTabsPayload();
  const ids = payload.tabs.map((tab) => tab.id);
  if (ids.length === 0 || new Set(ids).size !== ids.length) {
    return { ok: false, reason: "invalid" };
  }
  const duplicateId = ids.find((id) => before.tabs.some((tab) => tab.id === id));
  if (duplicateId) return { ok: false, reason: "duplicate", duplicateId };
  if (payload.source.kind === "tab" && ids.length !== 1) {
    return { ok: false, reason: "invalid" };
  }
  if (payload.source.kind === "segment" && ids.length !== 1) {
    return { ok: false, reason: "invalid" };
  }
  if (payload.source.kind !== "tab") {
    const memberIds = payload.source.memberIds;
    if (!memberIds) return { ok: false, reason: "invalid" };
    const visibleIds = payload.source.visibleIds ?? memberIds?.slice(0, 2) ?? [];
    const focusedId = payload.source.focusedId ?? visibleIds[0];
    if (payload.source.kind === "group" && ids.length > 3) {
      return { ok: false, reason: "group-full" };
    }
    const segmentAt = payload.source.memberIndex;
    const validMembers = payload.source.kind === "segment"
      ? segmentAt !== undefined
        && Number.isInteger(segmentAt)
        && segmentAt >= 0
        && sameIds(memberIds.slice(segmentAt, segmentAt + ids.length), ids)
      : sameIds(memberIds, ids);
    if (
      !validMembers
      || (payload.source.kind === "group" && memberIds.length < 2)
      || visibleIds.some((id) => !memberIds.includes(id))
      || visibleIds.length > 2
      || (focusedId !== undefined && !visibleIds.includes(focusedId))
      || (payload.source.kind !== "segment"
        && payload.source.memberIndex !== undefined
        && (!Number.isInteger(payload.source.memberIndex)
          || payload.source.memberIndex < 0))
    ) return { ok: false, reason: "invalid" };
    if (
      payload.source.groupId
      && (payload.source.kind === "group" || ids.length > 1)
      && before.groups.some((group) => group.id === payload.source.groupId)
    ) return { ok: false, reason: "invalid" };
  }
  if (placement.kind !== "strip-end") {
    const target = before.tabs.find((tab) => tab.id === placement.targetTabId);
    if (!target) return { ok: false, reason: "invalid" };
  }
  if (placement.kind === "merge") {
    const targetGroup = findCenterTabGroup(before.groups, placement.targetTabId);
    if ((targetGroup?.memberIds.length ?? 1) + ids.length > 3) {
      return { ok: false, reason: "group-full" };
    }
  }
  const after = transferredPayloadAfter(before, payload, placement);
  return after ? { ok: true, after } : { ok: false, reason: "invalid" };
}

export function insertTransferredTabs(
  payload: DesktopTransferPayload,
  placement: TabDropPlacement,
  _options: { persist: false },
): {
  ok: boolean;
  before: CenterTabsPersistedPayload;
  after: CenterTabsPersistedPayload;
} {
  void _options;
  const before = snapshotCenterTabsPayload();
  const validated = validateTransferredTabs(payload, placement);
  if (!validated.ok) return { ok: false, before, after: before };
  useCenterTabs.setState(persistedState(validated.after));
  return { ok: true, before, after: validated.after };
}

export function removeTransferredTabs(
  ids: string[],
  _options: { persist: false },
): {
  ok: boolean;
  empty: boolean;
  before: CenterTabsPersistedPayload;
  after: CenterTabsPersistedPayload;
} {
  void _options;
  const before = snapshotCenterTabsPayload();
  const removed = new Set(ids);
  const valid = ids.length > 0
    && removed.size === ids.length
    && ids.every((id) => before.tabs.some((tab) => tab.id === id));
  if (!valid) return { ok: false, empty: before.tabs.length === 0, before, after: before };

  const activeIndex = before.tabs.findIndex((tab) => tab.id === before.activeId);
  const tabs = before.tabs.filter((tab) => !removed.has(tab.id));
  const activeId = before.activeId && !removed.has(before.activeId)
    ? before.activeId
    : tabs[activeIndex]?.id ?? tabs[activeIndex - 1]?.id ?? null;
  const after = normalizeCenterTabsPayload({
    ...before,
    tabs,
    activeId,
    groups: before.groups.flatMap((group) => {
      const memberIds = group.memberIds.filter((id) => !removed.has(id));
      if (memberIds.length < 2) return [];
      const visibleIds = group.visibleIds.filter((id) => !removed.has(id));
      return [{
        ...group,
        memberIds,
        visibleIds,
        focusedId: visibleIds.includes(group.focusedId)
          ? group.focusedId
          : visibleIds[0] ?? memberIds[0],
      }];
    }),
    splitWebTabId: before.splitWebTabId && removed.has(before.splitWebTabId)
      ? null
      : before.splitWebTabId,
  });
  useCenterTabs.setState(persistedState(after));
  return { ok: true, empty: tabs.length === 0, before, after };
}

export function replaceCenterTabsPayload(
  payload: CenterTabsPersistedPayload,
  options: { persist: boolean },
): boolean {
  const normalized = normalizeCenterTabsPayload(payload);
  useCenterTabs.setState(persistedState(normalized));
  return options.persist ? persistCenterTabsPayload(normalized) : true;
}

export function persistCurrentCenterTabsPayload(): boolean {
  return persistCenterTabsPayload(snapshotCenterTabsPayload());
}
