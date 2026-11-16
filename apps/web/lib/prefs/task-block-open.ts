/**
 * task-block-open: whether the reader opened or folded a task block or one
 * of its steps. Their own choice wins over what the block does by itself and
 * is kept per reply in this browser, most recently used first out of
 * MAX_ENTRIES. Wherever storage is unavailable it holds for the page.
 */
import { useCallback, useSyncExternalStore } from "react";

const KEY = "op.taskBlockOpen.v1";
const EVENT = "openprogram:task-block-open";
const MAX_ENTRIES = 500;

let cached: Map<string, boolean> | null = null;

function read(): Map<string, boolean> {
  if (cached) return cached;
  cached = new Map();
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
    const saved: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(saved)) {
      for (const pair of saved) {
        if (Array.isArray(pair) && typeof pair[0] === "string") cached.set(pair[0], pair[1] === 1);
      }
    }
  } catch { /* an unreadable store starts empty */ }
  return cached;
}

/** The key of a task block's own choice. */
export function taskOpenKey(taskId: string): string {
  return `task:${taskId}`;
}

/** The key of one step's choice inside a task block. */
export function stepOpenKey(taskId: string, stepId: string): string {
  return `task:${taskId}:step:${stepId}`;
}

const full = (rowId: string, key: string) => `${rowId}\u0000${key}`;

export function getTaskOpen(rowId: string, key: string): boolean | null {
  return read().get(full(rowId, key)) ?? null;
}

export function setTaskOpen(rowId: string, key: string, open: boolean): void {
  const entries = read();
  const id = full(rowId, key);
  entries.delete(id);
  entries.set(id, open);
  while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value as string);
  try {
    localStorage.setItem(KEY, JSON.stringify([...entries].map(([name, value]) => [name, value ? 1 : 0])));
  } catch { /* the choice still holds for this page */ }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT));
}

/** Read the stored choices again (another tab wrote them, or a test did). */
export function reloadTaskOpen(): void {
  cached = null;
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY) { cached = null; onChange(); }
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** The reader's choice for one block or step of a reply, null until made. */
export function useTaskOpen(rowId: string, key: string): [boolean | null, (open: boolean) => void] {
  const chosen = useSyncExternalStore(subscribe, () => getTaskOpen(rowId, key), () => null);
  const set = useCallback((open: boolean) => setTaskOpen(rowId, key, open), [rowId, key]);
  return [chosen, set];
}
