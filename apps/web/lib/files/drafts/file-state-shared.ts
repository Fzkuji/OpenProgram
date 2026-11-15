/** Pure project-file identity, limits, and durable-draft error state. */

export interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
}

const FILE_OWNER_KEYS = [
  "project_id", "session_id", "assistant_msg_id", "path", "snapshot_id",
] as const;

export function fileResponseMatchesOwner(
  data: Record<string, unknown>,
  payload: Record<string, unknown>,
): boolean {
  const staleOrError = data.status === "stale"
    || data.status === "error"
    || typeof data.error_code === "string";
  return FILE_OWNER_KEYS.every((key) => {
    if (payload[key] === undefined) return true;
    if (key === "snapshot_id" && staleOrError && data[key] == null) return true;
    return data[key] === payload[key];
  });
}

export const draftPersistenceErrors = new Map<string, string>();
const draftErrorListeners = new Set<() => void>();

export function notifyDraftErrorListeners(): void {
  for (const listener of draftErrorListeners) listener();
}

export function getDraftPersistenceError(scope: string): string | null {
  return draftPersistenceErrors.get(scope) ?? null;
}

export function subscribeDraftPersistenceErrors(listener: () => void): () => void {
  draftErrorListeners.add(listener);
  return () => draftErrorListeners.delete(listener);
}

export function reportDraftPersistenceError(scope: string, message: string): void {
  draftPersistenceErrors.set(scope, message);
  notifyDraftErrorListeners();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("project-draft-error", {
      detail: { scope, message },
    }));
  }
}

export const DRAFT_MAX_ENTRIES = 32;
export const DRAFT_MAX_BYTES = 8 * 1024 * 1024;

export function fileScopeKey(projectId: string, path: string): string {
  return `${projectId}:${path}`;
}

export type FileReadResult = {
  project_id: string;
  path: string;
  content?: string;
  size: number;
  mtime: number;
  revision?: string;
  truncated?: boolean;
  binary?: boolean;
  too_large?: boolean;
  error?: string;
};
