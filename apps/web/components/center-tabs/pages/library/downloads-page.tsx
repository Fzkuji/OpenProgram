"use client";

/** The Downloads list. */
import { useCallback, useEffect, useState } from "react";
import { Download, Folder, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { desktopBridge, type DesktopDownloadEntry } from "@/lib/desktop/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import styles from "../pages.module.css";
import { PageFrame } from "./page-frame";

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

export function DownloadsPage() {
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
            <Download size={20} aria-hidden="true" />
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
      icon={<Download size={20} aria-hidden="true" />}
      title={title}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder={text("Search downloads", "搜索下载内容")}
      action={entries?.some((entry) => !entry.active) ? (
        <Button type="button" variant="elevated"
          onClick={() => void api.clear().then(refresh)}
        >
          <Trash2 size={14} aria-hidden="true" />
          <span>{text("Clear", "清除记录")}</span>
        </Button>
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
                <Download size={16} aria-hidden="true" />
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
                <span className="browsing-history-host">{progress}</span>
                {entry.active ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="browsing-history-delete"
                    onClick={() => void api.cancel(entry.id)}
                    title={text("Cancel download", "取消下载")}
                    aria-label={text("Cancel download", "取消下载")}
                  >
                    <X aria-hidden="true" />
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="browsing-history-delete"
                    onClick={() => void api.show(entry.id)}
                    title={text("Show in folder", "在文件夹中显示")}
                    aria-label={text("Show in folder", "在文件夹中显示")}
                  >
                    <Folder aria-hidden="true" />
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </PageFrame>
  );
}
