"use client";
import { useState } from "react";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { useCurrentProject } from "@/lib/files/files-shared";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useSessionStore } from "@/lib/session-store";
import { SimpleSelect } from "@/components/ui/select";

export function useApplicationLauncher(onOpened?: () => void) {
  const { text } = useTranslation();
  const [error, setError] = useState("");
  const project = useCurrentProject();
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const [choice, setChoice] = useState<{ application: ApplicationDefinition; projects: { id: string; name: string; path: string }[] } | null>(null);
  const [projectId, setProjectId] = useState("");
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
      // The window belongs to the current chat, or to the one that was
      // current before the New tab page: while it is open, the
      // application's entries are resident for that chat (program-model §5).
      const sessions = useSessionStore.getState();
      const sessionId = sessions.currentSessionId || sessions.lastSessionId || "";
      const opened = await applicationRequest<{ instance_id: string }>(`/api/applications/${encodeURIComponent(application.id)}/open`, "POST", {
        project_id: application.scope === "project" ? binding : "",
        session_id: sessionId,
      });
      setChoice(null);
      useCenterTabs.getState().openApplicationTab(application.id, opened.instance_id, application.display_title || application.title, sessionId);
      onOpened?.();
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

  return { launch, error, dialog: <>
        {choice && <section aria-label={text("Choose application project", "选择应用项目")} className="grid gap-2">
          <label htmlFor="application-project">{text("Project for", "应用项目：")} {choice.application.display_title || choice.application.title}</label>
          <SimpleSelect label={text("Project", "项目")} id="application-project" value={projectId} onValueChange={setProjectId} className="w-full" placeholder={text("Choose a project…", "选择项目…")} options={choice.projects.map(item => ({ value: item.id, label: `${item.name} — ${item.path}` }))} />
          <button type="button" disabled={!projectId} onClick={() => void launch(choice.application, projectId)}>{text("Open application", "打开应用")}</button>
          <button type="button" onClick={() => void chooseFolder()}>{text("Choose folder…", "选择文件夹…")}</button>
          <button type="button" onClick={() => setChoice(null)}>{text("Cancel", "取消")}</button>
        </section>}
        {folderPickerDialog}
  </> };
}
