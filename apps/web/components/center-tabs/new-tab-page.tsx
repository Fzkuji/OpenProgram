"use client";

import { useEffect, useState } from "react";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { wsRequest } from "@/lib/net/ws-request";
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
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const [choice, setChoice] = useState<{ application: ApplicationDefinition; projects: { id: string; name: string; path: string }[] } | null>(null);
  const [projectId, setProjectId] = useState("");
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
  async function launch(application: ApplicationDefinition, selectedProject?: string) {
    try {
      setError("");
      let binding = selectedProject || project?.id || "";
      if (application.scope === "project" && !binding) {
        const context = await applicationRequest<{ projects: { id: string; name: string; path: string }[]; bindings: string[] }>(`/api/applications/${encodeURIComponent(application.id)}/launch-context`);
        if (context.bindings.length === 1) binding = context.bindings[0];
        else {
          setChoice({ application, projects: context.projects });
          setProjectId("");
          return;
        }
      }
      const opened = await applicationRequest<{ instance_id: string }>(`/api/applications/${encodeURIComponent(application.id)}/open`, "POST", {
        project_id: application.scope === "project" ? binding : "",
      });
      setChoice(null);
      useCenterTabs.getState().openApplicationTab(application.id, opened.instance_id, application.display_title || application.title);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function chooseFolder() {
    if (!choice) return;
    const application = choice.application;
    try {
      const path = await pickFolder();
      if (!path) return;
      const response = await wsRequest<{ ok: boolean; project?: { id: string }; error?: string }>("create_project", { path, session_id: "" }, "project_created");
      if (!response?.ok || !response.project) throw new Error(response?.error || text("Could not add project", "无法添加项目"));
      await launch(application, response.project.id);
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
        {choice && <section aria-label={text("Choose application project", "选择应用项目")} className="grid gap-2">
          <label htmlFor="application-project">{text("Project for", "应用项目：")} {choice.application.display_title || choice.application.title}</label>
          <select id="application-project" value={projectId} onChange={event => setProjectId(event.target.value)} className="rounded border border-border bg-surface p-2">
            <option value="">{text("Choose a project…", "选择项目…")}</option>
            {choice.projects.map(item => <option key={item.id} value={item.id}>{item.name} — {item.path}</option>)}
          </select>
          <button type="button" disabled={!projectId} onClick={() => void launch(choice.application, projectId)}>{text("Open application", "打开应用")}</button>
          <button type="button" onClick={() => void chooseFolder()}>{text("Choose folder…", "选择文件夹…")}</button>
          <button type="button" onClick={() => setChoice(null)}>{text("Cancel", "取消")}</button>
        </section>}
        {folderPickerDialog}
        {error && <p role="alert" className="text-sm text-text-muted">{error}</p>}
      </div>
    </div>
  );
}
