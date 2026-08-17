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
  /** Was the view already parked at the bottom before this turn landed?
   *  A reader who has scrolled up keeps their place; only someone already
   *  following the tail gets carried along. */
  atBottom?: boolean;
  /** True when the new row is the user's own message. Sending is an
   *  explicit "take me to the conversation" gesture, so it follows even
   *  from far up the history — unlike an agent row arriving on its own. */
  ownTurn?: boolean;
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

/** Where the transcript should sit after this render.
 *
 *  Switching chats restores that chat's saved place (bottom if new).
 *  A new turn follows the tail only when the reader was already there,
 *  or when the turn is their own — being yanked to the bottom while
 *  reading back through history is the thing this avoids. `atBottom`
 *  defaults to true so a caller that doesn't track it keeps the old
 *  always-follow behaviour rather than silently freezing the view. */
export function resolveChatScrollTop({
  keyChanged,
  seedChanged,
  saved,
  scrollHeight,
  currentTop,
  atBottom = true,
  ownTurn = false,
}: ResolveChatScrollOptions): number {
  if (keyChanged) return saved ?? scrollHeight;
  if (seedChanged && (atBottom || ownTurn)) return scrollHeight;
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
