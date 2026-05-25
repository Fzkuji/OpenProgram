/**
 * Shared TS types for the right-rail Worktrees panel.
 *
 * Mirrors `openprogram/worktree/types.py::Worktree.to_dict()`. The
 * Python side stamps ``status`` as the enum string ("active",
 * "committing", "merged", "discarded", "kept", "errored") and emits
 * ``created_at`` / ``completed_at`` as floats (unix seconds).
 *
 * The WS wire shape is documented in
 * `openprogram/webui/ws_actions/worktree.py` — the panel sends
 * `list_worktrees` on mount and on the `op:worktree-status` window
 * event (broadcast from the WorktreeManager's `_transition`); each
 * row in the response is one `Worktree`.
 */

export type WorktreeStatus =
  | "active"
  | "committing"
  | "merged"
  | "discarded"
  | "kept"
  | "errored";

export interface Worktree {
  id: string;
  source_repo: string;
  worktree_path: string;
  branch_name: string;
  base_ref: string;
  status: WorktreeStatus;
  created_at: number;
  completed_at: number | null;
  parent_session: string | null;
  parent_task: string | null;
  merge_strategy: string;
  merge_sha: string | null;
  files_changed: number;
  error: string | null;
}

export interface WorktreeWindow {
  ws?: WebSocket;
}

export function wsSend(payload: unknown): void {
  const w = window as unknown as WorktreeWindow;
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

/** Statuses considered "still in play" — the panel surfaces these
 *  by default. Terminal-but-recent rows are kept around for a few
 *  minutes so a merge / discard doesn't make the row vanish before
 *  the user has a chance to read the result. */
export const NON_TERMINAL: ReadonlySet<WorktreeStatus> = new Set<WorktreeStatus>([
  "active",
  "committing",
  "errored",
]);

/** How long to keep terminal rows (merged / discarded / kept) on
 *  screen after their completed_at, in ms. Short (5s) so the user
 *  sees the result animate to a final state then the row falls off
 *  — panel is supposed to reflect what's CURRENTLY going on, not
 *  serve as a transaction log. History is available via /worktrees
 *  page (future) or git log. */
export const TERMINAL_DISPLAY_MS = 5 * 1000;

/** Shorten a long absolute path for display. Keeps the last two
 *  path segments so the user still recognises which repo it is. */
export function shortPath(p: string | null | undefined): string {
  if (!p) return "";
  const trimmed = p.replace(/\/$/, "");
  const parts = trimmed.split("/");
  if (parts.length <= 2) return trimmed;
  return ".../" + parts.slice(-2).join("/");
}

/** "5m ago" style relative timestamp. Falls back to absolute ISO for
 *  things older than a week. Mirrors what the topbar uses elsewhere
 *  so the visual language stays consistent. */
export function relativeTime(unix: number | null | undefined): string {
  if (!unix) return "";
  const seconds = Math.max(0, Date.now() / 1000 - unix);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)}d ago`;
  try {
    return new Date(unix * 1000).toISOString().slice(0, 10);
  } catch {
    return "";
  }
}
