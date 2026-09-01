/**
 * Shared file-browsing plumbing used by the right-sidebar FileTree,
 * the center FileTabPane / FileViewer, and anything else that talks
 * to the worker's project-file actions. (Was part of the v1
 * files-panel store; the tab state moved to center-tabs-store.)
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { wsRequest, type WsRequestOptions } from "@/lib/net/ws-request";
import { useSessionStore } from "@/lib/session-store";
import {
  IndexedDbDraftStore,
  DraftStoreQuotaError,
  type DraftStoreAdapter,
  type DraftStoreSnapshot,
  rebuildDraftIndexes,
} from "./file-draft-store";

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

function notifyDraftErrorListeners(): void {
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

function reportDraftPersistenceError(scope: string, message: string): void {
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

  const resolve = useCallback(async (): Promise<boolean> => {
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
    );
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
      window.removeEventListener("project-changed", onChanged);
    };
  }, [resolve]);

  return project;
}

/* ---- WS helpers shared by file-tree / file-viewer ----------------- */

export function filesWsRequest<T>(
  action: string,
  payload: Record<string, unknown>,
  responseType: string,
  options: WsRequestOptions = {},
): Promise<T | null> {
  const expected = Object.fromEntries(
    FILE_OWNER_KEYS.filter((key) => payload[key] !== undefined)
      .map((key) => [key, payload[key]]),
  );
  const match = (data: T) => {
    const owner = data as T & Record<string, unknown>;
    return fileResponseMatchesOwner(owner, expected);
  };
  return wsRequest<T>(action, payload, responseType, match, 4000, {
    ...options, requestId: true,
  });
}

/** Last mtime seen per project-relative file path (fed by the tree
 * listing) — lets the viewer cache invalidate on refetch. */
export const latestFileMtime = new Map<string, number>();

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

export function getCachedFileRead(projectId: string, path: string, mtime?: number): FileReadResult | undefined {
  const scope = fileScopeKey(projectId, path);
  const key = mtime === undefined
    ? [...readCache.keys()].reverse().find((candidate) => candidate.startsWith(`${scope}:`))
    : fileReadCacheKey(projectId, path, mtime);
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
  latestFileMtime.set(scope, mtime);
  if (previous === undefined || previous === mtime) return;
  for (const key of [...readCache.keys()]) {
    if (!key.startsWith(`${scope}:`)) continue;
    readCache.delete(key);
    readCacheBytes.delete(key);
  }
}

/** Drop the cached read (and known mtime) for one file so the next
 * viewer mount refetches — called after a successful save. */
