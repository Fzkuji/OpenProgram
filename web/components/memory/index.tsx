"use client";

/**
 * Memory page — wiki + journal + core memory editor.
 *
 * Originally a single 708-line file; now split into:
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
  FileTextIcon,
  RefreshCwIcon,
  SparklesIcon,
  SquarePenIcon,
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
  JournalEntry,
  Tab,
  WikiPage,
} from "./types";
import styles from "./memory-page.module.css";
import { SearchInput } from "@/components/ui/search-input";

export function MemoryPage() {
  const { t, text, locale } = useTranslation();
  const refreshIconRef = useRef<AnimatedNavIconHandle>(null);
  const [tab, setTab] = useState<Tab>("wiki");

  // Wiki state
  const [wikiPages, setWikiPages] = useState<WikiPage[]>([]);
  const [systemPages, setSystemPages] = useState<WikiPage[]>([]);
  const [wikiLoading, setWikiLoading] = useState(true);
  const [selectedWiki, setSelectedWiki] = useState<WikiPage | null>(null);
  const [wikiEditor, setWikiEditor] = useState<EditorState>({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [showSystem, setShowSystem] = useState(false);
  const [search, setSearch] = useState("");

  // Journal state
  const [journalEntries, setJournalEntries] = useState<JournalEntry[]>([]);
  const [journalLoading, setJournalLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [journalContent, setJournalContent] = useState("");

  // Core state
  const [coreEditor, setCoreEditor] = useState<EditorState>({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
  const [coreMeta, setCoreMeta] = useState<{ size: number; mtime: number } | null>(null);
  const [coreLoading, setCoreLoading] = useState(true);

  const fetchWikiPages = useCallback(() => {
    setWikiLoading(true);
    Promise.all([
      fetch("/api/memory/wiki").then((r) => r.json()),
      fetch("/api/memory/wiki-system").then((r) => r.json()),
    ]).then(([pages, sys]) => {
      const content = Array.isArray(pages) ? pages : [];
      setWikiPages(content);
      setSystemPages(Array.isArray(sys) ? sys : []);
      const folders = new Set<string>();
      for (const p of content) {
        const parts = p.path.split("/");
        if (parts.length > 1) folders.add(parts[0]);
      }
      setExpandedFolders(folders);
      setWikiLoading(false);
    }).catch(() => setWikiLoading(false));
  }, []);

  useEffect(() => { fetchWikiPages(); }, [fetchWikiPages]);

  useEffect(() => {
    fetch("/api/memory/journal")
      .then((r) => r.json())
      .then((data) => { setJournalEntries(Array.isArray(data) ? data : []); setJournalLoading(false); })
      .catch(() => setJournalLoading(false));
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

  const openWikiPage = useCallback((page: WikiPage) => {
    setSelectedWiki(page);
    setWikiEditor({ content: "", saving: false, saveStatus: "", viewMode: "edit" });
    fetch(`/api/memory/wiki/${page.path}`)
      .then((r) => r.json())
      .then((data) => setWikiEditor((e) => ({ ...e, content: data.content ?? "" })));
  }, []);

  // Resolve wikilink target to a known page (match by slug or title)
  const resolveWikilink = useCallback((target: string): WikiPage | null => {
    const t = target.toLowerCase().trim();
    const all = [...wikiPages, ...systemPages];
    return (
      all.find((p) => p.path.toLowerCase() === t + ".md") ||
      all.find((p) => p.path.toLowerCase().endsWith("/" + t + ".md")) ||
      all.find((p) => (p.title || "").toLowerCase() === t) ||
      null
    );
  }, [wikiPages, systemPages]);

  async function deleteWikiPage() {
    if (!selectedWiki) return;
    if (!confirm(text(
      `Delete "${selectedWiki.title || selectedWiki.path}"? This cannot be undone.`,
      `删除“${selectedWiki.title || selectedWiki.path}”？此操作无法撤销。`,
    ))) return;
    const r = await fetch(`/api/memory/wiki/${selectedWiki.path}`, { method: "DELETE" });
    if (r.ok) {
      setSelectedWiki(null);
      fetchWikiPages();
    } else {
      alert(text("Delete failed", "删除失败"));
    }
  }

  async function saveWikiPage() {
    if (!selectedWiki) return;
    setWikiEditor((e) => ({ ...e, saving: true, saveStatus: "" }));
    try {
      const r = await fetch(`/api/memory/wiki/${selectedWiki.path}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: wikiEditor.content }),
      });
      setWikiEditor((e) => ({ ...e, saving: false, saveStatus: r.ok ? "saved" : "error" }));
    } catch {
      setWikiEditor((e) => ({ ...e, saving: false, saveStatus: "error" }));
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
      setCoreEditor((e) => ({ ...e, saving: false, saveStatus: r.ok ? "saved" : "error" }));
    } catch {
      setCoreEditor((e) => ({ ...e, saving: false, saveStatus: "error" }));
    }
  }

  function openJournal(date: string) {
    setSelectedDate(date);
    setJournalContent("");
    fetch(`/api/memory/journal/${date}`)
      .then((r) => r.json())
      .then((data) => setJournalContent(data.content ?? ""));
  }

  function toggleFolder(folder: string) {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  }

  const filteredWiki = useMemo(() => {
    if (!search.trim()) return wikiPages;
    const q = search.toLowerCase();
    return wikiPages.filter(
      (p) => p.path.toLowerCase().includes(q) || (p.title || "").toLowerCase().includes(q) || (p.type || "").toLowerCase().includes(q)
    );
  }, [wikiPages, search]);

  const wikiGroups = groupByFolder(filteredWiki);
  const filteredSystem = useMemo(
    () => systemPages.filter((p) => !search.trim() || p.path.toLowerCase().includes(search.toLowerCase()) || (p.title || "").toLowerCase().includes(search.toLowerCase())),
    [systemPages, search]
  );

  // Wikilink click handler: delegate from preview container
  function handlePreviewClick(e: React.MouseEvent) {
    const t = (e.target as HTMLElement).closest("a.wikilink") as HTMLAnchorElement | null;
    if (!t) return;
    e.preventDefault();
    const target = t.getAttribute("data-target");
    if (!target) return;
    const page = resolveWikilink(target);
    if (page) openWikiPage(page);
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
            <TabButton active={tab === "wiki"} onClick={() => setTab("wiki")} icon={<FileTextIcon size={14} />}>{text("Wiki", "维基")}</TabButton>
            <TabButton active={tab === "journal"} onClick={() => setTab("journal")} icon={<SquarePenIcon size={14} />}>{text("Journal", "日志")}</TabButton>
            <TabButton active={tab === "core"} onClick={() => setTab("core")} icon={<SparklesIcon size={14} />}>{text("Core", "核心")}</TabButton>
          </div>

          {/* ── Wiki ── */}
          {tab === "wiki" && (
            <>
              <div className={styles.tree}>
              {/* Search + refresh */}
              <div className={styles.treeToolbar}>
                <SearchInput
                  className="flex-1"
                  placeholder={text("Search pages...", "搜索页面...")}
                  value={search}
                  onChange={setSearch}
                />
                <button
                  className={styles.iconBtn}
                  onClick={fetchWikiPages}
                  title={t("sidebar.refresh")}
                  onMouseEnter={() => refreshIconRef.current?.startAnimation?.()}
                  onMouseLeave={() => refreshIconRef.current?.stopAnimation?.()}
                >
                  <RefreshCwIcon ref={refreshIconRef} size={13} />
                </button>
              </div>
              {wikiLoading ? <LoadingSkeleton /> : filteredWiki.length === 0 && filteredSystem.length === 0 ? (
                <EmptyState
                  icon="doc"
                  text={search ? text("No matches", "没有匹配结果") : text("No wiki pages found", "没有找到维基页面")}
                  sub={search ? text("Try a different query", "换一个查询试试") : text("The agent will populate this as it runs", "Agent 运行后会填充这里")}
                />
              ) : (
                <div className={styles.treeContent}>
                  {Array.from(wikiGroups.entries()).map(([folder, pages]) => (
                    <TreeGroup
                      key={folder}
                      folder={folder}
                      pages={pages}
                      expanded={expandedFolders}
                      onToggle={toggleFolder}
                      selected={selectedWiki}
                      onSelect={openWikiPage}
                      forceOpen={!!search}
                    />
                  ))}
                  {/* System / governance section */}
                  {filteredSystem.length > 0 && (
                    <div className={styles.sysSection}>
                      <button className={styles.folderRow} onClick={() => setShowSystem((v) => !v)}>
                        <svg viewBox="0 0 10 10" fill="currentColor" width="8" height="8" className={`${styles.chevron} ${(showSystem || search) ? styles.chevronOpen : ""}`}>
                          <path d="M2 3l3 4 3-4H2z"/>
                        </svg>
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" width="12" height="12">
                          <circle cx="8" cy="8" r="6"/>
                          <path d="M8 5v3M8 10.5v.5" strokeLinecap="round"/>
                        </svg>
                        <span className={styles.folderName}>{text("System", "系统")}</span>
                        <span className={styles.folderCount}>{filteredSystem.length}</span>
                      </button>
                      {(showSystem || search) && filteredSystem.map((page) => (
                        <button
                          key={page.path}
                          className={`${styles.fileRow} ${selectedWiki?.path === page.path ? styles.fileRowActive : ""}`}
                          onClick={() => openWikiPage(page)}
                        >
                          <DocIcon className={styles.fileIcon} />
                          <span className={styles.fileName}>{page.title}</span>
                          <span className={styles.fileMeta}>{formatDate(page.mtime, locale)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
              <div className={styles.rightPane}>
                {selectedWiki ? (
                  <EditorPanel
                    title={selectedWiki.title || selectedWiki.path}
                    badge={selectedWiki.type ? <TypeBadge type={selectedWiki.type} /> : null}
                    meta={[
                      selectedWiki.path,
                      formatSize(selectedWiki.size),
                      text(`Modified ${formatDate(selectedWiki.mtime, locale)}`, `修改于 ${formatDate(selectedWiki.mtime, locale)}`),
                    ]}
                    state={wikiEditor}
                    onChange={(c) => setWikiEditor((e) => ({ ...e, content: c, saveStatus: "" }))}
                    onSave={saveWikiPage}
                    onDelete={deleteWikiPage}
                    onViewMode={(m) => setWikiEditor((e) => ({ ...e, viewMode: m }))}
                    onPreviewClick={handlePreviewClick}
                  />
                ) : (
                  <Placeholder icon="doc" text={text("Select a page to view or edit", "选择一个页面进行查看或编辑")} />
                )}
              </div>
            </>
          )}

          {/* ── Journal ── */}
          {tab === "journal" && (
            <>
              <div className={styles.tree}>
              {journalLoading ? <LoadingSkeleton /> : journalEntries.length === 0 ? (
                <EmptyState
                  icon="clock"
                  text={text("No journal memory", "没有日志记忆")}
                  sub={text("Context commits appear here after sessions", "会话后的上下文提交会显示在这里")}
                />
              ) : (
                <div className={styles.treeContent}>
                  {journalEntries.map((entry) => (
                    <button
                      key={entry.date}
                      className={`${styles.sessionRow} ${selectedDate === entry.date ? styles.fileRowActive : ""}`}
                      onClick={() => openJournal(entry.date)}
                    >
                      <ClockIcon className={styles.fileIcon} />
                      <div className={styles.sessionInfo}>
                        <span className={styles.fileName}>{entry.date}</span>
                        <span className={styles.fileMeta}>{formatSize(entry.size)}</span>
                      </div>
                      <span className={styles.fileMeta}>{formatDate(entry.mtime, locale)}</span>
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
                        <span className={styles.fileMeta}>{journalContent.length} {text("chars", "字符")}</span>
                      </div>
                    </div>
                    <div className={styles.preview}>
                      <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: journalContent ? renderMarkdown(journalContent) : `<em>${text("Loading...", "加载中...")}</em>` }} />
                    </div>
                  </div>
                ) : (
                  <Placeholder icon="clock" text={text("Select a session to view its memory", "选择一个会话查看它的记忆")} />
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
