"use client";

/**
 * BuiltinTabPane — the wide, full-center form of the two library pages
 * that used to live in the right sidebar: Bookmarks and Web history.
 * They are cross-session archives, not views of the current session,
 * so they belong in the center as their own tab (Chrome's
 * chrome://bookmarks / chrome://history), reachable from the main menu.
 *
 * The rows reuse the shared `.bookmark-*` / `.bookmarks-*` vocabulary
 * from right-dock.css — same list grammar, just laid out in a wider
 * centered column with a page header and a select-and-delete mode.
 * Bookmarks is a folder TREE (Chrome's bookmark manager): rows nest by
 * depth, folders collapse, and any row can be dragged into a folder.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bookmark,
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  Globe,
  Pencil,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { FolderPlusIcon, type AnimatedNavIconHandle } from "@/components/animated-icons";
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
  type DesktopHistoryEntry,
} from "@/lib/desktop-bridge";
import { useCenterTabs, type BuiltinPage } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";

const HISTORY_LIST_LIMIT = 200;

export function BuiltinTabPane({ page }: { page: BuiltinPage }) {
  return page === "bookmarks" ? <BookmarksPage /> : <HistoryPage />;
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
        <div className="bookmarks-search">
          <Search size={14} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
          />
        </div>
        {children}
      </div>
    </div>
  );
}

/** A node matches if it, or anything under it, matches the needle — so
 *  searching keeps the folders that lead to a hit. */
function matchesQuery(node: BookmarkNode, needle: string): boolean {
  if (!needle) return true;
  if (node.title.toLowerCase().includes(needle)) return true;
  return node.kind === "folder"
    ? node.children.some((child) => matchesQuery(child, needle))
    : node.url.toLowerCase().includes(needle);
}

function BookmarksPage() {
  const { text } = useTranslation();
  const [tree, setTree] = useState<BookmarkFolder>(readBookmarkTree);
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  // Between-row insertion point: which sibling list, and where in it.
  const [dropAt, setDropAt] = useState<{ parentId: string; index: number } | null>(null);
  const newFolderIconRef = useRef<AnimatedNavIconHandle>(null);

  useEffect(() => {
    const refresh = () => setTree(readBookmarkTree());
    refresh();
    return subscribeBookmarks(refresh);
  }, []);

  const needle = query.trim().toLowerCase();

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

  /** Drop onto a folder row moves into it; drop on the page background
   *  moves back out to the root. */
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

  /** The insertion line between two sibling rows. */
  function dropZone(parentId: string, index: number, depth: number) {
    const active = dropAt?.parentId === parentId && dropAt.index === index;
    return (
      <div
        className="bookmark-drop-zone"
        style={{ marginInlineStart: depth * 16 }}
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

  /** A sibling list, with an insertion zone before every row and one
   *  after the last — so any position among the siblings is reachable.
   *  Zones carry the index into the real (unfiltered) children, which is
   *  what moveNode wants; a search hides rows, so the gaps would point at
   *  the wrong slots and reordering is left out while one is active. */
  function renderChildren(parent: BookmarkFolder, depth: number): React.ReactNode[] {
    const out: React.ReactNode[] = [];
    parent.children.forEach((child, index) => {
      if (!matchesQuery(child, needle)) return;
      if (!needle) {
        out.push(
          <div key={`zone-${parent.id}-${index}`}>{dropZone(parent.id, index, depth)}</div>,
        );
      }
      out.push(renderNode(child, depth));
    });
    if (!needle && out.length > 0) {
      out.push(
        <div key={`zone-${parent.id}-end`}>
          {dropZone(parent.id, parent.children.length, depth)}
        </div>,
      );
    }
    return out;
  }

  function renderNode(node: BookmarkNode, depth: number): React.ReactNode {
    if (!matchesQuery(node, needle)) return null;
    const editing = editingId === node.id;
    const isFolder = node.kind === "folder";
    // A search always shows its hits, however deep they sit.
    const open = isFolder && (!collapsed.has(node.id) || Boolean(needle));
    return (
      <div key={node.id}>
        <div
          className="bookmark-row"
          style={{ marginInlineStart: depth * 16 }}
          data-drop-target={dropId === node.id ? "true" : undefined}
          draggable={!editing}
          onDragStart={(event) => {
            event.dataTransfer.effectAllowed = "move";
            setDragId(node.id);
          }}
          onDragEnd={clearDrag}
          onDragOver={
            isFolder && dragId && dragId !== node.id
              ? (event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  event.dataTransfer.dropEffect = "move";
                  setDropId(node.id);
                }
              : undefined
          }
          onDragLeave={isFolder ? () => setDropId(null) : undefined}
          onDrop={
            isFolder
              ? (event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  handleDrop(node.id);
                }
              : undefined
          }
        >
          <div className="bookmark-row-main">
            {isFolder ? (
              <button
                type="button"
                className="bookmark-twisty"
                onClick={() => toggleCollapsed(node.id)}
                title={open ? collapseLabel : expandLabel}
                aria-label={open ? collapseLabel : expandLabel}
              >
                {open ? (
                  <ChevronDown size={14} aria-hidden="true" />
                ) : (
                  <ChevronRight size={14} aria-hidden="true" />
                )}
              </button>
            ) : null}
            {isFolder ? (
              <Folder size={14} aria-hidden="true" className="bookmark-node-icon" />
            ) : null}
            {editing ? (
              <input
                className="bookmark-title-input"
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
                className="bookmark-title"
                onClick={() =>
                  isFolder
                    ? toggleCollapsed(node.id)
                    : useCenterTabs.getState().openWebTab(node.url)
                }
                title={node.title}
              >
                {node.title}
              </button>
            )}
            <div className="bookmark-actions">
              {editing ? (
                <>
                  <button
                    type="button"
                    onClick={() => saveRename(node.id)}
                    title={saveLabel}
                    aria-label={saveLabel}
                  >
                    <Check size={14} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingId(null)}
                    title={cancelLabel}
                    aria-label={cancelLabel}
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setEditingId(node.id);
                    setDraftTitle(node.title);
                  }}
                  title={editLabel}
                  aria-label={editLabel}
                >
                  <Pencil size={14} aria-hidden="true" />
                </button>
              )}
              <button
                type="button"
                onClick={() => deleteNode(node.id)}
                title={deleteLabel}
                aria-label={deleteLabel}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>
          </div>
          {node.kind === "bookmark" ? (
            <span className="bookmark-url" title={node.url}>
              {node.url}
            </span>
          ) : null}
        </div>
        {isFolder && open ? renderChildren(node, depth + 1) : null}
      </div>
    );
  }

  const rows = renderChildren(tree, 0);

  return (
    <PageFrame
      icon={<Bookmark size={18} aria-hidden="true" />}
      title={text("Bookmarks", "书签")}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search bookmarks", "搜索书签")}
      action={
        <button
          type="button"
          className={styles.builtinClear}
          onClick={() => createFolder(newFolderLabel)}
          onMouseEnter={() => newFolderIconRef.current?.startAnimation()}
          onMouseLeave={() => newFolderIconRef.current?.stopAnimation()}
          title={newFolderLabel}
          aria-label={newFolderLabel}
        >
          <FolderPlusIcon ref={newFolderIconRef} size={14} aria-hidden="true" />
          <span>{newFolderLabel}</span>
        </button>
      }
    >
      {tree.children.length === 0 ? (
        <div className="bookmarks-empty">
          {text("No bookmarks yet", "还没有书签")}
        </div>
      ) : rows.length === 0 ? (
        <div className="bookmarks-empty">
          {text("No matching bookmarks", "没有匹配的书签")}
        </div>
      ) : (
        <div
          className="bookmarks-list"
          onDragOver={(event) => {
            if (dragId) event.preventDefault();
          }}
          onDrop={() => handleDrop(BOOKMARKS_ROOT_ID)}
        >
          {rows}
        </div>
      )}
    </PageFrame>
  );
}

