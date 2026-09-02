/** Durable draft state and lifecycle operations for project file tabs. */
import {
  fileScopeKey,
  draftPersistenceErrors,
  DRAFT_MAX_ENTRIES,
  DRAFT_MAX_BYTES,
  reportDraftPersistenceError,
  notifyDraftErrorListeners,
} from "./file-state-shared";
import {
  IndexedDbDraftStore,
  DraftStoreQuotaError,
  type DraftStoreAdapter,
  type DraftStoreSnapshot,
  rebuildDraftIndexes,
} from "./file-draft-store";

const utf8 = new TextEncoder();
export interface FileDraft {
  draft: string;
  baselineContent: string;
  baselineMtime: number;
  baselineRevision?: string;
  save_status?: "pending" | "persisted" | "error";
}

export function fileDraftKey(projectId: string, path: string): string {
  return fileScopeKey(projectId, path);
}

/** Unsaved drafts surviving tab switches. The pane mirrors dirty entries
 * to IndexedDB and removes them only on save, revert, or explicit discard. */
export const fileDrafts = new Map<string, FileDraft>();

export type DraftPersistenceErrorCode = "DRAFT_QUOTA_EXCEEDED" | "DRAFT_PERSISTENCE_FAILED" | "DRAFT_CONFLICT";

export interface DraftPersistenceResult {
  ok: boolean;
  code?: DraftPersistenceErrorCode;
  message?: string;
  status?: string;
  error_code?: string;
  idempotency_key?: string;
  operation_id?: string;
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

class DraftStoreConflictError extends Error {
  constructor() {
    super("A dirty draft already exists at the rename target.");
    this.name = "DraftConflictError";
  }
}

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
    save_status: draft.save_status ?? "persisted",
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
  const normalizedDrafts = drafts.map(normalizeDraftRecord);
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
      save_status: entry.save_status ?? "persisted",
    });
    draftClock = Math.max(draftClock, entry.updatedAt ?? 0);
  }
  for (const [key, value] of liveEntries) {
    fileDrafts.set(key, value);
  }
}

