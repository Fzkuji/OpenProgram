import { IndexedDbDocumentDraftStore, type DocumentDraftPending, type DocumentDraftRecord } from "./file-draft-store";
import { discardFileDraft, loadFileDraft } from "./file-drafts";
import { invalidateFileRead } from "./files-shared";
import type { DocumentControllerOptions, DocumentHistoryEntry, DocumentIdentity, DocumentSnapshot } from "./document-types";

export type DocumentStatus = "idle" | "dirty" | "saving" | "error" | "conflict" | "closed";
export interface DocumentControllerState {
  identity: DocumentIdentity;
  snapshot: DocumentSnapshot | null;
  draft: Blob | null;
  generation: number;
  status: DocumentStatus;
  error: string | null;
  restoring: boolean;
  renaming: boolean;
}
export type DocumentListener = (state: DocumentControllerState) => void;
const controllers = new Map<string, DocumentController>();
const renames = new Set<{ projectId: string; paths: string[] }>();
function matchesRename(identity: DocumentIdentity, rename: { projectId: string; paths: string[] }): boolean {
  return identity.kind === "project" && identity.projectId === rename.projectId &&
    rename.paths.some((path) => identity.path === path || identity.path.startsWith(`${path}/`));
}
const binaryStore = new IndexedDbDocumentDraftStore();
const blobOf = (value: Blob | Uint8Array | string): Blob => value instanceof Blob
  ? value.slice(0, value.size, value.type)
  : new Blob([typeof value === "string" ? value : new Uint8Array(value)],
    typeof value === "string" ? { type: "text/plain;charset=utf-8" } : undefined);
const revisionValid = (value: unknown): value is string => typeof value === "string" && /^(?:[a-f0-9]{64}|absent)$/.test(value);
const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

function identityFor(options: DocumentControllerOptions): DocumentIdentity {
  // Absolute attachment paths are not project-relative paths and must stay absolute.
  if (options.readOnly) return { kind: "attachment", sessionId: options.sessionId ?? "", path: options.path, readOnly: true };
  const path = options.path.replace(/\\/g, "/");
  if (!options.projectId || !path || path.startsWith("/") || path.split("/").some((part) => !part || part === "." || part === ".."))
    throw new Error("A project and a relative document path are required.");
  return { kind: "project", projectId: options.projectId, path };
}
export const documentIdentityKey = (identity: DocumentIdentity) => identity.kind === "project"
  ? `project:${identity.projectId}:${identity.path}` : `attachment:${identity.sessionId}:${identity.path}`;

/** One document's durable draft and serial publication, independent of React. */
export class DocumentController {
  readonly identity: DocumentIdentity;
  private readonly fetcher: typeof fetch;
  private readonly debounceMs: number;
  private readonly maxDebounceMs: number;
  private readonly store: IndexedDbDocumentDraftStore;
  private editorId: string;
  private listeners = new Set<DocumentListener>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private maxTimer: ReturnType<typeof setTimeout> | null = null;
  private request: Promise<void> | null = null;
  private restoreTask: Promise<void> | null = null;
  private storageQueue: Promise<unknown> = Promise.resolve();
  private loading: Promise<DocumentSnapshot> | null = null;
  private initialized = false;
  private localLoaded = false;
  private baselineRevision = "";
  private storageVersion = 0;
  private pending: DocumentDraftPending | undefined;
  private closeRequested = false;
  private persistenceError: string | null = null;
  private legacyDraft = false;
  private state: DocumentControllerState;

  constructor(options: DocumentControllerOptions) {
    this.identity = identityFor(options);
    this.fetcher = options.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
    this.store = options.draftStore ?? binaryStore;
    this.editorId = options.editorId ?? crypto.randomUUID();
    this.debounceMs = options.debounceMs ?? 300;
    this.maxDebounceMs = options.maxDebounceMs ?? 2000;
    this.state = { identity: this.identity, snapshot: null, draft: null, generation: 0,
      status: "idle", error: null, restoring: false,
      renaming: [...renames].some((rename) => matchesRename(this.identity, rename)) };
    controllers.set(documentIdentityKey(this.identity), this);
  }

