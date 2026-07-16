"use client";

/**
 * FilesPanel — collapsible project-file browser living INSIDE the
 * persistent chat surface (mounted by AppShell between the chat shell
 * and the right sidebar; no route of its own). Layout, left→right:
 * [tab strip + breadcrumb + viewer] flex-1, [file tree + filter] 260px.
 *
 * Project resolution mirrors the topbar ProjectBadge: one-shot
 * ``list_projects`` over WS (retried until the socket answers),
 * re-resolved on session change and on the ``project-changed`` event
 * the project menu fires.
 */
import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";
import { useFilesPanel } from "@/lib/state/files-panel-store";
import { FileTree } from "./file-tree";
import { FileViewer } from "./file-viewer";
import styles from "./files-panel.module.css";

interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
}

/** Resolve the conversation's current project (id + path). Returns
 * undefined while resolving, null when nothing browsable is bound. */
function useCurrentProject(): Project | null | undefined {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [project, setProject] = useState<Project | null | undefined>(undefined);

  const resolve = useCallback(async (): Promise<boolean> => {
    const data = await wsRequest<{
      projects: Project[];
      current_project_id: string | null;
    }>("list_projects", { session_id: sessionId ?? "" }, "projects_list");
    if (!data) return false;
    const projects = data.projects || [];
    const w = window as unknown as { _pendingProjectId?: string };
    const wantId = data.current_project_id ?? w._pendingProjectId ?? null;
    const cur =
      projects.find((p) => p.id === wantId) ??
      projects.find((p) => p.is_default) ??
      null;
    // The default ad-hoc project may have no real folder — treat a
    // pathless project as "nothing bound".
    setProject(cur && cur.path ? cur : null);
    return true;
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    let tries = 0;
    const attempt = () => {
      if (cancelled) return;
      resolve().then((ok) => {
        if (!ok && !cancelled && tries++ < 20) setTimeout(attempt, 300);
      });
    };
    attempt();
    const onChanged = () => resolve();
    window.addEventListener("project-changed", onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("project-changed", onChanged);
    };
  }, [resolve]);

  return project;
}

export function FilesPanel() {
  const project = useCurrentProject();
  const open = useFilesPanel((s) => s.open);
  const setProject = useFilesPanel((s) => s.setProject);

  // Swap the store to the resolved project's persisted tab state.
  // Runs even while the panel is closed so a project whose panel was
  // left open restores as open on reload / project switch.
  useEffect(() => {
    if (project !== undefined) setProject(project?.id ?? null);
  }, [project, setProject]);

  if (!open) return null;
  return <FilesPanelBody project={project} />;
}

function FilesPanelBody({ project }: { project: Project | null | undefined }) {
  const { text } = useTranslation();
  const { tabs, activePath, setActive, closeTab } = useFilesPanel(
    useShallow((s) => ({
      tabs: s.tabs,
      activePath: s.activePath,
      setActive: s.setActive,
      closeTab: s.closeTab,
    })),
  );

  if (!project) {
    return (
      <aside className={styles.panel} id="filesPanel">
        <div className={styles.centerHint}>
          {project === undefined
            ? text("Loading…", "加载中…")
            : text("Bind a project to browse files", "绑定项目后可浏览文件")}
        </div>
      </aside>
    );
  }

  return (
    <aside className={styles.panel} id="filesPanel">
      <div className={styles.main}>
        <div className={styles.tabStrip}>
          {tabs.map((tab) => (
            <div
              key={tab.path}
              className={`${styles.tab} ${tab.path === activePath ? styles.tabActive : ""}`}
              title={tab.path}
              onClick={() => setActive(tab.path)}
            >
              <span className={styles.tabName}>{tab.name}</span>
              <span
                role="button"
                className={styles.tabClose}
                aria-label={text("Close tab", "关闭标签")}
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(tab.path);
                }}
              >
                <X size={12} />
              </span>
            </div>
          ))}
        </div>
        {activePath ? (
          <div className={styles.breadcrumb}>
            {activePath.split("/").map((seg, i, arr) => (
              <span key={i}>
                <span className={styles.crumb}>{seg}</span>
                {i < arr.length - 1 ? (
                  <span className={styles.crumbSep}>/</span>
                ) : null}
              </span>
            ))}
          </div>
        ) : null}
        <div className={styles.viewerHost}>
          {activePath ? (
            <FileViewer projectId={project.id} path={activePath} />
          ) : (
            <div className={styles.centerHint}>
              {text(
                "Pick a file from the tree to view it",
                "从右侧文件树选择一个文件",
              )}
            </div>
          )}
        </div>
      </div>
      <FileTree projectId={project.id} />
    </aside>
  );
}
