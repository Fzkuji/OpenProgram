export interface DraftStoreRecord {
  key: string;
  projectId: string;
  path: string;
  draft: string;
  baselineContent: string;
  baselineMtime: number;
  baselineRevision?: string;
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

export interface DraftStoreAdapter {
  load(): Promise<DraftStoreSnapshot>;
  put(record: DraftStoreRecord, index: DraftStoreIndex): Promise<void>;
  remove(keys: string[], index?: DraftStoreIndex): Promise<void>;
  move(records: Array<{ oldKey: string; record: DraftStoreRecord }>, index?: DraftStoreIndex): Promise<void>;
  clear(keys: string[], projectId: string): Promise<void>;
}

export class DraftStoreQuotaError extends Error {
  constructor(message = "The local dirty-draft quota is full.") {
    super(message);
    this.name = "QuotaExceededError";
  }
}

function transactionResult(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
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
    this.dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(IndexedDbDraftStore.databaseName, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("drafts")) {
          const drafts = db.createObjectStore("drafts", { keyPath: "key" });
          drafts.createIndex("projectId", "projectId", { unique: false });
        }
        if (!db.objectStoreNames.contains("project_index"))
          db.createObjectStore("project_index", { keyPath: "projectId" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("Unable to open draft store"));
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

  async put(record: DraftStoreRecord, index: DraftStoreIndex): Promise<void> {
    const db = await this.open();
    const tx = db.transaction(["drafts", "project_index"], "readwrite");
    tx.objectStore("drafts").put(record);
    tx.objectStore("project_index").put(index);
    await transactionResult(tx);
  }

  async remove(keys: string[], index?: DraftStoreIndex): Promise<void> {
    const db = await this.open();
    const tx = db.transaction(["drafts", "project_index"], "readwrite");
    for (const key of keys) tx.objectStore("drafts").delete(key);
    if (index) tx.objectStore("project_index").put(index);
    await transactionResult(tx);
  }

  async move(records: Array<{ oldKey: string; record: DraftStoreRecord }>, index?: DraftStoreIndex): Promise<void> {
    const db = await this.open();
    const tx = db.transaction(["drafts", "project_index"], "readwrite");
    for (const { oldKey, record } of records) {
      tx.objectStore("drafts").put(record);
      tx.objectStore("drafts").delete(oldKey);
    }
    if (index) tx.objectStore("project_index").put(index);
    await transactionResult(tx);
  }

  async clear(keys: string[], projectId: string): Promise<void> {
    const db = await this.open();
    const tx = db.transaction(["drafts", "project_index"], "readwrite");
    for (const key of keys) tx.objectStore("drafts").delete(key);
    tx.objectStore("project_index").delete(projectId);
    await transactionResult(tx);
  }
}

/** Test-only in-memory adapter. Each operation clones its maps first, then
 * commits both stores together, matching IndexedDB transaction semantics. */
export class MemoryDraftStore implements DraftStoreAdapter {
  readonly drafts = new Map<string, DraftStoreRecord>();
  readonly indexes = new Map<string, DraftStoreIndex>();
  failNextWrite = false;

  async load(): Promise<DraftStoreSnapshot> {
    return {
      drafts: [...this.drafts.values()].map((record) => structuredClone(record)),
      indexes: [...this.indexes.values()].map((index) => structuredClone(index)),
    };
  }

  private maybeFail(): void {
    if (!this.failNextWrite) return;
    this.failNextWrite = false;
    throw new DraftStoreQuotaError();
  }

  async put(record: DraftStoreRecord, index: DraftStoreIndex): Promise<void> {
    this.maybeFail();
    this.drafts.set(record.key, structuredClone(record));
    this.indexes.set(index.projectId, structuredClone(index));
  }

  async remove(keys: string[], index?: DraftStoreIndex): Promise<void> {
    this.maybeFail();
    for (const key of keys) this.drafts.delete(key);
    if (index) this.indexes.set(index.projectId, structuredClone(index));
  }

  async move(records: Array<{ oldKey: string; record: DraftStoreRecord }>, index?: DraftStoreIndex): Promise<void> {
    this.maybeFail();
    for (const { oldKey, record } of records) {
      this.drafts.delete(oldKey);
      this.drafts.set(record.key, structuredClone(record));
    }
    if (index) this.indexes.set(index.projectId, structuredClone(index));
  }

  async clear(keys: string[], projectId: string): Promise<void> {
    this.maybeFail();
    for (const key of keys) this.drafts.delete(key);
    this.indexes.delete(projectId);
  }
}
