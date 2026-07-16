"use client";

/**
 * NewTabPage — the ＋ tab's content. One big "New session" action
 * that triggers the existing new-chat flow (window.newSession, same
 * as the left sidebar's New chat); the resulting draft session tab
 * replaces this page in place, browser-style.
 */
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";

export function NewTabPage() {
  const { text } = useTranslation();

  function onNewSession() {
    (window as unknown as { newSession?: () => void }).newSession?.();
  }

  return (
    <div className={styles.ntp}>
      <h2 className={styles.ntpTitle}>{text("New tab", "新标签页")}</h2>
      <button type="button" className={styles.ntpCard} onClick={onNewSession}>
        <span aria-hidden="true">💬</span>
        {text("New session", "新会话")}
      </button>
      <div className={styles.ntpHint}>
        {text(
          "Sessions and files both open as tabs here",
          "会话和文件都会在这里以标签页打开",
        )}
      </div>
    </div>
  );
}
