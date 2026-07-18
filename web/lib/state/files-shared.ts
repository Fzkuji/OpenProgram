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
      session_id: string | null;
    }>(
      "list_projects",
      { session_id: sessionId ?? "" },
      "projects_list",
      // 为什么要认领回复：侧栏 Projects 分组、/projects 页也会并发发
      // list_projects（session_id 为空），那些回复的 current_project_id
      // 恒为 null。wsRequest 仅按帧类型匹配时会拿到别人的空回复，导致
      // 这里误回落到默认项目——右栏文件树被钉死在默认根目录。后端会
      // 回显请求的 session_id（空串回显 null），据此只认自己那条。
      (d) => (d.session_id ?? null) === (sessionId || null),
    );
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

/** Wire shape of a ``project_file_read_result`` reply. */
export interface FileReadResult {
  project_id: string;
  path: string;
  content?: string;
  size: number;
  mtime: number;
  truncated?: boolean;
  binary?: boolean;
  too_large?: boolean;
  error?: string;
}

/** Read-result cache keyed `${projectId}:${path}` — shared between the
 * viewer (fills it) and the editor (invalidates it after a save). */
// ponytail: unbounded per-session cache; add LRU if memory ever matters.
export const readCache = new Map<string, FileReadResult>();

/** Drop the cached read (and known mtime) for one file so the next
 * viewer mount refetches — called after a successful save. */
export function invalidateFileRead(projectId: string, path: string): void {
  readCache.delete(`${projectId}:${path}`);
  latestFileMtime.delete(path);
}

/** URL of the backend raw-bytes endpoint (proxied by
 * app/files/[...path]/route.ts so the worker port stays live). */
export function rawFileUrl(projectId: string, path: string): string {
  return `/files/raw?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`;
}

/* ---- Unsaved editor drafts ---------------------------------------- */

/** One file tab's unsaved editor buffer: the user's draft plus the
 * content+mtime of the read it drifted from (the mtime is the
 * optimistic-lock token a later save presents as expected_mtime). */
export interface FileDraft {
  draft: string;
  baselineContent: string;
  baselineMtime: number;
}

export function fileDraftKey(projectId: string, path: string): string {
  return `${projectId}:${path}`;
}

/** Unsaved drafts surviving tab switches (the pane unmounts when its
 * tab loses focus). The pane mirrors its buffer in while dirty and
 * removes the entry on save / revert / confirmed discard.
 * ponytail: in-memory only — a page reload loses drafts (the strip's
 * persisted `dirty` flag is reset on restore for the same reason). */
export const fileDrafts = new Map<string, FileDraft>();
