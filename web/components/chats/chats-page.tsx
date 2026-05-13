"use client";

/**
 * /chats — list of past conversations.
 *
 * Shell mirrors /programs and /memory: sticky topbar with title +
 * toolbar (search, New chat), 287px nav rail on the left with quick
 * date / channel filters, content column on the right showing chats
 * grouped by recency. Sessions stream in via WebSocket
 * (list_sessions → sessions_list / history_list events).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./chats-page.module.css";

type SortKey = "recent" | "oldest" | "title";
type StatusFilter = "all" | "active" | "archived";

interface ConvSummary {
  id: string;
  title: string;
  created_at?: number;
  has_session?: boolean;
}

type FilterId =
  | "all"
  | "today"
  | "week"
  | "month"
  | "older";

const NAV_GROUPS: Array<{
  label: string;
  items: Array<{ id: FilterId; name: string; icon: string }>;
}> = [
  {
    label: "Library",
    items: [
      { id: "all", name: "All chats", icon: "💬" },
    ],
  },
  {
    label: "By recency",
    items: [
      { id: "today", name: "Today", icon: "·" },
      { id: "week", name: "Last 7 days", icon: "·" },
      { id: "month", name: "Last 30 days", icon: "·" },
      { id: "older", name: "Older", icon: "·" },
    ],
  },
];

const DAY = 86400;

function bucketOf(ts: number): Exclude<FilterId, "all"> {
  const now = Date.now() / 1000;
  const age = now - (ts || 0);
  if (age < DAY) return "today";
  if (age < DAY * 7) return "week";
  if (age < DAY * 30) return "month";
  return "older";
}

const SECTION_LABELS: Record<Exclude<FilterId, "all">, string> = {
  today: "Today",
  week: "Last 7 days",
  month: "Last 30 days",
  older: "Older",
};

export function ChatsPage() {
  const router = useRouter();
  const [convs, setConvs] = useState<Record<string, ConvSummary>>({});
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterId>("all");
  const [sort, setSort] = useState<SortKey>("recent");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
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
        if (msg.type === "sessions_list" || msg.type === "history_list") {
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

  // Per-bucket counts for the nav badges (always computed against the
  // search-filtered list so the counts agree with what's visible).
  const searched = useMemo(() => {
    const q = query.trim().toLowerCase();
    let arr = Object.values(convs);
    if (q) {
      arr = arr.filter((c) => (c.title || "").toLowerCase().includes(q));
    }
    if (statusFilter === "active") {
      arr = arr.filter((c) => c.has_session === true);
    } else if (statusFilter === "archived") {
      arr = arr.filter((c) => c.has_session === false);
    }
    arr.sort((a, b) => {
      if (sort === "recent") return (b.created_at ?? 0) - (a.created_at ?? 0);
      if (sort === "oldest") return (a.created_at ?? 0) - (b.created_at ?? 0);
      // title
      return (a.title || "").localeCompare(b.title || "");
    });
    return arr;
  }, [convs, query, statusFilter, sort]);

  const counts = useMemo(() => {
    const c = { all: searched.length, today: 0, week: 0, month: 0, older: 0 } as Record<FilterId, number>;
    for (const x of searched) c[bucketOf(x.created_at ?? 0)]++;
    return c;
  }, [searched]);

  // Apply the active filter on top of the searched list.
  const items = useMemo(() => {
    if (filter === "all") return searched;
    return searched.filter((c) => bucketOf(c.created_at ?? 0) === filter);
  }, [searched, filter]);

  // Group items by recency bucket when showing "All" so the user gets
  // visual date headers — same shape as Programs' category sections.
  const grouped = useMemo(() => {
    if (filter !== "all") return null;
    const out: Partial<Record<Exclude<FilterId, "all">, ConvSummary[]>> = {};
    for (const c of items) {
      const b = bucketOf(c.created_at ?? 0);
      (out[b] ??= []).push(c);
    }
    return out;
  }, [items, filter]);

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>Chats</span>
          <div className={styles.toolbar}>
            <input
              type="text"
              className={styles.search}
              placeholder="Search chats..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <CustomSelect
              value={sort}
              onChange={setSort}
              options={[
                { value: "recent", label: "Sort: Recent" },
                { value: "oldest", label: "Sort: Oldest" },
                { value: "title", label: "Sort: Title" },
              ]}
            />
            <CustomSelect
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "all", label: "All" },
                { value: "active", label: "Active sessions" },
                { value: "archived", label: "No session" },
              ]}
            />
          </div>
        </div>

        <div className={styles.body}>
          <div className={styles.nav}>
            {NAV_GROUPS.map((group) => (
              <div key={group.label}>
                <div className={styles.navGroupLabel}>{group.label}</div>
                {group.items.map((it) => (
                  <div
                    key={it.id}
                    className={
                      styles.navItem +
                      (filter === it.id ? " " + styles.active : "")
                    }
                    onClick={() => setFilter(it.id)}
                  >
                    <span className={styles.navIcon}>{it.icon}</span>
                    <span className={styles.navName}>{it.name}</span>
                    <span className={styles.navCount}>{counts[it.id]}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>

          <div className={styles.content}>
            {items.length === 0 ? (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>💬</div>
                <div className={styles.emptyText}>
                  {query
                    ? "No chats match your search"
                    : filter === "all"
                      ? "No conversations yet — start one above"
                      : "Nothing in this range"}
                </div>
              </div>
            ) : grouped ? (
              <>
                {(["today", "week", "month", "older"] as const)
                  .filter((b) => grouped[b]?.length)
                  .map((b) => (
                    <div className={styles.section} key={b}>
                      <div className={styles.sectionHeader}>
                        {SECTION_LABELS[b]} ({grouped[b]!.length})
                      </div>
                      <div>
                        {grouped[b]!.map((c) => (
                          <ChatRow
                            key={c.id}
                            conv={c}
                            onClick={() => router.push(`/s/${c.id}`)}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
              </>
            ) : (
              <div>
                {items.map((c) => (
                  <ChatRow
                    key={c.id}
                    conv={c}
                    onClick={() => router.push(`/s/${c.id}`)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ChatRow({
  conv,
  onClick,
}: {
  conv: ConvSummary;
  onClick: () => void;
}) {
  const title = conv.title || "Untitled";
  const initial = title.replace(/^\s+/, "").slice(0, 1).toUpperCase() || "?";
  return (
    <div className={styles.row} onClick={onClick}>
      <div className={styles.rowAvatar}>{initial}</div>
      <div className={styles.rowBody}>
        <div className={styles.rowTitle}>{title}</div>
        <div className={styles.rowMeta}>
          <span title={String(conv.id)}>{conv.id.slice(0, 12)}</span>
        </div>
      </div>
      <div className={styles.rowTime}>{formatRelativeTime(conv.created_at ?? 0)}</div>
    </div>
  );
}

/* Themed dropdown — same shape as the Programs page CustomSelect so
   chats / programs / settings selects look identical. */
function CustomSelect<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const current = options.find((o) => o.value === value);

  return (
    <div ref={ref} className={styles.selectWrap}>
      <button
        type="button"
        className={styles.select}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{current?.label}</span>
        <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden>
          <path
            d="M2 4l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.5"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        <div className={styles.selectMenu} role="listbox">
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              className={
                styles.selectOption +
                (o.value === value ? " " + styles.selectOptionActive : "")
              }
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
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
    return m === 1 ? "1m ago" : `${m}m ago`;
  }
  if (diff < 86400) {
    const h = Math.floor(diff / 3600);
    return h === 1 ? "1h ago" : `${h}h ago`;
  }
  if (diff < 86400 * 30) {
    const d = Math.floor(diff / 86400);
    return d === 1 ? "1d ago" : `${d}d ago`;
  }
  const mo = Math.floor(diff / (86400 * 30));
  return mo === 1 ? "1mo ago" : `${mo}mo ago`;
}
