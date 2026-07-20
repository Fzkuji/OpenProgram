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
 */

import { useCallback, useEffect, useState } from "react";
import { Bookmark, Check, Globe, Pencil, Search, Trash2, X } from "lucide-react";

import {
  readBookmarks,
  removeBookmark,
  renameBookmark,
  subscribeBookmarks,
  type Bookmark as BookmarkItem,
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

function BookmarksPage() {
  const { text } = useTranslation();
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>(readBookmarks);
  const [query, setQuery] = useState("");
  const [editingUrl, setEditingUrl] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  useEffect(() => {
    const refresh = () => setBookmarks(readBookmarks());
    refresh();
    return subscribeBookmarks(refresh);
  }, []);

  const needle = query.trim().toLowerCase();
  const filtered = bookmarks.filter(
    (bookmark) =>
      !needle ||
      bookmark.title.toLowerCase().includes(needle) ||
      bookmark.url.toLowerCase().includes(needle),
  );

  function saveRename(url: string) {
    renameBookmark(url, draftTitle);
    setEditingUrl(null);
  }

  const saveLabel = text("Save bookmark title", "保存书签标题");
  const cancelLabel = text("Cancel editing", "取消编辑");
  const editLabel = text("Edit bookmark title", "编辑书签标题");
  const deleteLabel = text("Delete bookmark", "删除书签");

  return (
    <PageFrame
      icon={<Bookmark size={18} aria-hidden="true" />}
      title={text("Bookmarks", "书签")}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search bookmarks", "搜索书签")}
    >
      {bookmarks.length === 0 ? (
        <div className="bookmarks-empty">
          {text("No bookmarks yet", "还没有书签")}
        </div>
      ) : filtered.length === 0 ? (
        <div className="bookmarks-empty">
          {text("No matching bookmarks", "没有匹配的书签")}
        </div>
      ) : (
        <div className="bookmarks-list">
          {filtered.map((bookmark) => {
            const editing = editingUrl === bookmark.url;
            return (
              <div className="bookmark-row" key={bookmark.url}>
                <div className="bookmark-row-main">
                  {editing ? (
                    <input
                      className="bookmark-title-input"
                      value={draftTitle}
                      onChange={(event) => setDraftTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") saveRename(bookmark.url);
                        if (event.key === "Escape") setEditingUrl(null);
                      }}
                      aria-label={editLabel}
                      autoFocus
                    />
                  ) : (
                    <button
                      type="button"
                      className="bookmark-title"
                      onClick={() =>
                        useCenterTabs.getState().openWebTab(bookmark.url)
                      }
                      title={bookmark.title}
                    >
                      {bookmark.title}
                    </button>
                  )}
                  <div className="bookmark-actions">
                    {editing ? (
                      <>
                        <button
                          type="button"
                          onClick={() => saveRename(bookmark.url)}
                          title={saveLabel}
                          aria-label={saveLabel}
                        >
                          <Check size={14} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingUrl(null)}
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
                          setEditingUrl(bookmark.url);
                          setDraftTitle(bookmark.title);
                        }}
                        title={editLabel}
                        aria-label={editLabel}
                      >
                        <Pencil size={14} aria-hidden="true" />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => removeBookmark(bookmark.url)}
                      title={deleteLabel}
                      aria-label={deleteLabel}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                    </button>
                  </div>
                </div>
                <span className="bookmark-url" title={bookmark.url}>
                  {bookmark.url}
                </span>
              </div>
            );
          })}
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
