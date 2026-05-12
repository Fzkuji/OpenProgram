"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { marked } from "marked";
import styles from "./memory-page.module.css";

// Parse frontmatter from raw markdown: returns { frontmatter, body }
function parseFrontmatter(raw: string): { frontmatter: Record<string, string>; body: string } {
  if (!raw.startsWith("---\n") && !raw.startsWith("---\r\n")) {
    return { frontmatter: {}, body: raw };
  }
  const end = raw.indexOf("\n---", 4);
  if (end < 0) return { frontmatter: {}, body: raw };
  const fmText = raw.slice(4, end);
  const body = raw.slice(end + 4).replace(/^\s*\n/, "");
  const fm: Record<string, string> = {};
  for (const line of fmText.split("\n")) {
    const m = line.match(/^([\w-]+):\s*(.*)$/);
    if (m) fm[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return { frontmatter: fm, body };
}

// Convert [[wikilink]] → clickable anchor before marked renders
function expandWikilinks(md: string): string {
  return md.replace(/\[\[([^\]|]+)(\|([^\]]+))?\]\]/g, (_m, target, _p, alias) => {
    const label = alias || target;
    const slug = String(target).trim();
    return `<a class="wikilink" data-target="${slug}">${label}</a>`;
  });
}

function renderMarkdown(md: string): string {
  try {
    return marked.parse(expandWikilinks(md), { breaks: true, async: false }) as string;
  } catch {
    return `<pre>${md.replace(/</g, "&lt;")}</pre>`;
  }
}

interface WikiPage {
  path: string;
  title: string;
  type: string;
  size: number;
  mtime: number;
}

