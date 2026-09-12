import { DRAFT_MAX_BYTES } from "./file-state-shared.ts";

export interface DraftStoreRecord {
  key: string;
  projectId: string;
  path: string;
  draft: string;
  baselineContent: string;
  baselineMtime: number;
  baselineRevision?: string;
  save_status?: "pending" | "persisted" | "error";
  bytes: number;
  updatedAt: number;
}

export interface DraftStoreIndex {
  projectId: string;
  keys: string[];
  count: number;
  bytes: number;
}

export interface DraftStoreSnapshot {
  drafts: DraftStoreRecord[];
  indexes: DraftStoreIndex[];
}

export type DraftStoreMutation = (snapshot: DraftStoreSnapshot) => DraftStoreSnapshot;

/** Rebuild aggregate indexes exclusively from draft records. Stale index keys
 * are ignored and projects with no remaining draft receive no index. */
export function rebuildDraftIndexes(snapshot: DraftStoreSnapshot): DraftStoreIndex[] {
  const grouped = new Map<string, DraftStoreIndex>();
  for (const record of snapshot.drafts) {
    const index = grouped.get(record.projectId) ?? {
      projectId: record.projectId, keys: [], count: 0, bytes: 0,
    };
    if (!index.keys.includes(record.key)) index.keys.push(record.key);
    index.count = index.keys.length;
    index.bytes = index.keys.reduce((sum, key) => {
      const draft = snapshot.drafts.find((candidate) => candidate.key === key);
      return sum + (draft?.bytes ?? 0);
    }, 0);
    grouped.set(record.projectId, index);
  }
  return [...grouped.values()];
}

export interface DraftStoreAdapter {
  load(): Promise<DraftStoreSnapshot>;
  mutate(operation: DraftStoreMutation): Promise<DraftStoreSnapshot>;
  repair(): Promise<DraftStoreSnapshot>;
}

export interface DocumentDraftPending {
  kind: "content" | "restore";
  bytes: Blob;
  baseline: string;
  key: string;
  editor: string;
  close: boolean;
  generation: number;
  version?: string;
  side?: "before" | "after";
}
export interface DocumentDraftRecord {
  key: string;
  projectId: string;
  path: string;
  baselineRevision: string;
  latestDraft: Blob;
  generation: number;
  editorId: string;
  pending?: DocumentDraftPending;
  updatedAt: number;
  storageVersion?: number;
}
function textDraftBytes(record: DocumentDraftRecord): number {
  return [record.latestDraft, record.pending?.bytes].reduce((total, blob) =>
    total + (blob?.type.startsWith("text/") ? blob.size : 0), 0);
}

export interface DocumentDraftMetadata {
  key: string;
  projectId: string;
  path: string;
  bytes: number;
  storageVersion: number;
  textBytes?: number;
}

function completeTransaction(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

// Both legacy text drafts and document drafts must create the complete schema,
// regardless of which reader opens a fresh database first.
let fileDraftDatabase: Promise<IDBDatabase> | null = null;
function openFileDraftDatabase(): Promise<IDBDatabase> {
  if (fileDraftDatabase) return fileDraftDatabase;
  fileDraftDatabase = new Promise<IDBDatabase>((resolve, reject) => {
    let rejected = false;
    const request = indexedDB.open("openprogram-file-drafts", 3);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("drafts")) {
        const drafts = db.createObjectStore("drafts", { keyPath: "key" });
        drafts.createIndex("projectId", "projectId", { unique: false });
      }
      if (!db.objectStoreNames.contains("project_index"))
        db.createObjectStore("project_index", { keyPath: "projectId" });
      if (!db.objectStoreNames.contains("document_drafts"))
        db.createObjectStore("document_drafts", { keyPath: "key" });
      if (!db.objectStoreNames.contains("document_draft_metadata")) {
        const metadata = db.createObjectStore("document_draft_metadata", { keyPath: "key" });
        // Recover metadata for drafts written by the earlier v2 implementation.
        const cursor = request.transaction!.objectStore("document_drafts").openCursor();
        cursor.onsuccess = () => {
          const item = cursor.result;
          if (!item) return;
          const record = item.value as DocumentDraftRecord;
          metadata.put({ key: record.key, projectId: record.projectId,
            path: record.path, bytes: record.latestDraft.size + (record.pending?.bytes.size ?? 0),
            storageVersion: record.storageVersion ?? 0 });
          item.continue();
        };
      }
    };
    request.onblocked = () => {
      rejected = true;
      reject(new Error("Local draft storage is blocked by another window. Close that window and retry."));
    };
    request.onerror = () => reject(request.error ?? new Error("Unable to open local drafts"));
    request.onsuccess = () => {
      const db = request.result;
      if (rejected) { db.close(); return; }
      db.onversionchange = () => { db.close(); fileDraftDatabase = null; };
      resolve(db);
    };
  }).catch((error) => { fileDraftDatabase = null; throw error; });
  return fileDraftDatabase;
}

