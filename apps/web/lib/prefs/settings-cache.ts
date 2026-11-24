/**
 * The last answer of every Settings read, kept for the whole visit.
 *
 * A Settings page shows what it read last time the moment it mounts. The
 * answer stays fresh until the Worker says what it was read from changed
 * (`changed` frames, lib/net/ws/changes.ts: `settings` for config.json,
 * `routing` for providers, credentials and usage, `channels` for channel
 * accounts) or the socket reconnects; then a page showing it reads again at
 * once, keeping the old answer on screen, and any other reads it the next
 * time it is asked for. Its age does not matter. Settings opening reads
 * every page ahead (`prefetchSettings`), so even a first visit usually
 * finds its answer here.
 *
 * - Two callers asking at the same moment share one request.
 * - A read that fails keeps the last answer, is logged, and is tried again
 *   quietly (1 s, 2 s, 4 s … up to 30 s) while a page still shows it.
 *   Nothing is ever put on screen about it.
 * - A change (toggle a provider, add a channel, save a key) calls
 *   `invalidate(url)` or `refreshSettings(url)`: the next read goes to the
 *   network, and the page keeps showing the old answer until the new one
 *   arrives.
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { onChanged } from "@/lib/net/ws/change-bus";

const RETRY_FIRST_MS = 1_000;
const RETRY_MAX_MS = 30_000;

type Entry = {
  data?: unknown;
  /** When `data` arrived; 0 once invalidated. */
  ts: number;
  inflight?: Promise<unknown>;
  /** Bumped by `invalidate`: an answer to an older request is not fresh. */
  generation: number;
  failures: number;
  retry?: ReturnType<typeof setTimeout>;
  listeners: Set<() => void>;
  /** A change arrived while the window was hidden: read once it shows. */
  held?: boolean;
};

const cache = new Map<string, Entry>();

/** The Worker topics whose change makes the answer for `url` old. Every
 *  answer depends on the config (`settings`); a reconnect makes them all old. */
export function settingsTopicsOf(url: string): string[] {
  const path = url.split("?")[0];
  if (path.startsWith("/api/providers") || path.startsWith("/api/usage")) return ["settings", "routing"];
  if (path.startsWith("/api/channels")) return ["settings", "channels"];
  return ["settings"];
}

const followed = new Set<string>();

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/** Shown answers made old in this turn of the event loop: read once each,
 *  however many topics said so (a reconnect names them all). */
const rereads = new Set<string>();

/** `topic` changed: every answer read from it is old, and a page showing
 *  one reads it again (once the window shows). */
function topicChanged(topic: string): void {
  for (const [url, entry] of cache) {
    if (!settingsTopicsOf(url).includes(topic)) continue;
    entry.ts = 0;
    entry.generation += 1;
    if (!entry.listeners.size) continue;
    if (hidden()) { entry.held = true; continue; }
    if (!rereads.size) queueMicrotask(reread);
    rereads.add(url);
  }
}

function reread(): void {
  const urls = [...rereads];
  rereads.clear();
  for (const url of urls) {
    const entry = cache.get(url);
    if (entry) entry.inflight = undefined;
    void load(url).catch(() => {});
  }
}

function readHeld(): void {
  if (hidden()) return;
  for (const [url, entry] of cache) {
    if (!entry.held) continue;
    entry.held = false;
    if (entry.listeners.size) void load(url).catch(() => {});
  }
}

function follow(url: string): void {
  for (const topic of settingsTopicsOf(url)) {
    if (followed.has(topic)) continue;
    followed.add(topic);
    onChanged(topic, () => topicChanged(topic));
  }
  if (!followed.has("") && typeof document !== "undefined") {
    followed.add("");
    document.addEventListener("visibilitychange", readHeld);
  }
}

function entryFor(url: string): Entry {
  let entry = cache.get(url);
  if (!entry) {
    entry = { ts: 0, generation: 0, failures: 0, listeners: new Set() };
    cache.set(url, entry);
    follow(url);
  }
  return entry;
}

function notify(entry: Entry): void {
  for (const listener of [...entry.listeners]) listener();
}

async function request(url: string): Promise<unknown> {
  const r = await fetch(url);
  if (!r.ok) {
    // Keep what the server said went wrong, for the console.
    let detail = "";
    try {
      const body = (await r.json()) as { error?: unknown } | null;
      if (typeof body?.error === "string") detail = body.error;
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `${url} → ${r.status}`);
  }
  return r.json();
}

function scheduleRetry(url: string, entry: Entry): void {
  if (entry.retry || entry.listeners.size === 0) return;
  const wait = Math.min(RETRY_FIRST_MS * 2 ** Math.max(0, entry.failures - 1), RETRY_MAX_MS);
  entry.retry = setTimeout(() => {
    entry.retry = undefined;
    if (entry.listeners.size > 0) void load(url).catch(() => {});
  }, wait);
}

