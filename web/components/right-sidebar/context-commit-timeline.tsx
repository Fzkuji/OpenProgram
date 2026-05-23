"use client";

/**
 * Context-commit timeline — right-dock "Context" view.
 *
 * Lists this session's context commits (newest first). Clicking a row
 * expands its items so the user can see exactly what the LLM was given
 * on that turn (rendered text + per-item state).
 *
 * Talks to the backend via the existing `window.ws` socket:
 *   send  → {action:"list_context_commits", session_id}
 *           {action:"get_context_commit_detail", commit_id}
 *   recv  → {type:"context_commits_list",  data:{session_id, commits}}
 *           {type:"context_commit_detail", data:{id, items, ...}}
 *
 * Local state only — no zustand slice. We piggyback on the raw
 * MessageEvent stream alongside the legacy dispatcher.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useSessionStore } from "@/lib/session-store";
import styles from "./context-commit-timeline.module.css";

type StateName = "full" | "aged" | "cleared" | "summarized" | "summary";

interface CommitMeta {
  id: string;
  parent_id: string | null;
  created_at: number;
  head_node_id: string;
  /** Commits sharing the same turn_group_id are parallel attempts
   *  (same DAG fork point: multi-agent / modify-retry siblings) and
   *  collapse into one row with an attempt switcher. */
  turn_group_id: string;
  total_tokens: number;
  rules_version?: string;
  summary: string;
  item_count: number;
  state_counts: Partial<Record<StateName, number>>;
}

interface CommitItem {
  source_node_id: string;
  role: string;
  state: StateName;
  rendered: string;
  tokens: number;
  reason: string;
  locked: boolean;
  is_anchor: boolean;
  merged_into: string | null;
}

interface CommitDetail {
  id: string;
  session_id?: string;
  parent_id?: string | null;
  created_at?: number;
  head_node_id?: string;
  total_tokens?: number;
  summary?: string;
  items: CommitItem[];
  error?: string | null;
}

// "full" is the unchanged / default state — render it muted so the
// non-trivial states (aged, cleared, summarized) actually stand out.
const STATE_COLOR: Record<StateName, { bg: string; fg: string }> = {
  full:       { bg: "rgba(255, 255, 255, 0.06)", fg: "var(--text-muted)" },
  aged:       { bg: "rgba(227, 179, 65, 0.14)",  fg: "#e3b341" },
  cleared:    { bg: "rgba(248, 81, 73, 0.14)",   fg: "#f85149" },
  summarized: { bg: "rgba(110, 118, 129, 0.20)", fg: "var(--text-muted)" },
  summary:    { bg: "rgba(86, 211, 100, 0.14)",  fg: "#56d364" },
};

function fmtRelTime(ts: number): string {
  const now = Date.now() / 1000;
  const d = Math.max(0, now - ts);
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  return `${Math.round(d / 86400)}d ago`;
}

function send(obj: unknown): void {
  const w = window as unknown as { ws?: WebSocket | null };
  const sock = w.ws;
  if (sock && sock.readyState === WebSocket.OPEN) {
    sock.send(JSON.stringify(obj));
  }
}

export function ContextCommitTimeline() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [commits, setCommits] = useState<CommitMeta[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, CommitDetail>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Ref mirror of sessionId so the long-lived onMsg listener always sees
  // the latest value, not the one captured when the effect first ran.
  const sessionIdRef = useRef(sessionId);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);

  const refresh = useCallback(() => {
    if (!sessionId) {
      setCommits([]);
      return;
    }
    setLoading(true);
    send({ action: "list_context_commits", session_id: sessionId });
  }, [sessionId]);

  // Listen to ws messages of types we care about. Registered once and
  // kept alive — listener reads sessionIdRef so it survives sessionId
  // churn and the ws object swap on reconnect (both go through window.ws,
  // so re-checking each call works).
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
        // Drop late responses from a previous session, but accept anything
        // matching the current one — including the case where this listener
        // outlived a stale closure capture of sessionId.
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
    // Poll for window.ws to appear (it may not exist at mount time), then
    // attach. Cleared when the ws object is replaced (reconnect path
    // installs a new socket; we re-attach on the next tick).
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
  // (mount can race the socket open).
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
      send({ action: "get_context_commit_detail", commit_id: id });
    }
  }

  // Three-phase Refresh feedback: idle → loading ("…") → done ("Done!"
  // briefly in green) → idle. Without this the click is invisible on
  // fast round-trips. Reset to idle 900ms after the response lands.
  const [phase, setPhase] = useState<"idle" | "loading" | "done">("idle");
  const prevLoadingRef = useRef(loading);
  // Timer ref instead of useEffect cleanup: useEffect's cleanup fires on
  // every dependency change, including the loading→done transition we
  // just set, which would clear the 2s revert timer before it ever
  // fires and leave the hint stuck on "Done!" forever.
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

