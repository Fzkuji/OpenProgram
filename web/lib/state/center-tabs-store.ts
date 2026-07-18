/**
 * Center tab container state — the browser-model tab strip that owns
 * the CENTER of the app: session tabs (💬, bookmarks over the single
 * live chat surface), file tabs (📄, per project+path), and one
 * reusable new-tab page.
 *
 * Deterministic ids make focus-or-create trivial:
 *   session  →  "s:<sessionId>"   (draft new chat → "s:draft")
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

export type CenterTabKind = "session" | "file" | "web" | "ntp";

export interface CenterTab {
  id: string;
  kind: CenterTabKind;
  /** Session tabs: conversation title (may lag; synced from the
   *  session store). File tabs: basename. Web tabs: hostname until
   *  updateWebTab sets a real title. NTP: unused (i18n label). */
  title: string;
  /** Session tabs only. Absent on the draft (not-yet-created) chat. */
  sessionId?: string;
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

export const DRAFT_SESSION_TAB_ID = "s:draft";
/** New-tab 页不再是单例（Chrome 行为：＋ 想开几个开几个），每个实例一个
 *  唯一 id。时间戳 + 自增序号，避免与持久化恢复的旧 id 撞车。 */
let ntpSeq = 0;
function nextNtpId(): string {
  return `ntp:${Date.now().toString(36)}:${(ntpSeq++).toString(36)}`;
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

const LS_KEY = "centerTabs";

interface Persisted {
  tabs: CenterTab[];
  activeId: string | null;
}

function readPersisted(): Persisted {
  if (typeof window === "undefined") return { tabs: [], activeId: null };
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { tabs: [], activeId: null };
    const parsed = JSON.parse(raw) as Persisted;
    if (!Array.isArray(parsed.tabs)) return { tabs: [], activeId: null };
    // Dirty never survives a reload — the unsaved buffer that set it
    // is gone. Older persisted entries (no "web" kind, no dirty) pass
    // through untouched.
    parsed.tabs = parsed.tabs.map((t) => (t.dirty ? { ...t, dirty: false } : t));
    return {
      tabs: parsed.tabs,
      activeId: parsed.tabs.some((t) => t.id === parsed.activeId)
        ? parsed.activeId
        : (parsed.tabs[0]?.id ?? null),
    };
  } catch {
    return { tabs: [], activeId: null };
  }
}

function persist(s: { tabs: CenterTab[]; activeId: string | null }): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({ tabs: s.tabs, activeId: s.activeId }),
    );
  } catch {
    /* quota / private mode — tabs still work, just don't restore */
  }
}

interface CenterTabsState {
  tabs: CenterTab[];
  activeId: string | null;
  setActive: (id: string) => void;
  /** Focus-or-create the tab for a live session. Browser semantics:
   *  if the ACTIVE tab is the draft chat or the new-tab page, the
   *  session "navigates" that tab (replaces it in place) instead of
   *  opening a new one. */
  openSessionTab: (sessionId: string, title: string) => void;
  /** Focus-or-create the draft new-chat tab (session without an id
   *  yet). An active new-tab page is navigated in place. */
  openDraftSessionTab: () => void;
  /** NTP 的 New session：草稿会话 tab 落在当前位置（活动 tab 原地变身），
   *  别处已有的草稿 tab 收掉。区别于 openDraftSessionTab 的"引导到已有
   *  草稿"（那是侧栏 New chat 的语义）。 */
  claimDraftSessionTab: () => void;
  openFileTab: (projectId: string, path: string) => void;
  /** Focus-or-create a web tab for `url` (must already be a valid
   *  http(s) URL — run user input through normalizeWebUrl first). */
  openWebTab: (url: string) => void;
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

export const useCenterTabs = create<CenterTabsState>((set) => {
  const initial = readPersisted();

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
      const next = { tabs, activeId: id };
      persist(next);
      return next;
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
    const next = { tabs, activeId: id };
    persist(next);
    return next;
  }

  return {
    tabs: initial.tabs,
    activeId: initial.activeId,

    setActive: (id) =>
      set((s) => {
        if (!s.tabs.some((t) => t.id === id) || s.activeId === id) return {};
        const next = { tabs: s.tabs, activeId: id };
        persist(next);
        return next;
      }),

    openSessionTab: (sessionId, title) =>
      set((s) =>
        focusOrCreate(
          s,
          sessionTabId(sessionId),
          () => ({
            id: sessionTabId(sessionId),
            kind: "session",
            title,
            sessionId,
          }),
          [DRAFT_SESSION_TAB_ID],
        ),
      ),

    openDraftSessionTab: () =>
      set((s) =>
        focusOrCreate(
          s,
          DRAFT_SESSION_TAB_ID,
          () => ({ id: DRAFT_SESSION_TAB_ID, kind: "session", title: "" }),
          [],
        ),
      ),

    claimDraftSessionTab: () =>
      set((s) => {
        const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
        if (activeIdx < 0) return {};
        const tabs = s.tabs
          .filter((t, i) => t.id !== DRAFT_SESSION_TAB_ID || i === activeIdx)
          .map((t) =>
            t.id === s.activeId
              ? ({ id: DRAFT_SESSION_TAB_ID, kind: "session", title: "" } as CenterTab)
              : t,
          );
        const next = { tabs, activeId: DRAFT_SESSION_TAB_ID };
        persist(next);
        return next;
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
      set((s) =>
        focusOrCreate(
          s,
          webTabId(url),
          () => ({
            id: webTabId(url),
            kind: "web",
            title: hostnameOf(url),
            url,
          }),
          [],
        ),
      ),

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
        const next = { tabs, activeId: s.activeId };
        persist(next);
        return next;
      }),

    setTabDirty: (id, dirty) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === id);
        if (!tab || !!tab.dirty === dirty) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, dirty } : t));
        const next = { tabs, activeId: s.activeId };
        persist(next);
        return next;
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
          const next = {
            tabs,
            activeId: s.activeId === oldId ? newId : s.activeId,
          };
          persist(next);
          return next;
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
        const next = {
          tabs,
          activeId: s.activeId === oldId ? newId : s.activeId,
        };
        persist(next);
        return next;
      }),

    // ＋ 永远追加一个新的 New-tab 页并聚焦（Chrome：想开几个开几个，不做
    // 单例限制；也不走 focusOrCreate 的 NTP 原地替换——从 NTP 上点＋就该
    // 多出一个）。
    openNewTabPage: () =>
      set((s) => {
        const tab: CenterTab = { id: nextNtpId(), kind: "ntp", title: "" };
        const next = { tabs: [...s.tabs, tab], activeId: tab.id };
        persist(next);
        return next;
      }),

    closeTab: (id) =>
      set((s) => {
        const idx = s.tabs.findIndex((t) => t.id === id);
        if (idx < 0) return {};
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
        const next = { tabs, activeId };
        persist(next);
        return next;
      }),

    renameSessionTab: (sessionId, title) =>
      set((s) => {
        const id = sessionTabId(sessionId);
        if (!s.tabs.some((t) => t.id === id && t.title !== title)) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, title } : t));
        const next = { tabs, activeId: s.activeId };
        persist(next);
        return next;
      }),
  };
});
