"use client";

/**
 * Context-commit timeline — right-dock "Context" view.
 *
 * Lists this session's context commits (newest first). Clicking a row
 * expands its items so the user can see exactly what the LLM was given
 * on that turn (rendered text + per-item state). Parallel commits at
 * the same DAG fork point collapse into a single row with an attempt
 * switcher (CommitGroupRow). Item list is paginated (ItemList).
 *
 * Talks to the backend via the shared app socket (`getSocket()`):
 *   send  → {action:"list_context_commits", session_id}
 *           {action:"get_context_commit_detail", commit_id}
 *   recv  → {type:"context_commits_list",  data:{session_id, commits}}
 *           {type:"context_commit_detail", data:{id, items, ...}}
 *
 * Local state only — no zustand slice. We piggyback on the raw
 * MessageEvent stream alongside the existing window-bridge dispatcher.
 *
 * Each conversation's list and every commit's items are kept for the life
 * of the window: coming back draws them at once and the list is asked for
 * again behind them. A list reply that carries an error leaves what is
 * shown and is asked for again, without a word, waiting 1 s and doubling
 * up to 30 s. A commit whose items do not arrive is asked for again the same
 * way while its row is open; a reply that says it has none (not found, a
 * refused read) is final and the row simply shows no items.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSessionStore } from "@/lib/session-store";
import { getSocket } from "@/lib/runtime-bridge/state";

import { CommitGroupRow } from "./rows/commit-group-row";
import type { CommitDetail, CommitMeta } from "./types";
import { groupCommits, wsSend } from "./utils";
import { detailReplyOutcome, retryDelay } from "./retry";
import { activateOnKey } from "@/lib/utils";

/** The last list read of each conversation, and every commit's items. */
const keptCommits = new Map<string, CommitMeta[]>();
const keptDetails = new Map<string, CommitDetail>();
/** Commits the server answered it has no items for; not asked again. */
const refusedDetails = new Set<string>();

