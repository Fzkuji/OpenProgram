"use client";

/**
 * BuiltinTabPane — the wide, full-center form of the two library pages
 * that used to live in the right sidebar: Bookmarks and Web history.
 * They are cross-session archives, not views of the current session,
 * so they belong in the center as their own tab (Chrome's
 * chrome://bookmarks / chrome://history), reachable from the main menu.
 *
 * Bookmarks follows the browser-manager structure: fixed search header,
 * folder-only navigation, and one content list for the selected folder.
 * Web history keeps the shared wide list frame below.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Bookmark,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Folder,
  History,
  MoreVertical,
  Trash2,
  X,
} from "lucide-react";

import { ChromeIcon } from "@/components/animated-icons";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/chat/top-bar/menu-styles";
import {
  BOOKMARKS_ROOT_ID,
  createFolder,
  deleteNode,
  findNode,
  moveNode,
  readBookmarkTree,
  renameNode,
  subscribeBookmarks,
  type BookmarkFolder,
  type BookmarkNode,
} from "@/lib/bookmarks";
import {
  desktopBridge,
  type DesktopDownloadEntry,
  type DesktopHistoryEntry,
} from "@/lib/desktop-bridge";
import { useCenterTabs, type BuiltinPage } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { groupHistoryByLocalDate } from "@/lib/history-groups";
import { SearchInput } from "@/components/ui/search-input";
import styles from "./center-tabs.module.css";

const HISTORY_PAGE_SIZE = 250;
const HISTORY_LIST_LIMIT = 5_000;

export function BuiltinTabPane({ page }: { page: BuiltinPage }) {
  if (page === "bookmarks") return <BookmarksPage />;
  if (page === "downloads") return <DownloadsPage />;
  return <HistoryPage />;
}

/** Shared page chrome: title row + search box, then the caller's list. */
function PageFrame({
  icon,
  title,
  query,
  onQueryChange,
  searchPlaceholder,
  action,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  query: string;
  onQueryChange: (value: string) => void;
  searchPlaceholder: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.builtinPane}>
      <div className={styles.builtinInner}>
        <div className={styles.builtinHeader}>
          {icon}
          <h1 className={styles.builtinTitle}>{title}</h1>
          {action}
        </div>
        <SearchInput
          className="mx-1 mb-2.5"
          value={query}
          onChange={onQueryChange}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
        />
        {children}
      </div>
    </div>
  );
}

/** Folder navigation keeps ancestors visible while searching. */
function matchesQuery(node: BookmarkNode, needle: string): boolean {
  if (!needle) return true;
  if (node.title.toLowerCase().includes(needle)) return true;
  return node.kind === "folder"
    ? node.children.some((child) => matchesQuery(child, needle))
    : node.url.toLowerCase().includes(needle);
}

function bookmarkSearchResults(folder: BookmarkFolder, needle: string): BookmarkNode[] {
  const results: BookmarkNode[] = [];
  const visit = (node: BookmarkNode) => {
    const ownMatch = node.title.toLowerCase().includes(needle)
      || (node.kind === "bookmark" && node.url.toLowerCase().includes(needle));
    if (ownMatch) results.push(node);
    if (node.kind === "folder") node.children.forEach(visit);
  };
  folder.children.forEach(visit);
  return results;
}

function BookmarkFavicon({ node }: { node: BookmarkNode }) {
  const [broken, setBroken] = useState(false);
  if (node.kind === "folder") {
    return <Folder size={17} aria-hidden="true" />;
  }
  if (!node.faviconUrl || broken) {
    return <ChromeIcon size={16} aria-hidden="true" />;
  }
  return <img src={node.faviconUrl} alt="" onError={() => setBroken(true)} />;
}

