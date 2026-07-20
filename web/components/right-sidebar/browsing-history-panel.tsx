"use client";

/**
 * Web browsing history for the History view, shown under the session
 * history DAG. Desktop-only: the entries come from the Electron main
 * process (`desktop/browsing-history-store.js`) over the preload bridge,
 * so in plain web mode the whole section renders nothing.
 */

import { useCallback, useEffect, useState } from "react";
import { Globe, Search, Trash2 } from "lucide-react";

import {
  desktopBridge,
  type DesktopHistoryEntry,
} from "@/lib/desktop-bridge";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";

const LIST_LIMIT = 200;

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

export function BrowsingHistoryPanel() {
  const { text, locale } = useTranslation();
  const history = desktopBridge()?.history;
  const [entries, setEntries] = useState<DesktopHistoryEntry[] | null>(null);
  const [query, setQuery] = useState("");

  const refresh = useCallback(
    (needle: string) => {
      if (!history) return;
      void history
        .list({ limit: LIST_LIMIT, query: needle })
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

  // Plain web mode has no bridge — render nothing rather than an empty
  // section that can never fill.
  if (!history) return null;

  const dateLocale = locale === "zh" ? "zh-CN" : "en-US";
  const clearLabel = text("Clear browsing history", "清空浏览历史");
  const deleteLabel = text("Delete entry", "删除记录");

  return (
    <div className="browsing-history-section">
      <div className="sidebar-section-header">
        <Globe size={13} aria-hidden="true" />
        <span className="sidebar-section-title">
          {text("Web history", "网页历史")}
        </span>
        {entries && entries.length > 0 ? (
          <button
            type="button"
            className="browsing-history-clear"
            onClick={() => {
              void history.clear().then(() => setEntries([]));
            }}
            title={clearLabel}
            aria-label={clearLabel}
          >
            <Trash2 size={13} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      <div className="bookmarks-search">
        <Search size={14} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={text("Search history", "搜索历史")}
          aria-label={text("Search browsing history", "搜索浏览历史")}
        />
      </div>

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
                  onClick={() =>
                    useCenterTabs.getState().openWebTab(entry.url)
                  }
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
    </div>
  );
}
