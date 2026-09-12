import { idempotencyKeyFor } from "@/lib/net/ws-request";
import type { DocumentControllerOptions, DocumentHistoryEntry, DocumentIdentity, DocumentSnapshot } from "./document-types";

export type DocumentStatus = "idle" | "dirty" | "saving" | "error" | "conflict" | "closed";
export interface DocumentControllerState { identity: DocumentIdentity; snapshot: DocumentSnapshot | null; draft: Blob | null; generation: number; status: DocumentStatus; error: string | null; }
export type DocumentListener = (state: DocumentControllerState) => void;
const binaryDrafts = new Map<string, Blob>();
const controllers = new Map<string, DocumentController>();
let binaryDb: Promise<IDBDatabase> | null = null;
function openBinaryDb(): Promise<IDBDatabase> { if (binaryDb) return binaryDb; binaryDb = new Promise((resolve, reject) => { const request = indexedDB.open("openprogram-document-blobs", 1); request.onupgradeneeded = () => { if (!request.result.objectStoreNames.contains("drafts")) request.result.createObjectStore("drafts"); }; request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); }); return binaryDb; }
type BinaryRecord = { blob: Blob; baselineRevision: string; operationKey?: string; generation: number };
async function persistBinary(key: string, value: BinaryRecord) { if (typeof indexedDB === "undefined") return; const db = await openBinaryDb(); await new Promise<void>((resolve, reject) => { const tx = db.transaction("drafts", "readwrite"); tx.objectStore("drafts").put(value, key); tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); }); }
async function removeBinary(key: string) { if (typeof indexedDB === "undefined") return; const db = await openBinaryDb(); await new Promise<void>((resolve, reject) => { const tx = db.transaction("drafts", "readwrite"); tx.objectStore("drafts").delete(key); tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); }); }
async function loadBinary(key: string): Promise<BinaryRecord | null> { if (typeof indexedDB === "undefined") return null; const db = await openBinaryDb(); return new Promise((resolve, reject) => { const request = db.transaction("drafts", "readonly").objectStore("drafts").get(key); request.onsuccess = () => resolve(request.result?.blob instanceof Blob ? request.result : null); request.onerror = () => reject(request.error); }); }
const encoder = new TextEncoder();
const blobOf = (value: Blob | Uint8Array | string): Blob => value instanceof Blob ? value : new Blob([typeof value === "string" ? value : new Uint8Array(value)]);
const identityKey = (i: DocumentIdentity) => i.kind === "project" ? `project:${i.projectId}:${i.path}` : `attachment:${i.sessionId}:${i.path}`;
async function bytesDigest(value: Blob): Promise<string> { const raw = await value.arrayBuffer(); if (typeof crypto !== "undefined" && crypto.subtle) { const digest = await crypto.subtle.digest("SHA-256", raw); return [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2, "0")).join(""); } return `${value.size}:${value.type}`; }

