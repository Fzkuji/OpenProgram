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
    return this.mutate((snapshot) => ({
      drafts: snapshot.drafts,
      indexes: rebuildDraftIndexes(snapshot),
    }));
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
    return this.mutate((snapshot) => ({ drafts: snapshot.drafts, indexes: rebuildDraftIndexes(snapshot) }));
  }

  private maybeFail(): void {
    if (!this.failNextWrite) return;
    this.failNextWrite = false;
    throw new DraftStoreQuotaError();
  }

}