export function invalidateFileRead(projectId: string, path: string): void {
  const scope = fileScopeKey(projectId, path);
  for (const key of [...readCache.keys()]) {
    if (!key.startsWith(`${scope}:`)) continue;
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

/* ---- Unsaved editor drafts ---------------------------------------- */

/** One file tab's unsaved editor buffer: the user's draft plus the
 * content+mtime of the read it drifted from (the mtime is the
 * optimistic-lock token a later save presents as expected_mtime). */
export interface FileDraft {
  draft: string;
  baselineContent: string;
  baselineMtime: number;
  baselineRevision?: string;
}

export function fileDraftKey(projectId: string, path: string): string {
  return fileScopeKey(projectId, path);
}

/** Unsaved drafts surviving tab switches. The pane mirrors dirty entries
 * to IndexedDB and removes them only on save, revert, or explicit discard. */
export const fileDrafts = new Map<string, FileDraft>();

export type DraftPersistenceErrorCode = "DRAFT_QUOTA_EXCEEDED" | "DRAFT_PERSISTENCE_FAILED";

export interface DraftPersistenceResult {
  ok: boolean;
  code?: DraftPersistenceErrorCode;
  message?: string;
}

interface StoredDraft extends FileDraft {
  key: string;
  projectId: string;
  path: string;
  bytes: number;
  updatedAt: number;
}

interface ProjectDraftIndex {
  projectId: string;
  keys: string[];
  count: number;
  bytes: number;
}

let draftHydration: Promise<void> | null = null;
let draftBytesByKey = new Map<string, number>();
let draftIndexByProject = new Map<string, ProjectDraftIndex>();
let draftClock = 0;
let draftQueue: Promise<unknown> = Promise.resolve();
let draftStorePromise: Promise<DraftStoreAdapter | null> | null = null;
/** Bytes of the latest live buffer, including a buffer whose IDB write failed. */
const liveDraftBytesByKey = new Map<string, number>();
let draftStoreOverride: DraftStoreAdapter | null | undefined;

function getDraftStore(): Promise<DraftStoreAdapter | null> {
  if (draftStoreOverride !== undefined) return Promise.resolve(draftStoreOverride);
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  if (!draftStorePromise) draftStorePromise = Promise.resolve(new IndexedDbDraftStore());
  return draftStorePromise;
}

function splitDraftKey(key: string): { projectId: string; path: string } {
  const separator = key.indexOf(":");
  return separator < 0
    ? { projectId: key, path: "" }
    : { projectId: key.slice(0, separator), path: key.slice(separator + 1) };
}

/** Canonical persisted draft payload. The byte field is solved to a fixed
 * point because its decimal representation is itself part of the payload;
 * all StoredDraft fields use this serializer for quota accounting and writes. */
function canonicalDraftPayload(key: string, draft: FileDraft, updatedAt = 0, bytes = 0): Record<string, unknown> {
  const { projectId, path } = splitDraftKey(key);
  return {
    key,
    projectId,
    path,
    draft: draft.draft,
    baselineContent: draft.baselineContent,
    baselineMtime: draft.baselineMtime,
    baselineRevision: draft.baselineRevision ?? null,
    bytes,
    updatedAt,
  };
}

export function fileDraftBytes(key: string, draft: FileDraft, updatedAt = 0): number {
  let bytes = 0;
  for (let i = 0; i < 8; i++) {
    const next = utf8.encode(JSON.stringify(canonicalDraftPayload(key, draft, updatedAt, bytes))).byteLength;
    if (next === bytes) return bytes;
    bytes = next;
  }
  return bytes;
}

function projectIndexBytes(index: ProjectDraftIndex): number {
  return utf8.encode(JSON.stringify({
    projectId: index.projectId,
    keys: index.keys,
    count: index.count,
    bytes: index.bytes,
  })).byteLength;
}

function storedDraftBytes(entry: StoredDraft): number {
  return fileDraftBytes(entry.key, entry, entry.updatedAt);
}

function applyDraftStoreSnapshot(snapshot: DraftStoreSnapshot): void {
  const drafts = snapshot.drafts as StoredDraft[];
  const liveEntries = [...fileDrafts.entries()]
    .filter(([key]) => liveDraftBytesByKey.has(key));
  const normalizedDrafts = drafts.map((entry) => ({
    ...entry,
    bytes: storedDraftBytes(entry),
  }));
  draftBytesByKey = new Map(normalizedDrafts.map((entry) => [entry.key, entry.bytes]));
  draftIndexByProject = new Map(
    rebuildDraftIndexes({ drafts: normalizedDrafts, indexes: snapshot.indexes }).map((index) => [index.projectId, index]),
  );
  fileDrafts.clear();
  for (const entry of normalizedDrafts) {
    fileDrafts.set(entry.key, {
      draft: entry.draft,
      baselineContent: entry.baselineContent,
      baselineMtime: entry.baselineMtime,
      baselineRevision: entry.baselineRevision,
    });
    draftClock = Math.max(draftClock, entry.updatedAt ?? 0);
  }
  for (const [key, value] of liveEntries) {
    fileDrafts.set(key, value);
  }
}

function normalizeDraftRecord(entry: StoredDraft): StoredDraft {
  return { ...entry, bytes: storedDraftBytes(entry) };
}

function normalizedStoreSnapshot(snapshot: DraftStoreSnapshot): DraftStoreSnapshot {
  const drafts = (snapshot.drafts as StoredDraft[]).map(normalizeDraftRecord);
  return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
}

function snapshotStorageBytes(snapshot: DraftStoreSnapshot): number {
  return snapshot.drafts.reduce((sum, entry) => sum + entry.bytes, 0)
    + snapshot.indexes.reduce((sum, index) => sum + projectIndexBytes(index), 0);
}

async function hydrateDraftState(): Promise<void> {
  if (draftHydration) return draftHydration;
  draftHydration = (async () => {
    const store = await getDraftStore();
    if (!store) return;
    const snapshot = await store.load();
    applyDraftStoreSnapshot(snapshot);
    const repaired = await store.repair();
    applyDraftStoreSnapshot(repaired);
  })().catch(() => {
    reportDraftPersistenceError("__store__", "Unable to load local dirty drafts; they were retained for retry.");
    draftHydration = null;
  });
  return draftHydration;
}

function draftBytesTotal(extraKey?: string, extraBytes?: number): number {
  const entries = new Map<string, number>();
  for (const key of new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()]))
    entries.set(key, liveDraftBytesByKey.get(key) ?? draftBytesByKey.get(key) ?? 0);
  if (extraKey !== undefined && extraBytes !== undefined) entries.set(extraKey, extraBytes);
  let total = [...entries.values()].reduce((sum, bytes) => sum + bytes, 0);
  const projectKeys = new Map<string, string[]>();
  for (const key of entries.keys()) {
    const { projectId } = splitDraftKey(key);
    const keys = projectKeys.get(projectId) ?? [];
    keys.push(key);
    projectKeys.set(projectId, keys);
  }
  for (const [projectId, keys] of projectKeys) {
    const old = draftIndexByProject.get(projectId);
    const ordered = [
      ...(old?.keys ?? []).filter((key) => entries.has(key)),
      ...keys.filter((key) => !(old?.keys ?? []).includes(key)).sort(),
    ];
    const index = {
      projectId,
      keys: ordered,
      count: ordered.length,
      bytes: ordered.reduce((sum, key) => sum + (entries.get(key) ?? 0), 0),
    } satisfies ProjectDraftIndex;
    total += projectIndexBytes(index);
  }
  return total;
}