/** One request for `url`, shared by everyone asking while it runs. */
function sameAnswer(kept: unknown, fresh: unknown): boolean {
  try {
    return JSON.stringify(kept) === JSON.stringify(fresh);
  } catch {
    return false;
  }
}

function load<T>(url: string): Promise<T> {
  const entry = entryFor(url);
  if (entry.inflight) return entry.inflight as Promise<T>;
  const generation = entry.generation;
  const pending: Promise<unknown> = request(url).then(
    (data) => {
      if (entry.inflight === pending) entry.inflight = undefined;
      entry.failures = 0;
      // Started before a change landed: show it, but read again next time.
      entry.ts = generation === entry.generation ? Date.now() : 0;
      // An answer that reads the same keeps what is drawn: nothing redraws.
      if (entry.data !== undefined && sameAnswer(entry.data, data)) return entry.data;
      entry.data = data;
      notify(entry);
      return data;
    },
    (error: unknown) => {
      if (entry.inflight === pending) entry.inflight = undefined;
      entry.failures += 1;
      console.warn(`Settings read ${url} failed; trying again quietly`, error);
      scheduleRetry(url, entry);
      throw error;
    },
  );
  entry.inflight = pending;
  return pending as Promise<T>;
}

/** Fresh until a change or a reconnect made it old (`ts` back to 0). */
function isFresh(entry: Entry | undefined): boolean {
  return !!entry && entry.data !== undefined && entry.ts !== 0;
}

/** The last answer for `url`, however old; undefined before the first one. */
export function readCachedSettings<T = unknown>(url: string): T | undefined {
  return cache.get(url)?.data as T | undefined;
}

/** A fresh answer for `url`: the kept one while it is fresh, else a new read.
 *  Rejects when that read fails (the last answer stays kept). */
export function cachedFetch<T = unknown>(url: string): Promise<T> {
  const entry = cache.get(url);
  if (entry?.inflight) return entry.inflight as Promise<T>;
  if (isFresh(entry)) return Promise.resolve(entry!.data as T);
  return load<T>(url);
}

/** Read `url` ahead when its kept answer is missing or made old. Never rejects. */
export function prefetchSettings(url: string): void {
  void cachedFetch(url).catch(() => {});
}

/** Read `url` again now, keeping the old answer on screen until the new one
 *  arrives. Resolves true when the new answer arrived; never rejects. */
export function refreshSettings(url: string): Promise<boolean> {
  invalidate(url);
  return load(url).then(() => true, () => false);
}

/** Read `url` again as a page opens, for data that changes outside
 *  OpenProgram: joins a read already on its way (the one Settings opening or
 *  the page's own mount started) instead of sending a second one. Resolves
 *  true when an answer arrived; never rejects. */
export function revalidateSettings(url: string): Promise<boolean> {
  const entry = cache.get(url);
  const pending = entry?.inflight ?? (invalidate(url), load(url));
  return pending.then(() => true, () => false);
}

/** Replace the kept answer for `url` (an answer a change returned, a quota
 *  frame folded in) and redraw every page showing it. */
export function setCachedSettings<T>(url: string, next: T | ((previous: T | undefined) => T | undefined)): void {
  const entry = entryFor(url);
  const value = typeof next === "function"
    ? (next as (previous: T | undefined) => T | undefined)(entry.data as T | undefined)
    : next;
  if (value === undefined || value === entry.data) return;
  entry.data = value;
  entry.ts = Date.now();
  notify(entry);
}

/** The next read of `url` (or every url `url` matches) goes to the network.
 *  The last answer stays shown until then. */
export function invalidate(url: string | RegExp): void {
  const urls = typeof url === "string" ? [url] : [...cache.keys()].filter((key) => url.test(key));
  for (const key of urls) {
    const entry = cache.get(key);
    if (!entry) continue;
    entry.ts = 0;
    entry.generation += 1;
    entry.inflight = undefined;
  }
}

/** Forget every kept answer (tests start from nothing). */
export function invalidateAllSettings(): void {
  for (const entry of cache.values()) clearTimeout(entry.retry);
  cache.clear();
}

/** The kept answer for `url`, redrawn whenever a new one arrives. Mounting
 *  reads when the answer is missing or made old, and a change while it is
 *  mounted reads again; a failed read is retried quietly while the page is
 *  mounted. `null` reads nothing. */
export function useSettingsData<T>(url: string | null): T | undefined {
  const subscribe = useCallback((listener: () => void) => {
    if (!url) return () => {};
    const entry = entryFor(url);
    entry.listeners.add(listener);
    return () => {
      entry.listeners.delete(listener);
      if (entry.listeners.size === 0 && entry.retry) {
        clearTimeout(entry.retry);
        entry.retry = undefined;
      }
    };
  }, [url]);
  const snapshot = useCallback(() => (url ? cache.get(url)?.data as T | undefined : undefined), [url]);
  const data = useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => {
    if (url) prefetchSettings(url);
  }, [url]);
  return data;
}
