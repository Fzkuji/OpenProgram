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
export const NTP_TAB_ID = "ntp";

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
export function normalizeWebUrl(input: string): string | null {
  const raw = input.trim();
  if (!raw) return null;
  const withScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)
    ? raw
    : `https://${raw}`;
  try {
    const u = new URL(withScheme);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    if (!u.hostname) return null;
    return u.href;
  } catch {
    return null;
  }
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
   *  the active tab when it's one of `replaceable` (in-place browser
   *  navigation), else appending at the end. */
  function focusOrCreate(
    s: CenterTabsState,
    id: string,
    make: () => CenterTab,
    replaceable: string[],
  ): Partial<CenterTabsState> {
    const existing = s.tabs.find((t) => t.id === id);
    if (existing) {
      const next = { tabs: s.tabs, activeId: id };
      persist(next);
      return next;
    }
    const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
    let tabs: CenterTab[];
    if (activeIdx >= 0 && replaceable.includes(s.tabs[activeIdx].id)) {
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
          [DRAFT_SESSION_TAB_ID, NTP_TAB_ID],
        ),
      ),

    openDraftSessionTab: () =>
      set((s) =>
        focusOrCreate(
          s,
          DRAFT_SESSION_TAB_ID,
          () => ({ id: DRAFT_SESSION_TAB_ID, kind: "session", title: "" }),
          [NTP_TAB_ID],
        ),
      ),

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
          [NTP_TAB_ID],
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
          [NTP_TAB_ID],
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

    openNewTabPage: () =>
      set((s) =>
        focusOrCreate(
          s,
          NTP_TAB_ID,
          () => ({ id: NTP_TAB_ID, kind: "ntp", title: "" }),
          [],
        ),
      ),

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
          tabs = [{ id: NTP_TAB_ID, kind: "ntp", title: "" }];
          activeId = NTP_TAB_ID;
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