  getState(): DocumentControllerState { return this.state; }
  currentDraft(): Blob | null { return this.state.draft; }
  exportDraft(): Blob | null { return this.state.draft; }
  subscribe(listener: DocumentListener): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.release(listener);
  }
  release(listener?: DocumentListener): void {
    if (listener) this.listeners.delete(listener);
    if (!this.listeners.size && !this.state.draft && !this.pending && !this.request && !this.restoreTask && this.initialized)
      this.evict();
  }
  private evict(): void {
    const key = documentIdentityKey(this.identity);
    if (controllers.get(key) === this) controllers.delete(key);
  }
  private setState(patch: Partial<DocumentControllerState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener(this.state);
  }
  private fail(error: unknown, conflict = false): void {
    this.setState({ status: conflict ? "conflict" : "error", error: errorMessage(error) });
  }
  private clearTimers(): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.maxTimer) clearTimeout(this.maxTimer);
    this.timer = this.maxTimer = null;
  }

  /** Queue current state rather than a stale captured draft. New input cannot
   * be deleted by the completion of an older request or storage transaction. */
  private persistCurrent(): Promise<void> {
    if (this.identity.kind !== "project") return Promise.resolve();
    const identity = this.identity;
    const task = this.storageQueue.then(async () => {
      const key = documentIdentityKey(identity);
      if (!this.state.draft && !this.pending) {
        if (this.storageVersion) await this.store.delete(key, this.storageVersion);
        this.storageVersion = 0;
      } else {
        const record: DocumentDraftRecord = {
          key, projectId: identity.projectId, path: identity.path,
          latestDraft: this.state.draft ?? this.pending!.bytes,
          baselineRevision: this.baselineRevision, generation: this.state.generation,
          editorId: this.editorId, pending: this.pending, updatedAt: Date.now(),
          storageVersion: this.storageVersion,
        };
        this.storageVersion = await this.store.put(record);
      }
      this.persistenceError = null;
    });
    this.storageQueue = task.catch((error) => {
      this.persistenceError = errorMessage(error);
      this.fail(error);
    });
    return task;
  }

  private async loadLocal(): Promise<void> {
    if (this.localLoaded || this.identity.kind !== "project") return;
    const record = await this.store.get(documentIdentityKey(this.identity));
    if (record) {
      if (!revisionValid(record.baselineRevision) || !(record.latestDraft instanceof Blob))
        throw new Error("The local document draft is unreadable. It has been retained.");
      this.baselineRevision = record.baselineRevision;
      this.editorId = record.editorId;
      this.storageVersion = record.storageVersion ?? 0;
      this.pending = record.pending;
      this.setState({ draft: record.latestDraft, generation: record.generation, status: "dirty" });
    } else {
      const legacy = await loadFileDraft(this.identity.projectId, this.identity.path);
      if (legacy) {
        // Older drafts retain the original text even when they lack a digest.
        // Hash that original text, never the current disk contents.
        const digest = legacy.baselineRevision ? null : await crypto.subtle.digest(
          "SHA-256", new TextEncoder().encode(legacy.baselineContent));
        this.baselineRevision = legacy.baselineRevision ?? Array.from(new Uint8Array(digest!),
          (byte) => byte.toString(16).padStart(2, "0")).join("");
        this.legacyDraft = true;
        this.setState({ draft: blobOf(legacy.draft), generation: 1, status: "dirty" });
      }
    }
    this.localLoaded = true;
  }

  async hydrate(input: { bytes: Blob | Uint8Array | string; revision: string; mtime?: number; binary?: boolean }): Promise<void> {
    await this.loadLocal();
    const snapshot = { ...input, bytes: blobOf(input.bytes) };
    if (!this.state.draft && !this.pending) this.baselineRevision = input.revision;
    this.initialized = true;
    this.setState({ snapshot, status: this.state.draft ? "dirty" : "idle", error: null });
    if (this.identity.kind === "project" && !revisionValid(this.baselineRevision))
      this.fail(new Error("The original draft revision is unavailable. Export the draft or discard it explicitly."), true);
  }

  async readDisk(): Promise<DocumentSnapshot> {
    const identity = this.identity;
    const params = identity.kind === "project"
      ? new URLSearchParams({ project_id: identity.projectId, path: identity.path })
      : new URLSearchParams({ session_id: identity.sessionId, path: identity.path });
    const response = await this.fetcher(`${identity.kind === "project" ? "/api/documents/content" : "/api/file-read"}?${params}`);
    if (!response.ok) throw new Error(`Unable to read document (${response.status}).`);
    if (identity.kind === "attachment") {
      const value = await response.json() as { content?: string; binary?: boolean };
      return { bytes: new Blob([value.content ?? ""]), revision: "", binary: value.binary };
    }
    const revision = response.headers.get("x-document-revision");
    if (!revisionValid(revision)) throw new Error("The document revision is missing or invalid.");
    const bytes = await response.blob();
    // A text suffix does not make arbitrary bytes safely editable. Decode
    // strictly before allowing an editor to rewrite their encoding.
    let binary = false;
    try {
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(await bytes.arrayBuffer());
      binary = decoded.includes("\0");
    } catch { binary = true; }
    return { bytes, revision, binary };
  }

  load(): Promise<DocumentSnapshot> {
    if (this.initialized && this.state.snapshot) return Promise.resolve(this.state.snapshot);
    if (this.loading) return this.loading;
    if (this.state.renaming) return Promise.reject(new Error("This document is being renamed."));
    const task = (async () => {
      await this.loadLocal();
      const snapshot = await this.readDisk();
      await this.hydrate(snapshot);
      return snapshot;
    })();
    this.loading = task.catch((error) => { this.fail(error); throw error; }).finally(() => { this.loading = null; });
    return this.loading;
  }

  update(value: Blob | Uint8Array | string): void {
    if (this.identity.kind !== "project" || this.state.status === "closed" || this.state.restoring || this.state.renaming) return;
    if (!this.initialized) { this.fail(new Error("Wait for the document to finish loading before editing.")); return; }
    const draft = blobOf(value);
    this.setState({ draft, generation: this.state.generation + 1, status: "dirty", error: null });
    void this.persistCurrent().catch(() => undefined);
    const save = () => { void this.drain(false).catch(() => undefined); };
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; save(); }, this.debounceMs);
    if (!this.maxTimer) this.maxTimer = setTimeout(() => { this.maxTimer = null; save(); }, this.maxDebounceMs);
  }

  private drain(retry: boolean): Promise<void> {
    if (this.request) return this.request;
    // Install the serial request before digest, persistence, or network awaits.
    const task = Promise.resolve().then(async () => {
      if (this.state.status === "closed") return;
      if (!retry && (this.state.status === "error" || this.state.status === "conflict")) return;
      if (!this.pending && !this.state.draft) { await this.persistCurrent(); return; }
      if (!revisionValid(this.baselineRevision) && this.identity.kind === "project")
        throw new Error("The original document revision is unavailable; the draft was retained.");
      await this.persistCurrent();
      while (this.pending || this.state.draft) {
        if (!this.pending) this.pending = {
          kind: "content", bytes: this.state.draft!, baseline: this.baselineRevision,
          key: crypto.randomUUID(), editor: this.editorId, close: this.closeRequested,
          generation: this.state.generation,
        };
        await this.persistCurrent();
        const operation = this.pending;
        this.setState({ status: "saving", error: null });
        const result = await this.send(operation);
        this.baselineRevision = result.revision;
        this.pending = undefined;
        const newer = this.state.generation !== operation.generation;
        this.setState({ snapshot: { bytes: operation.bytes, revision: result.revision, mtime: result.mtime },
          draft: newer ? this.state.draft : null, status: newer ? "dirty" : "idle", error: null });
        if (this.identity.kind === "project") invalidateFileRead(this.identity.projectId, this.identity.path);
        await this.persistCurrent();
        if (this.legacyDraft && this.identity.kind === "project") {
          const discarded = await discardFileDraft(this.identity.projectId, this.identity.path);
          if (!discarded.ok) throw new Error(discarded.message ?? "Unable to clear the migrated draft.");
          this.legacyDraft = false;
        }
      }
    }).catch((error) => {
      if (this.state.status !== "conflict") this.fail(error);
      throw error;
    });
    this.request = task.finally(() => { this.request = null; this.release(); });
    return this.request;
  }

  private async send(operation: DocumentDraftPending): Promise<{ revision: string; mtime?: number }> {
    if (this.identity.kind !== "project") throw new Error("This attachment is read-only.");
    const identity = this.identity;
    const url = operation.kind === "restore" ? "/api/documents/history/restore"
      : `/api/documents/content?${new URLSearchParams({ project_id: identity.projectId, path: identity.path })}`;
    const init: RequestInit = operation.kind === "restore" ? {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ project_id: identity.projectId, path: identity.path,
        version: operation.version, side: operation.side, baseline_revision: operation.baseline,
        idempotency_key: operation.key, editor_id: operation.editor }),
    } : {
      method: "PUT", body: operation.bytes,
      headers: { "content-type": operation.bytes.type || "application/octet-stream",
        "x-baseline-revision": operation.baseline, "idempotency-key": operation.key,
        "x-editor-id": operation.editor, "x-history-close": String(operation.close) },
    };
    let response: Response | undefined;
    for (let attempt = 0; attempt < 2; attempt++) {
      try { response = await this.fetcher(url, init); }
      catch (error) { if (attempt === 1) throw error; continue; }
      if (response.status < 500 || attempt === 1) break;
    }
    if (!response?.ok) {
      const error = new Error(response?.status === 409
        ? "The file changed on disk. Your draft was retained."
        : `Document save could not be confirmed (${response?.status ?? "offline"}).`);
      if (response?.status === 409) this.fail(error, true);
      throw error;
    }
    const result = await response.json() as { ok?: boolean; status?: string; revision?: string; mtime?: number };
    if (result.ok !== true || result.status !== "committed" || !revisionValid(result.revision))
      throw new Error("Document save returned an unconfirmed result. The pending operation was retained.");
    return { revision: result.revision, mtime: result.mtime };
  }

  async flush(): Promise<void> {
    if (this.state.renaming) throw new Error("Wait for the document rename to finish.");
    this.clearTimers();
    if (this.loading) {
      try { await this.loading; }
      catch (error) {
        if (!this.localLoaded || this.state.draft || this.pending) throw error;
      }
    }
    if (this.restoreTask) await this.restoreTask;
    if (this.identity.kind === "attachment") return;
    await this.drain(true);
  }
  async discard(): Promise<void> {
    this.clearTimers();
    if (this.restoreTask) await this.restoreTask.catch(() => undefined);
    if (this.request) await this.request.catch(() => undefined);
    await this.storageQueue;
    if (this.identity.kind === "project") {
      await this.store.delete(documentIdentityKey(this.identity), this.storageVersion);
      const legacy = await discardFileDraft(this.identity.projectId, this.identity.path);
      if (this.legacyDraft && !legacy.ok) throw new Error(legacy.message ?? "Unable to discard legacy draft.");
    }
    this.storageVersion = 0;
    this.pending = undefined;
    this.persistenceError = null;
    this.legacyDraft = false;
    this.setState({ draft: null, status: "idle", error: null });
  }
  async discardDraft(): Promise<void> { await this.reloadDisk(); }
  async reloadDisk(): Promise<void> {
    if (this.restoreTask) await this.restoreTask.catch(() => undefined);
    this.setState({ restoring: true });
    this.clearTimers();
    try {
      if (this.request) await this.request.catch(() => undefined);
      const disk = await this.readDisk();
      await this.discard();
      this.baselineRevision = disk.revision;
      this.setState({ snapshot: disk, draft: null, status: "idle", error: null });
    } finally { this.setState({ restoring: false }); }
  }
  async listHistory(limit = 25, cursor?: string): Promise<{ entries: DocumentHistoryEntry[]; next_cursor?: string | null }> {
    if (this.identity.kind !== "project") return { entries: [] };
    const params = new URLSearchParams({ project_id: this.identity.projectId, path: this.identity.path, limit: String(limit) });
    if (cursor) params.set("cursor", cursor);
    const response = await this.fetcher(`/api/documents/history?${params}`);
    if (!response.ok) throw new Error("Unable to load document history.");
    return response.json();
  }
  async historyContent(version: string, side: "before" | "after" = "after"): Promise<Blob> {
    if (this.identity.kind !== "project") throw new Error("History is unavailable for this attachment.");
    const params = new URLSearchParams({ project_id: this.identity.projectId, path: this.identity.path, version, side });
    const response = await this.fetcher(`/api/documents/history/content?${params}`);
    if (!response.ok) throw new Error("Unable to read this history version.");
    return response.blob();
  }
  restore(version: string, side: "before" | "after" = "after"): Promise<void> {
    if (this.state.renaming) return Promise.reject(new Error("Wait for the document rename to finish."));
    if (this.restoreTask) return this.restoreTask;
    this.setState({ restoring: true });
    const task = Promise.resolve().then(async () => {
      await this.drain(true);
      const bytes = await this.historyContent(version, side);
      this.pending = { kind: "restore", bytes, baseline: this.baselineRevision,
        key: crypto.randomUUID(), editor: this.editorId, close: true,
        generation: this.state.generation, version, side };
      await this.persistCurrent();
      await this.drain(true);
    }).catch((error) => { this.fail(error, this.state.status === "conflict"); throw error; });
    this.restoreTask = task.finally(() => { this.restoreTask = null; this.setState({ restoring: false }); });
    return this.restoreTask;
  }
  async prepareRename(): Promise<void> {
    this.setState({ renaming: true });
    // Finish reads started before the rename barrier so preflight sees any
    // recovered draft. New controllers cannot begin reads through the barrier.
    if (this.loading) await this.loading.catch(() => undefined);
  }
  finishRename(succeeded: boolean): void {
    if (succeeded && !this.state.draft && !this.pending) {
      this.clearTimers();
      this.setState({ status: "closed" });
      this.evict();
    } else this.setState({ renaming: false });
  }
  async close(): Promise<void> {
    this.closeRequested = true;
    try { await this.flush(); }
    catch (error) { this.closeRequested = false; throw error; }
    this.setState({ status: "closed" });
    this.evict();
    this.listeners.clear();
  }
}
/** Freeze affected editors for the entire structured server rename. The
 * caller releases this barrier in finally, including compensation failures. */
export async function beginDocumentRename(projectId: string, oldPath: string, newPath: string): Promise<(succeeded: boolean) => void> {
  const rename = { projectId, paths: [oldPath, newPath] };
  renames.add(rename);
  await Promise.all([...controllers.values()].filter((controller) => matchesRename(controller.identity, rename))
    .map((controller) => controller.prepareRename()));
  return (succeeded) => {
    renames.delete(rename);
    for (const controller of [...controllers.values()])
      if (matchesRename(controller.identity, rename)) controller.finishRename(succeeded);
  };
}
export function getOrCreateDocumentController(options: DocumentControllerOptions): DocumentController {
  const identity = identityFor(options);
  return controllers.get(documentIdentityKey(identity)) ?? new DocumentController(options);
}
export function lookupDocumentController(identity: DocumentIdentity): DocumentController | null {
  return controllers.get(documentIdentityKey(identity)) ?? null;
}
export async function closeDocumentController(identity: DocumentIdentity): Promise<void> {
  await lookupDocumentController(identity)?.close();
}
export { controllers as documentControllers };
