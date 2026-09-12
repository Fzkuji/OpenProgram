import { idempotencyKeyFor } from "@/lib/net/ws-request";
import { IndexedDbDocumentDraftStore, type DocumentDraftPending, type DocumentDraftRecord } from "./file-draft-store.ts";
import { loadFileDraft } from "./file-drafts.ts";
import type { DocumentControllerOptions, DocumentHistoryEntry, DocumentIdentity, DocumentSnapshot } from "./document-types";

export type DocumentStatus = "idle" | "dirty" | "saving" | "error" | "conflict" | "closed";
export interface DocumentControllerState { identity: DocumentIdentity; snapshot: DocumentSnapshot | null; draft: Blob | null; generation: number; status: DocumentStatus; error: string | null; }
export type DocumentListener = (state: DocumentControllerState) => void;
const controllers = new Map<string, DocumentController>();
const memoryRecords = new Map<string, DocumentDraftRecord>();
const binaryStore = new IndexedDbDocumentDraftStore();
const identityKey = (identity: DocumentIdentity) => identity.kind === "project" ? `project:${identity.projectId}:${identity.path}` : `attachment:${identity.sessionId}:${identity.path}`;
const normalizePath = (path: string) => path.replace(/^\/+/, "");
const blobOf = (value: Blob | Uint8Array | string): Blob => value instanceof Blob ? value.slice(0, value.size, value.type) : new Blob([typeof value === "string" ? value : new Uint8Array(value)]);
const cloneBlob = (value: Blob) => value.slice(0, value.size, value.type);
const hasBrowserIndexedDb = () => typeof indexedDB !== "undefined" && typeof window !== "undefined";
async function getRecord(key: string): Promise<DocumentDraftRecord | null> { return !hasBrowserIndexedDb() ? memoryRecords.get(key) ?? null : binaryStore.get(key); }
async function putRecord(record: DocumentDraftRecord): Promise<void> { if (!hasBrowserIndexedDb()) memoryRecords.set(record.key, record); else await binaryStore.put(record); }
async function deleteRecord(key: string): Promise<void> { if (!hasBrowserIndexedDb()) memoryRecords.delete(key); else await binaryStore.delete(key); }
async function bytesDigest(blob: Blob): Promise<string> { if (typeof crypto === "undefined" || !crypto.subtle) throw new Error("Content digest is unavailable; the document was not saved."); const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()); return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }
function newEditorId(): string { return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `editor-${Date.now()}-${Math.random().toString(36).slice(2)}`; }

