/**
 * Shared file-browsing plumbing used by the right-sidebar FileTree,
 * the center FileTabPane / FileViewer, and anything else that talks
 * to the worker's project-file actions. (Was part of the v1
 * files-panel store; the tab state moved to center-tabs-store.)
 */
import { useCallback, useEffect, useState } from "react";

import { wsRequest, type WsRequestOptions } from "@/lib/net/ws-request";
import { useSessionStore } from "@/lib/session-store";
import {
  IndexedDbDraftStore,
  type DraftStoreAdapter,
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

function reportDraftPersistenceError(scope: string, message: string): void {
  draftPersistenceErrors.set(scope, message);
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
    // Only a successful registry response is authoritative. Empty is a
    // removal snapshot only after a prior successful snapshot; no product
    // delete action is inferred from a transient unavailable response.
    await reconcileProjectSnapshot(projects.map((item) => item.id));
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

const DRAFT_DB_NAME = "openprogram-file-drafts";
const DRAFT_DB_VERSION = 1;
const DRAFT_STORE = "drafts";
const DRAFT_INDEX_STORE = "project_index";
let draftDbPromise: Promise<IDBDatabase | null> | null = null;
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

function draftMetadataBytes(key: string, draft: FileDraft): number {
  return utf8.encode(JSON.stringify({
    key,
    baselineMtime: draft.baselineMtime,
    baselineRevision: draft.baselineRevision ?? null,
  })).byteLength;
}

export function fileDraftBytes(key: string, draft: FileDraft): number {
  return utf8.encode(draft.draft).byteLength
    + utf8.encode(draft.baselineContent).byteLength
    + draftMetadataBytes(key, draft);
}

function idbAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

function openDraftDb(): Promise<IDBDatabase | null> {
  if (!idbAvailable()) return Promise.resolve(null);
  if (draftDbPromise) return draftDbPromise;
  draftDbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DRAFT_DB_NAME, DRAFT_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(DRAFT_STORE)) {
        const store = db.createObjectStore(DRAFT_STORE, { keyPath: "key" });
        store.createIndex("projectId", "projectId", { unique: false });
      }
      if (!db.objectStoreNames.contains(DRAFT_INDEX_STORE))
        db.createObjectStore(DRAFT_INDEX_STORE, { keyPath: "projectId" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Unable to open draft store"));
  }).catch(() => null);
  return draftDbPromise!;
}

function idbRequest<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function waitForTransaction(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

async function hydrateDraftState(): Promise<void> {
  if (draftHydration) return draftHydration;
  draftHydration = (async () => {
    const db = await openDraftDb();
    if (!db) return;
    const tx = db.transaction([DRAFT_STORE, DRAFT_INDEX_STORE], "readonly");
    const drafts = await idbRequest(tx.objectStore(DRAFT_STORE).getAll()) as StoredDraft[];
    const indexes = await idbRequest(tx.objectStore(DRAFT_INDEX_STORE).getAll()) as ProjectDraftIndex[];
    draftBytesByKey = new Map(drafts.map((entry) => [entry.key, entry.bytes ?? fileDraftBytes(entry.key, entry)]));
    draftIndexByProject = new Map(indexes.map((entry) => [entry.projectId, entry]));
    for (const entry of drafts) {
      fileDrafts.set(entry.key, {
        draft: entry.draft,
        baselineContent: entry.baselineContent,
        baselineMtime: entry.baselineMtime,
        baselineRevision: entry.baselineRevision,
      });
      const index = draftIndexByProject.get(entry.projectId) ?? {
        projectId: entry.projectId, keys: [], count: 0, bytes: 0,
      };
      if (!index.keys.includes(entry.key)) index.keys.push(entry.key);
      index.count = index.keys.length;
      index.bytes = index.keys.reduce((sum, key) => sum + (draftBytesByKey.get(key) ?? 0), 0);
      draftIndexByProject.set(entry.projectId, index);
      draftClock = Math.max(draftClock, entry.updatedAt ?? 0);
    }
    const repair = db.transaction(DRAFT_INDEX_STORE, "readwrite");
    for (const index of draftIndexByProject.values()) repair.objectStore(DRAFT_INDEX_STORE).put(index);
    await waitForTransaction(repair).catch(() => undefined);
  })().catch(() => undefined);
  return draftHydration;
}

function draftBytesTotal(): number {
  const keys = new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()]);
  return [...keys].reduce((sum, key) => sum + (liveDraftBytesByKey.get(key) ?? draftBytesByKey.get(key) ?? 0), 0);
}

