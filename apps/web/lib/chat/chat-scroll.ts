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
 *  The transcript pad (`max(25vh, composer + 24)`) is larger than the
 *  composer. At remaining=0 the last bubble sits well above the input.
 *  It tucks under the composer after you scroll `pad - composer`
 *  pixels. Using the full pad as slack hid Jump to latest until you
 *  had scrolled an extra ~25vh past that.
 */
export function chatAtBottomSlack(
  paddingBottom: number,
  composerHeight = 0,
): number {
  const pad = Number.isFinite(paddingBottom) ? Math.max(0, paddingBottom) : 0;
  const cover = Number.isFinite(composerHeight) ? Math.max(0, composerHeight) : 0;
  return Math.max(0, pad - cover) + CHAT_AT_BOTTOM_EPSILON;
}

export function isChatAtBottom(
  area: ScrollMetrics,
  paddingBottom: number,
  composerHeight = 0,
): boolean {
  return remainingScroll(area) <= chatAtBottomSlack(paddingBottom, composerHeight);
}

export function readBottomPadding(el: Element | null): number {
  if (!el || typeof getComputedStyle !== "function") return 0;
  const n = parseFloat(getComputedStyle(el).paddingBottom);
  return Number.isFinite(n) ? n : 0;
}

export function readComposerHeight(): number {
  if (typeof getComputedStyle !== "function") return 0;
  const n = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue(
      "--main-composer-height",
    ),
  );
  return Number.isFinite(n) ? n : 0;
}

/** Same settle window as the left message rail. */
export const CHAT_SMOOTH_SCROLL_FALLBACK_MS = 700;

/** Fire once the area's native smooth scroll has stopped. */
export function whenAreaScrollSettles(
  area: HTMLElement,
  onDone: () => void,
  fallbackMs = CHAT_SMOOTH_SCROLL_FALLBACK_MS,
): () => void {
  let done = false;
  const fire = () => {
    if (done) return;
    done = true;
    area.removeEventListener("scrollend", fire);
    window.clearTimeout(tid);
    onDone();
  };
  area.addEventListener("scrollend", fire, { once: true });
  const tid = window.setTimeout(fire, fallbackMs);
  return () => {
    done = true;
    area.removeEventListener("scrollend", fire);
    window.clearTimeout(tid);
  };
}

/** Native smooth scroll — same curve as the left rail ticks. */
export function animateJumpToLatest(
  area: HTMLElement,
  onDone?: () => void,
): () => void {
  const to = Math.max(0, area.scrollHeight - area.clientHeight);
  if (Math.abs(area.scrollTop - to) < 2) {
    onDone?.();
    return () => {};
  }
  const cancel = whenAreaScrollSettles(area, () => onDone?.());
  area.scrollTo({ top: to, behavior: "smooth" });
  return cancel;
}