function effectiveDraftBytes(key: string): number {
  return liveDraftBytesByKey.get(key) ?? draftBytesByKey.get(key) ?? 0;
}

export function canPersistFileDraft(key: string, draft: FileDraft): boolean {
  const bytes = fileDraftBytes(key, draft, draftClock + 1);
  const keys = new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()]);
  const count = keys.has(key) ? keys.size : keys.size + 1;
  return count <= DRAFT_MAX_ENTRIES && draftBytesTotal(key, bytes) <= DRAFT_MAX_BYTES;
}

/** Inject a transactional fake for executable web tests. Production leaves
 * this unset and always uses the native IndexedDB adapter. */
export function setDraftStoreAdapterForTests(adapter: DraftStoreAdapter | null): void {
  draftStoreOverride = adapter;
  draftStorePromise = null;
  draftHydration = null;
  draftBytesByKey = new Map();
  draftIndexByProject = new Map();
  liveDraftBytesByKey.clear();
  fileDrafts.clear();
  draftPersistenceErrors.clear();
  notifyDraftErrorListeners();
}

function enqueueDraft<T>(operation: () => Promise<T>): Promise<T> {
  const lockedOperation = () => {
    if (typeof navigator !== "undefined" && "locks" in navigator) {
      return navigator.locks.request("openprogram-drafts", { mode: "exclusive" }, async () => operation()) as unknown as Promise<T>;
    }
    return operation();
  };
  const next = draftQueue.then(lockedOperation, lockedOperation);
  draftQueue = next.catch(() => undefined);
  return next;
}

export async function loadFileDraft(projectId: string, path: string): Promise<FileDraft | null> {
  await hydrateDraftState();
  const key = fileDraftKey(projectId, path);
  const value = fileDrafts.get(key);
  return value ? structuredClone(value) : null;
}