export function ContextCommitTimeline() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  // Subscribe so this component re-runs the auto-refresh effect both
  // when the active turn completes (runningTask falls from set → null)
  // and when the user reveals the Context tab. List should never go
  // stale relative to the chat; refresh is now event-driven, not the
  // "click Refresh manually" behavior the user complained about.
  const runningTask = useSessionStore((s) =>
    sessionId ? s.runningTasks[sessionId] : null,
  );
  const rightDockView = useSessionStore((s) => s.rightDock.view);
  const rightDockOpen = useSessionStore((s) => s.rightDock.open);
  const [commits, setCommits] = useState<CommitMeta[]>(() => (sessionId && keptCommits.get(sessionId)) || []);
  // Whose list has been read: until then an empty list says nothing.
  const [readSession, setReadSession] = useState<string | null>(() => sessionId && keptCommits.has(sessionId) ? sessionId : null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, CommitDetail>>(() => Object.fromEntries(keptDetails));
  const [refused, setRefused] = useState<ReadonlySet<string>>(() => new Set(refusedDetails));
  const [loading, setLoading] = useState(false);

  // Ref mirror of sessionId so the long-lived onMsg listener always
  // sees the latest value, not the one captured when the effect first
  // ran. See the listener block below.
  const sessionIdRef = useRef(sessionId);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  // Another conversation draws what was last read of it.
  const shownSession = useRef(sessionId);
  useEffect(() => {
    if (shownSession.current === sessionId) return;
    shownSession.current = sessionId;
    setCommits((sessionId && keptCommits.get(sessionId)) || []);
    setReadSession(sessionId && keptCommits.has(sessionId) ? sessionId : null);
    setExpanded(null);
  }, [sessionId]);

  const refresh = useCallback(() => {
    if (!sessionId) {
      setCommits([]);
      return;
    }
    setLoading(true);
    wsSend({ action: "list_context_commits", session_id: sessionId });
  }, [sessionId]);
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  // Listen to ws messages on the current socket. The listener reads
  // sessionIdRef so it survives sessionId churn; it moves to the new socket
  // when the connection's state changes (a reconnect swaps the socket).
  const wsStatus = useSessionStore((s) => s.wsStatus);
  const listRetry = useRef(0);
  const listFailures = useRef(0);
  useEffect(() => () => window.clearTimeout(listRetry.current), []);
  useEffect(() => { listFailures.current = 0; }, [sessionId]);
  const onMsg = useCallback((ev: MessageEvent) => {
    let msg: { type?: string; data?: Record<string, unknown> } | null = null;
    try {
      msg = JSON.parse(typeof ev.data === "string" ? ev.data : "");
    } catch {
      return;
    }
    if (!msg) return;
    if (msg.type === "context_commits_list") {
      const d = msg.data as { session_id?: string; commits?: CommitMeta[]; error?: unknown };
      // Drop late responses from a previous session.
      const session = sessionIdRef.current;
      if (!session || (d.session_id && d.session_id !== session)) return;
      setLoading(false);
      if (d.error || !Array.isArray(d.commits)) {
        // The list on screen stays; it is asked for again in a moment.
        console.warn("Context: could not read the commits", d.error);
        if (!listRetry.current) {
          const wait = retryDelay(listFailures.current++);
          listRetry.current = window.setTimeout(() => { listRetry.current = 0; refreshRef.current(); }, wait);
        }
        return;
      }
      listFailures.current = 0;
      keptCommits.set(session, d.commits);
      setCommits(d.commits);
      setReadSession(session);
    } else if (msg.type === "context_commit_detail") {
      const d = msg.data as unknown as CommitDetail;
      if (!d || !d.id) return;
      // The server's answer that a commit has no items is final.
      if (detailReplyOutcome(d) === "refused") {
        console.warn("Context: no items for a commit", d.id, d.error);
        refusedDetails.add(d.id);
        setRefused(new Set(refusedDetails));
        return;
      }
      keptDetails.set(d.id, d);
      setDetails((prev) => ({ ...prev, [d.id]: d }));
    }
  }, []);
  useEffect(() => {
    const sock = getSocket();
    if (!sock) return;
    sock.addEventListener("message", onMsg);
    return () => sock.removeEventListener("message", onMsg);
  }, [onMsg, wsStatus]);

  // An open row whose items have not come back asks again, waiting 1 s and
  // doubling up to 30 s, until the items or the server's final answer arrive.
  const expandedAnswered = expanded ? Boolean(details[expanded]) || refused.has(expanded) : true;
  useEffect(() => {
    if (!expanded || expandedAnswered) return;
    const id = expanded;
    let failures = 0;
    let timer = 0;
    const askLater = () => {
      timer = window.setTimeout(() => {
        wsSend({ action: "get_context_commit_detail", commit_id: id });
        askLater();
      }, retryDelay(failures++));
    };
    askLater();
    return () => window.clearTimeout(timer);
  }, [expanded, expandedAnswered]);

  // Auto-refresh. Fires on any of:
  //   - session change (currentSessionId)
  //   - turn finishes  (runningTask falls from set → null)
  //   - user reveals Context tab (rightDockOpen + view === "context")
  //   - the socket opens (mount can race socket-open; a reconnect)
  // Only the dock-visibility branch is short-circuited when the tab is
  // hidden — there's no point hitting the server if the user can't see
  // the result. The other branches still arm so opening the tab later
  // shows up-to-date data.
  const isContextVisible = rightDockOpen && rightDockView === "context";
  // runningTask is the dependency, but we read its presence as a bool —
  // null means "no turn running" which is what we care about; the
  // ref-equality on the same object would skip the refresh otherwise.
  const turnIdle = runningTask == null;
  const socketOpen = wsStatus === "open";
  useEffect(() => {
    if (!sessionId) return;
    if (!isContextVisible) return;
    if (!turnIdle) return;
    const sock = getSocket();
    if (socketOpen && sock && sock.readyState === WebSocket.OPEN) refresh();
  }, [refresh, sessionId, isContextVisible, turnIdle, socketOpen]);

  function toggleRow(id: string) {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (!details[id] && !refused.has(id)) {
      wsSend({ action: "get_context_commit_detail", commit_id: id });
    }
  }

  // Three-phase Refresh hint: idle ("Refresh") → loading ("…") →
  // done ("Done!" green for 2s) → idle. Timer parked in a ref so
  // dependency churn can't cancel the revert.
  const [phase, setPhase] = useState<"idle" | "loading" | "done">("idle");
  const prevLoadingRef = useRef(loading);
  const doneTimerRef = useRef<number>(0);
  useEffect(() => {
    if (prevLoadingRef.current && !loading && phase === "loading") {
      setPhase("done");
      if (doneTimerRef.current) window.clearTimeout(doneTimerRef.current);
      doneTimerRef.current = window.setTimeout(() => setPhase("idle"), 2000);
    }
    prevLoadingRef.current = loading;
  }, [loading, phase]);
  function onRefreshClick() {
    if (!sessionId) return;
    setPhase("loading");
    refresh();
  }

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        fontSize: "var(--right-panel-text-size, var(--fs-sm))",
        color: "var(--text-bright)",
      }}
    >
      <div
        className="sidebar-section-header"
        style={{ cursor: "default", marginTop: 16 }}
      >
        <span className="sidebar-section-title">
          Context{commits.length ? ` (${commits.length})` : ""}
        </span>
        <span
          className="sidebar-section-hint"
          style={{
            cursor: sessionId ? "pointer" : "not-allowed",
            opacity: phase !== "idle" ? 0.85 : undefined,
            color: phase === "done" ? "var(--commit-refresh-done)" : undefined,
          }}
          onClick={onRefreshClick}
          role="button"
          tabIndex={0}
          onKeyDown={activateOnKey(onRefreshClick)}
        >
          {phase === "loading" ? "…" : phase === "done" ? "Done!" : "Refresh"}
        </span>
      </div>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {!sessionId && (
          <div style={{ padding: 14, color: "var(--text-muted)" }}>
            No active session.
          </div>
        )}
        {sessionId && readSession === sessionId && commits.length === 0 && (
          <div style={{ padding: 14, color: "var(--text-muted)" }}>
            No commits yet.
          </div>
        )}
        {groupCommits(commits).map((g) => (
          <CommitGroupRow
            key={g.id}
            attempts={g.attempts}
            expandedId={expanded}
            details={details}
            onToggle={toggleRow}
          />
        ))}
      </div>
    </div>
  );
}