/** Per-file Blob operations never read or rewrite other documents' contents. */
export class IndexedDbDocumentDraftStore {
  static readonly databaseName = "openprogram-file-drafts";
  static readonly maxBytes = 64 * 1024 * 1024;
  static readonly maxTotalBytes = 256 * 1024 * 1024;
  static readonly maxEntries = 32;
  static readonly maxTextBytes = DRAFT_MAX_BYTES;

  async get(key: string): Promise<DocumentDraftRecord | null> {
    const db = await openFileDraftDatabase();
    const tx = db.transaction("document_drafts", "readonly");
    const done = completeTransaction(tx);
    const result = await requestResult(tx.objectStore("document_drafts").get(key));
    await done;
    return result ?? null;
  }

  async list(projectId: string, path = ""): Promise<DocumentDraftMetadata[]> {
    const db = await openFileDraftDatabase();
    const tx = db.transaction("document_draft_metadata", "readonly");
    const done = completeTransaction(tx);
    const values = await requestResult<DocumentDraftMetadata[]>(tx.objectStore("document_draft_metadata").getAll());
    await done;
    return values.filter((entry) => entry.projectId === projectId &&
      (!path || entry.path === path || entry.path.startsWith(`${path}/`)));
  }

  async put(record: DocumentDraftRecord): Promise<number> {
    if (record.latestDraft.size > IndexedDbDocumentDraftStore.maxBytes ||
        (record.pending?.bytes.size ?? 0) > IndexedDbDocumentDraftStore.maxBytes)
      throw new DraftStoreQuotaError("The local draft is larger than 64 MiB.");
    const db = await openFileDraftDatabase();
    return new Promise<number>((resolve, reject) => {
      const tx = db.transaction(["document_drafts", "document_draft_metadata"], "readwrite");
      const metadata = tx.objectStore("document_draft_metadata");
      const query = metadata.getAll();
      let version = 0;
      let failure: Error | null = null;
      query.onsuccess = () => {
        const entries = query.result as DocumentDraftMetadata[];
        const previous = entries.find((entry) => entry.key === record.key);
        if ((previous?.storageVersion ?? 0) !== (record.storageVersion ?? 0)) {
          failure = new Error("Another window changed this document draft. Export your changes before resolving it.");
          tx.abort(); return;
        }
        const bytes = record.latestDraft.size + (record.pending?.bytes.size ?? 0);
        const textBytes = textDraftBytes(record);
        const totalText = entries.reduce((sum, entry) => sum + (entry.key === record.key ? 0 : entry.textBytes ?? 0), textBytes);
        const total = entries.reduce((sum, entry) => sum + (entry.key === record.key ? 0 : entry.bytes), bytes);
        if ((!previous && entries.length >= IndexedDbDocumentDraftStore.maxEntries) ||
            total > IndexedDbDocumentDraftStore.maxTotalBytes ||
            totalText > IndexedDbDocumentDraftStore.maxTextBytes) {
          failure = new DraftStoreQuotaError(); tx.abort(); return;
        }
        version = (previous?.storageVersion ?? 0) + 1;
        tx.objectStore("document_drafts").put({ ...record, storageVersion: version });
        metadata.put({ key: record.key, projectId: record.projectId, path: record.path, bytes, textBytes, storageVersion: version });
      };
      tx.oncomplete = () => resolve(version);
      tx.onerror = tx.onabort = () => reject(failure ?? tx.error ?? new Error("Unable to persist document draft"));
    });
  }

