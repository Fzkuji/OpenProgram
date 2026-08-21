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
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import Link from "next/link";
import { Settings2 } from "lucide-react";

import { renderMarkdown } from "./markdown";
import { formatDate, formatSize, groupByFolder, groupTimelineDays } from "./format";
import { ClockIcon, TypeBadge } from "./icons";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  ActivityIcon,
  FileTextIcon,
  RefreshCwIcon,
  SparklesIcon,
} from "@/components/animated-icons";
import {
  DisclosureChevron,
  EditorPanel,
  EmptyState,
  LoadingSkeleton,
  Placeholder,
  TabButton,
  TreeGroup,
} from "./parts";
import type {
  EditorState,
  RecentEvent,
  Tab,
  TimelineDay,
  TopicPage,
} from "./types";
import styles from "./memory-page.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { coreSaveStatus } from "./core-save-state";

export function MemoryPage({
  embedded,
  query,
}: {
  embedded?: boolean;
  query?: string;
} = {}) {
  const { t, text, locale } = useTranslation();
  const refreshIconRef = useRef<AnimatedNavIconHandle>(null);
  const [tab, setTab] = useState<Tab>("topics");

  // Topics state
  const [topicPages, setTopicPages] = useState<TopicPage[]>([]);
  const [topicsLoading, setTopicsLoading] = useState(true);
  const [selectedTopic, setSelectedTopic] = useState<TopicPage | null>(null);
  const [topicEditor, setTopicEditor] = useState<EditorState>({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
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
  const [coreEditor, setCoreEditor] = useState<EditorState>({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
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
    fetch("/api/memory/topics")
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
  }, []);

  useEffect(() => { fetchTopics(); }, [fetchTopics]);

  useEffect(() => {
    fetch("/api/memory/timeline")
      .then((r) => r.json())
      .then((data) => { setTimelineDays(Array.isArray(data) ? data : []); setTimelineLoading(false); })
      .catch(() => setTimelineLoading(false));
  }, []);

  useEffect(() => {
    fetch("/api/memory/recent")
      .then((r) => {
        const limit = Number(r.headers.get("X-Memory-Recent-Limit"));
        if (Number.isInteger(limit) && limit > 0) setRecentLimit(limit);
        return r.json();
      })
      .then((data) => { setRecentEvents(Array.isArray(data) ? data : []); setRecentLoading(false); })
      .catch(() => setRecentLoading(false));
  }, []);

  const fetchCore = useCallback((submittedContent?: string) => {
    return fetch("/api/memory/core")
      .then((r) => {
        if (!r.ok) throw new Error(`Core request failed: ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setCoreEditor((e) => (
          submittedContent === undefined || e.content === submittedContent
            ? { ...e, content: data.content ?? "" }
            : e
        ));
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
  }, []);

  useEffect(() => { void fetchCore(); }, [fetchCore]);

  const openTopic = useCallback((page: TopicPage) => {
    setSelectedTopic(page);
    setTopicEditor({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
    fetch(`/api/memory/topics/${page.path}`)
      .then((r) => r.json())
      .then((data) => setTopicEditor((e) => ({ ...e, content: data.content ?? "" })));
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

  async function deleteTopic() {
    if (!selectedTopic) return;
    if (!confirm(text(
      `Delete "${selectedTopic.title || selectedTopic.path}"? This cannot be undone.`,
      `删除“${selectedTopic.title || selectedTopic.path}”？此操作无法撤销。`,
    ))) return;
    const r = await fetch(`/api/memory/topics/${selectedTopic.path}`, { method: "DELETE" });
    if (r.ok) {
      setSelectedTopic(null);
      fetchTopics();
    } else {
      // A refused delete says why (a block another topic still links to,
      // for one), so show the reason instead of swallowing it — same as
      // the PUT path above.
      const detail = await r.json().catch(() => ({}));
      alert(detail.error || text("Delete failed", "删除失败"));
    }
  }

  async function saveTopic() {
    if (!selectedTopic) return;
    setTopicEditor((e) => ({ ...e, saving: true, saveStatus: "" }));
    try {
      const r = await fetch(`/api/memory/topics/${selectedTopic.path}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: topicEditor.content }),
      });
      // A rejected edit is put back on disk, so say why rather than
      // leaving the editor showing text that was not saved.
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        if (detail.error) alert(detail.error);
      }
      setTopicEditor((e) => ({ ...e, saving: false, saveStatus: r.ok ? "saved" : "error" }));
    } catch {
      setTopicEditor((e) => ({ ...e, saving: false, saveStatus: "error" }));
    }
  }

  async function saveCore() {
    const submittedContent = coreEditor.content;
    setCoreEditor((e) => ({ ...e, saving: true, saveStatus: "" }));
    try {
      const r = await fetch("/api/memory/core", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: submittedContent }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        if (detail.error) alert(detail.error);
      }
      if (r.ok) await fetchCore(submittedContent);
      setCoreEditor((e) => ({
        ...e,
        saving: false,
        saveStatus: coreSaveStatus(e.content, submittedContent, r.ok),
      }));
    } catch {
      setCoreEditor((e) => ({ ...e, saving: false, saveStatus: "error" }));
    }
  }

  function openTimelineDay(date: string) {
    setSelectedDate(date);
    setTimelineContent("");
    fetch(`/api/memory/timeline/${date}`)
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
        <Link className={styles.settingsLink} href="/settings/memory">
          <Settings2 size={15} />
          {text("Memory settings", "Memory 设置")}
        </Link>
      </div>
      )}

      {/* Body — single grid: tabBar (row 1 col 1) + tree (row 2 col 1) +
          editor/placeholder (col 2 spans both rows so it starts right at
          the topbar, ignoring the tab bar's height). */}
      <div className={styles.body}>
        <div className={styles.layout}>
          <nav className={styles.tabBar} aria-label={text("Memory sections", "Memory 分区")}>
            <TabButton active={tab === "topics"} onClick={() => setTab("topics")} icon={<FileTextIcon size={13} />}>{text("Topics", "主题")}</TabButton>
            <TabButton active={tab === "timeline"} onClick={() => setTab("timeline")} icon={<ClockIcon className={styles.fileIcon} />}>{text("Timeline", "时间线")}</TabButton>
            <TabButton active={tab === "recent"} onClick={() => setTab("recent")} icon={<ActivityIcon size={13} />}>{text("Recent", "最近")}</TabButton>
            <TabButton active={tab === "core"} onClick={() => setTab("core")} icon={<SparklesIcon size={13} />}>{text("Core", "核心")}</TabButton>
          </nav>

          {/* ── Topics ── */}
          {tab === "topics" && (
            <>
              <div className={styles.tree}>
              {/* Search + refresh */}
              <div className={styles.treeToolbar}>
                {query === undefined && (
                <SearchInput
                  className="flex-1"
                  placeholder={text("Search topics...", "搜索主题...")}
                  value={search}
                  onChange={setSearch}
                />
                )}
                <button
                  className={styles.iconBtn}
                  onClick={fetchTopics}
                  title={t("sidebar.refresh")}
                  onMouseEnter={() => refreshIconRef.current?.startAnimation?.()}
                  onMouseLeave={() => refreshIconRef.current?.stopAnimation?.()}
                >
                  <RefreshCwIcon ref={refreshIconRef} size={13} />
                </button>
              </div>
              {topicsLoading ? <LoadingSkeleton /> : filteredTopics.length === 0 ? (
                <EmptyState
                  icon="doc"
                  text={searchValue ? text("No matches", "没有匹配结果") : text("No topics yet", "还没有主题")}
                  sub={searchValue ? text("Try a different query", "换一个查询试试") : text("Conversations are written here in the background", "对话会在后台写入这里")}
                />
              ) : (
                <div className={styles.treeContent}>
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
                </div>
              )}
            </div>
              <div className={styles.rightPane}>
                {selectedTopic ? (
                  <EditorPanel
                    title={selectedTopic.title || selectedTopic.path}
                    badge={selectedTopic.type ? <TypeBadge type={selectedTopic.type} /> : null}
                    meta={[
                      selectedTopic.path,
                      formatSize(selectedTopic.size),
                      text(`Modified ${formatDate(selectedTopic.mtime, locale)}`, `修改于 ${formatDate(selectedTopic.mtime, locale)}`),
                    ]}
                    state={topicEditor}
                    onChange={(c) => setTopicEditor((e) => ({ ...e, content: c, saveStatus: "" }))}
                    onSave={saveTopic}
                    onDelete={deleteTopic}
                    onViewMode={(m) => setTopicEditor((e) => ({ ...e, viewMode: m }))}
                    onPreviewClick={handlePreviewClick}
                  />
                ) : !topicsLoading && topicPages.length === 0 && !searchValue ? (
                  <div className={styles.emptyPanel}>
                    <FileTextIcon size={25} />
                    <h2>{text("No topics yet", "还没有主题")}</h2>
                    <p>{text(
                      "Durable memory created from conversations and the background writer will appear here. Core rules are managed separately.",
                      "由对话和后台写入器生成的持久记忆会显示在这里；Core 规则单独管理。",
                    )}</p>
                    <Link className={styles.emptySettingsLink} href="/settings/memory">
                      {text("Open Memory settings", "打开 Memory 设置")}
                    </Link>
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
              <div className={styles.tree}>
              {timelineLoading ? <LoadingSkeleton /> : timelineGroups.length === 0 ? (
                <EmptyState
                  icon="clock"
                  text={text("No timeline yet", "还没有时间线")}
                  sub={text("Rebuilt from topics after every write", "每次写入后从主题重建")}
                />
              ) : (
                <div className={styles.timelineTree}>
                  {timelineGroups.map((year, yearIndex) => (
                    <details key={year.year} className={styles.timelineYear} open={yearIndex === 0}>
                      <summary className={`${styles.timelineYearSummary} ${styles.sidebarRow}`}>
                        <DisclosureChevron className={styles.timelineChevron} />
                        <span>{year.year}</span>
                        <span className={styles.sidebarCount}>{year.entryCount}</span>
                      </summary>
                      <div className={styles.timelinePeriodEntries}>
                        {year.entries.map((entry) => (
                          <button key={entry.date} className={`${styles.timelinePeriod} ${styles.sidebarRow} ${selectedDate === entry.date ? `${styles.timelinePeriodActive} ${styles.sidebarRowActive}` : ""}`} onClick={() => openTimelineDay(entry.date)}>
                            <span>{entry.label}</span><span className={styles.fileMeta}>{formatSize(entry.size)}</span>
                          </button>
                        ))}
                      </div>
                      {year.months.map((month, monthIndex) => (
                        <details key={month.key} className={styles.timelineMonth} open={yearIndex === 0 && monthIndex === 0}>
                          <summary className={`${styles.timelineMonthSummary} ${styles.sidebarRow}`}>
                            <DisclosureChevron className={styles.timelineChevron} />
                            <span>{month.label}</span>
                            <span className={styles.sidebarCount}>{month.entries.length + month.days.length}</span>
                          </summary>
                          <div className={styles.timelineDays}>
                            {month.entries.map((entry) => (
                              <button key={entry.date} className={`${styles.timelinePeriod} ${styles.sidebarRow} ${selectedDate === entry.date ? `${styles.timelinePeriodActive} ${styles.sidebarRowActive}` : ""}`} onClick={() => openTimelineDay(entry.date)}>
                                <span>{entry.label}</span><span className={styles.fileMeta}>{formatSize(entry.size)}</span>
                              </button>
                            ))}
                            {month.days.map((day) => (
                              <button
                                key={day.date}
                                className={`${styles.timelineDay} ${styles.sidebarRow} ${selectedDate === day.date ? `${styles.timelineDayActive} ${styles.sidebarRowActive}` : ""}`}
                                onClick={() => openTimelineDay(day.date)}
                              >
                                <span className={styles.timelineDayNumber}>{day.dayLabel}</span>
                                <span className={styles.timelineWeekday}>{day.weekday}</span>
                                <span className={styles.fileMeta}>{formatSize(day.size)}</span>
                              </button>
                            ))}
                          </div>
                        </details>
                      ))}
                    </details>
                  ))}
                </div>
              )}
            </div>
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
                    <div className={styles.preview}>
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
              <div className={`${styles.tree} ${styles.treeEmpty}`} />
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
                    <div className={styles.preview}>
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
              <div className={`${styles.tree} ${styles.treeEmpty}`} />
              <div className={styles.rightPane}>
                <div className={styles.coreControls}>
                  <div className={styles.coreViewSwitch} role="group" aria-label={text("Core view", "Core 视图")}>
                    <button
                      className={coreView === "injected" ? styles.coreViewButtonActive : styles.coreViewButton}
                      aria-pressed={coreView === "injected"}
                      onClick={() => setCoreView("injected")}
                    >
                      {text("Prompt preview", "提示词预览")}
                    </button>
                    <button
                      className={coreView === "records" ? styles.coreViewButtonActive : styles.coreViewButton}
                      aria-pressed={coreView === "records"}
                      onClick={() => setCoreView("records")}
                    >
                      {text("Source records", "源记录")}
                    </button>
                  </div>
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
                        aria-label={text("Core token budget usage", "Core token 预算使用量")}
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
                  <EditorPanel
                    title={text("Core source records", "Core 源记录")}
                    meta={["topics/core.md"]}
                    state={coreEditor}
                    onChange={(c) => setCoreEditor((e) => ({ ...e, content: c, saveStatus: "" }))}
                    onSave={saveCore}
                    onViewMode={(m) => setCoreEditor((e) => ({ ...e, viewMode: m }))}
                  />
                ) : (
                  <div className={styles.editor}>
                    <div className={styles.editorHeader}>
                      <div className={styles.editorHeaderLeft}>
                        <SparklesIcon size={14} />
                        <span className={styles.editorTitle}>{text("Prompt preview", "提示词预览")}</span>
                      </div>
                    </div>
                    <div className={styles.preview}>
                      <div
                        className={styles.markdown}
                        dangerouslySetInnerHTML={{ __html: coreInjected ? renderMarkdown(coreInjected) : `<em>${text("No Core memory is currently injected", "当前没有注入 Core 记忆")}</em>` }}
                      />
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

  if (embedded) return view;
  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      {view}
    </div>
  );
}
