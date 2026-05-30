"use client";

/**
 * /chats — list of past conversations.
 *
 * Shell mirrors /functions and /memory: sticky topbar with title +
 * toolbar (search, New chat), 287px nav rail on the left with quick
 * date / channel filters, content column on the right showing chats
 * grouped by recency. Sessions stream in via WebSocket
 * (list_sessions → sessions_list / history_list events).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./chats-page.module.css";
import { useTranslation, type Locale } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  ClockIcon,
  MessageCircleIcon,
} from "@/components/animated-icons";

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

const DAY = 86400;

function bucketOf(ts: number): Exclude<FilterId, "all"> {
  const now = Date.now() / 1000;
  const age = now - (ts || 0);
  if (age < DAY) return "today";
  if (age < DAY * 7) return "week";
  if (age < DAY * 30) return "month";
  return "older";
}

export function ChatsPage() {
  const { t, text, locale } = useTranslation();
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
  // visual date headers — same shape as Functions' category sections.
  const grouped = useMemo(() => {
    if (filter !== "all") return null;
    const out: Partial<Record<Exclude<FilterId, "all">, ConvSummary[]>> = {};
    for (const c of items) {
      const b = bucketOf(c.created_at ?? 0);
      (out[b] ??= []).push(c);
    }
    return out;
  }, [items, filter]);

  const navGroups: Array<{
    label: string;
    items: Array<{ id: FilterId; name: string }>;
  }> = [
    {
      label: text("Library", "会话库"),
      items: [
        { id: "all", name: text("All chats", "全部会话") },
      ],
    },
    {
      label: text("By recency", "按时间"),
      items: [
        { id: "today", name: text("Today", "今天") },
        { id: "week", name: text("Last 7 days", "最近 7 天") },
        { id: "month", name: text("Last 30 days", "最近 30 天") },
        { id: "older", name: text("Older", "更早") },
      ],
    },
  ];
  const sectionLabels: Record<Exclude<FilterId, "all">, string> = {
    today: text("Today", "今天"),
    week: text("Last 7 days", "最近 7 天"),
    month: text("Last 30 days", "最近 30 天"),
    older: text("Older", "更早"),
  };

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{t("nav.chats")}</span>
          <div className={styles.toolbar}>
            <input
              type="text"
              className={styles.search}
              placeholder={text("Search chats...", "搜索会话...")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <CustomSelect
              value={sort}
              onChange={setSort}
              options={[
                { value: "recent", label: text("Sort: Recent", "排序：最近") },
                { value: "oldest", label: text("Sort: Oldest", "排序：最早") },
                { value: "title", label: text("Sort: Title", "排序：标题") },
              ]}
            />
            <CustomSelect
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "all", label: text("All", "全部") },
                { value: "active", label: text("Active sessions", "活动会话") },
                { value: "archived", label: text("No session", "无会话") },
              ]}
            />
          </div>
        </div>

        <div className={styles.body}>
          <div className={styles.nav}>
            {navGroups.map((group) => (
              <div key={group.label}>
                <div className={styles.navGroupLabel}>{group.label}</div>
                {group.items.map((it) => (
                  <ChatsNavRow
                    key={it.id}
                    id={it.id}
                    name={it.name}
                    count={counts[it.id]}
                    active={filter === it.id}
                    onSelect={() => setFilter(it.id)}
                  />
                ))}
              </div>
            ))}
          </div>

          <div className={styles.content}>
            {items.length === 0 ? (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>
                  <MessageCircleIcon size={40} />
                </div>
                <div className={styles.emptyText}>
                  {query
                    ? text("No chats match your search", "没有匹配的会话")
                    : filter === "all"
                      ? text("No conversations yet. Start one above.", "暂无会话。可以从上方开始。")
                      : text("Nothing in this range", "这个时间范围内没有内容")}
                </div>
              </div>
            ) : grouped ? (
              <>
                {(["today", "week", "month", "older"] as const)
                  .filter((b) => grouped[b]?.length)
                  .map((b) => (
                    <div className={styles.section} key={b}>
                      <div className={styles.sectionHeader}>
                        {sectionLabels[b]} ({grouped[b]!.length})
                      </div>
                      <div>
                        {grouped[b]!.map((c) => (
                          <ChatRow
                            key={c.id}
                            conv={c}
                            locale={locale}
                            untitled={t("sidebar.untitled")}
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
                    locale={locale}
                    untitled={t("sidebar.untitled")}
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
  locale,
  untitled,
  onClick,
}: {
  conv: ConvSummary;
  locale: Locale;
  untitled: string;
  onClick: () => void;
}) {
  const title = conv.title || untitled;
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
      <div className={styles.rowTime}>{formatRelativeTime(conv.created_at ?? 0, locale)}</div>
    </div>
  );
}

/* Themed dropdown — same shape as the Functions page CustomSelect so
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

function formatRelativeTime(ts: number, locale: Locale): string {
  if (!ts) return "";
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - ts);
  if (locale === "zh") {
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
    return `${Math.floor(diff / (86400 * 30))} 个月前`;
  }
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

/** One left-rail filter row. "All chats" shows the chat-bubble glyph; the
 *  "by recency" buckets share the clock. The animated icon is driven from
 *  the whole row's hover (controlled mode), like the sidebar nav. */
function ChatsNavRow({
  id,
  name,
  count,
  active,
  onSelect,
}: {
  id: FilterId;
  name: string;
  count: number;
  active: boolean;
  onSelect: () => void;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const Icon = id === "all" ? MessageCircleIcon : ClockIcon;
  return (
    <div
      className={styles.navItem + (active ? " " + styles.active : "")}
      onClick={onSelect}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      <span className={styles.navIcon}>
        <Icon ref={iconRef} size={16} />
      </span>
      <span className={styles.navName}>{name}</span>
      <span className={styles.navCount}>{count}</span>
    </div>
  );
}