  async delete(key: string, expectedVersion?: number): Promise<void> {
    const db = await openFileDraftDatabase();
    return new Promise<void>((resolve, reject) => {
      const tx = db.transaction(["document_drafts", "document_draft_metadata"], "readwrite");
      const query = tx.objectStore("document_draft_metadata").get(key);
      let mismatch = false;
      query.onsuccess = () => {
        if (expectedVersion !== undefined && (query.result?.storageVersion ?? 0) !== expectedVersion) {
          mismatch = true; tx.abort(); return;
        }
        tx.objectStore("document_drafts").delete(key);
        tx.objectStore("document_draft_metadata").delete(key);
      };
      tx.oncomplete = () => resolve();
      tx.onerror = tx.onabort = () => reject(mismatch
        ? new Error("Another window changed this draft; it was retained.")
        : tx.error ?? new Error("Unable to remove local draft"));
    });
  }
}

export class DraftStoreQuotaError extends Error {
  constructor(message = "The local dirty-draft quota is full.") {
    super(message);
    this.name = "QuotaExceededError";
  }
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

export class IndexedDbDraftStore implements DraftStoreAdapter {
  static readonly databaseName = "openprogram-file-drafts";
  private open(): Promise<IDBDatabase> { return openFileDraftDatabase(); }

  async load(): Promise<DraftStoreSnapshot> {
    const db = await this.open();
    const tx = db.transaction(["drafts", "project_index"], "readonly");
    return {
      drafts: await requestResult(tx.objectStore("drafts").getAll()),
      indexes: await requestResult(tx.objectStore("project_index").getAll()),
    };
  }

  async mutate(operation: DraftStoreMutation): Promise<DraftStoreSnapshot> {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(["drafts", "project_index"], "readwrite");
      const draftsRequest = tx.objectStore("drafts").getAll();
      const indexesRequest = tx.objectStore("project_index").getAll();
      let drafts: DraftStoreRecord[] | undefined;
      let indexes: DraftStoreIndex[] | undefined;
      let next: DraftStoreSnapshot | undefined;
      const run = () => {
        if (!drafts || !indexes || next) return;
        try {
          next = operation({ drafts, indexes });
          const draftStore = tx.objectStore("drafts");
          const indexStore = tx.objectStore("project_index");
          draftStore.clear();
          indexStore.clear();
          for (const record of next.drafts) draftStore.put(record);
          for (const index of next.indexes) indexStore.put(index);
        } catch (error) {
          tx.abort();
          reject(error);
        }
      };
      draftsRequest.onsuccess = () => { drafts = draftsRequest.result as DraftStoreRecord[]; run(); };
      indexesRequest.onsuccess = () => { indexes = indexesRequest.result as DraftStoreIndex[]; run(); };
      tx.oncomplete = () => { if (next) resolve(next); };
      tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
      tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
    });
  }

  repair(): Promise<DraftStoreSnapshot> {
    return this.mutate((snapshot) => {
      const drafts = snapshot.drafts.map((record) => ({
        ...record,
        save_status: record.save_status ?? "persisted",
      }));
      return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
    });
  }

}

/** Test-only in-memory adapter. Each operation clones its maps first, then
 * commits both stores together, matching IndexedDB transaction semantics. */
export class MemoryDraftStore implements DraftStoreAdapter {
  readonly drafts = new Map<string, DraftStoreRecord>();
  readonly indexes = new Map<string, DraftStoreIndex>();
  failNextWrite = false;
  private mutationQueue: Promise<unknown> = Promise.resolve();

  async load(): Promise<DraftStoreSnapshot> {
    return {
      drafts: [...this.drafts.values()].map((record) => structuredClone(record)),
      indexes: [...this.indexes.values()].map((index) => structuredClone(index)),
    };
  }

  mutate(operation: DraftStoreMutation): Promise<DraftStoreSnapshot> {
    const next = this.mutationQueue.then(async () => {
      this.maybeFail();
      const snapshot = await this.load();
      const result = operation(snapshot);
      this.drafts.clear();
      this.indexes.clear();
      for (const record of result.drafts) this.drafts.set(record.key, structuredClone(record));
      for (const index of result.indexes) this.indexes.set(index.projectId, structuredClone(index));
      return result;
    });
    this.mutationQueue = next.catch(() => undefined);
    return next;
  }

  repair(): Promise<DraftStoreSnapshot> {
    return this.mutate((snapshot) => {
      const drafts = snapshot.drafts.map((record) => ({
        ...record,
        save_status: record.save_status ?? "persisted",
      }));
      return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
    });
  }

  private maybeFail(): void {
    if (!this.failNextWrite) return;
    this.failNextWrite = false;
    throw new DraftStoreQuotaError();
  }

}
