"use client";

import { FileText, Globe2, MessageCirclePlus, TerminalSquare } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { newSession } from "@/lib/runtime-bridge/conversations";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

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
          <FileText size={15} aria-hidden="true" />
          {text("Files", "文件")}
        </button>
        <button type="button" className={styles.ntpCard} onClick={openSideChat}>
          <MessageCirclePlus size={15} aria-hidden="true" />
          {text("Side chat", "侧边聊天")}
        </button>
        <button type="button" className={styles.ntpCard} onClick={() => openBuiltinTab("browser")}>
          <Globe2 size={15} aria-hidden="true" />
          {text("Browser", "浏览器")}
        </button>
        <button type="button" className={styles.ntpCard} onClick={() => openBuiltinTab("terminal")}>
          <TerminalSquare size={15} aria-hidden="true" />
          {text("Terminal", "终端")}
        </button>
      </div>
    </div>
  );
}
