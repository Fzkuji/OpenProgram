/** Measure-then-recycle window for long transcripts.
 *
 *  Scroll restore is a raw pixel `scrollTop` (`chatScrollByKey`). Off-screen
 *  rows may unmount only after their `offsetHeight` is cached, so the
 *  placeholder stack stays close to a fully mounted `scrollHeight`.
 */

// ponytail: fixed 40 / 1600px; raise overscan if scroll-restore holes appear.
export const RECYCLE_MIN_ROWS = 40;
export const OVERSCAN_PX = 1600;
export const LIST_LEAD_PX = 24;

export type WindowNode =
  | { kind: "fold"; id: string }
  | { kind: "row"; id: string };

export type HeightWrite = "first" | "update" | "same";

/** Same chatKey isolation as `chatScrollByKey`; sibling sessionStorage blob. */
export const CHAT_HEIGHTS_STORAGE_KEY = "chatHeightsByKey";
export const HEIGHT_PERSIST_MS = 200;

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

type StoredChat = { w?: number; h: Record<string, number> };

const EMPTY: ReadonlyMap<string, number> = new Map();
const byChat = new Map<string, Map<string, number>>();
const widthByChat = new Map<string, number>();
const hydrated = new Set<string>();
const dirty = new Set<string>();
let persistTimer: ReturnType<typeof setTimeout> | null = null;
let boundStorage: StorageLike | null | undefined;

export function foldKey(firstCoveredId: string): string {
  return `fold:${firstCoveredId}`;
}

export function bindHeightStorage(storage: StorageLike | null): void {
  boundStorage = storage;
}

function activeStorage(): StorageLike | null {
  if (boundStorage !== undefined) return boundStorage;
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

function parseBucket(raw: unknown): StoredChat | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rec = raw as { w?: unknown; h?: unknown };
  if (!rec.h || typeof rec.h !== "object" || Array.isArray(rec.h)) return null;
  const h: Record<string, number> = {};
  for (const [id, value] of Object.entries(rec.h as Record<string, unknown>)) {
    if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
      h[id] = value;
    }
  }
  const w = rec.w;
  const width = typeof w === "number" && Number.isFinite(w) && w > 0
    ? Math.round(w)
    : undefined;
  if (Object.keys(h).length === 0 && width == null) return null;
  return width != null ? { w: width, h } : { h };
}