/** Bucket commits sharing a turn_group_id into one row. Preserves the
 *  order commits arrive in (newest-first per backend), with attempts
 *  inside a group sorted oldest→newest so the switcher numbers ascend
 *  chronologically. */
function groupCommits(commits: CommitMeta[]): Array<{ id: string; attempts: CommitMeta[] }> {
  const out: Array<{ id: string; attempts: CommitMeta[] }> = [];
  const idx = new Map<string, number>();
  for (const c of commits) {
    let i = idx.get(c.turn_group_id);
    if (i === undefined) {
      i = out.length;
      idx.set(c.turn_group_id, i);
      out.push({ id: c.turn_group_id, attempts: [] });
    }
    out[i].attempts.push(c);
  }
  for (const g of out) g.attempts.sort((a, b) => a.created_at - b.created_at);
  return out;
}

/** Multi-attempt group row. When the group has one commit it just
 *  renders CommitRow. When it has multiple (parallel branches at the
 *  same fork point) the row header gets a `< n/N >` switcher and the
 *  selected attempt drives the expanded popout below. */
function CommitGroupRow(props: {
  attempts: CommitMeta[];
  expandedId: string | null;
  details: Record<string, CommitDetail>;
  onToggle: (id: string) => void;
}) {
  const { attempts, expandedId, details, onToggle } = props;
  const [pick, setPick] = useState(attempts.length - 1); // default to newest
  // Clamp if attempts list shrinks between refreshes.
  const idx = Math.min(pick, attempts.length - 1);
  const meta = attempts[idx];
  const switcher = attempts.length > 1 ? (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        marginLeft: 8,
        fontSize: 10,
        color: "var(--text-muted)",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={() => setPick((p) => Math.max(0, p - 1))}
        disabled={idx === 0}
        style={{
          background: "transparent",
          border: "none",
          color: "inherit",
          cursor: idx === 0 ? "default" : "pointer",
          padding: "0 4px",
          opacity: idx === 0 ? 0.4 : 1,
        }}
      >‹</button>
      <span>{idx + 1}/{attempts.length}</span>
      <button
        type="button"
        onClick={() => setPick((p) => Math.min(attempts.length - 1, p + 1))}
        disabled={idx === attempts.length - 1}
        style={{
          background: "transparent",
          border: "none",
          color: "inherit",
          cursor: idx === attempts.length - 1 ? "default" : "pointer",
          padding: "0 4px",
          opacity: idx === attempts.length - 1 ? 0.4 : 1,
        }}
      >›</button>
    </span>
  ) : null;
  return (
    <CommitRow
      meta={meta}
      switcher={switcher}
      open={expandedId === meta.id}
      detail={details[meta.id]}
      onToggle={() => onToggle(meta.id)}
    />
  );
}

