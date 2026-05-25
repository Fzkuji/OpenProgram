"use client";

/**
 * Worktrees panel — right-rail surface for the agent's active git
 * worktrees (the worktree_create / worktree_merge tool family).
 *
 * Sits below `<BranchesPanel />` in the History view. The list is
 * hydrated on mount via the `list_worktrees` WS action and refreshed
 * whenever the backend broadcasts `worktree_status` (see
 * `WorktreeManager._transition` -> `_broadcast_worktree_status`).
 *
 * The panel auto-hides when there's nothing to show — both the empty
 * "no worktrees" case AND the "every worktree is terminal and older
 * than TERMINAL_DISPLAY_MS" case. We don't want a dead section
 * taking up space when the agent isn't using worktrees.
 *
 * Scope: by default we show worktrees for the current session only
 * (matches the way the agent tools tag them via `parent_session`).
 * The WS API supports a global scope too but that's not surfaced
 * here yet — there's no demand for it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

import { WorktreeItem } from "./worktree-item";
import {
  NON_TERMINAL,
  TERMINAL_DISPLAY_MS,
  wsSend,
  type Worktree,
} from "./types";

interface WorktreesMessageDetail {
  type: string;
  data: Record<string, unknown>;
}

interface WorktreeStatusDetail {
  worktree_id?: string;
  status?: string;
  source_repo?: string | null;
  branch_name?: string | null;
  parent_session?: string | null;
  merge_sha?: string | null;
  error?: string | null;
  worktree?: Record<string, unknown> | null;
}

export function WorktreesPanel() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  // We tick the clock every 30s so the relative-time labels and the
  // "fade out terminal" filter both stay fresh without depending on
  // a backend broadcast for a purely visual refresh.
  const [, setNow] = useState(() => Date.now());
  // Pending list_worktrees we haven't received a reply for yet —
  // suppresses races where the request goes out, the user switches
  // session, and the stale response replaces the new session's list.
  const requestSession = useRef<string | null | undefined>(null);

  const requestList = useCallback((sid: string | null) => {
    requestSession.current = sid;
    wsSend({
      action: "list_worktrees",
      session_id: sid,
      scope: sid ? "session" : "all",
    });
  }, []);

  // Initial fetch + re-fetch on session change. We deliberately do
  // NOT include worktrees terminal-filter on the wire — let the
  // server return everything for the session, and let the client
  // drop old terminals on the timer below. That way a merge that
  // just completed still flashes on screen for a few minutes.
  useEffect(() => {
    if (sessionId === undefined) return;
    setWorktrees([]);
    requestList(sessionId);
  }, [sessionId, requestList]);

  // Listen for the worktrees_list response and refresh the local
  // list. We route through the same `op:worktree-message` window
  // event family as branches uses — emitted by use-ws.ts dispatch.
  useEffect(() => {
    const onMsg = (e: Event) => {
      const ce = e as CustomEvent<WorktreesMessageDetail>;
      const det = ce.detail;
      if (!det) return;
      if (det.type !== "worktrees_list") return;
      const replySid = det.data?.session_id as string | null | undefined;
      // Race guard: only honour the response for the session that
      // matches our last request (or both null = global scope).
      if ((replySid ?? null) !== (requestSession.current ?? null)) return;
      const rows = (det.data?.worktrees as Worktree[] | undefined) || [];
      setWorktrees(rows);
    };
    window.addEventListener("op:worktree-message", onMsg as EventListener);
    return () => {
      window.removeEventListener("op:worktree-message", onMsg as EventListener);
    };
  }, []);

  // Listen for individual worktree status broadcasts and patch the
  // local list. The backend `_broadcast_worktree_status` emits the
  // full row (`worktree` field) so we can splice it in directly
  // without another round-trip.
  useEffect(() => {
    const onStatus = (e: Event) => {
      const ce = e as CustomEvent<WorktreeStatusDetail>;
      const det = ce.detail;
      if (!det || !det.worktree_id) return;
      const row = det.worktree as unknown as Worktree | undefined;
      setWorktrees((cur) => {
        const idx = cur.findIndex((w) => w.id === det.worktree_id);
        if (!row) {
          // The row was destroyed (rare — manager keeps records); if
          // we knew about it, drop it.
          if (idx < 0) return cur;
          const next = cur.slice();
          next.splice(idx, 1);
          return next;
        }
        // Filter to current session scope. If the broadcast row
        // belongs to a different session we ignore it.
        if (sessionId && row.parent_session && row.parent_session !== sessionId) {
          // Different session — drop if it was here.
          if (idx >= 0) {
            const next = cur.slice();
            next.splice(idx, 1);
            return next;
          }
          return cur;
        }
        if (idx < 0) return [...cur, row];
        const next = cur.slice();
        next[idx] = row;
        return next;
      });
    };
    window.addEventListener("op:worktree-status", onStatus as EventListener);
    return () => {
      window.removeEventListener("op:worktree-status", onStatus as EventListener);
    };
  }, [sessionId]);

  // Periodic clock tick — only mounted while there are terminal
  // rows still being held on display (the timer is purely so the
  // useMemo below re-evaluates `Date.now()` and drops stale rows).
  // No need to spin a 30s interval when the panel is empty.
  useEffect(() => {
    if (worktrees.length === 0) return;
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, [worktrees.length]);

  // Compute the visible subset:
  //   - non-terminal rows: always visible
  //   - terminal rows: visible only while completed_at within window
  const visible = useMemo(() => {
    const cutoff = Date.now() / 1000 - TERMINAL_DISPLAY_MS / 1000;
    return worktrees
      .filter((w) => {
        if (NON_TERMINAL.has(w.status)) return true;
        const ts = w.completed_at || w.created_at;
        return ts >= cutoff;
      })
      .sort((a, b) => {
        // Non-terminal first, then newest-first within each bucket.
        const aN = NON_TERMINAL.has(a.status) ? 0 : 1;
        const bN = NON_TERMINAL.has(b.status) ? 0 : 1;
        if (aN !== bN) return aN - bN;
        return (b.created_at || 0) - (a.created_at || 0);
      });
  }, [worktrees]);

  // Auto-hide the whole section when there's nothing to show. We
  // don't want a "Worktrees (0)" header eating vertical space the
  // history graph could use.
  if (visible.length === 0) return null;

  return (
    <div className={"worktrees-section" + (collapsed ? " is-collapsed" : "")}>
      <div
        className="sidebar-section-header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="sidebar-section-title">
          Worktrees ({visible.length})
        </span>
        <span className="sidebar-section-hint">
          {collapsed ? "Show" : "Hide"}
        </span>
      </div>
      {!collapsed ? (
        <div className="worktrees-list">
          {visible.map((wt) => (
            <WorktreeItem key={wt.id} wt={wt} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