function normalizeDraftRecord(entry: StoredDraft): StoredDraft {
  const normalized = { ...entry, save_status: entry.save_status ?? "persisted" };
  return { ...normalized, bytes: storedDraftBytes(normalized) };
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

/** Load every dirty draft at a file path or below a directory path. The
 * durable index and failed-write overlay are both authoritative here. */
export async function loadFileDraftsForPath(
  projectId: string,
  path: string,
): Promise<Array<{ path: string; draft: FileDraft }>> {
  await hydrateDraftState();
  const prefix = fileDraftKey(projectId, path);
  const keys = [...new Set([...draftBytesByKey.keys(), ...liveDraftBytesByKey.keys()])]
    .filter((key) => key === prefix || key.startsWith(`${prefix}/`))
    .sort();
  return keys.flatMap((key) => {
    const value = fileDrafts.get(key);
    if (!value) return [];
    return [{ path: splitDraftKey(key).path, draft: structuredClone(value) }];
  });
}

export function persistFileDraft(projectId: string, path: string, value: FileDraft): Promise<DraftPersistenceResult> {
  const key = fileDraftKey(projectId, path);
  return enqueueDraft(async () => {
    await hydrateDraftState();
    const store = await getDraftStore();
    if (!store) {
      const message = "Local dirty-draft storage is unavailable; the dirty buffer was retained.";
      fileDrafts.set(key, { ...structuredClone(value), save_status: "error" });
      liveDraftBytesByKey.set(key, fileDraftBytes(key, value, draftClock + 1));
      reportDraftPersistenceError(key, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
    const stored: StoredDraft = {
      ...structuredClone(value), key, projectId, path, save_status: "persisted",
      bytes: 0, updatedAt: ++draftClock,
    };
    stored.bytes = storedDraftBytes(stored);
    // Record the live candidate before the write. If the browser rejects the
    // transaction, close/delete checks still see this unsaved content.
    fileDrafts.set(key, { ...structuredClone(value), save_status: "pending" });
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
      fileDrafts.set(key, { ...structuredClone(value), save_status: "error" });
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
  const store = await getDraftStore();
  if (!store) {
    const message = "Unable to move the local dirty draft because storage is unavailable.";
    reportDraftPersistenceError(projectId, message);
    return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
  }
  try {
    let movedKeys: string[] = [];
    const result = await store.mutate((snapshot) => {
      const plan = buildMovedDraftSnapshot(snapshot, projectId, oldPath, newPath);
      movedKeys = plan.movedKeys;
      assertDraftSnapshotWithinQuota(plan.snapshot);
      return plan.snapshot;
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
    if (error instanceof DraftStoreConflictError) {
      const message = "A dirty draft already exists at the rename target; the rename was not applied.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_CONFLICT", status: "conflict", error_code: "DRAFT_CONFLICT", message };
    }
    const quota = error instanceof DraftStoreQuotaError
      || (typeof error === "object" && error !== null && "name" in error && error.name === "QuotaExceededError");
    const message = quota
      ? "Local dirty-draft storage is full; the rename was not applied."
      : "Unable to move the local dirty draft.";
    reportDraftPersistenceError(projectId, message);
    return { ok: false, code: quota ? "DRAFT_QUOTA_EXCEEDED" : "DRAFT_PERSISTENCE_FAILED", message };
  }
}

interface DraftMovePlan {
  snapshot: DraftStoreSnapshot;
  movedKeys: string[];
}

function assertDraftSnapshotWithinQuota(snapshot: DraftStoreSnapshot): void {
  if (snapshot.drafts.length > DRAFT_MAX_ENTRIES || snapshotStorageBytes(snapshot) > DRAFT_MAX_BYTES)
    throw new DraftStoreQuotaError();
}

/** Build a move from the persisted snapshot plus live failed-write overlays.
 * A live entry replaces the persisted record at the same key, so a failed
 * newer buffer cannot be replaced by an older durable value. */
function buildMovedDraftSnapshot(
  snapshot: DraftStoreSnapshot,
  projectId: string,
  oldPath: string,
  newPath: string,
): DraftMovePlan {
  const current = normalizedStoreSnapshot(snapshot);
  const liveKeys = new Set(liveDraftBytesByKey.keys());
  const records = current.drafts.filter((entry) => !liveKeys.has(entry.key));
  for (const key of liveKeys) {
    const value = fileDrafts.get(key);
    if (!value) continue;
    const parsed = splitDraftKey(key);
    const updatedAt = ++draftClock;
    records.push({
      ...structuredClone(value), key, projectId: parsed.projectId, path: parsed.path,
      bytes: fileDraftBytes(key, value, updatedAt), updatedAt,
    });
  }
  const prefix = fileScopeKey(projectId, oldPath);
  const movedKeys: string[] = [];
  const moved = records
    .filter((entry) => entry.key === prefix || entry.key.startsWith(`${prefix}/`))
    .map((entry) => {
      const suffix = entry.key === prefix ? "" : entry.key.slice(prefix.length);
      const nextKey = fileDraftKey(projectId, newPath + suffix);
      movedKeys.push(entry.key);
      const updatedAt = ++draftClock;
      return {
        ...entry,
        key: nextKey,
        path: newPath + suffix,
        bytes: fileDraftBytes(nextKey, entry, updatedAt),
        updatedAt,
      };
    });
  if (moved.length === 0) return { snapshot: current, movedKeys };
  const movedKeySet = new Set(movedKeys);
  const existingKeys = new Set([...records.map((entry) => entry.key), ...liveKeys]);
  if (moved.some((entry) => existingKeys.has(entry.key) && !movedKeySet.has(entry.key)))
    throw new DraftStoreConflictError();
  const drafts = [
    ...records.filter((entry) => !movedKeySet.has(entry.key)),
    ...moved,
  ];
  return { snapshot: { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: current.indexes }) }, movedKeys };
}

/** Check the complete post-rename record and index in an adapter transaction,
 * but return the current snapshot so no local move is committed yet. */
async function preflightMoveFileDraftsInternal(
  projectId: string,
  oldPath: string,
  newPath: string,
): Promise<DraftPersistenceResult> {
  await hydrateDraftState();
  const store = await getDraftStore();
  if (!store) {
    const message = "Unable to validate the local dirty draft because storage is unavailable.";
    reportDraftPersistenceError(projectId, message);
    return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
  }
  try {
    await store.mutate((snapshot) => {
      const plan = buildMovedDraftSnapshot(snapshot, projectId, oldPath, newPath);
      assertDraftSnapshotWithinQuota(plan.snapshot);
      return normalizedStoreSnapshot(snapshot);
    });
    return { ok: true };
  } catch (error) {
    if (error instanceof DraftStoreConflictError) {
      const message = "A dirty draft already exists at the rename target; the rename was not applied.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_CONFLICT", status: "conflict", error_code: "DRAFT_CONFLICT", message };
    }
    const quota = error instanceof DraftStoreQuotaError
      || (typeof error === "object" && error !== null && "name" in error && error.name === "QuotaExceededError");
    const message = quota
      ? "Local dirty-draft storage is full; the rename was not applied."
      : "Unable to validate the local dirty draft rename.";
    reportDraftPersistenceError(projectId, message);
    return { ok: false, code: quota ? "DRAFT_QUOTA_EXCEEDED" : "DRAFT_PERSISTENCE_FAILED", message };
  }
}

export type ServerRenameResult = {
  status: "ready" | "error" | "recovery_required";
  error_code?: string;
  error?: string;
  idempotency_key?: string;
  operation_id?: string;
};

function withServerMetadata(
  result: DraftPersistenceResult,
  serverResult: ServerRenameResult,
): DraftPersistenceResult {
  return {
    ...result,
    status: serverResult.status,
    error_code: serverResult.error_code ?? result.error_code ?? result.code,
    idempotency_key: serverResult.idempotency_key,
    operation_id: serverResult.operation_id,
  };
}

/** Validate local quota, perform the structured server rename, then commit
 * the local move. A failed local commit invokes an idempotent reverse server
 * operation; local old-path records remain intact throughout. */
export function runServerRenameWithDrafts(
  projectId: string,
  oldPath: string,
  newPath: string,
  serverRename: () => Promise<ServerRenameResult>,
  reverseRename: (serverResult: ServerRenameResult) => Promise<ServerRenameResult>,
): Promise<DraftPersistenceResult> {
  return enqueueDraft(async () => {
    try {
      const preflight = await preflightMoveFileDraftsInternal(projectId, oldPath, newPath);
      if (!preflight.ok) return preflight;
      let serverResult: ServerRenameResult;
      try {
        serverResult = await serverRename();
      } catch {
        const message = "The server rename could not be confirmed; the local draft was retained.";
        reportDraftPersistenceError(projectId, message);
        return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
      }
      if (!serverResult || serverResult.status !== "ready") {
        const message = serverResult?.status === "recovery_required"
          ? "The server rename requires recovery; the local draft was retained."
          : serverResult?.error ?? "The server rejected the rename; the local draft was retained.";
        reportDraftPersistenceError(projectId, message);
        return withServerMetadata({ ok: false, code: "DRAFT_PERSISTENCE_FAILED", message }, serverResult ?? {
          status: "error", error_code: "TRANSPORT_ERROR", error: message,
        });
      }
      const moved = await moveFileDraftsInternal(projectId, oldPath, newPath);
      if (moved.ok) return withServerMetadata(moved, serverResult);
      try {
        const compensation = await reverseRename(serverResult);
        if (compensation?.status === "ready") {
          const result = withServerMetadata(moved, compensation);
          return { ...result, status: "error" };
        }
        const message = "The server rename succeeded but local draft movement failed; recovery is required and the old draft remains available.";
        reportDraftPersistenceError(projectId, message);
        return withServerMetadata({ ok: false, code: "DRAFT_PERSISTENCE_FAILED", message }, compensation ?? {
          status: "recovery_required", error_code: "TRANSPORT_ERROR", error: message,
        });
      } catch {
        // Fall through to the durable recovery error below.
      }
      const message = "The server rename succeeded but local draft movement failed; recovery is required and the old draft remains available.";
      reportDraftPersistenceError(projectId, message);
      return withServerMetadata({ ok: false, code: "DRAFT_PERSISTENCE_FAILED", message }, {
        ...serverResult, status: "recovery_required", error_code: "RENAME_COMPENSATION_FAILED", error: message,
      });
    } catch {
      const message = "The file rename could not be completed; the local draft was retained.";
      reportDraftPersistenceError(projectId, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message };
    }
  });
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
      reportDraftPersistenceError(prefix, message);
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
      reportDraftPersistenceError(prefix, message);
      return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", status: "recovery_required", message };
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