export class DocumentController {
  readonly identity: DocumentIdentity;
  private readonly fetcher: typeof fetch;
  private readonly debounceMs: number;
  private readonly maxDebounceMs: number;
  private readonly editorId: string;
  private listeners = new Set<DocumentListener>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private maxTimer: ReturnType<typeof setTimeout> | null = null;
  private request: Promise<void> | null = null;
  private closed = false;
  private baseline: DocumentSnapshot | null = null;
  private generation = 0;
  private state: DocumentControllerState;
  private draftPersist: Promise<void> = Promise.resolve();
  constructor(options: DocumentControllerOptions) {
    this.identity = options.readOnly ? { kind: "attachment", sessionId: options.sessionId ?? "", path: options.path, readOnly: true } : { kind: "project", projectId: options.projectId ?? "", path: options.path };
    this.fetcher = options.fetchImpl ?? fetch;
    this.editorId = options.editorId ?? "manual";
    this.debounceMs = options.debounceMs ?? 300;
    this.maxDebounceMs = options.maxDebounceMs ?? 2000;
    this.state = { identity: this.identity, snapshot: null, draft: null, generation: 0, status: "idle", error: null };
    controllers.set(identityKey(this.identity), this);
  }
  getState() { return this.state; }
  subscribe(listener: DocumentListener) { this.listeners.add(listener); listener(this.state); return () => this.listeners.delete(listener); }
  private setState(patch: Partial<DocumentControllerState>) { this.state = { ...this.state, ...patch }; for (const listener of this.listeners) listener(this.state); }
  async hydrate(input: { bytes: Blob | Uint8Array | string; revision: string; mtime?: number; binary?: boolean }) { const snapshot = { ...input, bytes: blobOf(input.bytes) }; this.baseline = snapshot; const key = identityKey(this.identity); let draft = binaryDrafts.get(key) ?? null; try { const record = await loadBinary(key); if (record) { draft = record.blob; binaryDrafts.set(key, draft); this.baseline = { ...snapshot, revision: record.baselineRevision }; } } catch { this.setState({ error: "Unable to load the local document draft." }); } this.setState({ snapshot: this.baseline, draft, status: draft ? "dirty" : "idle", error: this.state.error }); }
  async load() {
    const identity = this.identity;
    const url = identity.kind === "project" ? `/api/documents/content?project_id=${encodeURIComponent(identity.projectId)}&path=${encodeURIComponent(identity.path)}` : `/api/file-read?path=${encodeURIComponent(identity.path)}&session_id=${encodeURIComponent(identity.sessionId)}`;
    const response = await this.fetcher(url); if (!response.ok) throw new Error(`Unable to read document (${response.status})`);
    if (identity.kind === "attachment") { const value = await response.json() as { content?: string }; const snapshot = { bytes: value.content ?? "", revision: "", mtime: 0 }; await this.hydrate(snapshot); return { ...snapshot, bytes: blobOf(snapshot.bytes) }; }
    const snapshot = { bytes: await response.blob(), revision: response.headers.get("x-document-revision") ?? "", mtime: Number(response.headers.get("x-document-mtime") ?? 0) || undefined }; await this.hydrate(snapshot); return snapshot;
  }
  update(value: Blob | Uint8Array | string) {
    if (this.closed || this.identity.kind === "attachment") return;
    const draft = blobOf(value); this.generation += 1; const key = identityKey(this.identity); binaryDrafts.set(key, draft); this.draftPersist = persistBinary(key, { blob: draft, baselineRevision: this.baseline?.revision ?? "", generation: this.generation }).catch(() => { this.setState({ status: "error", error: "Unable to persist the local document draft." }); }); this.setState({ draft, generation: this.generation, status: "dirty", error: null });
    if (this.timer) clearTimeout(this.timer); this.timer = setTimeout(() => { this.timer = null; void this.publish(); }, this.debounceMs);
    if (!this.maxTimer) this.maxTimer = setTimeout(() => { this.maxTimer = null; void this.publish(); }, this.maxDebounceMs);
  }
  async flush() { if (this.timer) clearTimeout(this.timer); this.timer = null; if (this.maxTimer) clearTimeout(this.maxTimer); this.maxTimer = null; do { await this.publish(); } while (this.request || (this.state.status === "dirty" && !this.closed)); if (this.state.status === "error" || this.state.status === "conflict") throw new Error(this.state.error ?? "Document save failed."); }
  private async publish() {
    if (this.closed || this.identity.kind !== "project" || !this.state.draft || !this.baseline) return;
    if (this.request) { await this.request; return; }
    const submitted = this.state.draft; const generation = this.generation; const baseline = this.baseline;
    const identity = this.identity;
    let key: string; try { key = idempotencyKeyFor("document_content_put", { project_id: this.identity.projectId, path: this.identity.path, revision: baseline.revision, generation, bytes_digest: await bytesDigest(submitted) }); } catch { this.setState({ status: "error", error: "Unable to create a document operation." }); return; }
    await this.draftPersist; if (this.state.status === "error") return; this.setState({ status: "saving", error: null });
    this.request = (async () => {
      const url = `/api/documents/content?project_id=${encodeURIComponent(identity.projectId)}&path=${encodeURIComponent(identity.path)}`;
      const init: RequestInit = { method: "PUT", body: submitted, headers: { "content-type": submitted.type || "application/octet-stream", "x-baseline-revision": baseline.revision, "idempotency-key": key, "x-editor-id": this.editorId } };
      let response: Response | null = null; let error: unknown;
      for (let attempt = 0; attempt < 2; attempt += 1) { try { response = await this.fetcher(url, init); if (response.status < 500 || attempt === 1) break; } catch (e) { error = e; if (attempt === 1) throw e; } }
      if (!response) throw error ?? new Error("document request failed");
      if (response.status === 409) { this.setState({ status: "conflict", error: "The file changed on disk. Export the draft before resolving the conflict." }); return; }
      if (!response.ok) throw new Error(`Document save failed (${response.status})`);
      const result = await response.json() as { revision?: string; mtime?: number };
      const next = { bytes: submitted, revision: result.revision ?? baseline.revision, mtime: result.mtime }; this.baseline = next;
      if (generation === this.generation) { binaryDrafts.delete(identityKey(this.identity)); void removeBinary(identityKey(this.identity)); this.setState({ snapshot: next, draft: null, status: "idle", error: null }); } else this.setState({ status: "dirty" });
    })().catch((e) => { if (this.state.status !== "conflict") this.setState({ status: "error", error: e instanceof Error ? e.message : "Document save failed." }); }).finally(() => { this.request = null; });
    await this.request;
  }
  async listHistory(limit = 25, cursor?: string): Promise<{ entries: DocumentHistoryEntry[]; next_cursor?: string | null }> { if (this.identity.kind !== "project") return { entries: [] }; const p = new URLSearchParams({ project_id: this.identity.projectId, path: this.identity.path, limit: String(limit) }); if (cursor) p.set("cursor", cursor); const r = await this.fetcher(`/api/documents/history?${p}`); if (!r.ok) throw new Error("Unable to load document history."); return r.json(); }
  async historyContent(version: string, side: "before" | "after" = "after") { if (this.identity.kind !== "project") throw new Error("History is unavailable for attachments."); const p = new URLSearchParams({ project_id: this.identity.projectId, path: this.identity.path, version, side }); const r = await this.fetcher(`/api/documents/history/content?${p}`); if (!r.ok) throw new Error("Unable to read document history."); return r.blob(); }
  async restore(version: string, side: "before" | "after" = "after") { if (this.identity.kind !== "project" || !this.baseline) return; await this.flush(); const identity = this.identity; const key = idempotencyKeyFor("document_history_restore", { project_id: identity.projectId, path: identity.path, version, side, baseline_revision: this.baseline.revision }); const r = await this.fetcher("/api/documents/history/restore", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: identity.projectId, path: identity.path, version, side, baseline_revision: this.baseline.revision, idempotency_key: key }) }); if (!r.ok) throw new Error("Unable to restore document history."); await this.load(); }
  async close() { await this.flush(); this.closed = true; if (this.timer) clearTimeout(this.timer); if (this.maxTimer) clearTimeout(this.maxTimer); controllers.delete(identityKey(this.identity)); this.setState({ status: "closed" }); this.listeners.clear(); }
}
export { binaryDrafts, controllers as documentControllers };
