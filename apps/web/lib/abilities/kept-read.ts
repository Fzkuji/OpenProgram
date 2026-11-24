/**
 * Reads a page shows, kept for the app's lifetime.
 *
 * A page opened again draws the last answer at once and asks again behind
 * it; a read that fails is tried again after a growing pause, with a console
 * warning and nothing on screen. A permanent answer (HTTP 4xx: not found,
 * refused, malformed) is not retried: it is a fact the caller acts on. The sidebar starts a page's first read on
 * hover and at idle (`prefetchKept`), so even a first visit usually finds
 * its answer already here.
 */
import { sharedRead } from "@/lib/net/shared-get";

const kept = new Map<string, unknown>();

/** The last answer the read named `key` gave, if any. */
export function lastKept<T>(key: string): T | undefined {
  return kept.get(key) as T | undefined;
}

/** Remember an answer a page changed locally (an action's reply). */
export function keep<T>(key: string, value: T): void {
  kept.set(key, value);
}

/** Forget every kept answer (tests). */
export function forgetKept(): void {
  kept.clear();
  for (const entry of retries.values()) if (entry.timer) clearTimeout(entry.timer);
  retries.clear();
}

/** One read, shared with callers asking at the same moment; its answer is kept. */
export function readKept<T>(key: string, read: () => Promise<T>): Promise<T> {
  return sharedRead(key, async () => {
    const value = await read();
    kept.set(key, value);
    return value;
  });
}

/** Start a read ahead of the page that shows it. A failure only warns. */
export function prefetchKept<T>(key: string, read: () => Promise<T>): Promise<void> {
  return readKept(key, read).then(
    () => undefined,
    (cause) => { console.warn(`[prefetch] ${key}`, cause); },
  );
}

/**
 * The server's answer is final, not a load problem: an HTTP 4xx other than
 * a timeout (408), too-early (425) or rate limit (429). Retrying it would
 * only ask the same question forever.
 */
export function isPermanent(cause: unknown): boolean {
  const status = (cause as { status?: unknown } | null)?.status;
  return typeof status === "number" && status >= 400 && status < 500
    && status !== 408 && status !== 425 && status !== 429;
}

/** The pause before the next attempt: 0.5 s, doubling, at most 30 s. */
export function retryDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 30_000);
}

function aborted(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

export function isAbort(cause: unknown): boolean {
  return (cause as { name?: unknown } | null)?.name === "AbortError";
}

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(aborted()); return; }
    const timer = setTimeout(() => { signal?.removeEventListener("abort", stop); resolve(); }, ms);
    const stop = () => { clearTimeout(timer); reject(aborted()); };
    signal?.addEventListener("abort", stop, { once: true });
  });
}

const retries = new Map<string, { timer: ReturnType<typeof setTimeout> | null; attempt: number }>();

/**
 * For a store's read that failed: warn, and run it again after
 * `retryDelay` (one pending retry per key). `answered` resets the pause.
 * A permanent answer (`isPermanent`) is not retried.
 */
export function retrySoon(key: string, run: () => void, cause: unknown): void {
  if (isPermanent(cause)) {
    console.warn(`[read] ${key} refused; not trying again`, cause);
    answered(key);
    return;
  }
  console.warn(`[read] ${key} failed; trying again`, cause);
  const entry = retries.get(key) ?? { timer: null, attempt: 0 };
  retries.set(key, entry);
  if (entry.timer) return;
  entry.timer = setTimeout(() => { entry.timer = null; run(); }, retryDelay(entry.attempt++));
}

/** The read named `key` answered: drop its pending retry. */
export function answered(key: string): void {
  const entry = retries.get(key);
  if (entry?.timer) clearTimeout(entry.timer);
  retries.delete(key);
}

/**
 * Read until an answer comes: a failure warns and tries again after
 * `retryDelay`. Rejects with an AbortError once `signal` aborts, and with
 * the cause itself on a permanent answer (`isPermanent`), whose kept answer
 * is forgotten so the next visit does not draw it.
 */
export async function readUntilAnswered<T>(key: string, read: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      const value = await readKept(key, read);
      if (signal?.aborted) throw aborted();
      return value;
    } catch (cause) {
      if (signal?.aborted || isAbort(cause)) throw aborted();
      if (isPermanent(cause)) { kept.delete(key); throw cause; }
      console.warn(`[read] ${key} failed; trying again`, cause);
      await pause(retryDelay(attempt), signal);
    }
  }
}
