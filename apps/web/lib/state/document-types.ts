import type { IndexedDbDocumentDraftStore } from "./file-draft-store";
export type DocumentIdentity =
  | { kind: "project"; projectId: string; path: string }
  | { kind: "attachment"; sessionId: string; path: string; readOnly: true };

export interface DocumentSnapshot { bytes: Blob; revision: string; mtime?: number; binary?: boolean; }
export interface DocumentHistoryEntry { version_id: string; project_id: string; path: string; editor_id?: string; actor?: string; created_at?: number; status?: string; before_revision?: string; after_revision?: string; }
export interface DocumentControllerOptions { projectId?: string; path: string; sessionId?: string; readOnly?: boolean; editorId?: string; fetchImpl?: typeof fetch; draftStore?: IndexedDbDocumentDraftStore; debounceMs?: number; maxDebounceMs?: number; }

/** The small surface the document owner needs from a rich editor.  The
 * renderer never publishes bytes directly; onSave is installed by the
 * controller owner and is awaited by the native editor. */
export interface RichDocumentEditor {
  save?(targetExt?: string, options?: { commitPendingInput?: boolean }): Promise<unknown>;
  flushPendingSaves(): Promise<void>;
  setReadonly(readonly: boolean): void;
  setInputEnabled?(enabled: boolean): void;
  destroy(): Promise<void> | void;
  getState?: () => { dirty?: boolean; readonly?: boolean; destroyed?: boolean; status?: string };
}
