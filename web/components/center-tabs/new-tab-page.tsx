"use client";

/**
 * NewTabPage — the ＋ tab's content. One big "New session" action
 * that triggers the existing new-chat flow (window.newSession, same
 * as the left sidebar's New chat); the resulting draft session tab
 * replaces this page in place, browser-style. Below it, a Browse-web
 * card + URL row opens a web tab (kind "web") the same way.
 */
import { useRef, useState } from "react";
import { Globe } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { normalizeWebUrl, useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

export function NewTabPage() {
  const { text } = useTranslation();
  const openWebTab = useCenterTabs((s) => s.openWebTab);
  const [url, setUrl] = useState("");
  const urlInputRef = useRef<HTMLInputElement>(null);

  function onNewSession() {
    (window as unknown as { newSession?: () => void }).newSession?.();
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
      <button type="button" className={styles.ntpCard} onClick={onNewSession}>
        <span aria-hidden="true">💬</span>
        {text("New session", "新会话")}
      </button>
      <button
        type="button"
        className={styles.ntpCard}
        onClick={() => urlInputRef.current?.focus()}
      >
        <Globe size={14} aria-hidden="true" />
        {text("Browse web", "浏览网页")}
      </button>
      <div className={styles.ntpUrlRow}>
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
      <div className={styles.ntpHint}>
        {text(
          "Sessions, files and web pages all open as tabs here",
          "会话、文件和网页都会在这里以标签页打开",
        )}
      </div>
    </div>
  );
}
