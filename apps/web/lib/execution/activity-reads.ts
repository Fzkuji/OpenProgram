"use client";

/**
 * What Activity last read of each conversation, kept for the life of the page.
 *
 * Activity reads four things of a conversation: its executions, its
 * programs and calls, the helpers' roster and its own work; its pages add a
 * branch's or a task's runs, an open program and an open run's record.
 * Opening the tab, a page, or coming back to a conversation draws the last
 * read at once and reads again behind it, so a refresh replaces rows in
 * place instead of starting from an empty, folded list. A read that fails
 * leaves the last one on screen.
 *
 * A read is fresh while no execution or Job has moved since it was taken,
 * the Worker has not said its runs, roster, work or processes changed, and
 * the socket that reports those moves has stayed open; its age does not
 * matter. Coming back to a fresh read does not read again.
 */
import { useSessionStore } from "@/lib/session-store";
import { onChanged } from "@/lib/net/ws/changes";

export type ActivityReadKind = "executions" | "processes" | "program" | "roster" | "work" | "runs" | "task-runs" | "inspection";

/** Reads kept; the least recently read go first. */
const KEPT = 256;

const reads = new Map<string, { value: unknown; at: number; moves: number }>();
/** How many times an execution or Job has moved, or the socket changed state. */
let moves = 0;
const changed = () => { moves += 1; };

if (typeof window !== "undefined") {
  window.addEventListener("op:execution-update", changed);
  window.addEventListener("op:job-status", changed);
  // The Worker's change notices for what Activity reads (runs, topics, the
  // roster, tasks, programs and calls).
  for (const topic of ["runs", "roster", "work", "processes"]) onChanged(topic, changed);
  useSessionStore.subscribe((state, previous) => {
    if (state.wsStatus !== previous.wsStatus) changed();
  });
}

const keyOf = (kind: ActivityReadKind, sessionId: string) => `${kind}\u0000${sessionId}`;

/** The key of one thing read inside a conversation (a branch's runs, a
 *  program), for the `sessionId` argument of the functions below. */
export const activityReadKey = (sessionId: string, ...parts: (string | null | undefined)[]): string =>
  [sessionId, ...parts.map(part => part ?? "")].join("\u0000");

/** Where a read starts: pass it back to `rememberActivityRead` with the
 *  answer, since a move while the request is out may be missing from it. */
export function activityReadStart(): { at: number; moves: number } {
  return { at: Date.now(), moves };
}

/** Keep what was just read of a conversation. */
export function rememberActivityRead<T>(kind: ActivityReadKind, sessionId: string, value: T, start: { at: number; moves: number }): void {
  const key = keyOf(kind, sessionId);
  reads.delete(key);
  reads.set(key, { value, ...start });
  while (reads.size > KEPT) reads.delete(reads.keys().next().value as string);
}

/** The last read of a conversation, or null when there is none. */
export function lastActivityRead<T>(kind: ActivityReadKind, sessionId: string | null): T | null {
  if (!sessionId) return null;
  const read = reads.get(keyOf(kind, sessionId));
  return read ? read.value as T : null;
}

/** True when the last read still says what the server would. */
export function activityReadIsFresh(kind: ActivityReadKind, sessionId: string): boolean {
  const read = reads.get(keyOf(kind, sessionId));
  if (!read) return false;
  return read.moves === moves && useSessionStore.getState().wsStatus === "open";
}

/** Forget every read (tests). */
export function forgetActivityReads(): void {
  reads.clear();
}
