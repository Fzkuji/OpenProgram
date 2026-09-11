"use client";

import { useEffect, useState } from "react";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";
import { useCurrentProject } from "@/lib/state/files-shared";
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
  const project = useCurrentProject();
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
  async function launch(application: ApplicationDefinition) {
    try {
      if (application.scope === "project" && !project) throw new Error(text("Select a project before opening this application", "请先选择项目，再打开此应用"));
      const opened = await applicationRequest<{ instance_id: string }>(`/api/applications/${encodeURIComponent(application.id)}/open`, "POST", {
        project_id: application.scope === "project" ? project?.id : "",
      });
      useCenterTabs.getState().openApplicationTab(application.id, opened.instance_id, application.display_title || application.title);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

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
        {error && <p role="alert" className="text-sm text-text-muted">{error}</p>}
      </div>
    </div>
  );
}
