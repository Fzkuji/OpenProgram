"use client";

import { useEffect, useState } from "react";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";
import { useApplicationLauncher } from "@/components/applications/use-application-launcher";
import { AppWindow, FileText, MessageCirclePlus, TerminalSquare } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { newSession } from "@/lib/runtime-bridge/conversations";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";
import { BrowserGlyph } from "./browser-glyph";

export function NewTabPage() {
  const { text } = useTranslation();
  const openBuiltinTab = useCenterTabs((state) => state.openBuiltinTab);

  const [applications, setApplications] = useState<ApplicationDefinition[]>([]);
  const [error, setError] = useState("");
  const { launch, error: launchError, dialog } = useApplicationLauncher();
  useEffect(() => {
    let disposed = false;
    const refresh = () => applicationRequest<{ applications: ApplicationDefinition[] }>("/api/applications")
      .then(value => { if (!disposed) { setApplications(value.applications); setError(""); } })
      .catch(reason => { if (!disposed) setError(String(reason.message ?? reason)); });
    void refresh();
    window.addEventListener("focus", refresh);
    const timer = setInterval(refresh, 10000);
    return () => { disposed = true; clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, []);
  function openNewChat() {
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
        <button type="button" className={styles.ntpCard} onClick={openNewChat}>
          <span className={styles.ntpGlyph} data-tone="chat" aria-hidden="true">
            <MessageCirclePlus size={11} strokeWidth={2.1} />
          </span>
          {text("New chat", "新建对话")}
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
        {applications.filter(application => application.enabled && !application.hidden).map(application => (
          <button key={application.id} type="button" className={styles.ntpCard} onClick={() => void launch(application)}>
            <span className={styles.ntpGlyph} data-tone="files" aria-hidden="true"><AppWindow size={11} /></span>
            {application.display_title || application.title}
          </button>
        ))}
        {dialog}
        {(error || launchError) && <p role="alert" className="text-sm text-text-muted">{error || launchError}</p>}
      </div>
    </div>
  );
}
