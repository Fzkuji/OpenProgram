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


export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export function remainingScroll(area: ScrollMetrics): number {
  return area.scrollHeight - area.scrollTop - area.clientHeight;
}

/** Extra pixels on top of the transcript pad. Subpixel / rubber-band. */
export const CHAT_AT_BOTTOM_EPSILON = 8;

/** Slack that still counts as "at the latest".
 *
 *  The transcript reserves bottom padding so the last bubble sits above
 *  the floating composer (`max(25vh, composer + 24)`). A fixed 80px
 *  threshold treated that pad as "scrolled up", so Jump to latest
 *  stayed on after the last message was already in view.
 */
export function chatAtBottomSlack(paddingBottom: number): number {
  const pad = Number.isFinite(paddingBottom) ? Math.max(0, paddingBottom) : 0;
  return pad + CHAT_AT_BOTTOM_EPSILON;
}

export function isChatAtBottom(
  area: ScrollMetrics,
  paddingBottom: number,
): boolean {
  return remainingScroll(area) <= chatAtBottomSlack(paddingBottom);
}

export function readBottomPadding(el: Element | null): number {
  if (!el || typeof getComputedStyle !== "function") return 0;
  const n = parseFloat(getComputedStyle(el).paddingBottom);
  return Number.isFinite(n) ? n : 0;
}
