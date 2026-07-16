/**
 * Files-panel state — open/closed flag, open tabs, and the active file
 * of the in-chat project files panel (components/files/).
 *
 * Deliberately its own store (not a session-store slice): the panel is
 * scoped to the *project*, not the conversation. State persists to
 * localStorage under `filesPanel:<projectId>` so each project restores
 * its own tab set; `setProject()` swaps the in-memory state when the
 * conversation's project changes.
 */
import { create } from "zustand";

import { wsRequest } from "@/lib/net/ws-request";

export interface FileTab {
  path: string; // project-relative, "/"-separated
  name: string; // basename, for the tab label
}

interface Persisted {
  open: boolean;
  tabs: FileTab[];
  activePath: string | null;
}

function storageKey(projectId: string): string {
  return `filesPanel:${projectId}`;
}

function readPersisted(projectId: string | null): Persisted | null {
  if (!projectId || typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    return raw ? (JSON.parse(raw) as Persisted) : null;
  } catch {
    return null;
  }
}

function persist(projectId: string | null, s: Persisted): void {
  if (!projectId || typeof window === "undefined") return;
  try {
    localStorage.setItem(storageKey(projectId), JSON.stringify(s));
  } catch {
    /* quota / private mode — panel still works, just doesn't restore */
  }
}

interface FilesPanelState {
  open: boolean;
  projectId: string | null;
  tabs: FileTab[];
  activePath: string | null;
  toggleOpen: () => void;
  setProject: (projectId: string | null) => void;
  openFile: (path: string) => void;
  closeTab: (path: string) => void;
  setActive: (path: string) => void;
}

export const useFilesPanel = create<FilesPanelState>((set) => ({
  open: false,
  projectId: null,
  tabs: [],
  activePath: null,

  toggleOpen: () =>
    set((s) => {
      const open = !s.open;
      persist(s.projectId, { open, tabs: s.tabs, activePath: s.activePath });
      return { open };
    }),

  setProject: (projectId) =>
    set((s) => {
      if (projectId === s.projectId) return {};
      const saved = readPersisted(projectId);
      return {
        projectId,
        // Keep the current open/closed state across a project switch
        // unless the new project has its own saved preference — so
        // toggling the panel before the project resolves isn't undone.
        open: saved?.open ?? s.open,
        tabs: saved?.tabs ?? [],
        activePath: saved?.activePath ?? null,
      };
    }),

  openFile: (path) =>
    set((s) => {
      const name = path.split("/").pop() || path;
      const tabs = s.tabs.some((t) => t.path === path)
        ? s.tabs
        : [...s.tabs, { path, name }];
      persist(s.projectId, { open: s.open, tabs, activePath: path });
      return { tabs, activePath: path };
    }),

  closeTab: (path) =>
    set((s) => {
      const idx = s.tabs.findIndex((t) => t.path === path);
      const tabs = s.tabs.filter((t) => t.path !== path);
      const activePath =
        s.activePath === path
          ? (tabs[Math.min(idx, tabs.length - 1)]?.path ?? null)
          : s.activePath;
      persist(s.projectId, { open: s.open, tabs, activePath });
      return { tabs, activePath };
    }),

  setActive: (path) =>
    set((s) => {
      persist(s.projectId, { open: s.open, tabs: s.tabs, activePath: path });
      return { activePath: path };
    }),
}));

/* ---- WS helpers shared by file-tree / file-viewer ----------------- */

// wsRequest matches replies by frame *type* only, so two in-flight
// requests of the same action would both resolve with whichever reply
// lands first. Serialise all files-panel requests through one chain.
// ponytail: global queue; per-action queues if tree loads ever feel slow.
let queue: Promise<unknown> = Promise.resolve();

export function filesWsRequest<T>(
  action: string,
  payload: Record<string, unknown>,
  responseType: string,
): Promise<T | null> {
  const next = queue.then(() => wsRequest<T>(action, payload, responseType));
  queue = next.catch(() => null);
  return next;
}

/** Last mtime seen per project-relative file path (fed by the tree
 * listing) — lets the viewer cache invalidate on refetch. */
export const latestFileMtime = new Map<string, number>();

/** URL of the backend raw-bytes endpoint (proxied by
 * app/files/[...path]/route.ts so the worker port stays live). */
export function rawFileUrl(projectId: string, path: string): string {
  return `/files/raw?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`;
}
