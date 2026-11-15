"use client";

/**
 * /chats — list of past conversations.
 *
 * Shell mirrors /programs and /memory: sticky topbar with title +
 * toolbar (search, New chat), a SecondaryRail on the left with quick
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
import { useEffect, useMemo, useState } from "react";
import styles from "./chats-page.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { RailCount, RailGroupLabel, RailItem, SecondaryRail } from "@/components/ui/secondary-rail";
import { ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, ChevronUp } from "lucide-react";
import { ManageEmptyState, ManageIconButton } from "@/components/ui/manage-page";
import { cn } from "@/lib/utils";
import { ChannelLogo } from "@/components/channels/channel-logo";
import { channelBrand } from "@/lib/channels/channel-brands";
import { Card, CardContent } from "@/components/ui/card";
import { useTranslation, type Locale } from "@/lib/i18n";
import { parseAttachments } from "@/components/chat/messages/attachments/user-attachments";
import { formatRelativeTime } from "@/lib/format-utils/format";
import { pushPath } from "@/lib/nav/shallow-nav";
import { useSessionStore, type ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  type BucketKey,
  activityTs,
  bucketIsOlder,
  bucketKey,
  bucketLabel,
  bucketSortKey,
  displayTitle,
  labelFor,
} from "@/components/sidebar/sessions/helpers";
import {
  ClockIcon,
  MessageCircleIcon,
} from "@/components/animated-icons";
import { SimpleSelect } from "@/components/ui/select";

type SortKey = "recent" | "oldest" | "title";
type StatusFilter = "all" | "active" | "archived";

type FilterId = "all" | "today" | "past7" | "older";

/** An open group lists this many chats, then "Show all N". */
const GROUP_PREVIEW = 8;
const GROUP_CHOICE_KEY = "chatsGroupsOpen";

