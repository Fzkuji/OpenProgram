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

/** Time-boxed subway hop.

Ease in and out last a fixed time. Cruise speed is whatever the
remaining distance needs. Duration grows with distance, then hard-caps
so a jump always finishes in ``JUMP_MAX_S`` — never a ten-second crawl.
*/
export const JUMP_EASE_S = 0.4;
export const JUMP_MIN_S = 0.55;
export const JUMP_MAX_S = 3.5;
/** Comfortable cruise used only to pick duration before the cap. */
export const JUMP_COMFORT_PX_S = 10000;

export type JumpMotionPlan = {
  distance: number;
  duration: number;
  kind: "triangle" | "trapezoid";
  vPeak: number;
  tAccel: number;
  tCruise: number;
  tDecel: number;
};

export function jumpMotionPlan(distance: number): JumpMotionPlan {
  const s = Math.abs(distance);
  if (s < 1) {
    return {
      distance: 0,
      duration: 0,
      kind: "triangle",
      vPeak: 0,
      tAccel: 0,
      tCruise: 0,
      tDecel: 0,
    };
  }
  let duration = 2 * JUMP_EASE_S + s / JUMP_COMFORT_PX_S;
  duration = Math.min(JUMP_MAX_S, Math.max(JUMP_MIN_S, duration));
  if (duration <= 2 * JUMP_EASE_S + 1e-6) {
    const tAccel = duration / 2;
    const vPeak = s / tAccel;
    return {
      distance: s,
      duration,
      kind: "triangle",
      vPeak,
      tAccel,
      tCruise: 0,
      tDecel: tAccel,
    };
  }
  const tAccel = JUMP_EASE_S;
  const tCruise = duration - 2 * tAccel;
  const vPeak = s / (duration - tAccel);
  return {
    distance: s,
    duration,
    kind: "trapezoid",
    vPeak,
    tAccel,
    tCruise,
    tDecel: tAccel,
  };
}

export function jumpTraveled(plan: JumpMotionPlan, elapsed: number): number {
  if (plan.duration <= 0 || plan.tAccel <= 0) return plan.distance;
  const t = Math.min(plan.duration, Math.max(0, elapsed));
  const a = plan.vPeak / plan.tAccel;
  if (t <= plan.tAccel) return 0.5 * a * t * t;
  const sAccel = 0.5 * plan.vPeak * plan.tAccel;
  if (t <= plan.tAccel + plan.tCruise) {
    return sAccel + plan.vPeak * (t - plan.tAccel);
  }
  const td = t - plan.tAccel - plan.tCruise;
  const sCruise = plan.vPeak * plan.tCruise;
  return sAccel + sCruise + plan.vPeak * td - 0.5 * a * td * td;
}

export function jumpScrollDuration(distance: number): number {
  return jumpMotionPlan(distance).duration * 1000;
}

export function jumpScrollTopAt(from: number, to: number, elapsedSec: number): number {
  const dist = to - from;
  if (dist === 0) return to;
  const plan = jumpMotionPlan(dist);
  const traveled = jumpTraveled(plan, elapsedSec);
  const sign = dist < 0 ? -1 : 1;
  return from + sign * Math.min(Math.abs(dist), traveled);
}

/** Animate a scroller to the latest message. Returns a cancel function. */
export function animateJumpToLatest(
  area: { scrollTop: number; scrollHeight: number; clientHeight: number },
  onDone?: () => void,
): () => void {
  const from = area.scrollTop;
  const to = Math.max(0, area.scrollHeight - area.clientHeight);
  const dist = to - from;
  if (Math.abs(dist) < 2) {
    area.scrollTop = to;
    onDone?.();
    return () => {};
  }
  const plan = jumpMotionPlan(dist);
  const t0 = performance.now();
  let raf = 0;
  let cancelled = false;
  const step = (now: number) => {
    if (cancelled) return;
    const elapsed = (now - t0) / 1000;
    area.scrollTop = jumpScrollTopAt(from, to, elapsed);
    if (elapsed < plan.duration) {
      raf = requestAnimationFrame(step);
      return;
    }
    area.scrollTop = to;
    onDone?.();
  };
  raf = requestAnimationFrame(step);
  return () => {
    cancelled = true;
    if (raf) cancelAnimationFrame(raf);
  };
}
