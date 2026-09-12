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

/** Durable binary document records share the file-draft database.  The
 * `pending` member is deliberately separate from `draft`: a newer edit can
 * never change the bytes or idempotency key of an in-flight operation. */
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
  projectId?: string;
  path: string;
  sessionId?: string;
  readOnly?: boolean;
  baselineRevision: string;
  latestDraft: Blob;
  generation: number;
  editorId: string;
  pending?: DocumentDraftPending;
  restore?: { key: string; version: string; side: "before" | "after"; baseline: string };
  updatedAt: number;
}

function completeTransaction(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

/** A small per-document adapter over `openprogram-file-drafts`, with bounded
 * records and transaction completion awaited by every mutation. */
export class IndexedDbDocumentDraftStore {
  static readonly databaseName = "openprogram-file-drafts";
  static readonly maxBytes = 64 * 1024 * 1024;
  private dbPromise: Promise<IDBDatabase> | null = null;
  private open(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise;
    this.dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(IndexedDbDocumentDraftStore.databaseName, 2);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("document_drafts")) db.createObjectStore("document_drafts", { keyPath: "key" });
      };
      req.onblocked = () => reject(new Error("Local draft storage is blocked by another tab."));
      req.onerror = () => reject(req.error ?? new Error("Unable to open document draft storage"));
      req.onsuccess = () => {
        const db = req.result;
        db.onversionchange = () => db.close();
        resolve(db);
      };
    }).catch((error) => { this.dbPromise = null; throw error; });
    return this.dbPromise;
  }
  async get(key: string): Promise<DocumentDraftRecord | null> {
    const db = await this.open();
    const tx = db.transaction("document_drafts", "readonly");
    const req = tx.objectStore("document_drafts").get(key);
    const result = await requestResult(req);
    await completeTransaction(tx);
    return result ?? null;
  }
  async put(record: DocumentDraftRecord): Promise<void> {
    if (record.latestDraft.size > IndexedDbDocumentDraftStore.maxBytes || (record.pending?.bytes.size ?? 0) > IndexedDbDocumentDraftStore.maxBytes)
      throw new DraftStoreQuotaError("The local binary draft is larger than 64 MiB.");
    const db = await this.open();
    const tx = db.transaction("document_drafts", "readwrite");
    tx.objectStore("document_drafts").put(record);
    await completeTransaction(tx);
  }
  async delete(key: string): Promise<void> {
    const db = await this.open();
    const tx = db.transaction("document_drafts", "readwrite");
    tx.objectStore("document_drafts").delete(key);
    await completeTransaction(tx);
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
  private dbPromise: Promise<IDBDatabase> | null = null;

  private open(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise;
    const opening = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(IndexedDbDraftStore.databaseName, 2);
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
      };
      request.onblocked = () => reject(new Error("Local draft storage is blocked by another tab."));
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
      request.onerror = () => reject(request.error ?? new Error("Unable to open draft store"));
    });
    // A rejected open must not poison this store instance forever. A later
    // load/save can retry after the browser has recovered storage access.
    this.dbPromise = opening.catch((error) => {
      this.dbPromise = null;
      throw error;
    });
    return this.dbPromise;
  }

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
