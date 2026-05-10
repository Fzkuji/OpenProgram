"use client";

/**
 * /chats — list of past conversations.
 *
 * Native React port of web/public/html/chats.html. Owns its own
 * WebSocket subscription (open → list_sessions → render rows).
 * Styles are co-located in chats-page.module.css; design tokens
 * (--text-bright, --border, --text-muted, ...) come from globals.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./chats-page.module.css";

interface ConvSummary {
  id: string;
  title: string;
  created_at?: number;
  has_session?: boolean;
}

export function ChatsPage() {
  const router = useRouter();
  const [convs, setConvs] = useState<Record<string, ConvSummary>>({});
  const [query, setQuery] = useState("");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let reconnect: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      wsRef.current = ws;
      ws.onopen = () => {
        ws.send(JSON.stringify({ action: "list_sessions" }));
      };
      ws.onclose = () => {
        if (!cancelled) reconnect = setTimeout(connect, 2000);
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
      ws.onmessage = (e) => {
        let msg: { type?: string; data?: ConvSummary[] };
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        if (
          msg.type === "sessions_list" ||
          msg.type === "history_list"
        ) {
          const list = msg.data ?? [];
          setConvs((prev) => {
            const next = { ...prev };
            for (const c of list) {
              const cur = next[c.id] ?? { id: c.id, title: c.title };
              next[c.id] = {
                ...cur,
                title: c.title,
                created_at: c.created_at,
                has_session: c.has_session,
              };
            }
            return next;
          });
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      if (reconnect) clearTimeout(reconnect);
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    let arr = Object.values(convs).sort(
      (a, b) => (b.created_at ?? 0) - (a.created_at ?? 0),
    );
    if (q) {
      arr = arr.filter((c) =>
        (c.title || "").toLowerCase().includes(q),
      );
    }
    return arr;
  }, [convs, query]);

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.header}>
          <h1 className={styles.title}>Chats</h1>
          <button
            className={styles.newBtn}
            onClick={() => router.push("/chat")}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
            >
              <line x1={10} y1={4} x2={10} y2={16} />
              <line x1={4} y1={10} x2={16} y2={10} />
            </svg>
            New chat
          </button>
        </div>

        <div className={styles.searchWrap}>
          <svg
            className={styles.searchIcon}
            width="18"
            height="18"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.75}
            strokeLinecap="round"
          >
            <circle cx={9} cy={9} r={5.5} />
            <line x1={13} y1={13} x2={17} y2={17} />
          </svg>
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Search your chats..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <div className={styles.meta}>Your chats with OpenProgram</div>

        <div className={styles.list}>
          {items.length === 0 ? (
            <div className={styles.empty}>
              {query ? "No matches" : "No conversations yet"}
            </div>
          ) : (
            items.map((c) => (
              <div
                key={c.id}
                className={styles.row}
                onClick={() => router.push(`/s/${c.id}`)}
              >
                <div className={styles.rowTitle}>{c.title || "Untitled"}</div>
                <div className={styles.rowMeta}>
                  Last message {formatRelativeTime(c.created_at ?? 0)}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function formatRelativeTime(ts: number): string {
  if (!ts) return "";
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - ts);
  if (diff < 60) return "just now";
  if (diff < 3600) {
    const m = Math.floor(diff / 60);
    return m === 1 ? "1 minute ago" : `${m} minutes ago`;
  }
  if (diff < 86400) {
    const h = Math.floor(diff / 3600);
    return h === 1 ? "1 hour ago" : `${h} hours ago`;
  }
  if (diff < 86400 * 30) {
    const d = Math.floor(diff / 86400);
    return d === 1 ? "1 day ago" : `${d} days ago`;
  }
  const mo = Math.floor(diff / (86400 * 30));
  return mo === 1 ? "1 month ago" : `${mo} months ago`;
}
