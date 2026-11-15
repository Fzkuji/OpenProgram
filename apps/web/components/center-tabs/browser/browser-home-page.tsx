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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { importBookmarkTree } from "@/lib/tabs/browser/bookmarks";
import {
  browserImportPromptFinished,
  consumeBrowserImportRequest,
  markBrowserImportPromptFinished,
} from "@/lib/browser/browser-prefs";
import {
  addShortcut,
  hostOf,
  readShortcuts,
  removeShortcut,
  subscribeShortcuts,
  type Shortcut,
} from "@/lib/tabs/browser/ntp-shortcuts";
import {
  desktopBridge,
  type DesktopBrowserImportSource,
} from "@/lib/desktop/desktop-bridge";
import { LANE_COLORS } from "@/lib/format-utils/lane-colors";
import { normalizeWebUrl, useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { BrowserGlyph } from "./toolbar/browser-glyph";
import { BookmarksLibraryButton, BrowserMenu } from "./toolbar/browser-controls";
import { BookmarkBar } from "./bookmark-bar";
import styles from "./browser.module.css";
import { webToolbarButton } from "./toolbar/toolbar-button";
import { AddressField, addressStarButton, ToolbarTip } from "./toolbar/browser-chrome";
import { cachedFavicon, loadFavicon } from "@/lib/browser/favicon-cache";
import { Checkbox } from "@/components/ui/checkbox";

function hostColor(host: string): string {
  let hash = 0;
  for (let i = 0; i < host.length; i += 1) hash = (hash * 31 + host.charCodeAt(i)) >>> 0;
  return LANE_COLORS[hash % LANE_COLORS.length];
}

function siteIconUrl(url: string): string | undefined {
  try {
    const page = new URL(url);
    if (!/^https?:$/.test(page.protocol) || page.username || page.password) return undefined;
    return `${page.origin}/favicon.ico`;
  } catch {
    return undefined;
  }
}

/** The site's own icon; its initial in the site colour when it has none. */
function ShortcutIcon({ shortcut }: { shortcut: Shortcut }) {
  const source = siteIconUrl(shortcut.url);
  const [icon, setIcon] = useState(() => cachedFavicon(source));
  useEffect(() => {
    let current = true;
    void loadFavicon(source).then((value) => { if (current) setIcon(value); });
    return () => { current = false; };
  }, [source]);
  const paint = (node: HTMLCanvasElement | null) => {
    if (node && icon) node.getContext("2d")?.drawImage(icon, 0, 0);
  };
  if (icon) return <canvas ref={paint} width={32} height={32} aria-hidden="true" />;
  if (icon === undefined) return null;
  const host = hostOf(shortcut.url);
  return (
    <span className={styles.ntpTileInitial} style={{ color: hostColor(host || shortcut.label) }}>
      {(shortcut.label || host || "?").charAt(0).toUpperCase()}
    </span>
  );
}

function ShortcutTile({ shortcut, onOpen }: { shortcut: Shortcut; onOpen(): void }) {
  const { text } = useTranslation();
  return (
    <div className={`${styles.ntpTile} relative w-[68px]`}>
      <ToolbarTip label={shortcut.url}>
        <button type="button" className={styles.ntpTileOpen} onClick={onOpen}>
          <span className={styles.ntpTileIcon}>
            <ShortcutIcon shortcut={shortcut} />
          </span>
          <span className={styles.ntpTileLabel}>{shortcut.label}</span>
        </button>
      </ToolbarTip>
      <button
        type="button"
        className={styles.ntpTileRemove}
        onClick={() => removeShortcut(shortcut.url)}
        aria-label={text("Remove shortcut", "删除快捷方式")}
      >
        <X size={12} aria-hidden="true" />
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
          <div className={`${styles.browserImportOptions} flex flex-wrap items-center gap-[8px_12px] mt-[10px]`}>
            {sources.length === 0 ? (
              <span>{text("No supported browser profiles found", "未发现支持的浏览器 profile")}</span>
            ) : (
              <>
                <SimpleSelect
                  value={browser?.id ?? ""}
                  onValueChange={(value) => {
                    const next = sources.find((item) => item.id === value);
                    setBrowserId(value);
                    setProfileId(next?.profiles[0]?.id ?? "");
                  }}
                  className="max-w-[190px]"
                  aria-label={text("Source browser", "来源浏览器")}
                  options={sources.map((source) => ({ value: source.id, label: source.name }))}
                />
                <SimpleSelect
                  value={profile?.id ?? ""}
                  onValueChange={setProfileId}
                  className="max-w-[190px]"
                  aria-label={text("Browser profile", "浏览器 profile")}
                  options={(browser?.profiles ?? []).map((item) => ({ value: item.id, label: item.name }))}
                />
                {(["history", "bookmarks", "cookies"] as const).map((item) => (
                  <label key={item}>
                    <Checkbox
                      checked={selected[item] && !!profile?.available[item]}
                      disabled={!profile?.available[item] || busy}
                      onCheckedChange={(checked) => setSelected((value) => ({ ...value, [item]: (checked === true) }))}
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
      <Button type="button" variant="elevated"
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
      </Button>
      <Button type="button" variant="ghost" size="icon-sm" disabled={busy} onClick={onDismiss} aria-label={text("Dismiss", "关闭")}>
        <X className="size-4" aria-hidden="true" />
      </Button>
    </section>
  );
}

export function BrowserHomePage() {
  const { text } = useTranslation();
  const menuOwnerId = useId();
  const shortcutFormId = useId();
  const shortcutNameRef = useRef<HTMLInputElement>(null);
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
  function changeShortcutEditor(open: boolean) {
    setAdding(open);
    if (!open) {
      setNewLabel("");
      setNewUrl("");
    }
  }
  function submitShortcut() {
    const normalized = normalizeWebUrl(newUrl);
    if (!normalized) return;
    addShortcut({ url: normalized, label: newLabel });
    changeShortcutEditor(false);
  }

  return (
    <div className={`${styles.browserHome} ${styles.webPane}`}>
      <div className={`${styles.webChrome} relative flex flex-col shrink-0`}>
      <div className={styles.webToolbar}>
        <button type="button" className={webToolbarButton()} disabled aria-label={text("Back", "后退")}>
          <ArrowLeft size={14} aria-hidden="true" />
        </button>
        <button type="button" className={webToolbarButton(styles.webToolbarForward)} disabled aria-label={text("Forward", "前进")}>
          <ArrowRight size={14} aria-hidden="true" />
        </button>
        <button type="button" className={webToolbarButton()} disabled aria-label={text("Reload", "重新加载")}>
          <RotateCw size={14} aria-hidden="true" />
        </button>
        <button type="button" className={webToolbarButton(styles.webToolbarMedium)} disabled aria-label={text("Home", "主页")}>
          <House size={14} aria-hidden="true" />
        </button>
        <AddressField
          value={url}
          onChange={setUrl}
          onSubmit={() => go()}
          placeholder={text("Search or enter a URL", "搜索或输入网址")}
          ariaLabel={text("Address", "地址")}
          autoFocus
        >
          <button type="button" className={addressStarButton()} disabled aria-label={text("Bookmark", "添加书签")}>
            <Star size={14} aria-hidden="true" />
          </button>
        </AddressField>
        <BookmarksLibraryButton />
        {canImport ? (
          <ToolbarTip label={text("Import browser data", "导入浏览器资料")}>
            <button
              type="button"
              className={webToolbarButton(styles.webToolbarMedium)}
              onClick={() => setShowImport(true)}
              aria-label={text("Import browser data", "导入浏览器资料")}
            >
              <Download size={16} aria-hidden="true" />
            </button>
          </ToolbarTip>
        ) : null}
        <button type="button" className={webToolbarButton(styles.webToolbarMedium)} disabled aria-label={text("Open in browser", "在浏览器中打开")}>
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
      <div className={`${styles.browserHomeBody} flex-1 min-h-0 overflow-y-auto flex flex-col items-center gap-[22px] p-[24px]`}>
        {canImport && showImport && (
          <BrowserImportDialog onDismiss={() => {
            markBrowserImportPromptFinished();
            setShowImport(false);
          }} />
        )}
        <div className="flex flex-wrap justify-center gap-[4px] w-[min(560px,_82%)]">
          {shortcuts.map((shortcut) => (
            <ShortcutTile key={shortcut.url} shortcut={shortcut} onOpen={() => go(shortcut.url)} />
          ))}
          <div className={`${styles.ntpTile} relative w-[68px]`}>
            <Popover open={adding} onOpenChange={changeShortcutEditor}>
              <PopoverTrigger asChild>
                <button type="button" className={styles.ntpTileOpen}>
                  <span className={styles.ntpTileIcon}><Plus size={20} aria-hidden="true" /></span>
                  <span className={styles.ntpTileLabel}>{text("Add", "添加")}</span>
                </button>
              </PopoverTrigger>
              <PopoverContent
                className={styles.ntpShortcutPopover}
                align="end"
                sideOffset={8}
                collisionPadding={12}
                hideWhenDetached
                aria-labelledby={`${shortcutFormId}-title`}
                onOpenAutoFocus={(event) => {
                  event.preventDefault();
                  shortcutNameRef.current?.focus();
                }}
                onEscapeKeyDown={(event) => {
                  if (event.isComposing || event.keyCode === 229) event.preventDefault();
                }}
              >
                <div className={`${styles.ntpShortcutHeader} flex items-center justify-between gap-[12px]`}>
                  <h2 id={`${shortcutFormId}-title`}>{text("Add shortcut", "添加快捷方式")}</h2>
                  <Button type="button" variant="ghost" size="icon-sm" onClick={() => changeShortcutEditor(false)} aria-label={text("Close", "关闭")}>
                    <X aria-hidden="true" />
                  </Button>
                </div>
                <form
                  className={styles.ntpShortcutForm}
                  onSubmit={(event) => { event.preventDefault(); submitShortcut(); }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && (event.nativeEvent.isComposing || event.keyCode === 229)) event.preventDefault();
                  }}
                >
                  <label className={styles.ntpShortcutField} htmlFor={`${shortcutFormId}-name`}>
                    {text("Name (optional)", "名称（选填）")}
                    <Input ref={shortcutNameRef} id={`${shortcutFormId}-name`} className={styles.ntpShortcutInput} value={newLabel} onChange={(event) => setNewLabel(event.target.value)} placeholder={text("e.g. GitHub", "例如 GitHub")} autoComplete="off" />
                  </label>
                  <label className={styles.ntpShortcutField} htmlFor={`${shortcutFormId}-address`}>
                    {text("Address", "地址")}
                    <Input id={`${shortcutFormId}-address`} className={styles.ntpShortcutInput} value={newUrl} onChange={(event) => setNewUrl(event.target.value)} placeholder="example.com" autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} />
                  </label>
                  <div className="flex justify-end gap-[6px] pt-[2px]">
                    <Button type="button" variant="ghost" onClick={() => changeShortcutEditor(false)}>{text("Cancel", "取消")}</Button>
                    <Button type="submit" disabled={!newUrl.trim()}>{text("Add", "添加")}</Button>
                  </div>
                </form>
              </PopoverContent>
            </Popover>
          </div>
        </div>
      </div>
    </div>
  );
}
