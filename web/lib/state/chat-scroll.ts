export const CHAT_SCROLL_STORAGE_KEY = "chatScrollByKey";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface ScrollAreaLike {
  scrollTop: number;
}

interface ResolveChatScrollOptions {
  keyChanged: boolean;
  seedChanged: boolean;
  saved: number | null;
  scrollHeight: number;
  currentTop: number;
}

function readMap(storage: StorageLike): Record<string, number> {
  try {
    const parsed = JSON.parse(storage.getItem(CHAT_SCROLL_STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const positions: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
        positions[key] = value;
      }
    }
    return positions;
  } catch {
    return {};
  }
}

export function readChatScroll(
  storage: StorageLike,
  chatKey: string,
): number | null {
  return readMap(storage)[chatKey] ?? null;
}

export function writeChatScroll(
  storage: StorageLike,
  chatKey: string,
  scrollTop: number,
): void {
  if (!chatKey || !Number.isFinite(scrollTop)) return;
  try {
    const positions = readMap(storage);
    positions[chatKey] = Math.max(0, scrollTop);
    storage.setItem(CHAT_SCROLL_STORAGE_KEY, JSON.stringify(positions));
  } catch {
    /* Session storage can be unavailable in hardened browser contexts. */
  }
}

export function resolveChatScrollTop({
  keyChanged,
  seedChanged,
  saved,
  scrollHeight,
  currentTop,
}: ResolveChatScrollOptions): number {
  if (keyChanged) return saved ?? scrollHeight;
  if (seedChanged) return scrollHeight;
  return currentTop;
}

export function restoreChatScrollIfCurrent(
  area: ScrollAreaLike,
  expectedChatKey: string,
  activeChatKey: string | null,
  scrollTop: number,
): boolean {
  if (expectedChatKey !== activeChatKey) return false;
  area.scrollTop = scrollTop;
  return true;
}
