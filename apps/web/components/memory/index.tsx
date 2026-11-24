"use client";

/**
 * Memory page — topics, timeline, recent, core.
 *
 * Four tabs for the four things memory holds. Topics is the only one
 * that is edited; timeline and recent are rebuilt from it after every
 * write, and core is the block on every system prompt.
 *
 * Split across:
 *   - types.ts           types + EditorState
 *   - markdown.ts        marked + wikilink expansion
 *   - format.ts          formatSize / formatDate / groupByFolder
 *   - icons.tsx          TYPE_COLORS, TypeBadge, DocIcon, ClockIcon
 *   - parts.tsx          TabButton, TreeGroup, EditorPanel,
 *                        EmptyState, Placeholder
 *   - data/reads.ts      the four reads, kept across visits
 *
 * Each list draws the kept answer at once and is read again behind it;
 * until a first answer comes its place stays empty, and a failed read is
 * tried again quietly.
 */
import { Fragment, useState, useEffect, useCallback, useMemo, useRef } from "react";
import Link from "next/link";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import { renderMarkdown } from "./markdown/markdown";
import { formatDate, formatSize, groupByFolder, groupTimelineDays } from "./markdown/format";
import { ClockIcon, TypeBadge } from "./icons";
import { useTranslation } from "@/lib/i18n";
import {
  ActivityIcon,
  ClockIcon as TimelineIcon,
  FileTextIcon,
  RefreshCwIcon,
  SparklesIcon,
} from "@/components/animated-icons";
import {
  DocGlyph,
  EmptyState,
  FolderGlyph,
  Placeholder,
  TabButton,
  TreeGroup,
} from "./parts";
import type {
  RecentEvent,
  Tab,
  TimelineDay,
  TopicPage,
} from "./data/types";
import styles from "./memory-page.module.css";
import { RailAction, RailCount, RailGroupLabel, RailItem, RailSearch, SecondaryRail } from "@/components/ui/secondary-rail";
import { ManageEmptyState } from "@/components/ui/manage-page";
import { MemorySourcePreview } from "./source-preview";
import { MemoryDocument } from "./document";
import { MemoryProjectContext, memoryUrl } from "./data/api";
import { WriterHealthBanner } from "./writer-health";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { showToast } from "@/lib/format-utils/toast";
import { errorDetailText } from "@/lib/net/error-text";
import { isAbort, lastKept, readKept, readUntilAnswered } from "@/lib/abilities/kept-read";
import { readCore, readRecent, readTimeline, readTimelineDay, readTopics, type CoreState } from "./data/reads";

/** The folders a topic list opens with: every top-level folder. */
function topicFolders(pages: TopicPage[]): Set<string> {
  const folders = new Set<string>();
  for (const p of pages) {
    const parts = p.path.split("/");
    if (parts.length > 1) folders.add(parts[0]);
  }
  return folders;
}

function quiet(what: string) {
  return (cause: unknown) => { if (!isAbort(cause)) console.warn(`[memory] ${what} read failed`, cause); };
}

