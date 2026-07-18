"use client";

/**
 * NewTabPage — the new-tab button's content. Exactly two entries:
 * a "New session" card (triggers window.newSession, same as the left
 * sidebar's New chat — the draft session tab replaces this page in
 * place) and a URL row that IS the browser entry (globe + input + Go,
 * opens a web tab). No separate Browse-web button, no caption.
 */
import { useEffect, useRef, useState } from "react";

import { Bookmark, X } from "lucide-react";

import {
  EarthIcon,
  MessageCircleIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";

import { useTranslation } from "@/lib/i18n";
import {
  BOOKMARKS_CHANGE_EVENT,
  readBookmarks,
  removeBookmark,
  type Bookmark as BookmarkItem,
} from "@/lib/bookmarks";
import { normalizeWebUrl, useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

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

  useEffect(() => {
    const refresh = () => setBookmarks(readBookmarks());
    refresh();
    window.addEventListener(BOOKMARKS_CHANGE_EVENT, refresh);
    return () => window.removeEventListener(BOOKMARKS_CHANGE_EVENT, refresh);
  }, []);

  function onNewSession() {
    const draftId = useCenterTabs.getState().claimDraftSessionTab();
    (window as unknown as { newSession?: (draftId?: string) => void })
      .newSession?.(draftId);
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
