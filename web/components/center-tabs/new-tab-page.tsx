"use client";

/**
 * NewTabPage — the new-tab button's content. Exactly two entries:
 * a "New session" card (triggers newSession, same as the left
 * sidebar's New chat — the draft session tab replaces this page in
 * place) and a URL row that IS the browser entry (globe + input + Go,
 * opens a web tab). No separate Browse-web button, no caption.
 */
import { useEffect, useRef, useState } from "react";

import { Bookmark, Plus, X } from "lucide-react";

import {
  EarthIcon,
  MessageCircleIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";

import { useTranslation } from "@/lib/i18n";
import {
  readBookmarks,
  removeBookmark,
  subscribeBookmarks,
  type Bookmark as BookmarkItem,
} from "@/lib/bookmarks";
import {
  addShortcut,
  faviconUrl,
  hostOf,
  readShortcuts,
  removeShortcut,
  subscribeShortcuts,
  type Shortcut,
} from "@/lib/ntp-shortcuts";
import { LANE_COLORS } from "@/lib/format-utils/lane-colors";
import { normalizeWebUrl, useCenterTabs } from "@/lib/state/center-tabs-store";
import { newSession } from "@/lib/runtime-bridge/conversations";
import styles from "./center-tabs.module.css";

/** Stable per-host colour for the initial-letter fallback tile. */
function hostColor(host: string): string {
  let hash = 0;
  for (let i = 0; i < host.length; i += 1) hash = (hash * 31 + host.charCodeAt(i)) >>> 0;
  return LANE_COLORS[hash % LANE_COLORS.length];
}

function ShortcutTile({
  shortcut,
  onOpen,
  onRemove,
  removeLabel,
}: {
  shortcut: Shortcut;
  onOpen: () => void;
  onRemove: () => void;
  removeLabel: string;
}) {
  const [broken, setBroken] = useState(false);
  const host = hostOf(shortcut.url);
  const src = faviconUrl(shortcut.url);
  return (
    <div className={styles.ntpTile}>
      <button type="button" className={styles.ntpTileOpen} onClick={onOpen} title={shortcut.url}>
        <span className={styles.ntpTileIcon}>
          {broken || !src ? (
            <span
              className={styles.ntpTileInitial}
              style={{ background: hostColor(host || shortcut.label) }}
            >
              {(shortcut.label || host || "?").charAt(0).toUpperCase()}
            </span>
          ) : (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={src}
              alt=""
              width={24}
              height={24}
              onError={() => setBroken(true)}
              // s2/favicons answers an unknown host with its generic globe
              // at HTTP 200, so onError never fires. That placeholder is
              // always 16px wide even though we ask for sz=64 — the only
              // signal that the real favicon is missing.
              onLoad={(e) => {
                if (e.currentTarget.naturalWidth <= 16) setBroken(true);
              }}
            />
          )}
        </span>
        <span className={styles.ntpTileLabel}>{shortcut.label}</span>
      </button>
      <button
        type="button"
        className={styles.ntpTileRemove}
        onClick={onRemove}
        aria-label={removeLabel}
      >
        <X size={11} aria-hidden="true" />
      </button>
    </div>
  );
}

export function NewTabPage() {
  const { text } = useTranslation();
  const openWebTab = useCenterTabs((s) => s.openWebTab);
  const [url, setUrl] = useState("");
  const urlInputRef = useRef<HTMLInputElement>(null);
  // Card hover drives the icon animation (controlled mode, same
  // wiring as the sidebar nav rows).
  const sessionIconRef = useRef<AnimatedNavIconHandle>(null);
  const webIconRef = useRef<AnimatedNavIconHandle>(null);
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>(readBookmarks);
  const [shortcuts, setShortcuts] = useState<Shortcut[]>([]);
  const [adding, setAdding] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const [newUrl, setNewUrl] = useState("");

  useEffect(() => {
    const refresh = () => setBookmarks(readBookmarks());
    refresh();
    return subscribeBookmarks(refresh);
  }, []);

  useEffect(() => {
    const refresh = () => setShortcuts(readShortcuts());
    refresh();
    return subscribeShortcuts(refresh);
  }, []);

  function submitShortcut() {
    const normalized = normalizeWebUrl(newUrl);
    if (!normalized) return;
    addShortcut({ url: normalized, label: newLabel });
    setNewLabel("");
    setNewUrl("");
    setAdding(false);
  }

  function onNewSession() {
    const draftId = useCenterTabs.getState().claimDraftSessionTab();
    newSession(draftId);
  }

  function go() {
    const normalized = normalizeWebUrl(url);
    if (!normalized) return;
    setUrl("");
    openWebTab(normalized);
  }

  return (
    <div className={styles.ntp}>
      <h2 className={styles.ntpTitle}>{text("New tab", "新标签页")}</h2>
      <button
        type="button"
        className={styles.ntpCard}
        onClick={onNewSession}
        onMouseEnter={() => sessionIconRef.current?.startAnimation()}
        onMouseLeave={() => sessionIconRef.current?.stopAnimation()}
      >
        <MessageCircleIcon ref={sessionIconRef} size={14} aria-hidden="true" />
        {text("New session", "新会话")}
      </button>
      {/* 浏览器入口 = 这一行本身：地球图标 + 网址输入 + 打开。 */}
      <div
        className={styles.ntpUrlRow}
        onMouseEnter={() => webIconRef.current?.startAnimation()}
        onMouseLeave={() => webIconRef.current?.stopAnimation()}
      >
        <EarthIcon ref={webIconRef} size={14} aria-hidden="true" />
        <input
          ref={urlInputRef}
          className={styles.ntpUrlInput}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") go();
          }}
          placeholder={text("Enter a URL — e.g. example.com", "输入网址，如 example.com")}
          spellCheck={false}
          autoComplete="off"
        />
        <button type="button" className={styles.ntpUrlGo} onClick={go}>
          {text("Go", "打开")}
        </button>
      </div>
      {/* Chrome 风格快捷方式网格：圆形图标 + 下方标签，末尾一个 + 磁贴。 */}
      <div className={styles.ntpTiles}>
        {shortcuts.map((shortcut) => (
          <ShortcutTile
            key={shortcut.url}
            shortcut={shortcut}
            onOpen={() => {
              const normalized = normalizeWebUrl(shortcut.url);
              if (normalized) openWebTab(normalized);
            }}
            onRemove={() => removeShortcut(shortcut.url)}
            removeLabel={text("Remove shortcut", "删除快捷方式")}
          />
        ))}
        <div className={styles.ntpTile}>
          <button
            type="button"
            className={styles.ntpTileOpen}
            onClick={() => setAdding(true)}
            title={text("Add shortcut", "添加快捷方式")}
          >
            <span className={`${styles.ntpTileIcon} ${styles.ntpTileAdd}`}>
              <Plus size={18} aria-hidden="true" />
            </span>
            <span className={styles.ntpTileLabel}>{text("Add", "添加")}</span>
          </button>
        </div>
      </div>
      {adding && (
        <div className={styles.ntpUrlRow}>
          <input
            className={styles.ntpTileFormLabel}
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitShortcut();
              if (e.key === "Escape") setAdding(false);
            }}
            placeholder={text("Name", "名称")}
            autoFocus
          />
          <input
            className={styles.ntpUrlInput}
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitShortcut();
              if (e.key === "Escape") setAdding(false);
            }}
            placeholder={text("URL — e.g. example.com", "网址，如 example.com")}
            spellCheck={false}
            autoComplete="off"
          />
          <button type="button" className={styles.ntpUrlGo} onClick={submitShortcut}>
            {text("Add", "添加")}
          </button>
        </div>
      )}
      {bookmarks.length > 0 && (
        <div className={styles.ntpBookmarks}>
          {bookmarks.map((bookmark) => (
            <div key={bookmark.url} className={styles.ntpBookmark}>
              <button
                type="button"
                className={styles.ntpBookmarkOpen}
                onClick={() => openWebTab(bookmark.url)}
                title={bookmark.url}
              >
                <Bookmark size={13} aria-hidden="true" />
                {bookmark.title || bookmark.url}
              </button>
              <button
                type="button"
                className={styles.ntpBookmarkDelete}
                onClick={() => removeBookmark(bookmark.url)}
                aria-label={text("Delete bookmark", "删除书签")}
              >
                <X size={13} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