function readGroupChoice(): Record<string, boolean> {
  try {
    const raw = window.localStorage.getItem(GROUP_CHOICE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? (parsed as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

function writeGroupChoice(choice: Record<string, boolean>) {
  try { window.localStorage.setItem(GROUP_CHOICE_KEY, JSON.stringify(choice)); } catch { /* per-viewer convenience only */ }
}

export function ChatsPage({
  embedded,
  query: queryProp,
  include,
  rail = true,
}: {
  embedded?: boolean;
  query?: string;
  /** Keep only these chats, e.g. one project's. */
  include?: (conv: ConvSummary) => boolean;
  /** The left rail of recency filters; off inside a project, where the
   *  list stays grouped by recency instead. */
  rail?: boolean;
} = {}) {
  const { t, text, locale } = useTranslation();
  // Same store the sidebar Recents list reads — one pipeline, no second
  // WebSocket. Adds / renames / deletes / archive flags land here the
  // moment the runtime-bridge processes `sessions_list`.
  const conversations = useSessionStore((s) => s.conversations);
  const [localQuery, setLocalQuery] = useState("");
  const query = queryProp !== undefined ? queryProp : localQuery;
  const setQuery = setLocalQuery;
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
    if (include) arr = arr.filter(include);
    if (q) {
      // Match what the row shows (the title with its preview fallback,
      // and the channel the logo stands for), not just the raw title —
      // otherwise searching for what you can see returns nothing.
      arr = arr.filter((c) =>
        `${channelBrand(c.channel)} ${labelFor(c, untitled)}`.toLowerCase().includes(q));
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
  }, [conversations, include, query, statusFilter, sort, untitled]);

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

  // Which recency groups are open. The viewer's own choices are kept per
  // bucket; a group with no choice opens when it is this week's (Today,
  // Last 7 days) or the newest one, so a long history starts folded.
  const [groupChoice, setGroupChoice] = useState<Record<string, boolean>>(readGroupChoice);
  const [showAll, setShowAll] = useState<Set<string>>(() => new Set());
  const searching = query.trim() !== "";
  const isGroupOpen = (b: BucketKey, index: number) =>
    searching || (groupChoice[b] ?? (b === "today" || b === "past7" || index === 0));
  const setGroupsOpen = (keys: BucketKey[], open: boolean) => setGroupChoice((current) => {
    const next = { ...current };
    for (const key of keys) next[key] = open;
    writeGroupChoice(next);
    return next;
  });
  const visibleGroups = grouped?.filter(([, rows]) => rows.length) ?? [];
  const anyGroupClosed = visibleGroups.some(([b], index) => !isGroupOpen(b, index));

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

  const selects = (
    <>
      <SimpleSelect
        aria-label={text("Sort", "排序")}
        value={sort}
        onValueChange={setSort}
        options={[
          { value: "recent", label: text("Sort: Recent", "排序：最近") },
          { value: "oldest", label: text("Sort: Oldest", "排序：最早") },
          { value: "title", label: text("Sort: Title", "排序：标题") },
        ]}
      />
      <SimpleSelect
        aria-label={text("Status", "状态")}
        value={statusFilter}
        onValueChange={setStatusFilter}
        options={[
          // Same `archived` flag as the sidebar's Recents → Status filter;
          // prefixed like the sort select so the trigger says what it filters.
          { value: "all", label: text(`Status: ${t("sidebar.status_all")}`, `状态：${t("sidebar.status_all")}`) },
          { value: "active", label: text(`Status: ${t("sidebar.status_active")}`, `状态：${t("sidebar.status_active")}`) },
          { value: "archived", label: text(`Status: ${t("sidebar.status_archived")}`, `状态：${t("sidebar.status_archived")}`) },
        ]}
      />
    </>
  );

  const view = (
      <div className="h-full flex flex-col overflow-visible" style={embedded ? { flex: 1, minHeight: 0, height: "auto" } : undefined}>
        {/* Embedded: the hub header already owns title + search, so the
            64px topbar would render as an empty band — the selects move
            to the top of the content column instead. */}
        {!embedded && (
          <div className={styles.topbar}>
            <span className={styles.title}>{t("nav.chats")}</span>
            <div className="flex items-center gap-[10px] ml-[auto]">
              {queryProp === undefined && (
                <SearchInput
                  className="flex-1 max-w-[320px]"
                  placeholder={text("Search chats...", "搜索会话...")}
                  value={query}
                  onChange={setQuery}
                />
              )}
              {selects}
            </div>
          </div>
        )}

        <div className={rail ? styles.body : `${styles.body} ${styles.bodyNoRail}`}>
          {rail && (
          <SecondaryRail label={text("Chat filters", "会话筛选")}>
            {navGroups.map((group) => (
              <div key={group.label}>
                <RailGroupLabel>{group.label}</RailGroupLabel>
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
          </SecondaryRail>
          )}

          <div className={`${styles.content} min-w-0 overflow-y-auto flex flex-col gap-[24px] p-[20px_24px_32px]`}>
            {embedded && (
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="mr-auto flex min-w-0 items-baseline gap-2 text-fs-md font-medium text-text-primary">
                  {navGroups.flatMap((group) => group.items).find((it) => it.id === filter)?.name}
                  <span className="text-fs-base font-normal text-text-muted">{items.length}</span>
                </h3>
                {visibleGroups.length > 1 && !searching && (
                  <ManageIconButton
                    label={anyGroupClosed ? text("Expand all groups", "展开全部分组") : text("Collapse all groups", "折叠全部分组")}
                    onClick={() => setGroupsOpen(visibleGroups.map(([b]) => b), anyGroupClosed)}
                  >
                    {anyGroupClosed ? <ChevronsUpDown /> : <ChevronsDownUp />}
                  </ManageIconButton>
                )}
                {selects}
              </div>
            )}
            {items.length === 0 ? (
              <ManageEmptyState
                compact
                icon={<MessageCircleIcon size={20} />}
                title={query
                  ? text("No chats match your search", "没有匹配的会话")
                  : filter === "all"
                    ? include
                      ? text("No chats in this project yet", "这个项目还没有会话")
                      : text("No conversations yet", "还没有会话")
                    : text("Nothing in this range", "这个时间范围内没有内容")}
                description={query
                  ? text("Try a different search term or status filter.", "换个关键词或状态筛选试试。")
                  : filter === "all"
                    ? include
                      ? text("Chats started in this project appear here, grouped by recency.", "在这个项目里开始的会话会按时间分组显示在这里。")
                      : text(`Use ${t("nav.new_chat")} in the sidebar; conversations appear here grouped by recency.`, `使用侧栏的“${t("nav.new_chat")}”开始；会话会按时间分组显示在这里。`)
                    : text("Pick another range on the left.", "在左侧选择其他时间范围。")}
              />
            ) : grouped ? (
              <>
                {visibleGroups.map(([b, rows], index) => {
                  const open = isGroupOpen(b, index);
                  const all = showAll.has(b) || rows.length <= GROUP_PREVIEW + 2;
                  const shown = all ? rows : rows.slice(0, GROUP_PREVIEW);
                  const panelId = `chat-group-${b}`;
                  return (
                    <section className="flex flex-col gap-3" key={b}>
                      <h4 className="flex">
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-controls={panelId}
                          onClick={() => setGroupsOpen([b], !open)}
                          className="group/head -mx-2 flex items-center gap-1.5 rounded-md px-2 py-1 text-fs-base font-medium text-text-primary outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50"
                        >
                          <ChevronRight
                            aria-hidden="true"
                            className={cn("size-4 text-text-muted transition-transform duration-150", open && "rotate-90")}
                          />
                          {bucketLabel(b, locale, fixedLabels)}
                          <span className="font-normal text-text-muted">{rows.length}</span>
                        </button>
                      </h4>
                      {open && (
                        <ChatCard id={panelId}>
                          {shown.map((c) => (
                            <ChatRow
                              key={c.id}
                              conv={c}
                              locale={locale}
                              untitled={untitled}
                              onClick={() => openChat(c)}
                            />
                          ))}
                          {rows.length > GROUP_PREVIEW + 2 && (
                            <button
                              type="button"
                              onClick={() => setShowAll((current) => {
                                const next = new Set(current);
                                if (next.has(b)) next.delete(b); else next.add(b);
                                return next;
                              })}
                              className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-fs-base text-text-muted outline-none hover:bg-muted/60 hover:text-text-primary focus-visible:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
                            >
                              <span className="flex size-4 shrink-0 items-center justify-center" aria-hidden="true">
                                {all ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
                              </span>
                              {all
                                ? text("Show fewer", "收起")
                                : text(`Show all ${rows.length}`, `显示全部 ${rows.length} 个`)}
                            </button>
                          )}
                        </ChatCard>
                      )}
                    </section>
                  );
                })}
              </>
            ) : (
              <ChatCard>
                {items.map((c) => (
                  <ChatRow
                    key={c.id}
                    conv={c}
                    locale={locale}
                    untitled={untitled}
                    onClick={() => openChat(c)}
                  />
                ))}
              </ChatCard>
            )}
          </div>
        </div>
      </div>
  );

  if (embedded) return view;
  return <div className="main">{view}</div>;
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
  // Same label the sidebar renders: placeholder titles resolved to the
  // preview, "[attached: …]" markers stripped. A channel chat shows its
  // app's logo in the icon slot instead of the chat bubble.
  const title = labelFor(conv, untitled);
  // The second line is the latest question asked, so a row says what the
  // chat is about now. An untitled chat already shows it as its name.
  const latest = displayTitle(conv) && conv.preview ? parseAttachments(String(conv.preview)) : null;
  const asked = latest ? latest.text.trim() || latest.attachments[0]?.filename || "" : "";
  // A real <button> to open; the time is last activity, the same timestamp
  // the list sorts and buckets by, so a row can't contradict its section.
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full min-w-0 items-center gap-3 px-4 py-3 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
    >
      <span className="flex shrink-0 text-text-muted" aria-hidden="true">
        {conv.channel ? <ChannelLogo channel={conv.channel} size={16} /> : <MessageCircleIcon size={16} />}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-fs-base font-medium text-text-primary">
          {conv.channel ? <span className="sr-only">{channelBrand(conv.channel)}: </span> : null}
          {title}
        </span>
        {asked ? <span className="truncate text-fs-base text-text-muted">{asked}</span> : null}
      </span>
      <span className="shrink-0 text-fs-xs text-text-muted">{formatRelativeTime(activityTs(conv), locale)}</span>
    </button>
  );
}

/** One recency group's chats on a Card, rows divided by hairlines, as on
 *  the Settings page (SettingsSection). */
function ChatCard({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <Card id={id} className="gap-0 py-0">
      <CardContent className="divide-y divide-border px-0">{children}</CardContent>
    </Card>
  );
}

/** One left-rail filter row. "All chats" shows the chat-bubble glyph; the
 *  "by recency" buckets share the clock. */
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
  const Icon = id === "all" ? MessageCircleIcon : ClockIcon;
  return (
    <RailItem
      icon={<Icon size={16} />}
      label={name}
      trailing={<RailCount>{count}</RailCount>}
      active={active}
      onClick={onSelect}
    />
  );
}