export function persistFileDraft(projectId: string, path: string, value: FileDraft): Promise<DraftPersistenceResult> {
  const key = fileDraftKey(projectId, path);
  return enqueueDraft(async () => {
    await hydrateDraftState();
    if (!canPersistFileDraft(key, value)) {
      return { ok: false, code: "DRAFT_QUOTA_EXCEEDED", message: "Local dirty-draft storage is full; save, export, or discard a draft first." };
    }
    const store = await getDraftStore();
    if (!store) {
      const message = "Local dirty-draft storage is unavailable; the dirty buffer was retained.";
      fileDrafts.set(key, structuredClone(value));
      liveDraftBytesByKey.set(key, fileDraftBytes(key, value));
      reportDraftPersistenceError(key, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    const stored: StoredDraft = {
      ...structuredClone(value), key, projectId, path,
      bytes: 0, updatedAt: ++draftClock,
    };
    stored.bytes = storedDraftBytes(stored);
    // Record the live candidate before the write. If the browser rejects the
    // transaction, close/delete checks still see this unsaved content.
    fileDrafts.set(key, structuredClone(value));
    liveDraftBytesByKey.set(key, stored.bytes);
    try {
      const result = await store.mutate((snapshot) => {
        const current = normalizedStoreSnapshot(snapshot);
        const drafts = [...current.drafts.filter((entry) => entry.key !== key), stored];
        const next = {
          drafts,
          indexes: rebuildDraftIndexes({ drafts, indexes: current.indexes }),
        };
        if (next.drafts.length > DRAFT_MAX_ENTRIES || snapshotStorageBytes(next) > DRAFT_MAX_BYTES)
          throw new DraftStoreQuotaError();
        return next;
      });
      liveDraftBytesByKey.delete(key);
      applyDraftStoreSnapshot(result);
      draftPersistenceErrors.delete(key);
      notifyDraftErrorListeners();
      return { ok: true };
    } catch (error) {
      const quota = (typeof DOMException !== "undefined" && error instanceof DOMException && error.name === "QuotaExceededError")
        || (typeof error === "object" && error !== null && "name" in error && error.name === "QuotaExceededError");
      const result: DraftPersistenceResult = {
        ok: false,
        code: quota ? "DRAFT_QUOTA_EXCEEDED" : "DRAFT_PERSISTENCE_FAILED",
        message: quota ? "Local dirty-draft storage is full; the last saved draft was retained." : "Unable to persist the dirty draft; the last saved draft was retained.",
      };
      reportDraftPersistenceError(key, result.message ?? "Unable to persist the dirty draft.");
      return result;
    }
  });
}

export function discardFileDraft(projectId: string, path: string): Promise<DraftPersistenceResult> {
  const key = fileDraftKey(projectId, path);
  return enqueueDraft(async () => {
    await hydrateDraftState();
    const store = await getDraftStore();
    if (!store) {
      const message = "Unable to discard the local dirty draft because storage is unavailable.";
      reportDraftPersistenceError(key, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      const result = await store.mutate((snapshot) => {
        const current = normalizedStoreSnapshot(snapshot);
        const drafts = current.drafts.filter((entry) => entry.key !== key);
        return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: current.indexes }) };
      });
      liveDraftBytesByKey.delete(key);
      applyDraftStoreSnapshot(result);
      draftPersistenceErrors.delete(key);
      notifyDraftErrorListeners();
      return { ok: true };
    } catch {
      const message = "Unable to discard the local dirty draft.";
      reportDraftPersistenceError(key, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
  });
}

/** Move all draft keys below a renamed file or directory in one transaction. */
export function moveFileDrafts(projectId: string, oldPath: string, newPath: string): Promise<DraftPersistenceResult> {
  return enqueueDraft(() => moveFileDraftsInternal(projectId, oldPath, newPath));
}

async function moveFileDraftsInternal(projectId: string, oldPath: string, newPath: string): Promise<DraftPersistenceResult> {
    await hydrateDraftState();
    const prefix = fileScopeKey(projectId, oldPath);
    const store = await getDraftStore();
    if (!store) {
      const message = "Unable to move the local dirty draft because storage is unavailable.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      let movedKeys: string[] = [];
      const result = await store.mutate((snapshot) => {
        const current = normalizedStoreSnapshot(snapshot);
        const records = [...current.drafts];
        for (const [key, value] of fileDrafts) {
          if (!liveDraftBytesByKey.has(key) || records.some((entry) => entry.key === key)) continue;
          records.push({
            ...structuredClone(value), key, projectId,
            path: splitDraftKey(key).path,
            bytes: fileDraftBytes(key, value, ++draftClock), updatedAt: draftClock,
          });
        }
        const moved = records.filter((entry) => entry.key === prefix || entry.key.startsWith(`${prefix}/`)).map((entry) => {
          const suffix = entry.key === prefix ? "" : entry.key.slice(prefix.length);
          const nextKey = fileDraftKey(projectId, newPath + suffix);
          movedKeys.push(entry.key);
          return {
            ...entry,
            key: nextKey,
            path: newPath + suffix,
            bytes: fileDraftBytes(nextKey, entry, ++draftClock),
            updatedAt: draftClock,
          };
        });
        if (moved.length === 0) return current;
        const drafts = [...records.filter((entry) => !movedKeys.includes(entry.key)), ...moved];
        const next = { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: current.indexes }) };
        if (next.drafts.length > DRAFT_MAX_ENTRIES || snapshotStorageBytes(next) > DRAFT_MAX_BYTES)
          throw new DraftStoreQuotaError();
        return next;
      });
      if (movedKeys.length === 0) return { ok: true };
      // Remove the old live overlay before applying the moved persisted
      // snapshot; otherwise applyDraftStoreSnapshot would restore it at the
      // old key.
      for (const oldKey of movedKeys) liveDraftBytesByKey.delete(oldKey);
      applyDraftStoreSnapshot(result);
      for (const oldKey of movedKeys) {
        const nextPath = newPath + splitDraftKey(oldKey).path.slice(oldPath.length);
        const nextKey = fileDraftKey(projectId, nextPath);
        draftPersistenceErrors.delete(oldKey);
        draftPersistenceErrors.delete(nextKey);
      }
      notifyDraftErrorListeners();
      return { ok: true };
    } catch (error) {
      const quota = error instanceof DraftStoreQuotaError
        || (typeof error === "object" && error !== null && "name" in error && error.name === "QuotaExceededError");
      const message = quota
        ? "Local dirty-draft storage is full; the rename was not applied."
        : "Unable to move the local dirty draft.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: quota ? "DRAFT_QUOTA_EXCEEDED" : "DRAFT_PERSISTENCE_FAILED", message };
    }
}

