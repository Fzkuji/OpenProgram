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
 * Talks to the backend via the existing `window.ws` socket:
 *   send  → {action:"list_context_commits", session_id}
 *           {action:"get_context_commit_detail", commit_id}
 *   recv  → {type:"context_commits_list",  data:{session_id, commits}}
 *           {type:"context_commit_detail", data:{id, items, ...}}
 *
 * Local state only — no zustand slice. We piggyback on the raw
 * MessageEvent stream alongside the existing window-bridge dispatcher.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSessionStore } from "@/lib/session-store";

import { CommitGroupRow } from "./commit-group-row";
import type { CommitDetail, CommitMeta } from "./types";
import { groupCommits, wsSend } from "./utils";

export function ContextCommitTimeline() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [commits, setCommits] = useState<CommitMeta[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, CommitDetail>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Ref mirror of sessionId so the long-lived onMsg listener always
  // sees the latest value, not the one captured when the effect first
  // ran. See the listener block below.
  const sessionIdRef = useRef(sessionId);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);

  const refresh = useCallback(() => {
    if (!sessionId) {
      setCommits([]);
      return;
    }
    setLoading(true);
    wsSend({ action: "list_context_commits", session_id: sessionId });
  }, [sessionId]);

  // Listen to ws messages, registered once and kept alive. The
  // listener reads sessionIdRef so it survives sessionId churn and
  // the ws-object swap on reconnect (we poll for window.ws and
  // re-attach when it changes).
  useEffect(() => {
    function onMsg(ev: MessageEvent) {
      let msg: { type?: string; data?: Record<string, unknown> } | null = null;
      try {
        msg = JSON.parse(typeof ev.data === "string" ? ev.data : "");
      } catch {
        return;
      }
      if (!msg) return;
      if (msg.type === "context_commits_list") {
        const d = msg.data as { session_id?: string; commits?: CommitMeta[]; error?: string | null };
        // Drop late responses from a previous session.
        if (d.session_id && d.session_id !== sessionIdRef.current) return;
        setLoading(false);
        setError(d.error || null);
        setCommits(Array.isArray(d.commits) ? d.commits : []);
      } else if (msg.type === "context_commit_detail") {
        const d = msg.data as unknown as CommitDetail;
        if (!d || !d.id) return;
        setDetails((prev) => ({ ...prev, [d.id]: d }));
      }
    }
    let attached: WebSocket | null = null;
    const interval = window.setInterval(() => {
      const w = window as unknown as { ws?: WebSocket | null };
      const sock = w.ws || null;
      if (sock === attached) return;
      if (attached) attached.removeEventListener("message", onMsg);
      attached = sock;
      if (sock) sock.addEventListener("message", onMsg);
    }, 300);
    return () => {
      window.clearInterval(interval);
      if (attached) attached.removeEventListener("message", onMsg);
    };
  }, []);

  // Auto-refresh on session change. Retry once if ws isn't ready yet
  // (mount can race the socket-open).
  useEffect(() => {
    if (!sessionId) return;
    const w = window as unknown as { ws?: WebSocket | null };
    if (w.ws && w.ws.readyState === WebSocket.OPEN) {
      refresh();
      return;
    }
    const t = window.setInterval(() => {
      const ww = window as unknown as { ws?: WebSocket | null };
      if (ww.ws && ww.ws.readyState === WebSocket.OPEN) {
        window.clearInterval(t);
        refresh();
      }
    }, 400);
    return () => window.clearInterval(t);
  }, [refresh, sessionId]);

  function toggleRow(id: string) {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (!details[id]) {
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
        fontSize: 13,
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
            color: phase === "done" ? "#56d364" : undefined,
          }}
          onClick={onRefreshClick}
          role="button"
        >
          {phase === "loading" ? "…" : phase === "done" ? "Done!" : "Refresh"}
        </span>
      </div>
      {error && (
        <div style={{ padding: 10, color: "var(--red, #f85149)" }}>
          {error}
        </div>
      )}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {!sessionId && (
          <div style={{ padding: 14, color: "var(--text-muted)" }}>
            No active session.
          </div>
        )}
        {sessionId && commits.length === 0 && !loading && !error && (
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
