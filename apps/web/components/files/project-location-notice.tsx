"use client";

/**
 * Shown in place of the file tree when the project's folder is
 * unavailable: what happened, and the repair in one step — confirm the
 * folder at the recorded place, or pick the folder's new location.
 */
import { useCallback, useEffect, useState } from "react";
import { SolarIcon } from "@/components/solar-icons";
import { Button } from "@/components/ui/button";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { useSessionStore } from "@/lib/session-store";
import {
  canConfirmInPlace, confirmProjectFolder, locationMessage, locationProblem,
  notifyProjectLocationChanged, relocateProject, type ProjectLocation, type RelocateReply,
} from "@/lib/projects/project-location";
import { projectPathCache } from "./tree/file-tree-query";
import styles from "./files-panel.module.css";

export function ProjectLocationNotice({ projectId, onRepaired }: { projectId: string; onRepaired(): void }) {
  const { text } = useTranslation();
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const sessionId = useSessionStore(s => s.currentSessionId);
  const [project, setProject] = useState<ProjectLocation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const read = useCallback(async () => {
    const data = await wsRequest<{ projects?: ProjectLocation[]; session_id?: string | null }>(
      "list_projects", { session_id: sessionId ?? "" }, "projects_list",
      d => (d.session_id ?? null) === (sessionId || null),
    );
    // No answer (or an unreadable registry): keep the notice and its
    // repair buttons as shown rather than blanking them.
    if (!data || !Array.isArray(data.projects)) return;
    setProject(data.projects.find(p => p.id === projectId) ?? null);
  }, [projectId, sessionId]);
  useEffect(() => {
    void read();
    // A repair made elsewhere (another tab, the project menu) re-reads here.
    const onChanged = () => void read();
    window.addEventListener("project-changed", onChanged);
    return () => window.removeEventListener("project-changed", onChanged);
  }, [read]);

  async function repair(run: () => Promise<RelocateReply>) {
    setBusy(true);
    setError(null);
    try {
      const reply = await run();
      if (!reply.ok) {
        setError(reply.error ?? text("The folder could not be used.", "无法使用这个文件夹。"));
        return;
      }
      projectPathCache.delete(projectId);
      notifyProjectLocationChanged();
      onRepaired();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const problem = locationProblem(project) ?? "missing";
  return (
    <div className="flex flex-col gap-[10px] p-[16px_12px]" role="alert">
      <div className={styles.locationNoticeTitle}>
        <SolarIcon name="danger-triangle" size={14} motionPreset="none" aria-hidden="true" />
        <span>{text("Project folder unavailable", "找不到项目文件夹")}</span>
      </div>
      <div className={styles.locationNoticeText}>{project ? locationMessage(problem, project, text) : null}</div>
      {project?.path ? <div className={styles.locationNoticePath}>{project.path}</div> : null}
      {project ? (
        <div className="flex flex-wrap gap-[6px]">
          {canConfirmInPlace(problem) ? (
            <Button size="xs" disabled={busy} onClick={() => repair(() => confirmProjectFolder(project, sessionId))}>
              {text("Use the folder at this location", "就是这个位置的文件夹")}
            </Button>
          ) : null}
          <Button size="xs" variant={canConfirmInPlace(problem) ? "elevated" : "default"} disabled={busy} onClick={async () => {
            try {
              const path = await pickFolder(project.path);
              if (path) await repair(() => relocateProject(project, path, sessionId));
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            }
          }}>
            {text("Locate folder…", "定位文件夹…")}
          </Button>
        </div>
      ) : null}
      {error ? <div className={styles.locationNoticeText} style={{ color: "var(--accent-orange)" }}>{error}</div> : null}
      {folderPickerDialog}
    </div>
  );
}
