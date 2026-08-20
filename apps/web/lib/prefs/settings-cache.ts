/**
 * Tiny TTL cache for the /settings/* subpages.
 *
 * Why: each tab's index.tsx fires its own ``fetch()`` on mount, so
 * navigating between /settings/{providers,search,channels,general}
 * re-runs the same handful of endpoints (each adding ~30ms backend +
 * ~200ms RTT). On a cold refresh that's a visible "pause" before the
 * page becomes interactive — multiplied by 4 tabs as the user clicks
 * through them.
 *
 * Strategy:
 * 1. ``cachedFetch(url)`` memoises the **in-flight Promise**, not just
 *    the resolved value. Two callers in the same tick share one
 *    network round-trip. A successful response sticks for ``TTL_MS``
 *    so revisiting a tab inside that window returns instantly.
 * Invalidation is opt-in: callers that mutate (toggle a provider,
 * add a channel, save an API key) call ``invalidate(url)`` for the
 * affected endpoints so the next read goes to the network.
 */

const TTL_MS = 30_000;

type Entry<T = unknown> = {
  data?: T;
  inflight?: Promise<T>;
  ts: number;
};

const cache = new Map<string, Entry>();

export async function cachedFetch<T = unknown>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const now = Date.now();
  const hit = cache.get(url) as Entry<T> | undefined;
  if (hit) {
    if (hit.inflight) return hit.inflight;
    if (hit.data !== undefined && now - hit.ts < TTL_MS) return hit.data;
  }
  const p = (async () => {
    const r = await fetch(url, init);
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    const data = (await r.json()) as T;
    cache.set(url, { data, ts: Date.now() });
    return data;
  })();
  cache.set(url, { inflight: p, ts: now });
  try {
    return await p;
  } catch (err) {
    // Drop the failed promise so the next caller retries.
    cache.delete(url);
    throw err;
  }
}

export function invalidate(url: string | RegExp): void {
  if (typeof url === "string") {
    cache.delete(url);
    return;
  }
  for (const k of Array.from(cache.keys())) if (url.test(k)) cache.delete(k);
}

export function invalidateAllSettings(): void {
  cache.clear();
}
