"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  setShowBookmarksBar,
  showBookmarksBar,
  subscribeBrowserPrefs,
} from "@/lib/browser-prefs";
import { desktopBridge } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { BrowserImportDialog } from "@/components/center-tabs/browser-home-page";
import styles from "./settings-page.module.css";

export function BrowserSettings() {
  const { text } = useTranslation();
  const bridge = desktopBridge();
  const [bookmarksVisible, setBookmarksVisible] = useState(showBookmarksBar);
  const [showImport, setShowImport] = useState(false);
  const [clearHistory, setClearHistory] = useState(true);
  const [clearCookies, setClearCookies] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [clearResult, setClearResult] = useState("");

  useEffect(
    () => subscribeBrowserPrefs(() => setBookmarksVisible(showBookmarksBar())),
    [],
  );

  async function clearData() {
    if (!bridge?.browserData || (!clearHistory && !clearCookies)) return;
    setClearing(true);
    setClearResult("");
    try {
      const result = await bridge.browserData.clear({
        history: clearHistory,
        cookies: clearCookies,
      });
      setClearResult(result.ok
        ? text("Selected browsing data was cleared.", "已清除所选浏览数据。")
        : text("Browsing data could not be cleared.", "无法清除浏览数据。"));
    } catch {
      setClearResult(text("Browsing data could not be cleared.", "无法清除浏览数据。"));
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <h1 className={styles.pageTitle}>{text("Browser", "浏览器")}</h1>
        <p className={styles.pageMeta}>
          {text(
            "Manage the built-in browser appearance and local browsing data.",
            "管理内置浏览器的外观和本地浏览数据。",
          )}
        </p>
      </header>
      <div className={styles.pageBody}>
        <section>
          <h2 className={styles.sectionTitle}>{text("Appearance", "外观")}</h2>
          <div className={styles.card}>
            <div className={styles.row}>
              <span className={styles.label}>{text("Show bookmarks bar", "显示书签栏")}</span>
              <div className={styles.control}>
                <Switch
                  checked={bookmarksVisible}
                  onCheckedChange={(checked) => setShowBookmarksBar(checked)}
                  aria-label={text("Show bookmarks bar", "显示书签栏")}
                />
              </div>
            </div>
          </div>
        </section>

        {bridge?.browserImport ? (
          <section>
            <h2 className={styles.sectionTitle}>{text("Import", "导入")}</h2>
            {showImport ? (
              <BrowserImportDialog onDismiss={() => setShowImport(false)} />
            ) : (
              <div className={styles.card}>
                <div className={styles.row}>
                  <span className={styles.label}>
                    {text("Import history, bookmarks, and cookies from a local browser", "从本地浏览器导入历史、书签和 Cookie")}
                  </span>
                  <div className={styles.control}>
                    <Button variant="outline" size="sm" onClick={() => setShowImport(true)}>
                      {text("Import data", "导入资料")}
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </section>
        ) : null}

        <section id="clear-data">
          <h2 className={styles.sectionTitle}>{text("Clear browsing data", "清除浏览数据")}</h2>
          <div className={styles.card}>
            <label className={styles.row}>
              <span className={styles.label}>{text("Browsing history", "浏览历史")}</span>
              <span className={styles.control}>
                <input type="checkbox" checked={clearHistory} onChange={(event) => setClearHistory(event.target.checked)} />
              </span>
            </label>
            <label className={styles.row}>
              <span className={styles.label}>Cookies</span>
              <span className={styles.control}>
                <input type="checkbox" checked={clearCookies} onChange={(event) => setClearCookies(event.target.checked)} />
              </span>
            </label>
            <div className={styles.row}>
              <span className={styles.pageMeta}>
                {text("Bookmarks are not removed.", "不会删除书签。")}
              </span>
              <div className={styles.control}>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={clearing || !bridge?.browserData || (!clearHistory && !clearCookies)}
                  onClick={() => void clearData()}
                >
                  {clearing ? text("Clearing…", "正在清除…") : text("Clear data", "清除资料")}
                </Button>
              </div>
            </div>
            {clearResult ? <p className={styles.pageMeta} role="status">{clearResult}</p> : null}
          </div>
        </section>
      </div>
    </div>
  );
}
