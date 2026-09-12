export type DocumentIdentity =
  | { kind: "project"; projectId: string; path: string }
  | { kind: "attachment"; sessionId: string; path: string; readOnly: true };

export interface DocumentSnapshot { bytes: Blob; revision: string; mtime?: number; binary?: boolean; }
export interface DocumentHistoryEntry { version_id: string; project_id: string; path: string; editor_id?: string; actor?: string; created_at?: number; status?: string; before_revision?: string; after_revision?: string; }
export interface DocumentControllerOptions { projectId?: string; path: string; sessionId?: string; readOnly?: boolean; editorId?: string; fetchImpl?: typeof fetch; debounceMs?: number; maxDebounceMs?: number; }
