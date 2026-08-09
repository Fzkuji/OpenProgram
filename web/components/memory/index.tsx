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

import { renderMarkdown } from "./markdown";
import { formatDate, formatSize, groupByFolder } from "./format";
import { ClockIcon, DocIcon, TypeBadge } from "./icons";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  ActivityIcon,
  FileTextIcon,
  RefreshCwIcon,
  SparklesIcon,
} from "@/components/animated-icons";
import {
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

export function MemoryPage() {
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

  // Timeline state
  const [timelineDays, setTimelineDays] = useState<TimelineDay[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [timelineContent, setTimelineContent] = useState("");

  // Recent state
  const [recentEvents, setRecentEvents] = useState<RecentEvent[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);

  // Core state
  const [coreEditor, setCoreEditor] = useState<EditorState>({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
  const [coreMeta, setCoreMeta] = useState<{ size: number; mtime: number } | null>(null);
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
      .then((r) => r.json())
      .then((data) => { setRecentEvents(Array.isArray(data) ? data : []); setRecentLoading(false); })
      .catch(() => setRecentLoading(false));
  }, []);

  useEffect(() => {
    fetch("/api/memory/core")
      .then((r) => r.json())
      .then((data) => {
        setCoreEditor((e) => ({ ...e, content: data.content ?? "" }));
        setCoreMeta({ size: data.size ?? 0, mtime: data.mtime ?? 0 });
        setCoreLoading(false);
      })
      .catch(() => setCoreLoading(false));
  }, []);

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
      alert(text("Delete failed", "删除失败"));
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
    setCoreEditor((e) => ({ ...e, saving: true, saveStatus: "" }));
    try {
      const r = await fetch("/api/memory/core", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: coreEditor.content }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        if (detail.error) alert(detail.error);
      }
      setCoreEditor((e) => ({ ...e, saving: false, saveStatus: r.ok ? "saved" : "error" }));
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
    if (!search.trim()) return topicPages;
    const q = search.toLowerCase();
    return topicPages.filter(
      (p) => p.path.toLowerCase().includes(q) || (p.title || "").toLowerCase().includes(q) || (p.type || "").toLowerCase().includes(q)
    );
  }, [topicPages, search]);

  const topicGroups = groupByFolder(filteredTopics);

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

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={styles.view}>
      {/* Header — same pattern as functions page */}
      <div className={styles.topbar}>
        <span className={styles.title}>{t("nav.memory")}</span>
      </div>

      {/* Body — single grid: tabBar (row 1 col 1) + tree (row 2 col 1) +
          editor/placeholder (col 2 spans both rows so it starts right at
          the topbar, ignoring the tab bar's height). */}
      <div className={styles.body}>
        <div className={styles.layout}>
          <div className={styles.tabBar}>
            <TabButton active={tab === "topics"} onClick={() => setTab("topics")} icon={<FileTextIcon size={13} />}>{text("Topics", "主题")}</TabButton>
            <TabButton active={tab === "timeline"} onClick={() => setTab("timeline")} icon={<ClockIcon className={styles.fileIcon} />}>{text("Timeline", "时间线")}</TabButton>
            <TabButton active={tab === "recent"} onClick={() => setTab("recent")} icon={<ActivityIcon size={13} />}>{text("Recent", "最近")}</TabButton>
            <TabButton active={tab === "core"} onClick={() => setTab("core")} icon={<SparklesIcon size={13} />}>{text("Core", "核心")}</TabButton>
          </div>

          {/* ── Topics ── */}
          {tab === "topics" && (
            <>
              <div className={styles.tree}>
              {/* Search + refresh */}
              <div className={styles.treeToolbar}>
                <SearchInput
                  className="flex-1"
                  placeholder={text("Search topics...", "搜索主题...")}
                  value={search}
                  onChange={setSearch}
                />
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
                  text={search ? text("No matches", "没有匹配结果") : text("No topics yet", "还没有主题")}
                  sub={search ? text("Try a different query", "换一个查询试试") : text("Conversations are written here in the background", "对话会在后台写入这里")}
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
                      forceOpen={!!search}
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
              {timelineLoading ? <LoadingSkeleton /> : timelineDays.length === 0 ? (
                <EmptyState
                  icon="clock"
                  text={text("No timeline yet", "还没有时间线")}
                  sub={text("Rebuilt from topics after every write", "每次写入后从主题重建")}
                />
              ) : (
                <div className={styles.treeContent}>
                  {timelineDays.map((day) => (
                    <button
                      key={day.date}
                      className={`${styles.sessionRow} ${selectedDate === day.date ? styles.fileRowActive : ""}`}
                      onClick={() => openTimelineDay(day.date)}
                    >
                      <ClockIcon className={styles.fileIcon} />
                      <div className={styles.sessionInfo}>
                        <span className={styles.fileName}>{day.date}</span>
                        <span className={styles.fileMeta}>{formatSize(day.size)}</span>
                      </div>
                      <span className={styles.fileMeta}>{formatDate(day.mtime, locale)}</span>
                    </button>
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
              <div className={styles.tree}>
                <div className={styles.coreSidebar}>
                  <div className={styles.coreInfoIcon}>
                    <ActivityIcon size={22} />
                  </div>
                  <div className={styles.coreInfoTitle}>{text("Recent Memory", "最近记忆")}</div>
                  <div className={styles.coreInfoDesc}>
                    {text(
                      "The latest paragraphs written, newest first. Derived from topics after every write — nothing is stored here of its own.",
                      "最近写入的段落，新的在前。每次写入后从主题派生，本身不存储任何东西。",
                    )}
                  </div>
                  <div className={styles.coreInfoMeta}>
                    <span>{recentEvents.length} {text("entries", "条")}</span>
                  </div>
                </div>
              </div>
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
                        <span className={styles.fileMeta}>{recentEvents.length} {text("entries", "条")}</span>
                      </div>
                    </div>
                    <div className={styles.preview}>
                      <div className={styles.markdown}>
                        {recentEvents.map((event) => (
                          <div key={event.memory_id} style={{ marginBottom: "1rem" }}>
                            <div style={{ fontSize: "0.75rem", opacity: 0.6 }}>
                              {[event.when, event.topic_path].filter(Boolean).join(" · ")}
                            </div>
                            <div>{event.content}</div>
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
              <div className={styles.tree}>
              <div className={styles.coreSidebar}>
                <div className={styles.coreInfoIcon}>
                  <svg viewBox="0 0 256 256" fill="currentColor">
                    <path d="M234.29,114.85l-45,38.83L203,211.75a16.4,16.4,0,0,1-24.5,17.82L128,198.49,77.47,229.57A16.4,16.4,0,0,1,53,211.75l13.76-58.07-45-38.83A16.46,16.46,0,0,1,31.08,86l59-4.76,22.76-55.08a16.36,16.36,0,0,1,30.27,0l22.75,55.08,59,4.76a16.46,16.46,0,0,1,9.37,28.86Z"/>
                  </svg>
                </div>
                <div className={styles.coreInfoTitle}>{text("Core Memory", "核心记忆")}</div>
                <div className={styles.coreInfoDesc}>
                  {text(
                    "Injected into every system prompt. Keep it under 2 KB. Use concise facts about the user and current context.",
                    "会注入到每次系统提示词中。请控制在 2 KB 以内，记录关于用户和当前上下文的简洁事实。",
                  )}
                </div>
                {coreMeta && (
                  <div className={styles.coreInfoMeta}>
                    <span className={coreMeta.size > 2048 ? styles.metaWarn : styles.metaOk}>
                      {formatSize(coreMeta.size)}
                      {coreMeta.size > 2048
                        ? text(" exceeds 2 KB", " 超过 2 KB")
                        : " / 2 KB"}
                    </span>
                    {coreMeta.mtime > 0 && (
                      <span>
                        {text(`Modified ${formatDate(coreMeta.mtime, locale)}`, `修改于 ${formatDate(coreMeta.mtime, locale)}`)}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
              <div className={styles.rightPane}>
                {coreLoading ? (
                  <LoadingSkeleton />
                ) : (
                  <EditorPanel
                    title="core.md"
                    meta={[]}
                    state={coreEditor}
                    onChange={(c) => setCoreEditor((e) => ({ ...e, content: c, saveStatus: "" }))}
                    onSave={saveCore}
                    onViewMode={(m) => setCoreEditor((e) => ({ ...e, viewMode: m }))}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
    </div>
  );
}