export function MemoryPage({
  embedded,
  query,
  projectId = null,
}: {
  embedded?: boolean;
  query?: string;
  /** Show this project's memory instead of the personal one. Give the page
   *  a `key` per project so its state starts over on a switch. */
  projectId?: string | null;
} = {}) {
  const { t, text, locale } = useTranslation();
  const api = useCallback((path: string) => memoryUrl(path, projectId), [projectId]);
  const [tab, setTab] = useState<Tab>("topics");
  // Timeline years / months the user opened or closed; the rest use their
  // default (the newest year and its newest month start open).
  const [timelineOpen, setTimelineOpen] = useState<Record<string, boolean>>({});
  const isTimelineOpen = (key: string, fallback: boolean) => timelineOpen[key] ?? fallback;
  const toggleTimeline = (key: string, fallback: boolean) =>
    setTimelineOpen((current) => ({ ...current, [key]: !(current[key] ?? fallback) }));

  // Topics state
  const [topicPages, setTopicPages] = useState<TopicPage[]>(() => lastKept<TopicPage[]>(api("/api/memory/topics")) ?? []);
  const [topicsLoading, setTopicsLoading] = useState(() => lastKept(api("/api/memory/topics")) === undefined);
  const [selectedTopic, setSelectedTopic] = useState<TopicPage | null>(null);
  // Bumped when a topic save or delete lands, so the banner re-reads which
  // files are still held aside after the person fixes one in the editor.
  const [statusRevision, setStatusRevision] = useState(0);
  const refreshStatus = useCallback(() => setStatusRevision((value) => value + 1), []);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => topicFolders(topicPages));
  const [search, setSearch] = useState("");
  const searchValue = query !== undefined ? query : search;

  // Timeline state
  const [timelineDays, setTimelineDays] = useState<TimelineDay[]>(() => lastKept<TimelineDay[]>(api("/api/memory/timeline")) ?? []);
  const [timelineLoading, setTimelineLoading] = useState(() => lastKept(api("/api/memory/timeline")) === undefined);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [timelineContent, setTimelineContent] = useState("");

  // Recent state
  const [recentEvents, setRecentEvents] = useState<RecentEvent[]>(() => lastKept<{ events: RecentEvent[] }>(api("/api/memory/recent"))?.events ?? []);
  const [recentLimit, setRecentLimit] = useState(() => lastKept<{ limit: number | null }>(api("/api/memory/recent"))?.limit ?? 50);
  const [recentLoading, setRecentLoading] = useState(() => lastKept(api("/api/memory/recent")) === undefined);

  // Core state
  const [coreView, setCoreView] = useState<"injected" | "records">("injected");
  const [core, setCore] = useState<CoreState | null>(() => lastKept<CoreState>(api("/api/memory/core")) ?? null);
  const coreInjected = core?.injected ?? "";
  const coreMeta = core?.meta ?? null;
  const coreLoading = core === null;

  const showTopics = useCallback((pages: TopicPage[]) => {
    setTopicPages(pages);
    setExpandedFolders(topicFolders(pages));
    setTopicsLoading(false);
  }, []);
  // Refresh, and after a change here: one read; a failure keeps the list.
  const fetchTopics = useCallback(() => {
    const url = api("/api/memory/topics");
    readKept(url, () => readTopics(url)).then(showTopics).catch(quiet("topic list"));
  }, [api, showTopics]);
  const refreshTopics = (
    <RailAction label={t("sidebar.refresh")} onClick={fetchTopics}>
      <RefreshCwIcon size={14} />
    </RailAction>
  );

  // Opening: the four reads side by side, each tried again until it answers.
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const topics = api("/api/memory/topics");
    const timeline = api("/api/memory/timeline");
    const recent = api("/api/memory/recent");
    const coreUrl = api("/api/memory/core");
    readUntilAnswered(topics, () => readTopics(topics), signal).then(showTopics).catch(quiet("topic list"));
    readUntilAnswered(timeline, () => readTimeline(timeline), signal)
      .then((days) => { setTimelineDays(days); setTimelineLoading(false); })
      .catch(quiet("timeline"));
    readUntilAnswered(recent, () => readRecent(recent), signal)
      .then(({ events, limit }) => { setRecentEvents(events); if (limit) setRecentLimit(limit); setRecentLoading(false); })
      .catch(quiet("recent"));
    readUntilAnswered(coreUrl, () => readCore(coreUrl), signal).then(setCore).catch(quiet("core"));
    return () => controller.abort();
  }, [api, showTopics]);

  // After Core is saved: one read; a failure keeps what is shown.
  const fetchCore = useCallback(() => {
    const url = api("/api/memory/core");
    return readKept(url, () => readCore(url)).then(setCore).catch(quiet("core"));
  }, [api]);

  const openTopic = useCallback((page: TopicPage) => {
    setSelectedTopic(page);
  }, []);
  // A topic deleted elsewhere closes, and the list is read again without it.
  const closeGoneTopic = useCallback(() => {
    setSelectedTopic(null);
    fetchTopics();
  }, [fetchTopics]);
  const showInjectedCore = useCallback(() => setCoreView("injected"), []);

  // Resolve a wikilink target to a known page (match by slug or title)
  const resolveWikilink = useCallback((target: string): TopicPage | null => {
    const q = target.toLowerCase().trim();
    return (
      topicPages.find((p) => p.path.toLowerCase() === q + ".md") ||
      topicPages.find((p) => p.path.toLowerCase().endsWith("/" + q + ".md")) ||
      topicPages.find((p) => (p.title || "").toLowerCase() === q) ||
      null
    );
  }, [topicPages]);

  async function deleteTopic(target?: { removed?: boolean }) {
    if (!selectedTopic) return;
    if (!(await confirmAction({
      title: text(`Delete "${selectedTopic.title || selectedTopic.path}"?`, `删除“${selectedTopic.title || selectedTopic.path}”？`),
      description: text("This cannot be undone.", "此操作无法撤销。"),
      confirmLabel: text("Delete", "删除"),
      destructive: true,
    }))) return;
    // A save that left only the title already removed the file; deleting
    // it is closing what is left in the editor.
    const r = target?.removed
      ? { ok: true as const }
      : await fetch(api(`/api/memory/topics/${selectedTopic.path}`), { method: "DELETE" });
    if (r.ok) {
      setSelectedTopic(null);
      fetchTopics();
      refreshStatus();
    } else {
      // A refused delete says why (a block another topic still links to,
      // for one), so show the reason instead of swallowing it — same as
      // the PUT path above.
      const detail = await r.json().catch(() => ({})) as { error?: unknown };
      showToast(errorDetailText(detail.error, text("Delete failed", "删除失败")), { tone: "error" });
    }
  }

  const selectedDay = useRef(selectedDate);
  selectedDay.current = selectedDate;
  // A day's record: the kept one at once, then read again.
  function openTimelineDay(date: string) {
    selectedDay.current = date;
    const url = api(`/api/memory/timeline/${date}`);
    setSelectedDate(date);
    setTimelineContent(lastKept<string>(url) ?? "");
    readKept(url, () => readTimelineDay(url))
      .then((content) => { if (selectedDay.current === date) setTimelineContent(content); })
      .catch(quiet("timeline day"));
  }

  function toggleFolder(folder: string) {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  }

  const filteredTopics = useMemo(() => {
    if (!searchValue.trim()) return topicPages;
    const q = searchValue.toLowerCase();
    return topicPages.filter(
      (p) => p.path.toLowerCase().includes(q) || (p.title || "").toLowerCase().includes(q) || (p.type || "").toLowerCase().includes(q)
    );
  }, [topicPages, searchValue]);

  const topicGroups = groupByFolder(filteredTopics);
  const timelineGroups = useMemo(() => groupTimelineDays(timelineDays, locale), [timelineDays, locale]);

  // Wikilink click handler: delegate from the preview container
  function handlePreviewClick(e: React.MouseEvent) {
    const el = (e.target as HTMLElement).closest("a.wikilink") as HTMLAnchorElement | null;
    if (!el) return;
    e.preventDefault();
    const target = el.getAttribute("data-target");
    if (!target) return;
    const page = resolveWikilink(target);
    if (page) openTopic(page);
  }

  const view = (
    <div className={styles.view} style={embedded ? { flex: 1, minHeight: 0, height: "auto" } : undefined}>
      {!embedded && (
      <div className={styles.topbar}>
        <span className={styles.title}>{t("nav.memory")}</span>
        <Button asChild variant="elevated" className="ml-auto">
          <Link href="/settings/memory">
            <Settings2 size={16} />
            {text("Memory settings", "记忆设置")}
          </Link>
        </Button>
      </div>
      )}
      <WriterHealthBanner statusUrl={api("/api/memory/status")} revision={statusRevision} />

      {/* Body — single grid: tabBar (row 1 col 1) + tree (row 2 col 1) +
          editor/placeholder (col 2 spans both rows so it starts right at
          the topbar, ignoring the tab bar's height). */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <div className={`${styles.layout} grid grid-cols-[calc(var(--sidebar-width)_-_1px)_1fr] grid-rows-[auto_minmax(0,_1fr)] h-full min-h-0`}>
          <SecondaryRail className={styles.tabBar} label={text("Memory sections", "记忆分区")}>
            <TabButton active={tab === "topics"} onClick={() => setTab("topics")} icon={<FileTextIcon size={16} />}>{text("Topics", "主题")}</TabButton>
            <TabButton active={tab === "timeline"} onClick={() => setTab("timeline")} icon={<TimelineIcon size={16} />}>{text("Timeline", "时间线")}</TabButton>
            <TabButton active={tab === "recent"} onClick={() => setTab("recent")} icon={<ActivityIcon size={16} />}>{text("Recent", "最近")}</TabButton>
            <TabButton active={tab === "core"} onClick={() => setTab("core")} icon={<SparklesIcon size={16} />}>{text("Core", "核心")}</TabButton>
          </SecondaryRail>

          {/* ── Topics ── */}
          {tab === "topics" && (
            <>
              <SecondaryRail as="aside" className={styles.tree} label={text("Topics", "主题")}>
              {query === undefined ? (
                <RailSearch
                  placeholder={text("Search topics...", "搜索主题...")}
                  value={search}
                  onChange={setSearch}
                  action={refreshTopics}
                />
              ) : (
                // The hub header owns search; label the row so the
                // refresh button does not float on its own.
                <RailGroupLabel action={refreshTopics}>
                  {text("Topics", "主题")}
                  {!topicsLoading && <RailCount>{filteredTopics.length}</RailCount>}
                </RailGroupLabel>
              )}
              {topicsLoading ? null : filteredTopics.length === 0 ? (
                // Only the search miss lives in the rail; the plain empty
                // state is the right pane's emptyPanel — showing both said
                // "no topics" twice side by side.
                searchValue ? (
                  <EmptyState
                    icon="doc"
                    text={text("No matches", "没有匹配结果")}
                    sub={text("Try a different query", "换一个查询试试")}
                  />
                ) : null
              ) : (
                <>
                  {Array.from(topicGroups.entries()).map(([folder, pages]) => (
                    <TreeGroup
                      key={folder}
                      folder={folder}
                      pages={pages}
                      expanded={expandedFolders}
                      onToggle={toggleFolder}
                      selected={selectedTopic}
                      onSelect={openTopic}
                      forceOpen={!!searchValue}
                    />
                  ))}
                </>
              )}
            </SecondaryRail>
              <div className={styles.rightPane}>
                {selectedTopic ? (
                  <MemoryDocument
                    title={selectedTopic.title || selectedTopic.path}
                    badge={selectedTopic.type ? <TypeBadge type={selectedTopic.type} /> : null}
                    meta={[
                      selectedTopic.path,
                      formatSize(selectedTopic.size),
                      text(`Modified ${formatDate(selectedTopic.mtime, locale)}`, `修改于 ${formatDate(selectedTopic.mtime, locale)}`),
                    ]}
                    key={selectedTopic.path}
                    path={selectedTopic.path}
                    url={api(`/api/memory/topics/${selectedTopic.path.split("/").map(encodeURIComponent).join("/")}`)}
                    onDelete={deleteTopic}
                    onSaved={refreshStatus}
                    onRemovedChange={fetchTopics}
                    onGone={closeGoneTopic}
                    onPreviewClick={handlePreviewClick}
                  />
                ) : !topicsLoading && topicPages.length === 0 && !searchValue ? (
                  <div className={styles.emptyPanel}>
                    <FileTextIcon size={24} />
                    <h2>{text("No topics yet", "还没有主题")}</h2>
                    <p>{text(
                      "Durable memory created from conversations and the background writer will appear here. Core rules are managed separately.",
                      "由对话和后台写入器生成的持久记忆会显示在这里；核心规则单独管理。",
                    )}</p>
                    <Button asChild variant="elevated" className="mt-[7px]">
                      <Link href="/settings/memory">
                        {text("Open Memory settings", "打开记忆设置")}
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <Placeholder icon="doc" text={text("Select a topic to view or edit", "选择一个主题进行查看或编辑")} />
                )}
              </div>
            </>
          )}

          {/* ── Timeline ── */}
          {tab === "timeline" && (
            <>
              <SecondaryRail as="aside" className={styles.tree} label={text("Timeline", "时间线")}>
              {timelineLoading ? null : timelineGroups.length === 0 ? (
                <EmptyState
                  icon="clock"
                  text={text("No timeline yet", "还没有时间线")}
                  sub={text("Rebuilt from topics after every write", "每次写入后从主题重建")}
                />
              ) : timelineGroups.map((year, yearIndex) => {
                const yearOpen = isTimelineOpen(year.year, yearIndex === 0);
                return (
                  <Fragment key={year.year}>
                    <RailItem
                      icon={<FolderGlyph open={yearOpen} />}
                      label={year.year}
                      trailing={<RailCount>{year.entryCount}</RailCount>}
                      aria-expanded={yearOpen}
                      onClick={() => toggleTimeline(year.year, yearIndex === 0)}
                    />
                    {yearOpen && year.entries.map((entry) => (
                      <RailItem
                        key={entry.date}
                        depth={1}
                        icon={<DocGlyph />}
                        label={entry.label}
                        trailing={<RailCount>{formatSize(entry.size)}</RailCount>}
                        active={selectedDate === entry.date}
                        onClick={() => openTimelineDay(entry.date)}
                      />
                    ))}
                    {yearOpen && year.months.map((month, monthIndex) => {
                      const monthOpen = isTimelineOpen(month.key, yearIndex === 0 && monthIndex === 0);
                      return (
                        <Fragment key={month.key}>
                          <RailItem
                            depth={1}
                            icon={<FolderGlyph open={monthOpen} />}
                            label={month.label}
                            trailing={<RailCount>{month.entries.length + month.days.length}</RailCount>}
                            aria-expanded={monthOpen}
                            onClick={() => toggleTimeline(month.key, yearIndex === 0 && monthIndex === 0)}
                          />
                          {monthOpen && month.entries.map((entry) => (
                            <RailItem
                              key={entry.date}
                              depth={2}
                              icon={<DocGlyph />}
                              label={entry.label}
                              trailing={<RailCount>{formatSize(entry.size)}</RailCount>}
                              active={selectedDate === entry.date}
                              onClick={() => openTimelineDay(entry.date)}
                            />
                          ))}
                          {monthOpen && month.days.map((day) => (
                            <RailItem
                              key={day.date}
                              depth={2}
                              icon={<DocGlyph />}
                              label={<>{day.dayLabel} <span className={styles.timelineWeekday}>{day.weekday}</span></>}
                              name={`${day.dayLabel} ${day.weekday}`}
                              trailing={<RailCount>{formatSize(day.size)}</RailCount>}
                              active={selectedDate === day.date}
                              onClick={() => openTimelineDay(day.date)}
                            />
                          ))}
                        </Fragment>
                      );
                    })}
                  </Fragment>
                );
              })}
            </SecondaryRail>
              <div className={styles.rightPane}>
                {selectedDate ? (
                  <div className={styles.editor}>
                    <div className={styles.editorHeader}>
                      <div className={styles.editorHeaderLeft}>
                        <ClockIcon className={styles.fileIcon} />
                        <span className={styles.editorTitle}>{selectedDate}</span>
                      </div>
                      <div className={styles.editorActions}>
                        <span className={styles.fileMeta}>{timelineContent.length} {text("chars", "字符")}</span>
                      </div>
                    </div>
                    <div className="flex-1 overflow-y-auto min-h-0">
                      <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: timelineContent ? renderMarkdown(timelineContent) : "" }} />
                    </div>
                  </div>
                ) : (
                  <Placeholder icon="clock" text={text("Select a day to see what memory recorded", "选择一天，查看记忆当天记录了什么")} />
                )}
              </div>
            </>
          )}

          {/* ── Recent ── */}
          {tab === "recent" && (
            <>
              <SecondaryRail as="aside" className={`${styles.tree} ${styles.treeEmpty}`} label={text("Recent", "最近")}>{null}</SecondaryRail>
              <div className={styles.rightPane}>
                {recentLoading ? null : recentEvents.length === 0 ? (
                  <Placeholder icon="clock" text={text("Nothing written yet", "还没有写入任何内容")} />
                ) : (
                  <div className={styles.editor}>
                    <div className={styles.editorHeader}>
                      <div className={styles.editorHeaderLeft}>
                        <ActivityIcon size={14} />
                        <span className={styles.editorTitle}>{text("Recent", "最近")}</span>
                      </div>
                      <div className={styles.editorActions}>
                        <span className={styles.fileMeta}>
                          {recentEvents.length} / {recentLimit} {text("records", "条记录")}
                        </span>
                      </div>
                    </div>
                    <div className="flex-1 overflow-y-auto min-h-0">
                      <div className={styles.markdown}>
                        {recentEvents.map((event) => (
                          <div key={event.memory_id} className={styles.recentEvent}>
                            <div className={styles.recentEventMeta}>
                              {[event.when, event.topic_path].filter(Boolean).join(" · ")}
                            </div>
                            <div className={styles.recentEventContent}>{event.content}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}

          {/* ── Core ── */}
          {tab === "core" && (
            <>
              <SecondaryRail as="aside" className={`${styles.tree} ${styles.treeEmpty}`} label={text("Core", "核心")}>{null}</SecondaryRail>
              <div className={styles.rightPane}>
                <div className={`${styles.coreControls} grid grid-cols-[minmax(220px,_320px)_minmax(260px,_1fr)] gap-[10px] mb-[10px] shrink-0`}>
                  <ToggleGroup
                    type="single" variant="elevated" size="sm" spacing={1}
                    className="grid w-full grid-cols-2"
                    value={coreView}
                    onValueChange={(value: string) => { if (value) setCoreView(value as typeof coreView); }}
                    aria-label={text("Core view", "核心视图")}
                  >
                    <ToggleGroupItem value="injected" className="min-w-0">{text("MEMORY.md as injected", "注入的 MEMORY.md")}</ToggleGroupItem>
                    <ToggleGroupItem value="records" className="min-w-0">{text("Source records", "源记录")}</ToggleGroupItem>
                  </ToggleGroup>
                  {coreMeta && (
                    <div className={styles.coreStatusCard}>
                      <div className={styles.coreStatusRow}>
                        <span className={coreMeta.injectionEnabled ? styles.coreStatusOn : styles.coreStatusOff}>
                          {coreMeta.injectionEnabled ? text("Injection enabled", "注入已开启") : text("Injection disabled", "注入已关闭")}
                        </span>
                        <span>{coreMeta.injectedTokens.toLocaleString()} / {coreMeta.budgetTokens.toLocaleString()} tokens</span>
                      </div>
                      <div
                        className={styles.coreTokenMeter}
                        role="progressbar"
                        aria-label={text("Core token budget usage", "核心记忆 token 预算使用量")}
                        aria-valuemin={0}
                        aria-valuemax={coreMeta.budgetTokens}
                        aria-valuenow={coreMeta.injectedTokens}
                      >
                        <span
                          className={coreMeta.injectedTokens > coreMeta.budgetTokens ? styles.coreTokenFillWarn : styles.coreTokenFill}
                          style={{ width: `${Math.min(100, coreMeta.budgetTokens > 0 ? (coreMeta.injectedTokens / coreMeta.budgetTokens) * 100 : 0)}%` }}
                        />
                      </div>
                      <div className={styles.coreStatusFoot}>
                        {coreView === "injected" && coreMeta.renderedMtime <= 0
                          ? text("No prompt preview yet", "还没有提示词预览")
                          : coreView === "records" && coreMeta.mtime <= 0
                          ? text("No source records yet", "还没有源记录")
                          : coreView === "injected"
                          ? text(
                            `Preview rebuilt ${formatDate(coreMeta.renderedMtime, locale)}`,
                            `预览重建于 ${formatDate(coreMeta.renderedMtime, locale)}`,
                          )
                          : text(
                            `Source ${formatSize(coreMeta.size)} · modified ${formatDate(coreMeta.mtime, locale)}`,
                            `源记录 ${formatSize(coreMeta.size)} · 修改于 ${formatDate(coreMeta.mtime, locale)}`,
                          )}
                      </div>
                    </div>
                  )}
                </div>
                {coreLoading ? null : coreView === "records" ? (
                  <MemoryDocument
                    title={text("Core source records", "核心源记录")}
                    meta={["topics/core.md"]}
                    key="core.md"
                    path="core.md"
                    url={api("/api/memory/core")}
                    onSaved={fetchCore}
                    onGone={showInjectedCore}
                  />
                ) : (
                  <div className={styles.editor}>
                    <div className={styles.editorHeader}>
                      <div className={styles.editorHeaderLeft}>
                        <SparklesIcon size={14} />
                        <span className={styles.editorTitle}>{text("MEMORY.md as injected", "注入的 MEMORY.md")}</span>
                      </div>
                    </div>
                    <div className="flex-1 overflow-y-auto min-h-0">
                      {coreInjected ? (
                        <div
                          className={styles.markdown}
                          dangerouslySetInnerHTML={{ __html: renderMarkdown(coreInjected) }}
                        />
                      ) : (
                        <ManageEmptyState
                          compact
                          icon={<SparklesIcon size={20} />}
                          title={text("No Core memory is currently injected", "当前没有注入核心记忆")}
                          description={text("Core rules you add are injected into every conversation's prompt.", "你添加的核心规则会注入每个对话的提示词。")}
                        />
                      )}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );

  if (embedded) {
    return (
      <MemoryProjectContext.Provider value={projectId}>
        <MemorySourcePreview>{view}</MemorySourcePreview>
      </MemoryProjectContext.Provider>
    );
  }
  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      <MemoryProjectContext.Provider value={projectId}>
        <MemorySourcePreview>{view}</MemorySourcePreview>
      </MemoryProjectContext.Provider>
    </div>
  );
}