function effectiveDraftBytes(key: string): number {
  return liveDraftBytesByKey.get(key) ?? draftBytesByKey.get(key) ?? 0;
}

export function canPersistFileDraft(key: string, draft: FileDraft): boolean {
  const bytes = fileDraftBytes(key, draft);
  const keys = new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()]);
  const previous = effectiveDraftBytes(key);
  const count = keys.has(key) ? keys.size : keys.size + 1;
  return count <= DRAFT_MAX_ENTRIES && draftBytesTotal() - previous + bytes <= DRAFT_MAX_BYTES;
}

/** Inject a transactional fake for executable web tests. Production leaves
 * this unset and always uses the native IndexedDB adapter. */
export function setDraftStoreAdapterForTests(adapter: DraftStoreAdapter | null): void {
  draftStoreOverride = adapter;
  draftStorePromise = null;
  draftHydration = Promise.resolve();
  draftBytesByKey = new Map();
  draftIndexByProject = new Map();
  liveDraftBytesByKey.clear();
  fileDrafts.clear();
  draftPersistenceErrors.clear();
}

function enqueueDraft<T>(operation: () => Promise<T>): Promise<T> {
  const next = draftQueue.then(operation, operation);
  draftQueue = next.catch(() => undefined);
  return next;
}

export async function loadFileDraft(projectId: string, path: string): Promise<FileDraft | null> {
  await hydrateDraftState();
  const key = fileDraftKey(projectId, path);
  const inMemory = fileDrafts.get(key);
  if (inMemory) return structuredClone(inMemory);
  const db = await openDraftDb();
  if (!db) return null;
  try {
    const tx = db.transaction(DRAFT_STORE, "readonly");
    const stored = await idbRequest(tx.objectStore(DRAFT_STORE).get(key)) as StoredDraft | undefined;
    if (!stored) return null;
    const value: FileDraft = {
      draft: stored.draft,
      baselineContent: stored.baselineContent,
      baselineMtime: stored.baselineMtime,
      baselineRevision: stored.baselineRevision,
    };
    fileDrafts.set(key, value);
    return structuredClone(value);
  } catch {
    return null;
  }
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
    const previousBytes = draftBytesByKey.get(key) ?? 0;
    const stored: StoredDraft = {
      ...structuredClone(value), key, projectId, path,
      bytes: fileDraftBytes(key, value), updatedAt: ++draftClock,
    };
    const previousIndex = draftIndexByProject.get(projectId) ?? { projectId, keys: [], count: 0, bytes: 0 };
    const hasKey = previousIndex.keys.includes(key);
    const index: ProjectDraftIndex = {
      projectId,
      keys: hasKey ? [...previousIndex.keys] : [...previousIndex.keys, key],
      count: hasKey ? previousIndex.count : previousIndex.count + 1,
      bytes: previousIndex.bytes - previousBytes + stored.bytes,
    };
    // Record the live candidate before the write. If the browser rejects the
    // transaction, close/delete checks still see this unsaved content.
    fileDrafts.set(key, structuredClone(value));
    liveDraftBytesByKey.set(key, stored.bytes);
    try {
      await store.put(stored, index);
      draftBytesByKey.set(key, stored.bytes);
      liveDraftBytesByKey.delete(key);
      draftIndexByProject.set(projectId, index);
      draftPersistenceErrors.delete(key);
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
    const previous = effectiveDraftBytes(key);
    const oldIndex = draftIndexByProject.get(projectId);
    if (!store) {
      const message = "Unable to discard the local dirty draft because storage is unavailable.";
      reportDraftPersistenceError(key, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      const nextIndex = oldIndex && (() => {
        const keys = oldIndex.keys.filter((candidate) => candidate !== key);
        return { projectId, keys, count: keys.length, bytes: Math.max(0, oldIndex.bytes - previous) } satisfies ProjectDraftIndex;
      })();
      await store.remove([key], nextIndex);
      fileDrafts.delete(key);
      draftBytesByKey.delete(key);
      liveDraftBytesByKey.delete(key);
      draftPersistenceErrors.delete(key);
      if (nextIndex) draftIndexByProject.set(projectId, nextIndex);
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
  return enqueueDraft(async () => {
    await hydrateDraftState();
    const prefix = fileScopeKey(projectId, oldPath);
    const entries = [...fileDrafts.entries()].filter(([key]) => key === prefix || key.startsWith(`${prefix}/`));
    if (entries.length === 0) return { ok: true };
    const moved = entries.map(([key, value]) => {
      const suffix = key === prefix ? "" : key.slice(prefix.length);
      const nextPath = newPath + suffix;
      const nextKey = fileDraftKey(projectId, nextPath);
      return { key, value, nextPath, nextKey, bytes: fileDraftBytes(nextKey, value) };
    });
    const oldIndex = draftIndexByProject.get(projectId);
    const nextKeys = oldIndex?.keys.map((key) => {
      const item = moved.find((candidate) => candidate.key === key);
      return item?.nextKey ?? key;
    });
    const nextBytes = nextKeys?.reduce((sum, key) => {
      const item = moved.find((candidate) => candidate.nextKey === key);
      return sum + (item?.bytes ?? draftBytesByKey.get(key) ?? 0);
    }, 0);
    const store = await getDraftStore();
    if (!store) {
      const message = "Unable to move the local dirty draft because storage is unavailable.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      await store.move(
        moved.map((item) => ({
          oldKey: item.key,
          record: {
            ...structuredClone(item.value), key: item.nextKey, projectId,
            path: item.nextPath, bytes: item.bytes, updatedAt: ++draftClock,
          } satisfies StoredDraft,
        })),
        oldIndex && nextKeys && nextBytes !== undefined
          ? { ...oldIndex, keys: nextKeys, bytes: nextBytes }
          : undefined,
      );
      for (const item of moved) {
        fileDrafts.set(item.nextKey, item.value);
        draftBytesByKey.set(item.nextKey, item.bytes);
        if (liveDraftBytesByKey.has(item.key)) liveDraftBytesByKey.set(item.nextKey, item.bytes);
        draftBytesByKey.delete(item.key);
        liveDraftBytesByKey.delete(item.key);
        fileDrafts.delete(item.key);
        draftPersistenceErrors.delete(item.key);
      }
      if (oldIndex && nextKeys && nextBytes !== undefined) draftIndexByProject.set(projectId, { ...oldIndex, keys: nextKeys, bytes: nextBytes });
      return { ok: true };
    } catch {
      const message = "Unable to move the local dirty draft.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
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
): Promise<boolean> {
  for (const tab of tabs) {
    if (!tab.dirty || !tab.projectId || !tab.path) continue;
    if (!(await discard(tab.projectId, tab.path)).ok) return false;
  }
  return true;
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
    const keys = [...new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()])]
      .filter((key) => key === prefix || key.startsWith(`${prefix}/`));
    if (keys.length === 0) return { ok: true };
    const store = await getDraftStore();
    const index = draftIndexByProject.get(projectId);
    const nextKeys = index?.keys.filter((key) => !keys.includes(key));
    const nextIndex = index && nextKeys ? {
      projectId, keys: nextKeys, count: nextKeys.length,
      bytes: nextKeys.reduce((sum, key) => sum + (draftBytesByKey.get(key) ?? 0), 0),
    } satisfies ProjectDraftIndex : undefined;
    if (!store) {
      const message = "Unable to discard the local dirty draft because storage is unavailable.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      await store.remove(keys, nextIndex);
      for (const key of keys) {
        fileDrafts.delete(key);
        draftBytesByKey.delete(key);
        liveDraftBytesByKey.delete(key);
        draftPersistenceErrors.delete(key);
      }
      if (nextIndex) draftIndexByProject.set(projectId, nextIndex);
      return { ok: true };
    } catch {
      const message = "Unable to discard the local dirty draft.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
  });
}

export function clearProjectDrafts(projectId: string): Promise<DraftPersistenceResult> {
  return enqueueDraft(async () => {
    await hydrateDraftState();
    const store = await getDraftStore();
    const keys = [...new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()])]
      .filter((key) => key.startsWith(`${projectId}:`));
    if (!store) {
      const message = "Unable to clear project dirty drafts because storage is unavailable.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    try {
      await store.clear(keys, projectId);
      for (const key of keys) {
        fileDrafts.delete(key);
        draftBytesByKey.delete(key);
        liveDraftBytesByKey.delete(key);
        draftPersistenceErrors.delete(key);
      }
      draftIndexByProject.delete(projectId);
      draftPersistenceErrors.delete(projectId);
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
