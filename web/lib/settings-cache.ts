/**
 * Tiny TTL cache + parallel prefetch for the /settings/* subpages.
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
 * 2. ``prefetchSettings()`` fires the union of all 4 tabs' fetches
 *    in parallel. SettingsTabsLayout calls this on mount, so by the
 *    time the user clicks a sibling tab the data is usually already
 *    sitting in the cache.
 *
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

/**
 * Fire the union of all 4 settings tabs' fetches in parallel. Safe to
 * call repeatedly — ``cachedFetch`` dedupes in-flight requests, so the
 * second call within TTL is a no-op.
 *
 * Channels per-account status probes (~N requests) are skipped here —
 * the channels tab refreshes them on its own 30s interval and we
 * don't know the account list at prefetch time.
 */
export function prefetchSettings(): void {
  // Fire-and-forget. Failures are swallowed; the actual page will
  // surface its own error state when the user navigates.
  void cachedFetch("/api/providers/list").catch(() => {});
  void cachedFetch("/api/search-providers/list").catch(() => {});
  void cachedFetch("/api/channels/accounts").catch(() => {});
  void cachedFetch("/api/channels/bindings").catch(() => {});
}