/** Reserve the local draft move while the server rename runs. The same
 * draft queue covers the forward move, server operation, and compensation,
 * so another local save cannot interleave between those phases. */
export function runServerRenameWithDrafts(
  projectId: string,
  oldPath: string,
  newPath: string,
  serverRename: () => Promise<boolean>,
): Promise<DraftPersistenceResult> {
  return enqueueDraft(async () => {
    const moved = await moveFileDraftsInternal(projectId, oldPath, newPath);
    if (!moved.ok) return moved;
    if (await serverRename()) return moved;
    const rollback = await moveFileDraftsInternal(projectId, newPath, oldPath);
    if (rollback.ok) return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message: "Server rename failed; the local draft was restored." };
    const message = "Server rename failed and local draft compensation failed; the draft remains available for recovery.";
    reportDraftPersistenceError(projectId, message);
    return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
  });
}

/** Shared ordering boundary for file-tree mutations: server success must
 * precede draft movement, and tab retargeting must follow both. */
export async function runAfterServerFileOperation(
  serverOperation: () => Promise<boolean>,
  afterSuccess: () => Promise<boolean> | boolean,
): Promise<boolean> {
  if (!(await serverOperation())) return false;
  return Boolean(await afterSuccess());
}

/** Shared close boundary: every dirty draft is discarded successfully before
 * the caller mutates the tab strip. */
export async function discardFileDraftsBeforeClose(
  tabs: readonly { projectId?: string; path?: string; dirty?: boolean }[],
  discard: (projectId: string, path: string) => Promise<DraftPersistenceResult> = discardFileDraft,
  hasDirty: (projectId: string, path: string) => Promise<boolean> = hasDirtyDraftsForPath,
): Promise<boolean> {
  for (const tab of tabs) {
    if (!tab.projectId || !tab.path || !(await hasDirty(tab.projectId, tab.path))) continue;
    if (!(await discard(tab.projectId, tab.path)).ok) return false;
  }
  return true;
}

/** Resolve dirty file tabs before the close confirmation. The tab flag is
 * only one source: an inactive tab can have a durable draft while its local
 * React instance is not mounted. */
export async function collectDirtyFileTabs<T extends { projectId?: string; path?: string; dirty?: boolean }>(
  tabs: readonly T[],
  hasDirty: (projectId: string, path: string) => Promise<boolean> = hasDirtyDraftsForPath,
): Promise<T[]> {
  const dirty: T[] = [];
  for (const tab of tabs) {
    if (!tab.projectId || !tab.path) {
      if (tab.dirty) dirty.push(tab);
      continue;
    }
    const persisted = await hasDirty(tab.projectId, tab.path);
    if (tab.dirty || persisted) dirty.push(tab);
  }
  return dirty;
}

