"use client";

import { useEffect, useState } from "react";
import { Check, ExternalLink, Pencil, Search, Trash2, X } from "lucide-react";

import {
  readBookmarks,
  removeBookmark,
  renameBookmark,
  subscribeBookmarks,
  type Bookmark,
} from "@/lib/bookmarks";
import { desktopBridge, isDesktopSplitLayoutAvailable } from "@/lib/desktop-bridge";
import { openBookmark } from "@/lib/bookmark-navigation";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/state/center-tabs-store";

export function BookmarksPanel() {
  const { text } = useTranslation();
  const [bookmarks, setBookmarks] = useState<Bookmark[]>(readBookmarks);
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

  function openBesideChat(url: string) {
    const tabs = useCenterTabs.getState();
    const active = tabs.tabs.find((tab) => tab.id === tabs.activeId);
    openBookmark(url, {
      desktop: Boolean(desktopBridge()),
      activeKind: active?.kind,
      splitAvailable: isDesktopSplitLayoutAvailable(),
      openSplit: tabs.openWebTabInSplit,
      openTab: tabs.openWebTab,
      collapseDock: () => useSessionStore.getState().setRightDockOpen(false),
    });
  }

  function startRename(bookmark: Bookmark) {
    setEditingUrl(bookmark.url);
    setDraftTitle(bookmark.title);
  }

  function saveRename(url: string) {
    renameBookmark(url, draftTitle);
    setEditingUrl(null);
  }

  return (
    <div className="bookmarks-panel">
      <div className="bookmarks-search">
        <Search size={14} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={text("Search bookmarks", "搜索书签")}
          aria-label={text("Search bookmarks", "搜索书签")}
        />
      </div>

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
            const saveLabel = text("Save bookmark title", "保存书签标题");
            const cancelLabel = text("Cancel editing", "取消编辑");
            const editLabel = text("Edit bookmark title", "编辑书签标题");
            const fullTabLabel = text("Open in full tab", "在完整标签页中打开");
            const deleteLabel = text("Delete bookmark", "删除书签");

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
                      onClick={() => openBesideChat(bookmark.url)}
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
                        onClick={() => startRename(bookmark)}
                        title={editLabel}
                        aria-label={editLabel}
                      >
                        <Pencil size={14} aria-hidden="true" />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => useCenterTabs.getState().openWebTab(bookmark.url)}
                      title={fullTabLabel}
                      aria-label={fullTabLabel}
                    >
                      <ExternalLink size={14} aria-hidden="true" />
                    </button>
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
    </div>
  );
}