interface JournalEntry {
  date: string;
  size: number;
  mtime: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(mtime: number): string {
  const d = new Date(mtime * 1000);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)}d ago`;
  return d.toLocaleDateString();
}

const TYPE_COLORS: Record<string, string> = {
  concept: "#7c6fcd",
  entity: "#3b82f6",
  event: "#f59e0b",
  relation: "#10b981",
  procedure: "#06b6d4",
  user: "#ec4899",
  source: "#f97316",
  query: "#84cc16",
  synthesis: "#a855f7",
  attribute: "#6b7280",
  meta: "#ef4444",
  index: "#8b5cf6",
};

function TypeBadge({ type }: { type: string }) {
  if (!type) return null;
  const color = TYPE_COLORS[type.toLowerCase()] ?? "#6b7280";
  return (
    <span className={styles.typeBadge} style={{ background: color + "22", color, borderColor: color + "44" }}>
      {type}
    </span>
  );
}

function DocIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none">
      <rect x="2" y="1" width="10" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M4.5 5h5M4.5 7.5h5M4.5 10h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  );
}

function ClockIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M8 5v3l2 1.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  );
}

// Group pages by top-level folder prefix
function groupByFolder(pages: WikiPage[]): Map<string, WikiPage[]> {
  const groups = new Map<string, WikiPage[]>();
  for (const p of pages) {
    const parts = p.path.split("/");
    const folder = parts.length > 1 ? parts[0] : "";
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder)!.push(p);
  }
  return groups;
}

type Tab = "wiki" | "journal" | "core";

interface EditorState {
  content: string;
  saving: boolean;
  saveStatus: "" | "saved" | "error";
  viewMode: "edit" | "preview";
}

export function MemoryPage() {
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
    if (!confirm(`Delete "${selectedWiki.title || selectedWiki.path}"? This cannot be undone.`)) return;
    const r = await fetch(`/api/memory/wiki/${selectedWiki.path}`, { method: "DELETE" });
    if (r.ok) {
      setSelectedWiki(null);
      fetchWikiPages();
    } else {
      alert("Delete failed");
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
  const totalPages = wikiPages.length;
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
      {/* Header — same pattern as programs page */}
      <div className={styles.topbar}>
        <span className={styles.title}>Memory</span>
        <div className={styles.headerStats}>
          {tab === "wiki" && !wikiLoading && <span className={styles.statChip}>{totalPages} pages</span>}
          {tab === "journal" && !journalLoading && <span className={styles.statChip}>{journalEntries.length} sessions</span>}
          {tab === "core" && coreMeta && <span className={styles.statChip}>{formatSize(coreMeta.size)}</span>}
        </div>
      </div>

      {/* Tab bar */}
      <div className={styles.tabBar}>
        <TabButton active={tab === "wiki"} onClick={() => setTab("wiki")} icon={
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" width="13" height="13">
            <path d="M3 2.5A1.5 1.5 0 0 1 4.5 1h5L12 3.5V14a1.5 1.5 0 0 1-1.5 1.5h-6A1.5 1.5 0 0 1 3 14V2.5z"/>
            <path d="M9.5 1v2.5H12M5 7h5M5 9.5h5M5 12h3"/>
          </svg>
        }>Wiki</TabButton>
        <TabButton active={tab === "journal"} onClick={() => setTab("journal")} icon={
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" width="13" height="13">
            <circle cx="8" cy="8" r="6"/>
            <path d="M8 5v3l2 1.5"/>
          </svg>
        }>Journal</TabButton>
        <TabButton active={tab === "core"} onClick={() => setTab("core")} icon={
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" width="13" height="13">
            <circle cx="8" cy="8" r="2.5"/>
            <path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.2 3.2l1.1 1.1M11.7 11.7l1.1 1.1M12.8 3.2l-1.1 1.1M4.3 11.7l-1.1 1.1"/>
          </svg>
        }>Core</TabButton>
      </div>

      {/* Body */}
      <div className={styles.body}>
        {/* ── Wiki ── */}
        {tab === "wiki" && (
          <div className={styles.layout}>
            <div className={styles.tree}>
              {/* Search + refresh */}
              <div className={styles.treeToolbar}>
                <div className={styles.searchWrap}>
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" width="12" height="12" strokeLinecap="round">
                    <circle cx="7" cy="7" r="4.5"/>
                    <path d="M10.5 10.5L14 14"/>
                  </svg>
                  <input
                    type="text"
                    className={styles.searchInput}
                    placeholder="Search pages…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  {search && (
                    <button className={styles.searchClear} onClick={() => setSearch("")} title="Clear">✕</button>
                  )}
                </div>
                <button className={styles.iconBtn} onClick={fetchWikiPages} title="Refresh">
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" width="12" height="12" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 8a6 6 0 1 1-1.76-4.24"/>
                    <path d="M14 2v3h-3"/>
                  </svg>
                </button>
              </div>
              {wikiLoading ? <LoadingSkeleton /> : filteredWiki.length === 0 && filteredSystem.length === 0 ? (
                <EmptyState icon="doc" text={search ? "No matches" : "No wiki pages found"} sub={search ? "Try a different query" : "The agent will populate this as it runs"} />
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
                        <span className={styles.folderName}>System</span>
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
                          <span className={styles.fileMeta}>{formatDate(page.mtime)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            {selectedWiki ? (
              <EditorPanel
                title={selectedWiki.title || selectedWiki.path}
                badge={selectedWiki.type ? <TypeBadge type={selectedWiki.type} /> : null}
                meta={[selectedWiki.path, formatSize(selectedWiki.size), `Modified ${formatDate(selectedWiki.mtime)}`]}
                state={wikiEditor}
                onChange={(c) => setWikiEditor((e) => ({ ...e, content: c, saveStatus: "" }))}
                onSave={saveWikiPage}
                onDelete={deleteWikiPage}
                onViewMode={(m) => setWikiEditor((e) => ({ ...e, viewMode: m }))}
                onPreviewClick={handlePreviewClick}
              />
            ) : (
              <Placeholder icon="doc" text="Select a page to view or edit" />
            )}
          </div>
        )}

        {/* ── Journal ── */}
        {tab === "journal" && (
          <div className={styles.layout}>
            <div className={styles.tree}>
              {journalLoading ? <LoadingSkeleton /> : journalEntries.length === 0 ? (
                <EmptyState icon="clock" text="No journal memory" sub="Context snapshots appear here after sessions" />
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
                      <span className={styles.fileMeta}>{formatDate(entry.mtime)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {selectedDate ? (
              <div className={styles.editor}>
                <div className={styles.editorHeader}>
                  <div className={styles.editorHeaderLeft}>
                    <ClockIcon className={styles.fileIcon} />
                    <span className={styles.editorTitle}>{selectedDate}</span>
                  </div>
                  <div className={styles.editorActions}>
                    <span className={styles.fileMeta}>{journalContent.length} chars</span>
                  </div>
                </div>
                <div className={styles.preview}>
                  <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: journalContent ? renderMarkdown(journalContent) : "<em>Loading…</em>" }} />
                </div>
              </div>
            ) : (
              <Placeholder icon="clock" text="Select a session to view its memory" />
            )}
          </div>
        )}

        {/* ── Core ── */}
        {tab === "core" && (
          <div className={styles.coreLayout}>
            <div className={styles.coreInfo}>
              <div className={styles.coreInfoIcon}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="3.5"/>
                  <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M6.4 17L5 18.4"/>
                </svg>
              </div>
              <div>
                <div className={styles.coreInfoTitle}>Core Memory</div>
                <div className={styles.coreInfoDesc}>
                  Injected into every system prompt. Keep it under 2 KB — concise, high-signal facts about the user and current context.
                </div>
                {coreMeta && (
                  <div className={styles.coreInfoMeta}>
                    <span className={coreMeta.size > 2048 ? styles.metaWarn : styles.metaOk}>
                      {formatSize(coreMeta.size)}{coreMeta.size > 2048 ? " ⚠ exceeds 2 KB" : " / 2 KB"}
                    </span>
                    {coreMeta.mtime > 0 && <span>· Modified {formatDate(coreMeta.mtime)}</span>}
                  </div>
                )}
              </div>
            </div>
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
        )}
      </div>
    </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button className={`${styles.tabBtn} ${active ? styles.tabBtnActive : ""}`} onClick={onClick}>
      {icon}
      {children}
    </button>
  );
}

function TreeGroup({ folder, pages, expanded, onToggle, selected, onSelect, forceOpen }: {
  folder: string;
  pages: WikiPage[];
  expanded: Set<string>;
  onToggle: (f: string) => void;
  selected: WikiPage | null;
  forceOpen?: boolean;
  onSelect: (p: WikiPage) => void;
}) {
  const isExpanded = !folder || expanded.has(folder) || forceOpen;
  return (
    <div className={styles.treeGroup}>
      {folder && (
        <button className={styles.folderRow} onClick={() => onToggle(folder)}>
          <svg viewBox="0 0 10 10" fill="currentColor" width="8" height="8" className={`${styles.chevron} ${isExpanded ? styles.chevronOpen : ""}`}>
            <path d="M2 3l3 4 3-4H2z"/>
          </svg>
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" width="12" height="12">
            <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5H13A1.5 1.5 0 0 1 14.5 6v6A1.5 1.5 0 0 1 13 13.5H3.5A1.5 1.5 0 0 1 2 12V4.5z"/>
          </svg>
          <span className={styles.folderName}>{folder}</span>
          <span className={styles.folderCount}>{pages.length}</span>
        </button>
      )}
      {isExpanded && (
        <div className={folder ? styles.folderChildren : ""}>
          {pages.map((page) => (
            <button
              key={page.path}
              className={`${styles.fileRow} ${selected?.path === page.path ? styles.fileRowActive : ""}`}
              onClick={() => onSelect(page)}
            >
              <DocIcon className={styles.fileIcon} />
              <span className={styles.fileName}>{page.title || page.path.split("/").pop()}</span>
              {page.type && <TypeBadge type={page.type} />}
              <span className={styles.fileMeta}>{formatDate(page.mtime)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function EditorPanel({ title, badge, meta, state, onChange, onSave, onViewMode, onDelete, onPreviewClick }: {
  title: string;
  badge?: React.ReactNode;
  meta: string[];
  state: EditorState;
  onChange: (c: string) => void;
  onSave: () => void | Promise<void>;
  onViewMode: (m: "edit" | "preview") => void;
  onDelete?: () => void | Promise<void>;
  onPreviewClick?: (e: React.MouseEvent) => void;
}) {
  const { frontmatter, body } = parseFrontmatter(state.content);
  const lines = state.content.split("\n").length;
  const words = state.content.trim() ? state.content.trim().split(/\s+/).length : 0;
  const previewHtml = state.viewMode === "preview" ? renderMarkdown(body) : "";

  return (
    <div className={styles.editor}>
      <div className={styles.editorHeader}>
        <div className={styles.editorHeaderLeft}>
          <DocIcon className={styles.fileIcon} />
          <span className={styles.editorTitle}>{title}</span>
          {badge}
        </div>
        <div className={styles.editorActions}>
          <div className={styles.modeSwitcher}>
            <button className={`${styles.modeBtn} ${state.viewMode === "edit" ? styles.modeBtnActive : ""}`} onClick={() => onViewMode("edit")}>Edit</button>
            <button className={`${styles.modeBtn} ${state.viewMode === "preview" ? styles.modeBtnActive : ""}`} onClick={() => onViewMode("preview")}>Preview</button>
          </div>
          {state.saveStatus === "saved" && <span className={styles.saveOk}>✓ Saved</span>}
          {state.saveStatus === "error" && <span className={styles.saveErr}>✗ Error</span>}
          <button className={styles.saveBtn} onClick={onSave} disabled={state.saving}>
            {state.saving ? "Saving…" : "Save"}
          </button>
          {onDelete && (
            <button className={styles.dangerBtn} onClick={onDelete} title="Delete page">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" width="12" height="12" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 4h10M6 4V2.5h4V4M5 4l.5 9.5h5L11 4M6.5 6.5v5M9.5 6.5v5"/>
              </svg>
            </button>
          )}
        </div>
      </div>
      {meta.length > 0 && (
        <div className={styles.editorMeta}>{meta.map((m, i) => <span key={i}>{m}</span>)}</div>
      )}
      {state.viewMode === "edit" ? (
        <textarea
          className={styles.textarea}
          value={state.content}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          placeholder="Empty…"
        />
      ) : (
        <div className={styles.preview} onClick={onPreviewClick}>
          {Object.keys(frontmatter).length > 0 && (
            <div className={styles.frontmatter}>
              {Object.entries(frontmatter).map(([k, v]) => (
                <div key={k} className={styles.fmRow}>
                  <span className={styles.fmKey}>{k}</span>
                  <span className={styles.fmVal}>{v}</span>
                </div>
              ))}
            </div>
          )}
          {body ? (
            <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: previewHtml }} />
          ) : (
            <div className={styles.previewEmpty}>Nothing to preview</div>
          )}
        </div>
      )}
      <div className={styles.editorFooter}>
        <span>{lines} lines</span>
        <span>{words} words</span>
        <span>{state.content.length} chars</span>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className={styles.treeLoading}>
      {[100, 70, 85, 60].map((w, i) => (
        <div key={i} className={styles.skeleton} style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}

function EmptyState({ icon, text, sub }: { icon: "doc" | "clock"; text: string; sub: string }) {
  return (
    <div className={styles.emptyState}>
      {icon === "doc" ? (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" width="40" height="40" opacity="0.3">
          <rect x="8" y="6" width="32" height="36" rx="4"/>
          <path d="M16 18h16M16 24h16M16 30h10"/>
        </svg>
      ) : (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" width="40" height="40" opacity="0.3">
          <circle cx="24" cy="24" r="18"/>
          <path d="M24 14v10l6 4"/>
        </svg>
      )}
      <p>{text}</p>
      <span>{sub}</span>
    </div>
  );
}

function Placeholder({ icon, text }: { icon: "doc" | "clock"; text: string }) {
  return (
    <div className={styles.placeholder}>
      {icon === "doc" ? (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.2" width="44" height="44" opacity="0.2">
          <rect x="8" y="6" width="32" height="36" rx="4"/>
          <path d="M16 18h16M16 24h16M16 30h10"/>
        </svg>
      ) : (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.2" width="44" height="44" opacity="0.2">
          <circle cx="24" cy="24" r="18"/>
          <path d="M24 14v10l6 4"/>
        </svg>
      )}
      <p>{text}</p>
    </div>
  );
}