export function dirtyDraftsForPath(projectId: string, path: string): string[] {
  const prefix = fileDraftKey(projectId, path);
  return [...fileDrafts.keys()].filter((key) => key === prefix || key.startsWith(`${prefix}/`));
}

export async function hasDirtyDraftsForPath(projectId: string, path: string): Promise<boolean> {
  await hydrateDraftState();
  const prefix = fileDraftKey(projectId, path);
  return [...new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()])]
    .some((key) => key === prefix || key.startsWith(`${prefix}/`));
}

/** Explicitly discard a file or directory's drafts after its server-side
 * deletion has succeeded. The draft records and project index change in one
 * transaction; a failed transaction leaves every record intact. */
export function clearFileDraftsForPath(projectId: string, path: string): Promise<DraftPersistenceResult> {
  return enqueueDraft(async () => {
    await hydrateDraftState();
    const prefix = fileDraftKey(projectId, path);
    const store = await getDraftStore();
    if (!store) {
      const message = "Unable to discard the local dirty draft because storage is unavailable.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      const persistedKeys = [...draftBytesByKey.keys()]
        .filter((key) => key === prefix || key.startsWith(`${prefix}/`));
      const liveKeys = [...liveDraftBytesByKey.keys()]
        .filter((key) => key === prefix || key.startsWith(`${prefix}/`));
      const result = await store.mutate((snapshot) => {
        const current = normalizedStoreSnapshot(snapshot);
        const drafts = current.drafts.filter((entry) => entry.key !== prefix && !entry.key.startsWith(`${prefix}/`));
        return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: current.indexes }) };
      });
      for (const key of liveKeys) liveDraftBytesByKey.delete(key);
      applyDraftStoreSnapshot(result);
      const keys = [...new Set([...persistedKeys, ...liveKeys])];
      for (const key of keys) {
        draftPersistenceErrors.delete(key);
        notifyDraftErrorListeners();
      }
      return { ok: true };
    } catch {
      const message = "Unable to discard the local dirty draft.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
  });
}

/** Reserved for a future explicit project unlink/delete command. The current
 * project registry has no such action and never calls this from list refresh. */
export function clearProjectDrafts(projectId: string): Promise<DraftPersistenceResult> {
  return enqueueDraft(async () => {
    await hydrateDraftState();
    const store = await getDraftStore();
    if (!store) {
      const message = "Unable to clear project dirty drafts because storage is unavailable.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      const persistedKeys = [...draftBytesByKey.keys()].filter((key) => key.startsWith(`${projectId}:`));
      const liveKeys = [...liveDraftBytesByKey.keys()].filter((key) => key.startsWith(`${projectId}:`));
      const result = await store.mutate((snapshot) => {
        const current = normalizedStoreSnapshot(snapshot);
        const drafts = current.drafts.filter((entry) => entry.projectId !== projectId);
        return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: current.indexes }) };
      });
      for (const key of liveKeys) liveDraftBytesByKey.delete(key);
      applyDraftStoreSnapshot(result);
      const keys = [...new Set([...persistedKeys, ...liveKeys])];
      for (const key of keys) {
        draftPersistenceErrors.delete(key);
        notifyDraftErrorListeners();
      }
      draftPersistenceErrors.delete(projectId);
      notifyDraftErrorListeners();
      return { ok: true };
    } catch {
      const message = "Unable to clear project dirty drafts.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
  });
}

export interface FileDraftSnapshotEntry {
  key: string;
  existed: boolean;
  value?: FileDraft;
}

export function snapshotFileDrafts(
  keys: readonly string[],
): FileDraftSnapshotEntry[] {
  return keys.map((key) => {
    const value = fileDrafts.get(key);
    return value
      ? { key, existed: true, value: structuredClone(value) }
      : { key, existed: false };
  });
}

export function applyFileDraftSnapshot(
  snapshot: readonly FileDraftSnapshotEntry[],
): void {
  for (const entry of snapshot) {
    if (entry.existed && entry.value) {
      fileDrafts.set(entry.key, structuredClone(entry.value));
    } else {
      fileDrafts.delete(entry.key);
    }
  }
}
