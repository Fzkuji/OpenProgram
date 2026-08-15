"use client";

import { FileText, MessageCirclePlus, TerminalSquare } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { newSession } from "@/lib/runtime-bridge/conversations";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";
import { BrowserGlyph } from "./browser-glyph";

export function NewTabPage() {
  const { text } = useTranslation();
  const openBuiltinTab = useCenterTabs((state) => state.openBuiltinTab);

  function openSideChat() {
    const draftId = useCenterTabs.getState().claimDraftSessionTab();
    newSession(draftId);
  }

  return (
    <div className={styles.ntp}>
      <div className={styles.ntpLauncher}>
        <button type="button" className={styles.ntpCard} onClick={() => openBuiltinTab("files")}>
          <span className={styles.ntpGlyph} data-tone="files" aria-hidden="true">
            <FileText size={11} strokeWidth={2.1} />
          </span>
          {text("Files", "文件")}
        </button>
        <button type="button" className={styles.ntpCard} onClick={openSideChat}>
          <span className={styles.ntpGlyph} data-tone="chat" aria-hidden="true">
            <MessageCirclePlus size={11} strokeWidth={2.1} />
          </span>
          {text("Side chat", "侧边聊天")}
        </button>
        <button type="button" className={styles.ntpCard} onClick={() => openBuiltinTab("browser")}>
          <BrowserGlyph size={18} />
          {text("Browser", "浏览器")}
        </button>
        <button type="button" className={styles.ntpCard} onClick={() => openBuiltinTab("terminal")}>
          <span className={styles.ntpGlyph} data-tone="terminal" aria-hidden="true">
            <TerminalSquare size={11} strokeWidth={2.1} />
          </span>
          {text("Terminal", "终端")}
        </button>
      </div>
    </div>
  );
}
