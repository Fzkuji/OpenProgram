"use client";

/**
 * What Activity last read of each conversation, kept for the life of the page.
 *
 * Activity reads four things of a conversation: its executions, its
 * programs and calls, the helpers' roster and its own work. Opening the tab
 * or coming back to a conversation draws the last read at once and reads
 * again behind it, so a refresh replaces rows in place instead of starting
 * from an empty, folded list.
 *
 * A read is fresh while no execution or Job has moved since it was taken,
 * the socket that reports those moves has stayed open, and it is less than
 * `FRESH_MS` old. Coming back to a fresh read does not read again.
 */
import { useSessionStore } from "@/lib/session-store";

export type ActivityReadKind = "executions" | "processes" | "roster" | "work";

const FRESH_MS = 30000;
/** Conversations whose reads are kept; the least recently read go first. */
const KEPT = 24;

const reads = new Map<string, { value: unknown; at: number; moves: number }>();
/** How many times an execution or Job has moved, or the socket changed state. */
let moves = 0;
const changed = () => { moves += 1; };

if (typeof window !== "undefined") {
  window.addEventListener("op:execution-update", changed);
  window.addEventListener("op:job-status", changed);
  useSessionStore.subscribe((state, previous) => {
    if (state.wsStatus !== previous.wsStatus) changed();
  });
}

const keyOf = (kind: ActivityReadKind, sessionId: string) => `${kind}\u0000${sessionId}`;

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
  while (reads.size > KEPT * 4) reads.delete(reads.keys().next().value as string);
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
  return read.moves === moves
    && Date.now() - read.at < FRESH_MS
    && useSessionStore.getState().wsStatus === "open";
}

/** How long a fresh read stays fresh; the next periodic read waits that long. */
export function activityReadFreshFor(kind: ActivityReadKind, sessionId: string): number {
  const read = reads.get(keyOf(kind, sessionId));
  return read ? Math.max(0, FRESH_MS - (Date.now() - read.at)) : 0;
}

/** Forget every read (tests). */
export function forgetActivityReads(): void {
  reads.clear();
}
