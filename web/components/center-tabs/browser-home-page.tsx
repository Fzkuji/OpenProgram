"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, Globe2, Plus, X } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { importBookmarkTree } from "@/lib/bookmarks";
import {
  addShortcut,
  faviconUrl,
  hostOf,
  readShortcuts,
  removeShortcut,
  subscribeShortcuts,
  type Shortcut,
} from "@/lib/ntp-shortcuts";
import {
  desktopBridge,
  type DesktopBrowserImportSource,
} from "@/lib/desktop-bridge";
import { LANE_COLORS } from "@/lib/format-utils/lane-colors";
import { normalizeWebUrl, useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

const IMPORT_DISMISSED_KEY = "openprogram.browser-import-dismissed";

function hostColor(host: string): string {
  let hash = 0;
  for (let i = 0; i < host.length; i += 1) hash = (hash * 31 + host.charCodeAt(i)) >>> 0;
  return LANE_COLORS[hash % LANE_COLORS.length];
}

function ShortcutTile({ shortcut, onOpen }: { shortcut: Shortcut; onOpen(): void }) {
  const { text } = useTranslation();
  const [broken, setBroken] = useState(false);
  const host = hostOf(shortcut.url);
  const src = faviconUrl(shortcut.url);
  return (
    <div className={styles.ntpTile}>
      <button type="button" className={styles.ntpTileOpen} onClick={onOpen} title={shortcut.url}>
        <span className={styles.ntpTileIcon}>
          {broken || !src ? (
            <span className={styles.ntpTileInitial} style={{ background: hostColor(host || shortcut.label) }}>
              {(shortcut.label || host || "?").charAt(0).toUpperCase()}
            </span>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={src}
              alt=""
              width={24}
              height={24}
              onError={() => setBroken(true)}
              onLoad={(event) => {
                if (event.currentTarget.naturalWidth <= 16) setBroken(true);
              }}
            />
          )}
        </span>
        <span className={styles.ntpTileLabel}>{shortcut.label}</span>
      </button>
      <button
        type="button"
        className={styles.ntpTileRemove}
        onClick={() => removeShortcut(shortcut.url)}
        aria-label={text("Remove shortcut", "删除快捷方式")}
      >
        <X size={11} aria-hidden="true" />
      </button>
    </div>
  );
}

export function BrowserImportDialog({ onDismiss }: { onDismiss(): void }) {
  const { text } = useTranslation();
  const api = desktopBridge()?.browserImport;
  const [sources, setSources] = useState<DesktopBrowserImportSource[]>([]);
  const [browserId, setBrowserId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState({ history: true, bookmarks: true, cookies: true });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");

  useEffect(() => {
    if (!api) return;
    void api.listSources().then((items) => {
      setSources(items);
      const first = items[0];
      setBrowserId(first?.id ?? "");
      setProfileId(first?.profiles[0]?.id ?? "");
    }).catch(() => setSources([]));
  }, [api]);

  const browser = sources.find((item) => item.id === browserId) ?? sources[0];
  const profile = browser?.profiles.find((item) => item.id === profileId) ?? browser?.profiles[0];
  if (!api) return null;

  async function runImport() {
    if (!browser || !profile) return;
    const items = (["history", "bookmarks", "cookies"] as const).filter(
      (item) => selected[item] && profile.available[item],
    );
    if (items.length === 0) return;
    setBusy(true);
    setResult("");
    try {
      const response = await api!.run({ browserId: browser.id, profileId: profile.id, items });
      if (!response.ok) {
        setResult(text(`Import failed: ${response.error ?? "unknown error"}`, `导入失败：${response.error ?? "未知错误"}`));
        return;
      }
      const bookmarks = response.bookmarks ?? [];
      const bookmarkCount = importBookmarkTree(
        bookmarks,
        response.source ? `Imported from ${response.source.label}` : undefined,
      );
      setResult(text(
        `Imported ${response.history?.imported ?? 0} history entries, ${bookmarkCount} bookmarks, and ${response.cookies?.imported ?? 0} cookies.`,
        `已导入 ${response.history?.imported ?? 0} 条历史、${bookmarkCount} 个书签和 ${response.cookies?.imported ?? 0} 个 Cookie。`,
      ));
    } catch {
      setResult(text("Import failed", "导入失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.browserImport} aria-label={text("Import browser data", "导入浏览器资料")}>
      <Download size={22} aria-hidden="true" />
      <div className={styles.browserImportBody}>
        <strong>{text("Import data from your browser", "从浏览器导入资料")}</strong>
        <span>{text("Bring over history, bookmarks, and signed-in cookies", "导入历史、书签和登录 Cookie")}</span>
        {expanded && (
          <div className={styles.browserImportOptions}>
            {sources.length === 0 ? (
              <span>{text("No supported browser profiles found", "未发现支持的浏览器 profile")}</span>
            ) : (
              <>
                <select
                  value={browser?.id ?? ""}
                  onChange={(event) => {
                    const next = sources.find((item) => item.id === event.target.value);
                    setBrowserId(event.target.value);
                    setProfileId(next?.profiles[0]?.id ?? "");
                  }}
                  aria-label={text("Source browser", "来源浏览器")}
                >
                  {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
                </select>
                <select
                  value={profile?.id ?? ""}
                  onChange={(event) => setProfileId(event.target.value)}
                  aria-label={text("Browser profile", "浏览器 profile")}
                >
                  {browser?.profiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                {(["history", "bookmarks", "cookies"] as const).map((item) => (
                  <label key={item}>
                    <input
                      type="checkbox"
                      checked={selected[item] && !!profile?.available[item]}
                      disabled={!profile?.available[item] || busy}
                      onChange={(event) => setSelected((value) => ({ ...value, [item]: event.target.checked }))}
                    />
                    {item === "history"
                      ? text("History", "历史")
                      : item === "bookmarks"
                        ? text("Bookmarks", "书签")
                        : "Cookies"}
                  </label>
                ))}
              </>
            )}
            {result && <span role="status">{result}</span>}
          </div>
        )}
      </div>
      <button
        type="button"
        className={styles.browserImportAction}
        disabled={busy || (expanded && sources.length === 0)}
        onClick={() => expanded ? void runImport() : setExpanded(true)}
      >
        {busy ? text("Importing…", "正在导入…") : text("Import", "导入")}
      </button>
      <button type="button" className={styles.browserImportDismiss} onClick={onDismiss} aria-label={text("Dismiss", "关闭")}>
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  );
}

export function BrowserHomePage() {
  const { text } = useTranslation();
  const openWebTab = useCenterTabs((state) => state.openWebTab);
  const [url, setUrl] = useState("");
  const [shortcuts, setShortcuts] = useState<Shortcut[]>(readShortcuts);
  const [adding, setAdding] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [showImport, setShowImport] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(IMPORT_DISMISSED_KEY) !== "1";
  });

  useEffect(() => {
    const refresh = () => setShortcuts(readShortcuts());
    return subscribeShortcuts(refresh);
  }, []);

  const canGo = useMemo(() => !!normalizeWebUrl(url), [url]);
  function go(value = url) {
    const normalized = normalizeWebUrl(value);
    if (normalized) openWebTab(normalized);
  }
  function submitShortcut() {
    const normalized = normalizeWebUrl(newUrl);
    if (!normalized) return;
    addShortcut({ url: normalized, label: newLabel });
    setNewLabel("");
    setNewUrl("");
    setAdding(false);
  }

  return (
    <div className={styles.browserHome}>
      <div className={styles.browserHomeToolbar}>
        <Globe2 size={17} aria-hidden="true" />
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") go(); }}
          placeholder={text("Search or enter a URL", "搜索或输入网址")}
          aria-label={text("Address", "地址")}
          autoFocus
        />
        <button
          type="button"
          onClick={() => {
            localStorage.removeItem(IMPORT_DISMISSED_KEY);
            setShowImport(true);
          }}
          title={text("Import browser data", "导入浏览器资料")}
          aria-label={text("Import browser data", "导入浏览器资料")}
        >
          <Download size={15} aria-hidden="true" />
        </button>
        <button type="button" disabled={!canGo} onClick={() => go()}>{text("Open", "打开")}</button>
      </div>
      <div className={styles.browserHomeBody}>
        {showImport && (
          <BrowserImportDialog onDismiss={() => {
            localStorage.setItem(IMPORT_DISMISSED_KEY, "1");
            setShowImport(false);
          }} />
        )}
        <div className={styles.ntpTiles}>
          {shortcuts.map((shortcut) => (
            <ShortcutTile key={shortcut.url} shortcut={shortcut} onOpen={() => go(shortcut.url)} />
          ))}
          <div className={styles.ntpTile}>
            <button type="button" className={styles.ntpTileOpen} onClick={() => setAdding(true)}>
              <span className={`${styles.ntpTileIcon} ${styles.ntpTileAdd}`}><Plus size={18} aria-hidden="true" /></span>
              <span className={styles.ntpTileLabel}>{text("Add", "添加")}</span>
            </button>
          </div>
        </div>
        {adding && (
          <div className={styles.ntpUrlRow}>
            <input className={styles.ntpTileFormLabel} value={newLabel} onChange={(event) => setNewLabel(event.target.value)} placeholder={text("Name", "名称")} />
            <input className={styles.ntpUrlInput} value={newUrl} onChange={(event) => setNewUrl(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") submitShortcut(); }} placeholder="URL" />
            <button type="button" className={styles.ntpUrlGo} onClick={submitShortcut}>{text("Add", "添加")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