function formatVisitedAt(value: number, locale: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const sameDay = new Date().toDateString() === date.toDateString();
  return date.toLocaleString(locale, {
    hour: "2-digit",
    minute: "2-digit",
    ...(sameDay ? {} : { month: "short", day: "numeric" }),
  });
}

/** 历史行的站点图标：加载失败或没有 favicon 时退回 Globe（同 tab 条）。 */
function HistoryFavicon({ url }: { url: string }) {
  const [broken, setBroken] = useState(false);
  if (!url || broken) return <Globe size={16} aria-hidden="true" />;
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

  const refresh = useCallback(
    (needle: string) => {
      if (!history) return;
      void history
        .list({ limit: HISTORY_LIST_LIMIT, query: needle })
        .then(setEntries)
        .catch(() => setEntries([]));
    },
    [history],
  );

  // Re-query the main process on each keystroke: the store does the
  // filtering, so the renderer never holds the full 5000-row list.
  useEffect(() => {
    if (!history) return undefined;
    const timer = window.setTimeout(() => refresh(query), 150);
    return () => window.clearTimeout(timer);
  }, [history, query, refresh]);

  const title = text("Web history", "网页历史");
  const clearLabel = text("Clear browsing history", "清空浏览历史");
  const deleteLabel = text("Delete entry", "删除记录");

  // Plain web mode has no bridge to record visits. The tab still opens
  // (the menu entry is unconditional) and explains why it is empty,
  // rather than erroring or silently rendering a dead page.
  if (!history) {
    return (
      <div className={styles.builtinPane}>
        <div className={styles.builtinInner}>
          <div className={styles.builtinHeader}>
            <Globe size={18} aria-hidden="true" />
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

  return (
    <PageFrame
      icon={<Globe size={18} aria-hidden="true" />}
      title={title}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search history", "搜索历史")}
      action={
        entries && entries.length > 0 ? (
          <button
            type="button"
            className={styles.builtinClear}
            onClick={() => {
              void history.clear().then(() => setEntries([]));
            }}
            title={clearLabel}
            aria-label={clearLabel}
          >
            <Trash2 size={14} aria-hidden="true" />
            <span>{text("Clear all", "全部清空")}</span>
          </button>
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
        <div className="bookmarks-list">
          {entries.map((entry) => (
            <div
              className="bookmark-row"
              key={`${entry.url}:${entry.visitedAt}`}
            >
              <div className="bookmark-row-main">
                <HistoryFavicon url={entry.faviconUrl} />
                <button
                  type="button"
                  className="bookmark-title"
                  onClick={() => useCenterTabs.getState().openWebTab(entry.url)}
                  title={entry.title || entry.url}
                >
                  {entry.title || entry.url}
                </button>
                <div className="bookmark-actions">
                  <span className="browsing-history-time">
                    {formatVisitedAt(entry.visitedAt, dateLocale)}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      void history
                        .remove(entry.url, entry.visitedAt)
                        .then(() => refresh(query));
                    }}
                    title={deleteLabel}
                    aria-label={deleteLabel}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </div>
              </div>
              <span className="bookmark-url" title={entry.url}>
                {entry.url}
              </span>
            </div>
          ))}
        </div>
      )}
    </PageFrame>
  );
}