function CommitRow(props: {
  meta: CommitMeta;
  switcher?: React.ReactNode;
  open: boolean;
  detail?: CommitDetail;
  onToggle: () => void;
}) {
  const { meta, switcher, open, detail, onToggle } = props;
  const counts = meta.state_counts || {};
  // Commit row keeps its rectangular full-width shape (closed *and*
  // open). When opened the row tints to bg-hover so the user sees
  // which commit's detail is currently below. The popout is a
  // separate, narrower floating card — never merges with the row.
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={onToggle}
        role="button"
        style={{
          padding: "8px 10px",
          cursor: "pointer",
          display: "flex",
          flexDirection: "column",
          gap: 3,
          background: open ? "var(--bg-hover, rgba(255,255,255,0.04))" : "transparent",
          transition: "background 0.15s",
        }}
      >
        <CommitMetaContent meta={meta} counts={counts} switcher={switcher} />
      </div>
      {/* Wrapper is always mounted so the open/close transition runs
          both directions; grid-template-rows 0fr↔1fr animates the
          height, which smoothly pushes everything below. */}
      <div className={styles.popoutWrap + (open ? " " + styles.open : "")}>
        <div className={styles.popoutClip}>
          <div className={styles.popout}>
            {!detail && (
              <div className={styles.empty}>Loading…</div>
            )}
            {detail?.error && (
              <div className={styles.empty} style={{ color: "var(--red, #f85149)" }}>
                {detail.error}
              </div>
            )}
            {detail?.items?.map((it, idx) => (
              <ItemRow key={`${it.source_node_id}-${idx}`} item={it} />
            ))}
            {detail && detail.items && detail.items.length === 0 && !detail.error && (
              <div className={styles.empty}>(empty)</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CommitMetaContent(props: {
  meta: CommitMeta;
  counts: Partial<Record<StateName, number>>;
  switcher?: React.ReactNode;
}) {
  const { meta, counts, switcher } = props;
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
        <span style={{ color: "var(--text-bright)" }}>
          {meta.id.slice(0, 12)}
          {switcher}
        </span>
        <span style={{ color: "var(--text-muted)" }}>{fmtRelTime(meta.created_at)}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-muted)" }}>
        <span>{meta.total_tokens.toLocaleString()} tok · {meta.item_count} items</span>
        <span>{meta.rules_version || ""}</span>
      </div>
      {meta.summary && (
        <div style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {meta.summary}
        </div>
      )}
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 2 }}>
        {(Object.keys(counts) as StateName[])
          .filter((k) => (counts[k] || 0) > 0)
          .map((k) => (
            <StateBadge key={k} state={k} count={counts[k] || 0} />
          ))}
      </div>
    </>
  );
}

function StateBadge(props: { state: StateName; count?: number }) {
  const { state, count } = props;
  const c = STATE_COLOR[state];
  return (
    <span
      style={{
        background: c.bg,
        color: c.fg,
        padding: "1px 6px",
        borderRadius: 3,
        fontSize: 10,
        whiteSpace: "nowrap",
      }}
    >
      {state}{count !== undefined ? ` ${count}` : ""}
    </span>
  );
}

function ItemRow(props: { item: CommitItem }) {
  const it = props.item;
  const [open, setOpen] = useState(false);
  const oneLine = (it.rendered || "").replace(/\s+/g, " ").trim();
  return (
    <div
      className={styles.item + (open ? " " + styles.itemOpen : "")}
      onClick={() => setOpen((v) => !v)}
      role="button"
    >
      <div className={styles.itemHead}>
        <span className={styles.itemLabel}>{it.role}</span>
        <span className={styles.itemPreview}>
          {oneLine || "(empty)"}
        </span>
        <span className={styles.itemTokens}>{it.tokens}t</span>
      </div>
      {open && (
        <div className={styles.itemBody}>
          <div className={styles.itemChips}>
            <StateBadge state={it.state} />
            {it.is_anchor && (
              <span style={{ color: "var(--orange, #e3b341)", fontSize: 10 }}>anchor</span>
            )}
            {it.locked && (
              <span style={{ color: "var(--text-muted)", fontSize: 10 }}>locked</span>
            )}
            {it.reason && (
              <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                reason: {it.reason}
              </span>
            )}
          </div>
          <div className={styles.itemText}>
            {it.rendered || <span style={{ color: "var(--text-muted)" }}>(empty)</span>}
          </div>
        </div>
      )}
    </div>
  );
}
