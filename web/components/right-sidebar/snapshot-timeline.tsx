"use client";

/**
 * Snapshot timeline — right-dock "Snapshots" view.
 *
 * Lists this session's context_snapshots (newest first). Clicking a row
 * expands its items so the user can see exactly what the LLM was given
 * on that turn (rendered text + per-item state).
 *
 * Talks to the backend via the existing `window.ws` socket:
 *   send  → {action:"list_snapshots", session_id}
 *           {action:"get_snapshot_detail", snap_id}
 *   recv  → {type:"snapshots_list",  data:{session_id, snapshots}}
 *           {type:"snapshot_detail", data:{id, items, ...}}
 *
 * Local state only — no zustand slice. We piggyback on the raw
 * MessageEvent stream alongside the legacy dispatcher.
 */

import { useEffect, useState, useCallback } from "react";
import { useSessionStore } from "@/lib/session-store";

type StateName = "full" | "aged" | "cleared" | "summarized" | "summary";

interface SnapshotMeta {
  id: string;
  parent_id: string | null;
  created_at: number;
  head_node_id: string;
  total_tokens: number;
  rules_version?: string;
  summary: string;
  item_count: number;
  state_counts: Partial<Record<StateName, number>>;
}

interface SnapshotItem {
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

interface SnapshotDetail {
  id: string;
  session_id?: string;
  parent_id?: string | null;
  created_at?: number;
  head_node_id?: string;
  total_tokens?: number;
  summary?: string;
  items: SnapshotItem[];
  error?: string | null;
}

const STATE_COLOR: Record<StateName, { bg: string; fg: string }> = {
  full:       { bg: "rgba(88, 166, 255, 0.18)", fg: "var(--accent, #58a6ff)" },
  aged:       { bg: "rgba(227, 179, 65, 0.18)", fg: "var(--orange, #e3b341)" },
  cleared:    { bg: "rgba(248, 81, 73, 0.16)",  fg: "var(--red, #f85149)" },
  summarized: { bg: "rgba(110, 118, 129, 0.18)", fg: "var(--gray, #6e7681)" },
  summary:    { bg: "rgba(86, 211, 100, 0.18)",  fg: "var(--green, #56d364)" },
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

export function SnapshotTimeline() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [snapshots, setSnapshots] = useState<SnapshotMeta[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, SnapshotDetail>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(() => {
    if (!sessionId) {
      setSnapshots([]);
      return;
    }
    setLoading(true);
    send({ action: "list_snapshots", session_id: sessionId });
  }, [sessionId]);

  // Listen to ws messages of types we care about.
  useEffect(() => {
    const w = window as unknown as { ws?: WebSocket | null };
    const sock = w.ws;
    if (!sock) return;
    function onMsg(ev: MessageEvent) {
      let msg: { type?: string; data?: Record<string, unknown> } | null = null;
      try {
        msg = JSON.parse(typeof ev.data === "string" ? ev.data : "");
      } catch {
        return;
      }
      if (!msg) return;
      if (msg.type === "snapshots_list") {
        const d = msg.data as { session_id?: string; snapshots?: SnapshotMeta[]; error?: string | null };
        if (d.session_id !== sessionId) return;
        setLoading(false);
        setError(d.error || null);
        setSnapshots(Array.isArray(d.snapshots) ? d.snapshots : []);
      } else if (msg.type === "snapshot_detail") {
        const d = msg.data as unknown as SnapshotDetail;
        if (!d || !d.id) return;
        setDetails((prev) => ({ ...prev, [d.id]: d }));
      }
    }
    sock.addEventListener("message", onMsg);
    return () => sock.removeEventListener("message", onMsg);
  }, [sessionId]);

  // Auto-refresh on session change.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Refresh once when the socket finishes (re)connecting if it wasn't ready before.
  useEffect(() => {
    const w = window as unknown as { ws?: WebSocket | null };
    const sock = w.ws;
    if (!sock) return;
    function onOpen() { refresh(); }
    sock.addEventListener("open", onOpen);
    return () => sock.removeEventListener("open", onOpen);
  }, [refresh]);

  function toggleRow(id: string) {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (!details[id]) {
      send({ action: "get_snapshot_detail", snap_id: id });
    }
  }

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        fontSize: 12,
        fontFamily: "var(--font-mono, ui-monospace, SFMono-Regular, monospace)",
        color: "var(--text-bright)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 10px",
          borderBottom: "1px solid var(--border)",
          color: "var(--text-muted)",
        }}
      >
        <span>Snapshots {snapshots.length ? `(${snapshots.length})` : ""}</span>
        <button
          type="button"
          onClick={refresh}
          disabled={!sessionId || loading}
          style={{
            background: "transparent",
            border: "1px solid var(--border)",
            borderRadius: 4,
            color: "var(--text-muted)",
            padding: "2px 8px",
            cursor: sessionId ? "pointer" : "not-allowed",
            fontSize: 11,
          }}
        >
          {loading ? "…" : "Refresh"}
        </button>
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
        {sessionId && snapshots.length === 0 && !loading && !error && (
          <div style={{ padding: 14, color: "var(--text-muted)" }}>
            No snapshots yet.
          </div>
        )}
        {snapshots.map((s) => (
          <SnapshotRow
            key={s.id}
            meta={s}
            open={expanded === s.id}
            detail={details[s.id]}
            onToggle={() => toggleRow(s.id)}
          />
        ))}
      </div>
    </div>
  );
}