export class DocumentController {
  readonly identity: DocumentIdentity;
  private readonly fetcher: typeof fetch;
  private readonly debounceMs: number;
  private readonly maxDebounceMs: number;
  private editorId: string;
  private listeners = new Set<DocumentListener>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private maxTimer: ReturnType<typeof setTimeout> | null = null;
  private request: Promise<void> | null = null;
  private storageQueue: Promise<void> = Promise.resolve();
  private closed = false;
  private baseline: DocumentSnapshot | null = null;
  private generation = 0;
  private hydration: Promise<void> | null = null;
  private closeRequested = false;
  private pendingKind: "content" | "restore" | null = null;
  private state: DocumentControllerState;
  constructor(options: DocumentControllerOptions) {
    const path = normalizePath(options.path);
    this.identity = options.readOnly ? { kind: "attachment", sessionId: options.sessionId ?? "", path, readOnly: true } : { kind: "project", projectId: options.projectId ?? "", path };
    this.fetcher = options.fetchImpl ?? ((input, init) => globalThis.fetch(input, init)); this.debounceMs = options.debounceMs ?? 300; this.maxDebounceMs = options.maxDebounceMs ?? 2000; this.editorId = options.editorId ?? newEditorId();
    this.state = { identity: this.identity, snapshot: null, draft: null, generation: 0, status: "idle", error: null }; controllers.set(identityKey(this.identity), this);
  }
  getState(): DocumentControllerState { return this.state; }
  currentDraft(): Blob | null { return this.state.draft ? cloneBlob(this.state.draft) : null; }
  exportDraft(): Blob | null { return this.currentDraft(); }
  subscribe(listener: DocumentListener): () => void { this.listeners.add(listener); listener(this.state); return () => this.listeners.delete(listener); }
  release(listener?: DocumentListener): void { if (listener) this.listeners.delete(listener); }
  private setState(patch: Partial<DocumentControllerState>): void { this.state = { ...this.state, ...patch }; for (const listener of this.listeners) listener(this.state); }
  private persist(record: DocumentDraftRecord): Promise<void> { const task = this.storageQueue.then(() => putRecord(record)); this.storageQueue = task.catch(() => undefined); return task; }
  private async record(latestDraft: Blob, generation: number, pending?: DocumentDraftPending): Promise<void> { if (this.identity.kind !== "project") return; await this.persist({ key: identityKey(this.identity), projectId: this.identity.projectId, path: this.identity.path, baselineRevision: this.baseline?.revision ?? "", latestDraft: cloneBlob(latestDraft), generation, editorId: this.editorId, pending, updatedAt: Date.now() }); }
  async hydrate(input: { bytes: Blob | Uint8Array | string; revision: string; mtime?: number; binary?: boolean }): Promise<void> {
    const run = this.hydrateInternal(input); this.hydration = run; try { await run; } finally { if (this.hydration === run) this.hydration = null; }
  }
  private async hydrateInternal(input: { bytes: Blob | Uint8Array | string; revision: string; mtime?: number; binary?: boolean }): Promise<void> {
    const hydratedGeneration = this.generation;
    const snapshot = { ...input, bytes: blobOf(input.bytes) }; this.baseline = snapshot; let draft: Blob | null = null; let record: DocumentDraftRecord | null = null;
    try { record = await getRecord(identityKey(this.identity)); } catch { this.setState({ status: "error", error: "Unable to load the local document draft." }); }
    if (record) { this.generation = record.generation; this.editorId = record.editorId; this.pendingKind = record.pending?.kind ?? null; draft = cloneBlob(record.pending?.bytes ?? record.latestDraft); this.baseline = { ...snapshot, revision: record.pending?.baseline ?? record.baselineRevision }; if (record.pending?.kind === "restore") this.setState({ status: "error", error: "A document restore is pending confirmation; retry the restore operation." }); }
    else if (this.identity.kind === "project") { const legacy = await loadFileDraft(this.identity.projectId, this.identity.path); if (legacy) { draft = new Blob([legacy.draft]); this.generation = 1; } }
    if (this.generation === hydratedGeneration) this.setState({ snapshot: this.baseline, draft, generation: this.generation, status: draft ? "dirty" : "idle" });
    else if (this.state.draft) void this.record(this.state.draft, this.generation).catch(() => this.setState({ status: "error", error: "Unable to persist the local document draft." }));
  }
  async load(): Promise<DocumentSnapshot> {
    const i = this.identity; const url = i.kind === "project" ? `/api/documents/content?project_id=${encodeURIComponent(i.projectId)}&path=${encodeURIComponent(i.path)}` : `/api/file-read?path=${encodeURIComponent(i.path)}&session_id=${encodeURIComponent(i.sessionId)}`; const response = await this.fetcher(url); if (!response.ok) throw new Error(`Unable to read document (${response.status})`);
    const snapshot = i.kind === "project" ? { bytes: await response.blob(), revision: response.headers.get("x-document-revision") ?? "", mtime: Number(response.headers.get("x-document-mtime") ?? 0) || undefined } : { bytes: blobOf(((await response.json()) as { content?: string }).content ?? ""), revision: "", mtime: 0 }; await this.hydrate(snapshot); return snapshot;
  }
  update(value: Blob | Uint8Array | string): void {
    if (this.closed || this.identity.kind === "attachment") return; const draft = blobOf(value); this.generation += 1; this.setState({ draft, generation: this.generation, status: "dirty", error: null }); void this.record(draft, this.generation).catch(() => this.setState({ status: "error", error: "Unable to persist the local document draft." }));
    if (this.timer) clearTimeout(this.timer); this.timer = setTimeout(() => { this.timer = null; void this.publish(); }, this.debounceMs); if (!this.maxTimer) this.maxTimer = setTimeout(() => { this.maxTimer = null; void this.publish(); }, this.maxDebounceMs);
  }
  async flush(): Promise<void> { if (this.hydration) await this.hydration; if (this.pendingKind === "restore") throw new Error(this.state.error ?? "A document restore is pending confirmation."); if (this.state.status === "error") this.setState({ status: "dirty", error: null }); if (this.timer) clearTimeout(this.timer); this.timer = null; if (this.maxTimer) clearTimeout(this.maxTimer); this.maxTimer = null; while (this.state.draft && this.state.status !== "conflict" && this.state.status !== "error") { await this.publish(); if (!this.request && this.state.status !== "dirty") break; } if (this.state.status === "error" || this.state.status === "conflict") throw new Error(this.state.error ?? "Document save failed."); }
  private async publish(): Promise<void> { if (this.request) return this.request; const task = this.publishInternal(); this.request = task.finally(() => { this.request = null; }); return this.request; }
  private async publishInternal(): Promise<void> {
    if (this.closed || this.identity.kind !== "project" || !this.state.draft || !this.baseline) return; const submitted = cloneBlob(this.state.draft); const generation = this.generation; const baseline = this.baseline; const i = this.identity; let key: string;
    try { key = idempotencyKeyFor("document_content_put", { project_id: i.projectId, path: i.path, revision: baseline.revision, generation, bytes_digest: await bytesDigest(submitted) }); } catch (error) { this.setState({ status: "error", error: error instanceof Error ? error.message : "Unable to create a document operation." }); return; }
    const pending: DocumentDraftPending = { kind: "content", bytes: submitted, baseline: baseline.revision, key, editor: this.editorId, close: this.closeRequested, generation }; try { await this.record(this.state.draft, this.generation, pending); } catch { this.setState({ status: "error", error: "Unable to persist the pending document operation." }); return; } this.setState({ status: "saving", error: null });
    const url = `/api/documents/content?project_id=${encodeURIComponent(i.projectId)}&path=${encodeURIComponent(i.path)}`; const headers: Record<string, string> = { "content-type": submitted.type || "application/octet-stream", "x-baseline-revision": baseline.revision, "idempotency-key": key, "x-editor-id": this.editorId }; if (this.closeRequested) headers["x-history-close"] = "true"; const init: RequestInit = { method: "PUT", body: submitted, headers }; let response: Response | null = null; let failure: unknown;
    for (let attempt = 0; attempt < 2; attempt += 1) { try { response = await this.fetcher(url, init); if (response.status < 500 || attempt === 1) break; } catch (error) { failure = error; if (attempt === 1) break; } }
    if (!response) { this.setState({ status: "error", error: failure instanceof Error ? failure.message : "Document save could not be confirmed." }); return; } if (response.status === 409) { this.setState({ status: "conflict", error: "The file changed on disk. Export the draft before resolving the conflict." }); return; } if (!response.ok) { this.setState({ status: "error", error: `Document save failed (${response.status}).` }); return; }
    let result: { ok?: boolean; status?: string; revision?: string; mtime?: number; error?: string }; try { result = await response.json(); } catch { this.setState({ status: "error", error: "Document save returned an invalid result; retry the same draft." }); return; } if (result.ok === false || result.status === "error" || result.status === "conflict") { this.setState({ status: "error", error: result.error ?? "Document save was not confirmed." }); return; }
    const next = { bytes: submitted, revision: result.revision ?? baseline.revision, mtime: result.mtime }; this.baseline = next; this.pendingKind = null; try { if (generation === this.generation) { await deleteRecord(identityKey(i)); this.setState({ snapshot: next, draft: null, status: "idle", error: null }); } else { await this.record(this.state.draft!, this.generation); this.setState({ snapshot: next, status: "dirty", error: null }); } } catch { this.setState({ snapshot: next, status: "error", error: "The document was saved, but local draft cleanup failed; retry cleanup." }); }
  }
  async discard(): Promise<void> { if (this.identity.kind === "project") await deleteRecord(identityKey(this.identity)); this.setState({ draft: null, status: "idle", error: null }); }
  async reloadDisk(): Promise<void> { await this.discard(); await this.load(); }
  async listHistory(limit = 25, cursor?: string): Promise<{ entries: DocumentHistoryEntry[]; next_cursor?: string | null }> { if (this.identity.kind !== "project") return { entries: [] }; const p = new URLSearchParams({ project_id: this.identity.projectId, path: this.identity.path, limit: String(limit) }); if (cursor) p.set("cursor", cursor); const response = await this.fetcher(`/api/documents/history?${p}`); if (!response.ok) throw new Error("Unable to load document history."); return response.json(); }
  async historyContent(version: string, side: "before" | "after" = "after"): Promise<Blob> { if (this.identity.kind !== "project") throw new Error("History is unavailable for attachments."); const p = new URLSearchParams({ project_id: this.identity.projectId, path: this.identity.path, version, side }); const response = await this.fetcher(`/api/documents/history/content?${p}`); if (!response.ok) throw new Error("Unable to read document history."); return response.blob(); }
  async restore(version: string, side: "before" | "after" = "after"): Promise<void> { if (this.identity.kind !== "project" || !this.baseline) return; await this.flush(); const generation = this.generation; const i = this.identity; const key = idempotencyKeyFor("document_history_restore", { project_id: i.projectId, path: i.path, version, side, baseline_revision: this.baseline.revision }); const restoreRecord = this.state.draft ?? new Blob(); try { await this.record(restoreRecord, generation, { kind: "restore", bytes: restoreRecord, baseline: this.baseline.revision, key, editor: this.editorId, close: true, generation, version, side }); } catch { this.setState({ status: "error", error: "Unable to persist the pending restore operation." }); return; } const response = await this.fetcher("/api/documents/history/restore", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: i.projectId, path: i.path, version, side, baseline_revision: this.baseline.revision, idempotency_key: key, editor_id: this.editorId }) }); if (!response.ok) { this.setState({ status: response.status === 409 ? "conflict" : "error", error: "Unable to restore document history; the draft was retained." }); return; } const result = await response.json(); if (result.ok !== true || result.status !== "committed") { this.setState({ status: "error", error: result.error ?? "Document restore was not confirmed." }); return; } await deleteRecord(identityKey(i)); if (generation === this.generation) await this.load(); }
  async close(): Promise<void> { this.closeRequested = true; try { await this.flush(); } catch (error) { this.closeRequested = false; throw error; } this.closed = true; if (this.timer) clearTimeout(this.timer); if (this.maxTimer) clearTimeout(this.maxTimer); controllers.delete(identityKey(this.identity)); this.setState({ status: "closed" }); this.listeners.clear(); }
}
export function getOrCreateDocumentController(options: DocumentControllerOptions): DocumentController { const identity: DocumentIdentity = options.readOnly ? { kind: "attachment", sessionId: options.sessionId ?? "", path: normalizePath(options.path), readOnly: true } : { kind: "project", projectId: options.projectId ?? "", path: normalizePath(options.path) }; return controllers.get(identityKey(identity)) ?? new DocumentController({ ...options, path: identity.path }); }
export function lookupDocumentController(identity: DocumentIdentity): DocumentController | null { return controllers.get(identityKey(identity)) ?? null; }
export async function closeDocumentController(identity: DocumentIdentity): Promise<void> { const controller = lookupDocumentController(identity); if (controller) await controller.close(); }
export { controllers as documentControllers, memoryRecords as binaryDrafts };
