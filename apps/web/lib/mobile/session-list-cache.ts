/**
 * The phone app keeps the last chat list it was sent, per computer, and
 * shows it the moment the page starts, before the socket is up; the
 * computer's own list replaces it when it arrives (use-ws.ts). Happy and
 * Paseo open on their cached list the same way, so a cold start shows
 * the chats in a fraction of a second instead of after the connection.
 *
 * The project registry is kept the same way (`cacheProjects`), so the
 * chats are grouped under their projects from the first frame instead of
 * under their folder names until the registry answers.
 *
 * Only in the store app: a browser tab keeps its page and has no cold
 * start of its own. A row's run state is not kept, since a cached
 * "Running" would be wrong by the time it shows.
 */

import { mobileBridge } from "@/lib/mobile/bridge";

const PREFIX = "openprogram.mobile.sessions:";
const DELAY_MS = 1500;

let pending: ReturnType<typeof setTimeout> | undefined;

function key(): string | null {
  const mobile = mobileBridge();
  return mobile ? PREFIX + mobile.computer.name : null;
}

/** Keep the list the computer just sent (written after a pause, since lists come in bursts). */
export function cacheSessionList(rows: readonly object[]): void {
  const at = key();
  if (!at) return;
  clearTimeout(pending);
  pending = setTimeout(() => {
    try {
      const kept = rows.map((row) => ({ ...row, status: undefined }));
      localStorage.setItem(at, JSON.stringify(kept));
    } catch {
      /* storage full or blocked: the next start waits for the computer */
    }
  }, DELAY_MS);
}

/** The list kept from the last run, or null. */
export function cachedSessionList<T>(): T[] | null {
  const at = key();
  if (!at) return null;
  try {
    const raw = localStorage.getItem(at);
    const rows = raw ? (JSON.parse(raw) as unknown) : null;
    return Array.isArray(rows) && rows.length ? (rows as T[]) : null;
  } catch {
    return null;
  }
}

const PROJECTS = "openprogram.mobile.projects:";

/** Keep the project registry the computer just sent. */
export function cacheProjects(projects: readonly object[]): void {
  const mobile = mobileBridge();
  if (!mobile) return;
  try {
    localStorage.setItem(PROJECTS + mobile.computer.name, JSON.stringify(projects));
  } catch {
    /* storage full or blocked: the next start waits for the computer */
  }
}

/** The project registry kept from the last run, or null. */
export function cachedProjects<T>(): T[] | null {
  const mobile = mobileBridge();
  if (!mobile) return null;
  try {
    const raw = localStorage.getItem(PROJECTS + mobile.computer.name);
    const rows = raw ? (JSON.parse(raw) as unknown) : null;
    return Array.isArray(rows) && rows.length ? (rows as T[]) : null;
  } catch {
    return null;
  }
}