function SnapshotRow(props: {
  meta: SnapshotMeta;
  open: boolean;
  detail?: SnapshotDetail;
  onToggle: () => void;
}) {
  const { meta, open, detail, onToggle } = props;
  const counts = meta.state_counts || {};
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
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
          <span style={{ color: "var(--text-bright)" }}>{meta.id.slice(0, 12)}</span>
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
      </div>
      {open && (
        <div style={{ padding: "4px 10px 10px 10px", background: "var(--bg-tertiary, rgba(0,0,0,0.15))" }}>
          {!detail && (
            <div style={{ color: "var(--text-muted)", padding: 8 }}>Loading…</div>
          )}
          {detail?.error && (
            <div style={{ color: "var(--red, #f85149)", padding: 8 }}>{detail.error}</div>
          )}
          {detail?.items?.map((it, idx) => (
            <ItemRow key={`${it.source_node_id}-${idx}`} item={it} />
          ))}
          {detail && detail.items && detail.items.length === 0 && !detail.error && (
            <div style={{ color: "var(--text-muted)", padding: 8 }}>(empty)</div>
          )}
        </div>
      )}
    </div>
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

function ItemRow(props: { item: SnapshotItem }) {
  const it = props.item;
  const preview = (it.rendered || "").slice(0, 120).replace(/\s+/g, " ");
  return (
    <div
      style={{
        padding: "6px 0",
        borderTop: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        gap: 3,
      }}
    >
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span
          style={{
            color: "var(--text-muted)",
            border: "1px solid var(--border)",
            borderRadius: 3,
            padding: "0 4px",
            fontSize: 10,
          }}
        >
          {it.role}
        </span>
        <StateBadge state={it.state} />
        {it.is_anchor && (
          <span style={{ color: "var(--orange, #e3b341)", fontSize: 10 }}>anchor</span>
        )}
        {it.locked && (
          <span style={{ color: "var(--text-muted)", fontSize: 10 }}>locked</span>
        )}
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 10 }}>
          {it.tokens} tok
        </span>
      </div>
      <div style={{ color: "var(--text-bright)", lineHeight: 1.4, wordBreak: "break-word" }}>
        {preview || <span style={{ color: "var(--text-muted)" }}>(empty)</span>}
        {it.rendered && it.rendered.length > 120 && (
          <span style={{ color: "var(--text-muted)" }}>…</span>
        )}
      </div>
      {it.reason && (
        <div style={{ color: "var(--text-muted)", fontSize: 10 }}>reason: {it.reason}</div>
      )}
    </div>
  );
}
