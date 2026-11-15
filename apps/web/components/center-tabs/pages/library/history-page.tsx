"use client";

/** Browsing history, grouped by day and searchable. */
import { useCallback, useEffect, useState } from "react";
import { History, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChromeIcon } from "@/components/animated-icons";
import { desktopBridge, type DesktopHistoryEntry } from "@/lib/desktop/desktop-bridge";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { groupHistoryByLocalDate } from "@/lib/chat/history/history-groups";
import styles from "../pages.module.css";
import { PageFrame } from "./page-frame";
import { Checkbox } from "@/components/ui/checkbox";

const HISTORY_PAGE_SIZE = 250;

const HISTORY_LIST_LIMIT = 5_000;

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

export function HistoryPage() {
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
            <History size={20} aria-hidden="true" />
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
      icon={<History size={20} aria-hidden="true" />}
      title={title}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search history", "搜索历史")}
      action={
        entries && entries.length > 0 ? (
          <div className={styles.builtinHeaderActions}>
            {selectedCount > 0 && (
              <Button type="button" variant="destructive" onClick={() => {
                  const targets = entries.filter((entry) => selected.has(`${entry.url}:${entry.visitedAt}`));
                  void Promise.all(targets.map((entry) => history.remove(entry.url, entry.visitedAt))).then(() => {
                    setSelected(new Set());
                    refresh(query);
                  });
                }}
              >
                <Trash2 size={14} aria-hidden="true" />
                <span>{text(`Delete ${selectedCount}`, `删除 ${selectedCount} 项`)}</span>
              </Button>
            )}
            <Button type="button" variant="elevated"
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
            </Button>
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
                    <Checkbox
                      checked={selected.has(entryKey)}
                      onCheckedChange={(checked) => {
                        setSelected((value) => {
                          const next = new Set(value);
                          if (checked === true) next.add(entryKey);
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
                    <span className="browsing-history-host">{historyHost(entry.url)}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="browsing-history-delete hover:text-accent-red"
                      onClick={() => void history.remove(entry.url, entry.visitedAt).then(() => refresh(query))}
                      title={deleteLabel}
                      aria-label={deleteLabel}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </div>
                );
              })}
            </section>
          ))}
          {entries.length === visibleLimit && visibleLimit < HISTORY_LIST_LIMIT && (
            <Button
              type="button"
              variant="elevated"
              size="sm"
              className="self-center"
              onClick={() => setVisibleLimit((value) => Math.min(value + HISTORY_PAGE_SIZE, HISTORY_LIST_LIMIT))}
            >
              {text("Load more", "加载更多")}
            </Button>
          )}
        </div>
      )}
    </PageFrame>
  );
}
