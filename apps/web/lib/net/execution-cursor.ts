export type ExecutionCursor = {
  execution_id: string;
  next_sequence: number;
  snapshot_status_version: number;
};

const storageKey = "openprogram.execution-cursors.v1";
const cursors = new Map<string, ExecutionCursor>();

function persist(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(storageKey, JSON.stringify(Array.from(cursors.values())));
}

export function loadExecutionCursors(): ExecutionCursor[] {
  if (typeof window === "undefined" || cursors.size) return Array.from(cursors.values());
  try {
    const raw = JSON.parse(window.sessionStorage.getItem(storageKey) ?? "[]");
    if (Array.isArray(raw)) {
      for (const cursor of raw) {
        if (
          cursor && typeof cursor.execution_id === "string"
          && Number.isSafeInteger(cursor.next_sequence) && cursor.next_sequence > 0
          && Number.isSafeInteger(cursor.snapshot_status_version)
        ) cursors.set(cursor.execution_id, cursor);
      }
    }
  } catch { /* discard malformed browser state */ }
  return Array.from(cursors.values());
}

export function recordExecutionCursor(value: unknown): {
  cursor?: ExecutionCursor;
  replayAfter?: number;
} {
  if (!value || typeof value !== "object") return {};
  const raw = value as Partial<ExecutionCursor>;
  if (
    typeof raw.execution_id !== "string" || !raw.execution_id
    || !Number.isSafeInteger(raw.next_sequence) || raw.next_sequence < 1
    || !Number.isSafeInteger(raw.snapshot_status_version)
  ) return {};
  const cursor: ExecutionCursor = {
    execution_id: raw.execution_id,
    next_sequence: raw.next_sequence,
    snapshot_status_version: raw.snapshot_status_version,
  };
  const previous = cursors.get(cursor.execution_id);
  cursors.set(cursor.execution_id, cursor);
  persist();
  // A live cursor that skips local history must be replayed before its frame
  // is allowed to advance the reducer.  A snapshot/replay response replaces
  // state and therefore calls this after recovery, with no local gap.
  return previous && cursor.next_sequence > previous.next_sequence + 1
    ? { cursor, replayAfter: previous.next_sequence - 1 }
    : { cursor };
}