function readStore(storage: StorageLike): Record<string, StoredChat> {
  try {
    const parsed = JSON.parse(storage.getItem(CHAT_HEIGHTS_STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, StoredChat> = {};
    for (const [chatKey, raw] of Object.entries(parsed)) {
      const bucket = parseBucket(raw);
      if (bucket) out[chatKey] = bucket;
    }
    return out;
  } catch {
    return {};
  }
}

function writeStore(
  storage: StorageLike,
  data: Record<string, StoredChat>,
): void {
  try {
    storage.setItem(CHAT_HEIGHTS_STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* Session storage can be unavailable in hardened browser contexts. */
  }
}

function dumpChat(chatKey: string): StoredChat | null {
  const map = byChat.get(chatKey);
  if (!map || map.size === 0) return null;
  const h: Record<string, number> = {};
  for (const [id, value] of map) {
    if (Number.isFinite(value) && value >= 0) h[id] = value;
  }
  if (Object.keys(h).length === 0) return null;
  const w = widthByChat.get(chatKey);
  return w != null ? { w, h } : { h };
}

export function flushHeightPersist(): void {
  if (persistTimer != null) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  if (dirty.size === 0) return;
  const storage = activeStorage();
  const pending = [...dirty];
  dirty.clear();
  if (!storage) return;
  const data = readStore(storage);
  for (const chatKey of pending) {
    const bucket = dumpChat(chatKey);
    if (bucket) data[chatKey] = bucket;
    else delete data[chatKey];
  }
  writeStore(storage, data);
}

function schedulePersist(chatKey: string): void {
  dirty.add(chatKey);
  if (persistTimer != null) return;
  persistTimer = setTimeout(flushHeightPersist, HEIGHT_PERSIST_MS);
}

function hydrate(chatKey: string): void {
  if (hydrated.has(chatKey)) return;
  hydrated.add(chatKey);
  if (byChat.has(chatKey)) return;
  const storage = activeStorage();
  if (!storage) return;
  const bucket = readStore(storage)[chatKey];
  if (!bucket) return;
  if (bucket.w != null) widthByChat.set(chatKey, bucket.w);
  byChat.set(chatKey, new Map(Object.entries(bucket.h)));
}

function mapFor(chatKey: string): Map<string, number> {
  hydrate(chatKey);
  let map = byChat.get(chatKey);
  if (!map) {
    map = new Map();
    byChat.set(chatKey, map);
  }
  return map;
}

export function heightsFor(chatKey: string | null): ReadonlyMap<string, number> {
  if (!chatKey) return EMPTY;
  hydrate(chatKey);
  return byChat.get(chatKey) ?? EMPTY;
}

export function getRowHeight(chatKey: string, id: string): number | undefined {
  hydrate(chatKey);
  return byChat.get(chatKey)?.get(id);
}

export function setRowHeight(
  chatKey: string,
  id: string,
  height: number,
): HeightWrite {
  if (!chatKey || !id || !Number.isFinite(height) || height < 0) return "same";
  const map = mapFor(chatKey);
  const prev = map.get(id);
  if (prev === height) return "same";
  map.set(id, height);
  schedulePersist(chatKey);
  return prev === undefined ? "first" : "update";
}

/** Drop this chat's heights when the transcript column's width changed.
 *  No-op when width is unknown (keep memory / persisted rows). */
export function noteChatWidth(chatKey: string, width: number): boolean {
  if (!chatKey || !Number.isFinite(width) || width <= 0) return false;
  const w = Math.round(width);
  hydrate(chatKey);
  const prev = widthByChat.get(chatKey);
  if (prev != null && prev !== w) {
    byChat.delete(chatKey);
    widthByChat.set(chatKey, w);
    hydrated.add(chatKey);
    dirty.add(chatKey);
    flushHeightPersist();
    return true;
  }
  if (prev !== w) {
    widthByChat.set(chatKey, w);
    if (byChat.get(chatKey)?.size) schedulePersist(chatKey);
  }
  return false;
}

export function forgetHeightMemory(): void {
  byChat.clear();
  widthByChat.clear();
  hydrated.clear();
  dirty.clear();
  if (persistTimer != null) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
}

export function clearHeights(chatKey?: string): void {
  if (chatKey) {
    byChat.delete(chatKey);
    widthByChat.delete(chatKey);
    hydrated.delete(chatKey);
    dirty.delete(chatKey);
    const storage = activeStorage();
    if (storage) {
      const data = readStore(storage);
      if (data[chatKey]) {
        delete data[chatKey];
        writeStore(storage, data);
      }
    }
    return;
  }
  byChat.clear();
  widthByChat.clear();
  hydrated.clear();
  dirty.clear();
  if (persistTimer != null) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  const storage = activeStorage();
  if (storage) writeStore(storage, {});
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", flushHeightPersist);
}

export function offsetOf(
  nodes: WindowNode[],
  heights: ReadonlyMap<string, number>,
  targetId: string,
  lead = LIST_LEAD_PX,
): number | null {
  let y = lead;
  for (const node of nodes) {
    const key = node.kind === "fold" ? foldKey(node.id) : node.id;
    if (node.kind === "row" && node.id === targetId) return y;
    const h = heights.get(key);
    if (h == null) return null;
    y += h;
  }
  return null;
}

/** Singleton transcript is on screen — not a settings route, not a
 *  file/web-only pane, not the DAG perspective (`#chatArea` is `none`). */
export function hostPaintsRows(
  showChat: boolean,
  sessionPaneIndex: number,
  dagView: boolean,
): boolean {
  return showChat && sessionPaneIndex >= 0 && !dagView;
}

export type AlwaysLiveMsg = {
  kind?: string;
  slot?: string;
  status?: string;
};

export function collectAlwaysLive(
  ids: readonly string[],
  lookup: (id: string) => AlwaysLiveMsg | undefined,
  extra?: readonly (string | null | undefined)[],
): Set<string> {
  const always = new Set<string>();
  if (extra) {
    for (const id of extra) if (id) always.add(id);
  }
  for (const id of ids) {
    const m = lookup(id);
    if (!m) continue;
    if (m.kind === "compaction" && m.slot === "card") always.add(id);
    if (
      m.status === "streaming"
      || m.status === "pending"
      || m.status === "running"
      || m.status === "cancelling"
    ) {
      always.add(id);
    }
  }
  if (ids.length) always.add(ids[ids.length - 1]!);
  return always;
}

export function decideLiveRows(opts: {
  nodes: WindowNode[];
  heights: ReadonlyMap<string, number>;
  scrollTop: number;
  viewH: number;
  always: ReadonlySet<string>;
  listLen: number;
  overscan?: number;
  recycleMin?: number;
  lead?: number;
}): Set<string> | null {
  const recycleMin = opts.recycleMin ?? RECYCLE_MIN_ROWS;
  if (opts.listLen < recycleMin) return null;

  const live = new Set<string>(opts.always);
  if (opts.viewH <= 0) {
    for (const node of opts.nodes) {
      if (node.kind === "row") live.add(node.id);
    }
    return live;
  }

  const overscan = opts.overscan ?? OVERSCAN_PX;
  const viewLo = opts.scrollTop - overscan;
  const viewHi = opts.scrollTop + opts.viewH + overscan;
  let y = opts.lead ?? LIST_LEAD_PX;
  let prefixKnown = true;

  for (const node of opts.nodes) {
    const key = node.kind === "fold" ? foldKey(node.id) : node.id;
    const h = opts.heights.get(key);

    if (node.kind === "fold") {
      if (h == null) prefixKnown = false;
      else y += h;
      continue;
    }

    if (!prefixKnown || h == null) {
      live.add(node.id);
      if (h == null) prefixKnown = false;
      else y += h;
      continue;
    }

    if (y + h >= viewLo && y <= viewHi) live.add(node.id);
    y += h;
  }
  return live;
}
