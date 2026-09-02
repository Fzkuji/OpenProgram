/**
 * Shared file-browsing plumbing used by the right-sidebar FileTree,
 * the center FileTabPane / FileViewer, and anything else that talks
 * to the worker's project-file actions. (Was part of the v1
 * files-panel store; the tab state moved to center-tabs-store.)
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { wsRequest } from "@/lib/net/ws-request";
import { useSessionStore } from "@/lib/session-store";

export interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
}

const FILE_OWNER_KEYS = [
  "project_id", "session_id", "assistant_msg_id", "path", "snapshot_id",
] as const;

/**
 * Correlate a file reply after wsRequest has already checked request_id and
 * action.  A stale/error reply is still the reply for this request even
 * when the server cannot provide a snapshot id (for example an expired
 * cursor), so snapshot_id is optional for those terminal states.  Other
 * owner fields remain strict to prevent cross-project/session updates.
 */
export function fileResponseMatchesOwner(
  data: Record<string, unknown>,
  payload: Record<string, unknown>,
): boolean {
  const staleOrError = data.status === "stale"
    || data.status === "error"
    || typeof data.error_code === "string";
  return FILE_OWNER_KEYS.every((key) => {
    if (payload[key] === undefined) return true;
    if (key === "snapshot_id" && staleOrError && data[key] == null) return true;
    return data[key] === payload[key];
  });
}

interface ProjectListResponse {
  projects: Project[] | null;
  current_project_id: string | null;
  session_id: string | null;
  status?: "ready" | "error";
  error_code?: string | null;
  error?: string | null;
}

/** Last cleanup failure is retained for the existing file error surfaces. */
export const draftPersistenceErrors = new Map<string, string>();
const draftErrorListeners = new Set<() => void>();

export function notifyDraftErrorListeners(): void {
  for (const listener of draftErrorListeners) listener();
}

export function getDraftPersistenceError(scope: string): string | null {
  return draftPersistenceErrors.get(scope) ?? null;
}

export function subscribeDraftPersistenceErrors(listener: () => void): () => void {
  draftErrorListeners.add(listener);
  return () => draftErrorListeners.delete(listener);
}

export function useDraftPersistenceError(scope: string): string | null {
  return useSyncExternalStore(
    subscribeDraftPersistenceErrors,
    () => getDraftPersistenceError(scope),
    () => getDraftPersistenceError(scope),
  );
}

export function reportDraftPersistenceError(scope: string, message: string): void {
  draftPersistenceErrors.set(scope, message);
  notifyDraftErrorListeners();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("project-draft-error", {
      detail: { scope, message },
    }));
  }
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
  const pendingProjectId = useSessionStore((s) =>
    s.activeChatKey ? s.pendingProjectsByChat[s.activeChatKey] ?? null : null,
  );
  const [project, setProject] = useState<Project | null | undefined>(undefined);
  const resolveGeneration = useRef(0);
  const resolveController = useRef<AbortController | null>(null);

  const resolve = useCallback(async (): Promise<boolean> => {
    const generation = ++resolveGeneration.current;
    resolveController.current?.abort();
    const controller = new AbortController();
    resolveController.current = controller;
    setProject(undefined);
    const data = await wsRequest<ProjectListResponse>(
      "list_projects",
      { session_id: sessionId ?? "" },
      "projects_list",
      // 为什么要认领回复：侧栏 Projects 分组、/projects 页也会并发发
      // list_projects（session_id 为空），那些回复的 current_project_id
      // 恒为 null。wsRequest 仅按帧类型匹配时会拿到别人的空回复，导致
      // 这里误回落到默认项目——右栏文件树被钉死在默认根目录。后端会
      // 回显请求的 session_id（空串回显 null），据此只认自己那条。
      (d) => (d.session_id ?? null) === (sessionId || null),
      4000,
      { signal: controller.signal },
    );
    if (generation !== resolveGeneration.current || controller.signal.aborted) return false;
    if (!data || data.status === "error" || !Array.isArray(data.projects)) return false;
    const projects = data.projects;
    const wantId =
      (!sessionId ? pendingProjectId : data.current_project_id) ??
      data.current_project_id ??
      null;
    const cur =
      projects.find((p) => p.id === wantId) ??
      projects.find((p) => p.is_default) ??
      null;
    // The default ad-hoc project may have no real folder — treat a
    // pathless project as "nothing bound".
    setProject(cur && cur.path ? cur : null);
    return true;
  }, [pendingProjectId, sessionId]);

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
      resolveGeneration.current += 1;
      resolveController.current?.abort();
      resolveController.current = null;
      window.removeEventListener("project-changed", onChanged);
    };
  }, [resolve]);

  return project;
}

/** Last mtime seen per project-relative file path (fed by the tree
 * listing) — lets the viewer cache invalidate on refetch. */
export const latestFileMtime = new Map<string, number>();
export const LATEST_MTIME_MAX_ENTRIES = 256;

export const READ_CACHE_MAX_ENTRIES = 64;
export const READ_CACHE_MAX_BYTES = 16 * 1024 * 1024;
export const DRAFT_MAX_ENTRIES = 32;
export const DRAFT_MAX_BYTES = 8 * 1024 * 1024;

const utf8 = new TextEncoder();

