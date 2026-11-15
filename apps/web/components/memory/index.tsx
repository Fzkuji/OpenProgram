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
 *                        LoadingSkeleton, EmptyState, Placeholder
 */
import { Fragment, useState, useEffect, useCallback, useMemo } from "react";
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
  LoadingSkeleton,
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
  const [topicPages, setTopicPages] = useState<TopicPage[]>([]);
  const [topicsLoading, setTopicsLoading] = useState(true);
  const [selectedTopic, setSelectedTopic] = useState<TopicPage | null>(null);
  // Bumped when a topic save or delete lands, so the banner re-reads which
  // files are still held aside after the person fixes one in the editor.
  const [statusRevision, setStatusRevision] = useState(0);
  const refreshStatus = useCallback(() => setStatusRevision((value) => value + 1), []);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const searchValue = query !== undefined ? query : search;

  // Timeline state
  const [timelineDays, setTimelineDays] = useState<TimelineDay[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [timelineContent, setTimelineContent] = useState("");

  // Recent state
  const [recentEvents, setRecentEvents] = useState<RecentEvent[]>([]);
  const [recentLimit, setRecentLimit] = useState(50);
  const [recentLoading, setRecentLoading] = useState(true);

  // Core state
  const [coreView, setCoreView] = useState<"injected" | "records">("injected");
  const [coreInjected, setCoreInjected] = useState("");
  const [coreMeta, setCoreMeta] = useState<{
    size: number;
    mtime: number;
    renderedSize: number;
    renderedMtime: number;
    renderedTokens: number;
    injectedTokens: number;
    injectionEnabled: boolean;
    budgetTokens: number;
  } | null>(null);
  const [coreLoading, setCoreLoading] = useState(true);

  const fetchTopics = useCallback(() => {
    setTopicsLoading(true);
    fetch(api("/api/memory/topics"))
      .then((r) => r.json())
      .then((pages) => {
        const content = Array.isArray(pages) ? pages : [];
        setTopicPages(content);
        const folders = new Set<string>();
        for (const p of content) {
          const parts = p.path.split("/");
          if (parts.length > 1) folders.add(parts[0]);
        }
        setExpandedFolders(folders);
        setTopicsLoading(false);
      })
      .catch(() => setTopicsLoading(false));
  }, [api]);
  const refreshTopics = (
    <RailAction label={t("sidebar.refresh")} onClick={fetchTopics}>
      <RefreshCwIcon size={14} />
    </RailAction>
  );

  useEffect(() => { fetchTopics(); }, [fetchTopics]);

  useEffect(() => {
    fetch(api("/api/memory/timeline"))
      .then((r) => r.json())
      .then((data) => { setTimelineDays(Array.isArray(data) ? data : []); setTimelineLoading(false); })
      .catch(() => setTimelineLoading(false));
  }, [api]);

  useEffect(() => {
    fetch(api("/api/memory/recent"))
      .then((r) => {
        const limit = Number(r.headers.get("X-Memory-Recent-Limit"));
        if (Number.isInteger(limit) && limit > 0) setRecentLimit(limit);
        return r.json();
      })
      .then((data) => { setRecentEvents(Array.isArray(data) ? data : []); setRecentLoading(false); })
      .catch(() => setRecentLoading(false));
  }, [api]);

  const fetchCore = useCallback(() => {
    return fetch(api("/api/memory/core"))
      .then((r) => {
        if (!r.ok) throw new Error(`Core request failed: ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setCoreInjected(data.injected_content ?? "");
        setCoreMeta({
          size: data.size ?? 0,
          mtime: data.mtime ?? 0,
          renderedSize: data.rendered_size ?? 0,
          renderedMtime: data.rendered_mtime ?? 0,
          renderedTokens: data.rendered_tokens ?? 0,
          injectedTokens: data.injected_tokens ?? 0,
          injectionEnabled: data.injection_enabled ?? true,
          budgetTokens: data.budget_tokens ?? 2000,
        });
        setCoreLoading(false);
      })
      .catch(() => setCoreLoading(false));
  }, [api]);

  useEffect(() => { void fetchCore(); }, [fetchCore]);

  const openTopic = useCallback((page: TopicPage) => {
    setSelectedTopic(page);
  }, []);

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
      const detail = await r.json().catch(() => ({}));
      showToast(detail.error || text("Delete failed", "删除失败"), { tone: "error" });
    }
  }

  function openTimelineDay(date: string) {
    setSelectedDate(date);
    setTimelineContent("");
    fetch(api(`/api/memory/timeline/${date}`))
      .then((r) => r.json())
      .then((data) => setTimelineContent(data.content ?? ""));
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
              {topicsLoading ? <LoadingSkeleton /> : filteredTopics.length === 0 ? (
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
              {timelineLoading ? <LoadingSkeleton /> : timelineGroups.length === 0 ? (
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
                      <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: timelineContent ? renderMarkdown(timelineContent) : `<em>${text("Loading...", "加载中...")}</em>` }} />
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
                {recentLoading ? (
                  <LoadingSkeleton />
                ) : recentEvents.length === 0 ? (
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
                {coreLoading ? (
                  <LoadingSkeleton />
                ) : coreView === "records" ? (
                  <MemoryDocument
                    title={text("Core source records", "核心源记录")}
                    meta={["topics/core.md"]}
                    key="core.md"
                    path="core.md"
                    url={api("/api/memory/core")}
                    onSaved={fetchCore}
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
