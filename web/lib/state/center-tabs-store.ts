/**
 * Center tab container state — the browser-model tab strip that owns
 * the CENTER of the app: session tabs (💬, bookmarks over the single
 * live chat surface), file tabs (📄, per project+path), and one
 * reusable new-tab page.
 *
 * Deterministic ids make focus-or-create trivial:
 *   session  →  "s:<sessionId>"   (draft new chat → "s:draft")
 *   file     →  "f:<projectId>:<path>"
 *   ntp      →  "ntp"
 *
 * Navigation side effects (router.push on session-tab activation) are
 * NOT here — they live in <CenterTabStrip/>, which also syncs the
 * session store's currentSessionId / titles into this store.
 */
import { create } from "zustand";

export type CenterTabKind = "session" | "file" | "ntp";

export interface CenterTab {
  id: string;
  kind: CenterTabKind;
  /** Session tabs: conversation title (may lag; synced from the
   *  session store). File tabs: basename. NTP: unused (i18n label). */
  title: string;
  /** Session tabs only. Absent on the draft (not-yet-created) chat. */
  sessionId?: string;
  /** File tabs only. */
  projectId?: string;
  /** File tabs only — project-relative, "/"-separated. */
  path?: string;
}

export const DRAFT_SESSION_TAB_ID = "s:draft";
export const NTP_TAB_ID = "ntp";

export function sessionTabId(sessionId: string): string {
  return `s:${sessionId}`;
}
export function fileTabId(projectId: string, path: string): string {
  return `f:${projectId}:${path}`;
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
