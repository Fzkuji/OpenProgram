"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Download,
  ExternalLink,
  House,
  Plus,
  RotateCw,
  Star,
  X,
} from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { importBookmarkTree } from "@/lib/bookmarks";
import {
  browserImportPromptFinished,
  consumeBrowserImportRequest,
  markBrowserImportPromptFinished,
} from "@/lib/browser-prefs";
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
import { BrowserGlyph } from "./browser-glyph";
import { BookmarkBar, BookmarksLibraryButton, BrowserMenu } from "./browser-controls";
import styles from "./center-tabs.module.css";

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

export function BrowserImportDialog({
  onDismiss,
  onImported,
}: {
  onDismiss(): void;
  onImported?(): void;
}) {
  const { text } = useTranslation();
  const api = desktopBridge()?.browserImport;
  const [sources, setSources] = useState<DesktopBrowserImportSource[]>([]);
  const [browserId, setBrowserId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState({ history: true, bookmarks: true, cookies: true });
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [result, setResult] = useState("");
  const activeRequestId = useRef<string | null>(null);

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
    const requestId = crypto.randomUUID();
    activeRequestId.current = requestId;
    setBusy(true);
    setCancelling(false);
    setResult("");
    try {
      const response = await api!.run({ requestId, browserId: browser.id, profileId: profile.id, items });
      if (!response.ok) {
        setResult(response.error === "import_cancelled"
          ? text("Import cancelled", "已取消导入")
          : text(`Import failed: ${response.error ?? "unknown error"}`, `导入失败：${response.error ?? "未知错误"}`));
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
      markBrowserImportPromptFinished();
      onImported?.();
    } catch {
      setResult(text("Import failed", "导入失败"));
    } finally {
      if (activeRequestId.current === requestId) activeRequestId.current = null;
      setBusy(false);
      setCancelling(false);
    }
  }

  async function cancelImport() {
    const requestId = activeRequestId.current;
    if (!requestId || typeof api?.cancel !== "function" || cancelling) return;
    setCancelling(true);
    const accepted = await api.cancel(requestId).catch(() => false);
    if (!accepted) setCancelling(false);
  }

  return (
    <section className={styles.browserImport} aria-label={text("Import browser data", "导入浏览器资料")}>
      <BrowserGlyph size={30} />
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
        disabled={cancelling || (busy && typeof api.cancel !== "function") || (!busy && expanded && sources.length === 0)}
        onClick={() => busy ? void cancelImport() : expanded ? void runImport() : setExpanded(true)}
      >
        {cancelling
          ? text("Cancelling…", "正在取消…")
          : busy && typeof api.cancel === "function"
            ? text("Cancel", "取消")
            : busy
              ? text("Importing…", "正在导入…")
              : text("Import", "导入")}
      </button>
      <button type="button" className={styles.browserImportDismiss} disabled={busy} onClick={onDismiss} aria-label={text("Dismiss", "关闭")}>
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  );
}

export function BrowserHomePage() {
  const { text } = useTranslation();
  const menuOwnerId = useId();
  const canImport = Boolean(desktopBridge()?.browserImport);
  const openWebTab = useCenterTabs((state) => state.openWebTab);
  const [url, setUrl] = useState("");
  const [shortcuts, setShortcuts] = useState<Shortcut[]>(readShortcuts);
  const [adding, setAdding] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [showImport, setShowImport] = useState(() => {
    if (typeof window === "undefined") return false;
    return consumeBrowserImportRequest() || !browserImportPromptFinished();
  });

  useEffect(() => {
    const refresh = () => setShortcuts(readShortcuts());
    return subscribeShortcuts(refresh);
  }, []);

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
    <div className={`${styles.browserHome} ${styles.webPane}`}>
      <div className={styles.webChrome}>
      <div className={styles.webToolbar}>
        <button type="button" className={styles.webToolbarBtn} disabled title={text("Back", "后退")} aria-label={text("Back", "后退")}>
          <ArrowLeft size={14} aria-hidden="true" />
        </button>
        <button type="button" className={`${styles.webToolbarBtn} ${styles.webToolbarForward}`} disabled title={text("Forward", "前进")} aria-label={text("Forward", "前进")}>
          <ArrowRight size={14} aria-hidden="true" />
        </button>
        <button type="button" className={styles.webToolbarBtn} disabled title={text("Reload", "重新加载")} aria-label={text("Reload", "重新加载")}>
          <RotateCw size={14} aria-hidden="true" />
        </button>
        <button type="button" className={`${styles.webToolbarBtn} ${styles.webToolbarMedium}`} disabled title={text("Home", "主页")} aria-label={text("Home", "主页")}>
          <House size={14} aria-hidden="true" />
        </button>
        <input
          className={styles.webAddress}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") go(); }}
          placeholder={text("Search or enter a URL", "搜索或输入网址")}
          aria-label={text("Address", "地址")}
          autoFocus
        />
        <button type="button" className={styles.webToolbarBtn} disabled title={text("Bookmark", "添加书签")} aria-label={text("Bookmark", "添加书签")}>
          <Star size={14} aria-hidden="true" />
        </button>
        <BookmarksLibraryButton />
        {canImport ? (
          <button
            type="button"
            className={`${styles.webToolbarBtn} ${styles.webToolbarMedium}`}
            onClick={() => setShowImport(true)}
            title={text("Import browser data", "导入浏览器资料")}
            aria-label={text("Import browser data", "导入浏览器资料")}
          >
            <Download size={15} aria-hidden="true" />
          </button>
        ) : null}
        <button type="button" className={`${styles.webToolbarBtn} ${styles.webToolbarMedium}`} disabled title={text("Open in browser", "在浏览器中打开")} aria-label={text("Open in browser", "在浏览器中打开")}>
          <ExternalLink size={14} aria-hidden="true" />
        </button>
        <BrowserMenu
          ownerId={menuOwnerId}
          actions={{ home: () => {}, forward: () => {}, openExternal: () => {} }}
          canGoForward={false}
          canGoHome={false}
          canOpenExternal={false}
        />
      </div>
      <BookmarkBar ownerId={menuOwnerId} onNavigate={go} />
      </div>
      <div className={styles.browserHomeBody}>
        {canImport && showImport && (
          <BrowserImportDialog onDismiss={() => {
            markBrowserImportPromptFinished();
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