/** A project/path key is never shared with another project. */
export function fileScopeKey(projectId: string, path: string): string {
  return `${projectId}:${path}`;
}

/** Read cache revisions use mtime as the worker's content identity. */
export function fileReadCacheKey(projectId: string, path: string, mtime: number): string {
  return `${fileScopeKey(projectId, path)}:${mtime}`;
}

/** Wire shape of a ``project_file_read_result`` reply. */
export interface FileReadResult {
  project_id: string;
  path: string;
  content?: string;
  size: number;
  mtime: number;
  revision?: string;
  truncated?: boolean;
  binary?: boolean;
  too_large?: boolean;
  error?: string;
}

/** Read-result cache. Production writes use cacheFileRead to enforce bounds. */
export const readCache = new Map<string, FileReadResult>();
const readCacheBytes = new Map<string, number>();

function readResultBytes(result: FileReadResult): number {
  return result.content === undefined ? 0 : utf8.encode(result.content).byteLength;
}

function touchReadCache(key: string, value: FileReadResult): void {
  readCache.delete(key);
  readCache.set(key, value);
}

function dropReadCacheScope(scope: string): void {
  for (const key of [...readCache.keys()]) {
    if (!key.startsWith(`${scope}:`)) continue;
    readCache.delete(key);
    readCacheBytes.delete(key);
  }
}

export function getCachedFileRead(projectId: string, path: string, mtime?: number): FileReadResult | undefined {
  const scope = fileScopeKey(projectId, path);
  // Without a current tree mtime, a cached read has no verifiable identity.
  // Never return it merely because it is the newest entry for this path.
  const knownMtime = mtime ?? latestFileMtime.get(scope);
  if (knownMtime === undefined) return undefined;
  const key = fileReadCacheKey(projectId, path, knownMtime);
  if (!key) return undefined;
  const value = readCache.get(key);
  if (!value) return undefined;
  touchReadCache(key, value);
  return value;
}

export function cacheFileRead(result: FileReadResult): void {
  if (result.error || result.content === undefined || result.truncated) return;
  const key = fileReadCacheKey(result.project_id, result.path, result.mtime);
  const bytes = readResultBytes(result);
  if (bytes > READ_CACHE_MAX_BYTES) return;
  readCache.delete(key);
  readCacheBytes.delete(key);
  let total = [...readCacheBytes.values()].reduce((sum, value) => sum + value, 0);
  while (
    (readCache.size >= READ_CACHE_MAX_ENTRIES || total + bytes > READ_CACHE_MAX_BYTES) &&
    readCache.size > 0
  ) {
    const oldest = readCache.keys().next().value as string | undefined;
    if (!oldest) break;
    total -= readCacheBytes.get(oldest) ?? 0;
    readCacheBytes.delete(oldest);
    readCache.delete(oldest);
  }
  if (readCache.size >= READ_CACHE_MAX_ENTRIES || total + bytes > READ_CACHE_MAX_BYTES) return;
  readCache.set(key, result);
  readCacheBytes.set(key, bytes);
}

export function noteFileMtime(projectId: string, path: string, mtime: number): void {
  const scope = fileScopeKey(projectId, path);
  const previous = latestFileMtime.get(scope);
  latestFileMtime.delete(scope);
  latestFileMtime.set(scope, mtime);
  while (latestFileMtime.size > LATEST_MTIME_MAX_ENTRIES) {
    const oldest = latestFileMtime.keys().next().value as string | undefined;
    if (!oldest) break;
    latestFileMtime.delete(oldest);
    dropReadCacheScope(oldest);
  }
  if (previous === undefined || previous === mtime) return;
  dropReadCacheScope(scope);
}

/** Drop the cached read (and known mtime) for one file so the next
 * viewer mount refetches — called after a successful save. */
export function invalidateFileRead(projectId: string, path: string): void {
  const scope = fileScopeKey(projectId, path);
  for (const key of [...readCache.keys()]) {
    if (!key.startsWith(`${scope}:`) && !key.startsWith(`${scope}/`)) continue;
    readCache.delete(key);
    readCacheBytes.delete(key);
  }
  latestFileMtime.delete(scope);
}

/** URL of the worker's raw-bytes endpoint (same origin — single port). */
export function rawFileUrl(projectId: string, path: string): string {
  return `/files/raw?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`;
}

/** Raw bytes of an ABSOLUTE path — chat attachments, which live in the
 *  session workdir or a channel's inbound directory rather than under a
 *  project id. `/files/raw` refuses absolute paths on purpose; this is
 *  the separate route (`/api/file-raw`) that accepts one and checks it
 *  against the attachment roots. */
export function absRawFileUrl(absPath: string, sessionId?: string): string {
  const sid = sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : "";
  return `/api/file-raw?path=${encodeURIComponent(absPath)}${sid}`;
}

/** Text of an ABSOLUTE path, same root check as {@link absRawFileUrl}. */
export function absFileReadUrl(absPath: string, sessionId?: string): string {
  const sid = sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : "";
  return `/api/file-read?path=${encodeURIComponent(absPath)}${sid}`;
}

export * from "./file-drafts";

/* ---- Unsaved editor drafts ---------------------------------------- */

/** One file tab's unsaved editor buffer: the user's draft plus the
 * content+mtime of the read it drifted from (the mtime is the
 * optimistic-lock token a later save presents as expected_mtime). */