function BookmarksPage() {
  const { text } = useTranslation();
  const [tree, setTree] = useState<BookmarkFolder>(readBookmarkTree);
  const [query, setQuery] = useState("");
  const [selectedFolderId, setSelectedFolderId] = useState(BOOKMARKS_ROOT_ID);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const [dropAt, setDropAt] = useState<{ parentId: string; index: number } | null>(null);

  useEffect(() => {
    const refresh = () => setTree(readBookmarkTree());
    refresh();
    return subscribeBookmarks(refresh);
  }, []);

  const needle = query.trim().toLowerCase();
  const selectedNode = findNode(tree, selectedFolderId);
  const selectedFolder = selectedNode?.kind === "folder" ? selectedNode : tree;
  const visibleNodes = needle
    ? bookmarkSearchResults(tree, needle)
    : selectedFolder.children;

  useEffect(() => {
    if (selectedNode?.kind !== "folder") setSelectedFolderId(BOOKMARKS_ROOT_ID);
  }, [selectedNode]);

  function saveRename(id: string) {
    renameNode(id, draftTitle);
    setEditingId(null);
  }

  function toggleCollapsed(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearDrag() {
    setDragId(null);
    setDropId(null);
    setDropAt(null);
  }

  function handleDrop(parentId: string) {
    if (dragId) moveNode(dragId, parentId);
    clearDrag();
  }

  /** Drop on the gap between two rows reorders among siblings. The
   *  dragged node is spliced out before being re-inserted, so an index
   *  after its own old slot shifts down by one. */
  function handleReorderDrop(parentId: string, index: number) {
    if (dragId) {
      const siblings = findNode(tree, parentId);
      const from =
        siblings && siblings.kind === "folder"
          ? siblings.children.findIndex((child) => child.id === dragId)
          : -1;
      moveNode(dragId, parentId, from >= 0 && from < index ? index - 1 : index);
    }
    clearDrag();
  }

  function dropZone(parentId: string, index: number) {
    const active = dropAt?.parentId === parentId && dropAt.index === index;
    return (
      <div
        className={styles.bookmarkManagerDropZone}
        data-drop-target={active ? "true" : undefined}
        onDragOver={(event) => {
          if (!dragId) return;
          event.preventDefault();
          event.stopPropagation();
          event.dataTransfer.dropEffect = "move";
          setDropId(null);
          setDropAt({ parentId, index });
        }}
        onDragLeave={() => setDropAt(null)}
        onDrop={(event) => {
          event.preventDefault();
          event.stopPropagation();
          handleReorderDrop(parentId, index);
        }}
      />
    );
  }

  const saveLabel = text("Save bookmark title", "保存书签标题");
  const cancelLabel = text("Cancel editing", "取消编辑");
  const editLabel = text("Edit bookmark title", "编辑书签标题");
  const deleteLabel = text("Delete bookmark", "删除书签");
  const newFolderLabel = text("New folder", "新建文件夹");
  const expandLabel = text("Expand folder", "展开文件夹");
  const collapseLabel = text("Collapse folder", "折叠文件夹");
  const pageActionsLabel = text("Bookmark actions", "书签操作");
  const allBookmarksLabel = text("All bookmarks", "全部书签");

  function renderFolder(folder: BookmarkFolder, depth: number): React.ReactNode {
    const childFolders = folder.children.filter(
      (node): node is BookmarkFolder => node.kind === "folder" && matchesQuery(node, needle),
    );
    const open = folder.id === BOOKMARKS_ROOT_ID || !collapsed.has(folder.id) || Boolean(needle);
    const selected = selectedFolder.id === folder.id;
    const label = folder.id === BOOKMARKS_ROOT_ID ? allBookmarksLabel : folder.title;
    return (
      <div key={folder.id}>
        <div
          className={styles.bookmarkFolderRow + (selected ? ` ${styles.bookmarkFolderSelected}` : "")}
          style={{ paddingInlineStart: 8 + depth * 16 }}
          data-drop-target={dropId === folder.id ? "true" : undefined}
          onDragOver={(event) => {
            if (!dragId || dragId === folder.id) return;
            event.preventDefault();
            event.stopPropagation();
            setDropId(folder.id);
          }}
          onDragLeave={() => setDropId(null)}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            handleDrop(folder.id);
          }}
        >
          {childFolders.length > 0 && folder.id !== BOOKMARKS_ROOT_ID ? (
            <button
              type="button"
              className={styles.bookmarkFolderTwisty}
              onClick={() => toggleCollapsed(folder.id)}
              title={open ? collapseLabel : expandLabel}
              aria-label={open ? collapseLabel : expandLabel}
            >
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : (
            <span className={styles.bookmarkFolderTwisty} />
          )}
          <Folder size={16} aria-hidden="true" />
          <button
            type="button"
            className={styles.bookmarkFolderTitle}
            onClick={() => setSelectedFolderId(folder.id)}
            title={label}
          >
            {label}
          </button>
        </div>
        {open ? childFolders.map((child) => renderFolder(child, depth + 1)) : null}
      </div>
    );
  }

  function openNode(node: BookmarkNode) {
    if (node.kind === "folder") {
      setSelectedFolderId(node.id);
      setQuery("");
    } else {
      useCenterTabs.getState().openWebTab(node.url);
    }
  }

  function renderContentNode(node: BookmarkNode): React.ReactNode {
    const editing = editingId === node.id;
    const isFolder = node.kind === "folder";
    return (
      <div
        key={node.id}
        className={styles.bookmarkContentRow}
        data-drop-target={dropId === node.id ? "true" : undefined}
        draggable={!editing && !needle}
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = "move";
          setDragId(node.id);
        }}
        onDragEnd={clearDrag}
        onDragOver={isFolder && dragId && dragId !== node.id ? (event) => {
          event.preventDefault();
          event.stopPropagation();
          setDropId(node.id);
        } : undefined}
        onDragLeave={isFolder ? () => setDropId(null) : undefined}
        onDrop={isFolder ? (event) => {
          event.preventDefault();
          event.stopPropagation();
          handleDrop(node.id);
        } : undefined}
      >
        <span className={styles.bookmarkContentIcon}>
          <BookmarkFavicon node={node} />
        </span>
        {editing ? (
          <input
            className={styles.bookmarkContentTitleInput}
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") saveRename(node.id);
              if (event.key === "Escape") setEditingId(null);
            }}
            aria-label={editLabel}
            autoFocus
          />
        ) : (
          <button
            type="button"
            className={styles.bookmarkContentTitle}
            onClick={() => openNode(node)}
            title={node.title}
          >
            {node.title || (node.kind === "bookmark" ? node.url : newFolderLabel)}
          </button>
        )}
        {editing ? (
          <div className={styles.bookmarkContentEditActions}>
            <button type="button" onClick={() => saveRename(node.id)} title={saveLabel} aria-label={saveLabel}>
              <Check size={15} />
            </button>
            <button type="button" onClick={() => setEditingId(null)} title={cancelLabel} aria-label={cancelLabel}>
              <X size={15} />
            </button>
          </div>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className={styles.bookmarkManagerMenuButton}
                title={`${pageActionsLabel}: ${node.title}`}
                aria-label={`${pageActionsLabel}: ${node.title}`}
              >
                <MoreVertical size={17} aria-hidden="true" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={MENU_PANEL + " min-w-[180px]"}>
              <DropdownMenuItem
                className={itemCls(false)}
                onSelect={() => {
                  setEditingId(node.id);
                  setDraftTitle(node.title);
                }}
              >
                {editLabel}
              </DropdownMenuItem>
              <DropdownMenuSeparator className={MENU_SEPARATOR} />
              <DropdownMenuItem className={itemCls(false, true)} onSelect={() => deleteNode(node.id)}>
                {deleteLabel}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    );
  }

  const contentRows: React.ReactNode[] = [];
  visibleNodes.forEach((node, index) => {
    if (!needle) {
      contentRows.push(
        <div key={`zone-${selectedFolder.id}-${index}`}>
          {dropZone(selectedFolder.id, index)}
        </div>,
      );
    }
    contentRows.push(renderContentNode(node));
  });
  if (!needle && visibleNodes.length > 0) {
    contentRows.push(
      <div key={`zone-${selectedFolder.id}-end`}>
        {dropZone(selectedFolder.id, selectedFolder.children.length)}
      </div>,
    );
  }

  return (
    <div className={styles.bookmarkManager}>
      <header className={styles.bookmarkManagerHeader}>
        <div className={styles.bookmarkManagerBrand}>
          <Bookmark size={19} aria-hidden="true" />
          <h1>{text("Bookmarks", "书签")}</h1>
        </div>
        <SearchInput
          className={styles.bookmarkManagerSearch}
          value={query}
          onChange={setQuery}
          placeholder={text("Search bookmarks", "搜索书签")}
          aria-label={text("Search bookmarks", "搜索书签")}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={styles.bookmarkManagerMenuButton}
              title={pageActionsLabel}
              aria-label={pageActionsLabel}
            >
              <MoreVertical size={18} aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={MENU_PANEL + " min-w-[180px]"}>
            <DropdownMenuItem
              className={itemCls(false)}
              onSelect={() => createFolder(newFolderLabel, selectedFolder.id)}
            >
              {newFolderLabel}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>
      <div className={styles.bookmarkManagerBody}>
        <nav className={styles.bookmarkFolderTree} aria-label={text("Bookmark folders", "书签文件夹")}>
          {renderFolder(tree, 0)}
        </nav>
        <main
          className={styles.bookmarkManagerContent}
          onDragOver={(event) => {
            if (dragId && !needle) event.preventDefault();
          }}
          onDrop={() => {
            if (!needle) handleDrop(selectedFolder.id);
          }}
        >
          <div className={styles.bookmarkContentPanel}>
            {tree.children.length === 0 ? (
              <div className={styles.bookmarkContentEmpty}>
                {text("No bookmarks yet", "还没有书签")}
              </div>
            ) : visibleNodes.length === 0 ? (
              <div className={styles.bookmarkContentEmpty}>
                {needle
                  ? text("No matching bookmarks", "没有匹配的书签")
                  : text("This folder is empty", "此文件夹为空")}
              </div>
            ) : (
              <div className={styles.bookmarkContentList}>{contentRows}</div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function formatVisitedAt(value: number, locale: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function historyDateLabel(date: Date, locale: string, today: string, yesterday: string): string {
  const current = new Date();
  const startToday = new Date(current.getFullYear(), current.getMonth(), current.getDate()).getTime();
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  if (startDate === startToday) return today;
  if (startDate === startToday - 86_400_000) return yesterday;
  return date.toLocaleDateString(locale, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
}

function historyHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** 历史行的站点图标：加载失败或没有 favicon 时退回 ChromeIcon（同 tab 条）。 */
function HistoryFavicon({ url }: { url: string }) {
  const [broken, setBroken] = useState(false);
  if (!url || broken) return <ChromeIcon size={16} aria-hidden="true" />;
  return (
    <img
      className="browsing-history-favicon"
      src={url}
      alt=""
      onError={() => setBroken(true)}
    />
  );
}

function HistoryPage() {
  const { text, locale } = useTranslation();
  const history = desktopBridge()?.history;
  const [entries, setEntries] = useState<DesktopHistoryEntry[] | null>(null);
  const [query, setQuery] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(HISTORY_PAGE_SIZE);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  const refresh = useCallback(
    (needle: string) => {
      if (!history) return;
      void history
        .list({ limit: visibleLimit, query: needle })
        .then(setEntries)
        .catch(() => setEntries([]));
    },
    [history, visibleLimit],
  );

  // Re-query the main process on each keystroke: the store does the
  // filtering, so the renderer never holds the full 5000-row list.
  useEffect(() => {
    if (!history) return undefined;
    const timer = window.setTimeout(() => refresh(query), 150);
    return () => window.clearTimeout(timer);
  }, [history, query, refresh]);

  useEffect(() => {
    setVisibleLimit(HISTORY_PAGE_SIZE);
    setSelected(new Set());
  }, [query]);

  const title = text("Web history", "网页历史");
  const clearLabel = text("Clear browsing history", "清空浏览历史");
  const deleteLabel = text("Delete entry", "删除记录");
  const selectedCount = selected.size;

  // Plain web mode has no bridge to record visits. The tab still opens
  // (the menu entry is unconditional) and explains why it is empty,
  // rather than erroring or silently rendering a dead page.
  if (!history) {
    return (
      <div className={styles.builtinPane}>
        <div className={styles.builtinInner}>
          <div className={styles.builtinHeader}>
            <History size={18} aria-hidden="true" />
            <h1 className={styles.builtinTitle}>{title}</h1>
          </div>
          <div className="bookmarks-empty">
            {text(
              "Browsing history is recorded by the desktop app only.",
              "浏览历史仅在桌面应用中记录。",
            )}
          </div>
        </div>
      </div>
    );
  }

  const dateLocale = locale === "zh" ? "zh-CN" : "en-US";
  const groups = groupHistoryByLocalDate(entries ?? []);

  return (
    <PageFrame
      icon={<History size={18} aria-hidden="true" />}
      title={title}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search history", "搜索历史")}
      action={
        entries && entries.length > 0 ? (
          <div className={styles.builtinHeaderActions}>
            {selectedCount > 0 && (
              <button
                type="button"
                className={styles.builtinClear}
                onClick={() => {
                  const targets = entries.filter((entry) => selected.has(`${entry.url}:${entry.visitedAt}`));
                  void Promise.all(targets.map((entry) => history.remove(entry.url, entry.visitedAt))).then(() => {
                    setSelected(new Set());
                    refresh(query);
                  });
                }}
              >
                <Trash2 size={14} aria-hidden="true" />
                <span>{text(`Delete ${selectedCount}`, `删除 ${selectedCount} 项`)}</span>
              </button>
            )}
            <button
              type="button"
              className={styles.builtinClear}
              onClick={() => {
                void history.clear().then(() => {
                  setEntries([]);
                  setSelected(new Set());
                });
              }}
              title={clearLabel}
              aria-label={clearLabel}
            >
              <Trash2 size={14} aria-hidden="true" />
              <span>{text("Clear all", "全部清空")}</span>
            </button>
          </div>
        ) : null
      }
    >
      {entries === null ? null : entries.length === 0 ? (
        <div className="bookmarks-empty">
          {query.trim()
            ? text("No matching pages", "没有匹配的网页")
            : text("No pages visited yet", "还没有浏览记录")}
        </div>
      ) : (
        <div className="browsing-history-list">
          {groups.map((group) => (
            <section key={group.key} className="browsing-history-group">
              <h2>{historyDateLabel(group.date, dateLocale, text("Today", "今天"), text("Yesterday", "昨天"))}</h2>
              {group.entries.map((entry) => {
                const entryKey = `${entry.url}:${entry.visitedAt}`;
                return (
                  <div className="browsing-history-row" key={entryKey}>
                    <input
                      type="checkbox"
                      checked={selected.has(entryKey)}
                      onChange={(event) => {
                        setSelected((value) => {
                          const next = new Set(value);
                          if (event.target.checked) next.add(entryKey);
                          else next.delete(entryKey);
                          return next;
                        });
                      }}
                      aria-label={text("Select history entry", "选择历史记录")}
                    />
                    <span className="browsing-history-time">{formatVisitedAt(entry.visitedAt, dateLocale)}</span>
                    <HistoryFavicon url={entry.faviconUrl} />
                    <button
                      type="button"
                      className="browsing-history-title"
                      onClick={() => useCenterTabs.getState().openWebTab(entry.url)}
                      title={entry.title || entry.url}
                    >
                      {entry.title || entry.url}
                    </button>
                    <span className="browsing-history-host" title={entry.url}>{historyHost(entry.url)}</span>
                    <button
                      type="button"
                      className="browsing-history-delete"
                      onClick={() => void history.remove(entry.url, entry.visitedAt).then(() => refresh(query))}
                      title={deleteLabel}
                      aria-label={deleteLabel}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                    </button>
                  </div>
                );
              })}
            </section>
          ))}
          {entries.length === visibleLimit && visibleLimit < HISTORY_LIST_LIMIT && (
            <button
              type="button"
              className="browsing-history-more"
              onClick={() => setVisibleLimit((value) => Math.min(value + HISTORY_PAGE_SIZE, HISTORY_LIST_LIMIT))}
            >
              {text("Load more", "加载更多")}
            </button>
          )}
        </div>
      )}
    </PageFrame>
  );
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function DownloadsPage() {
  const { text } = useTranslation();
  const api = desktopBridge()?.downloads;
  const [entries, setEntries] = useState<DesktopDownloadEntry[] | null>(null);
  const [query, setQuery] = useState("");
  const refresh = useCallback(() => {
    if (!api) return;
    void api.list({ query }).then(setEntries).catch(() => setEntries([]));
  }, [api, query]);

  useEffect(() => {
    if (!api) return undefined;
    const timer = window.setTimeout(refresh, 100);
    const unsubscribe = api.onChanged(refresh);
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
    };
  }, [api, refresh]);

  const title = text("Downloads", "下载内容");
  if (!api) {
    return (
      <div className={styles.builtinPane}>
        <div className={styles.builtinInner}>
          <div className={styles.builtinHeader}>
            <Download size={18} aria-hidden="true" />
            <h1 className={styles.builtinTitle}>{title}</h1>
          </div>
          <div className="bookmarks-empty">
            {text("Downloads are managed by the desktop app only.", "下载内容仅由桌面应用管理。")}
          </div>
        </div>
      </div>
    );
  }

  return (
    <PageFrame
      icon={<Download size={18} aria-hidden="true" />}
      title={title}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search downloads", "搜索下载内容")}
      action={entries?.some((entry) => !entry.active) ? (
        <button
          type="button"
          className={styles.builtinClear}
          onClick={() => void api.clear().then(refresh)}
        >
          <Trash2 size={14} aria-hidden="true" />
          <span>{text("Clear", "清除记录")}</span>
        </button>
      ) : null}
    >
      {entries === null ? null : entries.length === 0 ? (
        <div className="bookmarks-empty">
          {query.trim()
            ? text("No matching downloads", "没有匹配的下载内容")
            : text("No downloads yet", "还没有下载内容")}
        </div>
      ) : (
        <div className="browsing-history-list">
          {entries.map((entry) => {
            const progress = entry.totalBytes > 0
              ? `${formatBytes(entry.receivedBytes)} / ${formatBytes(entry.totalBytes)}`
              : formatBytes(entry.receivedBytes);
            const state = entry.state === "completed"
              ? text("Completed", "已完成")
              : entry.state === "progressing"
                ? text("Downloading", "下载中")
                : entry.state === "cancelled"
                  ? text("Cancelled", "已取消")
                  : text("Interrupted", "已中断");
            return (
              <div className="browsing-history-row" key={entry.id}>
                <Download size={15} aria-hidden="true" />
                <span className="browsing-history-time">{state}</span>
                <button
                  type="button"
                  className="browsing-history-title"
                  disabled={entry.state !== "completed"}
                  onClick={() => void api.open(entry.id)}
                  title={entry.filename}
                >
                  {entry.filename}
                </button>
                <span className="browsing-history-host" title={entry.url}>{progress}</span>
                {entry.active ? (
                  <button
                    type="button"
                    className="browsing-history-delete"
                    onClick={() => void api.cancel(entry.id)}
                    title={text("Cancel download", "取消下载")}
                    aria-label={text("Cancel download", "取消下载")}
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                ) : (
                  <button
                    type="button"
                    className="browsing-history-delete"
                    onClick={() => void api.show(entry.id)}
                    title={text("Show in folder", "在文件夹中显示")}
                    aria-label={text("Show in folder", "在文件夹中显示")}
                  >
                    <Folder size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </PageFrame>
  );
}
