/**
 * Shared file-browsing plumbing used by the right-sidebar FileTree,
 * the center FileTabPane / FileViewer, and anything else that talks
 * to the worker's project-file actions. (Was part of the v1
 * files-panel store; the tab state moved to center-tabs-store.)
 */
import { useCallback, useEffect, useState } from "react";

import { wsRequest } from "@/lib/net/ws-request";
import { useSessionStore } from "@/lib/session-store";

export interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
}

/**
 * Resolve the conversation's current project (id + path). Returns
 * undefined while resolving, null when nothing browsable is bound.
 *
 * Mirrors the topbar ProjectBadge: one-shot ``list_projects`` over WS
 * (retried until the socket answers), re-resolved on session change
 * and on the ``project-changed`` event the project menu fires.
 */
export function useCurrentProject(): Project | null | undefined {
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

/* ---- WS helpers shared by file-tree / file-viewer ----------------- */

// wsRequest matches replies by frame *type* only, so two in-flight
// requests of the same action would both resolve with whichever reply
// lands first. Serialise all file-browsing requests through one chain.
// ponytail: global queue; per-action queues if tree loads ever feel slow.
let queue: Promise<unknown> = Promise.resolve();

export function filesWsRequest<T>(
  action: string,
  payload: Record<string, unknown>,
  responseType: string,
): Promise<T | null> {
  const next = queue.then(() => wsRequest<T>(action, payload, responseType));
  queue = next.catch(() => null);
  return next;
}

/** Last mtime seen per project-relative file path (fed by the tree
 * listing) — lets the viewer cache invalidate on refetch. */
export const latestFileMtime = new Map<string, number>();

/** URL of the backend raw-bytes endpoint (proxied by
 * app/files/[...path]/route.ts so the worker port stays live). */
export function rawFileUrl(projectId: string, path: string): string {
  return `/files/raw?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`;
}
