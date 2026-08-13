"use client";

/**
 * /chats — list of past conversations.
 *
 * Shell mirrors /programs and /memory: sticky topbar with title +
 * toolbar (search, New chat), 287px nav rail on the left with quick
 * date / channel filters, content column on the right showing chats
 * grouped by recency.
 *
 * Data comes from the SAME source as the sidebar Recents list: the
 * session store, which the runtime-bridge keeps authoritative from the
 * `sessions_list` WS event. This page used to open a second WebSocket
 * and maintain its own summary map, which drifted from the sidebar
 * (dropped `archived` / `updated_at`, so the status filter was dead and
 * recency was computed off creation time).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import styles from "./chats-page.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { useTranslation, type Locale } from "@/lib/i18n";
import { formatRelativeTime } from "@/lib/format-utils/format";
import { pushPath } from "@/lib/shallow-nav";
import { useSessionStore, type ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  type BucketKey,
  activityTs,
  bucketIsOlder,
  bucketKey,
  bucketLabel,
  bucketSortKey,
  labelFor,
} from "@/components/sidebar/sessions-list/helpers";
import {
  type AnimatedNavIconHandle,
  ClockIcon,
  MessageCircleIcon,
} from "@/components/animated-icons";

type SortKey = "recent" | "oldest" | "title";
type StatusFilter = "all" | "active" | "archived";

type FilterId = "all" | "today" | "past7" | "older";

export function ChatsPage() {
  const { t, text, locale } = useTranslation();
  // Same store the sidebar Recents list reads — one pipeline, no second
  // WebSocket. Adds / renames / deletes / archive flags land here the
  // moment the runtime-bridge processes `sessions_list`.
  const conversations = useSessionStore((s) => s.conversations);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterId>("all");
  const [sort, setSort] = useState<SortKey>("recent");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  // Bucketing is relative to "now"; pin it per render pass so every row
  // in one pass is bucketed against the same instant, and re-pin it on a
  // timer so a page left open overnight doesn't keep yesterday's rows
  // under "Today".
  const [nowTs, setNowTs] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const id = setInterval(() => setNowTs(Math.floor(Date.now() / 1000)), 60_000);
    return () => clearInterval(id);
  }, []);

  const untitled = t("sidebar.untitled");

  // Per-bucket counts for the nav badges (always computed against the
  // search-filtered list so the counts agree with what's visible).
  const searched = useMemo(() => {
    const q = query.trim().toLowerCase();
    let arr = Object.values(conversations) as ConvSummary[];
    if (q) {
      // Match the label the row actually renders (channel brand +
      // preview fallback), not just the raw title — otherwise searching
      // for text you can see on screen returns nothing.
      arr = arr.filter((c) => labelFor(c, untitled).toLowerCase().includes(q));
    }
    if (statusFilter === "active") {
      arr = arr.filter((c) => !c.archived);
    } else if (statusFilter === "archived") {
      arr = arr.filter((c) => !!c.archived);
    }
    arr = [...arr].sort((a, b) => {
      // "recent" / "oldest" follow last activity, like the sidebar's
      // recency sort — a conversation you replied in today is recent
      // even if it was created months ago.
      if (sort === "recent") return activityTs(b) - activityTs(a);
      if (sort === "oldest") return activityTs(a) - activityTs(b);
      return labelFor(a, untitled).localeCompare(labelFor(b, untitled));
    });
    return arr;
  }, [conversations, query, statusFilter, sort, untitled]);

  const counts = useMemo(() => {
    const c: Record<FilterId, number> = { all: searched.length, today: 0, past7: 0, older: 0 };
    for (const x of searched) {
      const k = bucketKey(activityTs(x), nowTs);
      c[bucketIsOlder(k) ? "older" : (k as FilterId)]++;
    }
    return c;
  }, [searched, nowTs]);

  // Apply the active filter on top of the searched list.
  const items = useMemo(() => {
    if (filter === "all") return searched;
    return searched.filter((c) => {
      const k = bucketKey(activityTs(c), nowTs);
      return filter === "older" ? bucketIsOlder(k) : k === filter;
    });
  }, [searched, filter, nowTs]);

  // Group items by recency bucket when showing "All" so the user gets
  // visual date headers — same shape as Functions' category sections.
  const grouped = useMemo(() => {
    if (filter !== "all") return null;
    const out = new Map<BucketKey, ConvSummary[]>();
    for (const c of items) {
      const b = bucketKey(activityTs(c), nowTs);
      if (!out.has(b)) out.set(b, []);
      out.get(b)!.push(c);
    }
    return Array.from(out.entries()).sort((a, b) =>
      bucketSortKey(a[0]).localeCompare(bucketSortKey(b[0])),
    );
  }, [items, filter, nowTs]);

  // Same open path as the sidebar's switchTo: focus-or-create the
  // session's center tab, THEN navigate. Navigating alone left the chat
  // with no tab when the user had closed it, so /s/<id> rendered empty.
  function openChat(c: ConvSummary) {
    useCenterTabs.getState().openSessionTab(c.id, labelFor(c, untitled));
    pushPath(`/s/${c.id}`);
  }

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
        { id: "past7", name: text("Last 7 days", "最近 7 天") },
        { id: "older", name: text("Older", "更早") },
      ],
    },
  ];
  const fixedLabels = {
    today: text("Today", "今天"),
    past7: text("Last 7 days", "最近 7 天"),
  };

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{t("nav.chats")}</span>
          <div className={styles.toolbar}>
            <SearchInput
              className="flex-1 max-w-[320px]"
              placeholder={text("Search chats...", "搜索会话...")}
              value={query}
              onChange={setQuery}
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
                // Wording matches the sidebar's Recents → Status filter;
                // these drive the same `archived` flag.
                { value: "all", label: t("sidebar.status_all") },
                { value: "active", label: t("sidebar.status_active") },
                { value: "archived", label: t("sidebar.status_archived") },
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
                {grouped
                  .filter(([, rows]) => rows.length)
                  .map(([b, rows]) => (
                    <div className={styles.section} key={b}>
                      <div className={styles.sectionHeader}>
                        {bucketLabel(b, locale, fixedLabels)} ({rows.length})
                      </div>
                      <div>
                        {rows.map((c) => (
                          <ChatRow
                            key={c.id}
                            conv={c}
                            locale={locale}
                            untitled={untitled}
                            onClick={() => openChat(c)}
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
                    untitled={untitled}
                    onClick={() => openChat(c)}
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
  // Same label the sidebar renders: channel brand prefix, placeholder
  // titles resolved to the preview, "[attached: …]" markers stripped.
  const title = labelFor(conv, untitled);
  const initial = title.replace(/^[\s[]+/, "").slice(0, 1).toUpperCase() || "?";
  return (
    <div className={styles.row} onClick={onClick}>
      <div className={styles.rowAvatar}>{initial}</div>
      <div className={styles.rowBody}>
        <div className={styles.rowTitle}>{title}</div>
        <div className={styles.rowMeta}>
          <span title={String(conv.id)}>{conv.id.slice(0, 12)}</span>
        </div>
      </div>
      {/* Last activity — the same timestamp the list sorts and buckets
          by, so the row's own time can't contradict its date section. */}
      <div className={styles.rowTime}>{formatRelativeTime(activityTs(conv), locale)}</div>
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
