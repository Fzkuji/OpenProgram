/**
 * Ask the worker which paths name a file the chat can open.
 *
 * Every reply on screen asks about its path-looking strings at once: the
 * questions of one tick are joined into one `/api/file-paths` request per
 * chat (64 paths at most each), and each answer is kept for a while, so
 * scrolling back over a reply asks nothing. A path that is not a file is
 * asked again sooner, because the agent may still make it.
 */

export type FileFetch = (input: string, init?: RequestInit) => Promise<Response>;

const FOUND_TTL = 5 * 60_000;
const MISSING_TTL = 20_000;
const MAX_PER_REQUEST = 64;
const MAX_KEPT = 2000;

interface Answer { value: string | null; at: number }

const answers = new Map<string, Answer>();
const waiting = new Map<string, Promise<string | null>>();
let batch: Map<string, Map<string, (value: string | null) => void>> | null = null;
let fetcher: FileFetch = (input, init) => fetch(input, init);

const key = (sessionId: string, path: string) => `${sessionId}\n${path}`;

function remember(sessionId: string, path: string, value: string | null, now: number) {
  if (answers.size >= MAX_KEPT) answers.delete(answers.keys().next().value!);
  answers.set(key(sessionId, path), { value, at: now });
}

/** The answer already known for a path, or undefined when it must be asked. */
export function knownFilePath(sessionId: string, path: string, now = Date.now()): string | null | undefined {
  const hit = answers.get(key(sessionId, path));
  if (!hit) return undefined;
  if (now - hit.at > (hit.value ? FOUND_TTL : MISSING_TTL)) return undefined;
  return hit.value;
}

async function send(sessionId: string, paths: Map<string, (value: string | null) => void>) {
  const list = [...paths.keys()];
  for (let start = 0; start < list.length; start += MAX_PER_REQUEST) {
    const chunk = list.slice(start, start + MAX_PER_REQUEST);
    const params = new URLSearchParams();
    for (const path of chunk) params.append("path", path);
    if (sessionId) params.set("session_id", sessionId);
    let files: Record<string, string | null> = {};
    try {
      const response = await fetcher(`/api/file-paths?${params}`, { cache: "no-store" });
      if (response.ok) files = ((await response.json()) as { files?: Record<string, string | null> }).files ?? {};
    } catch { /* Unreachable worker: every path stays plain text. */ }
    const now = Date.now();
    for (const path of chunk) {
      const value = typeof files[path] === "string" ? files[path] : null;
      remember(sessionId, path, value, now);
      waiting.delete(key(sessionId, path));
      paths.get(path)!(value);
    }
  }
}

/** The absolute file each path names, or null. Never rejects. */
export function lookupFilePaths(sessionId: string, paths: string[]): Promise<Map<string, string | null>> {
  const result = new Map<string, string | null>();
  const pending: Promise<void>[] = [];
  for (const path of new Set(paths)) {
    const known = knownFilePath(sessionId, path);
    if (known !== undefined) { result.set(path, known); continue; }
    let promise = waiting.get(key(sessionId, path));
    if (!promise) {
      if (!batch) {
        batch = new Map();
        setTimeout(() => {
          const due = batch!;
          batch = null;
          for (const [session, items] of due) void send(session, items);
        }, 0);
      }
      const items = batch.get(sessionId) ?? new Map<string, (value: string | null) => void>();
      batch.set(sessionId, items);
      promise = new Promise<string | null>(resolve => items.set(path, resolve));
      waiting.set(key(sessionId, path), promise);
    }
    pending.push(promise.then(value => { result.set(path, value); }));
  }
  return Promise.all(pending).then(() => result);
}

/** Tests replace the network and start from an empty memory. */
export function resetFilePathLookup(fetch?: FileFetch) {
  answers.clear();
  waiting.clear();
  batch = null;
  fetcher = fetch ?? ((input, init) => globalThis.fetch(input, init));
}
