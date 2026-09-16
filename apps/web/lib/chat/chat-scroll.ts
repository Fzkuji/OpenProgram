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

/** Exact Jump placement; do not substitute reattachment slack. */
export const JUMP_PLACEMENT_TOLERANCE_PX = 2;

export function latestScrollTop(area: ScrollMetrics): number {
  if (!Number.isFinite(area.scrollHeight) || !Number.isFinite(area.clientHeight)) {
    return 0;
  }
  return Math.max(0, area.scrollHeight - area.clientHeight);
}

export function snapToLatest(area: HTMLElement): void {
  area.scrollTop = latestScrollTop(area);
}

/** Stop in-flight native smooth scrolling without a later correction. */
export function stopAreaScroll(area: HTMLElement): void {
  const top = area.scrollTop;
  if (typeof area.scrollTo === "function") {
    area.scrollTo({ top, behavior: "auto" });
  } else {
    area.scrollTop = top;
  }
}

function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function"
    && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function placeAtLatest(
  area: HTMLElement,
  getTarget?: () => number,
): void {
  const target = getTarget ? getTarget() : latestScrollTop(area);
  if (Math.abs(area.scrollTop - target) > JUMP_PLACEMENT_TOLERANCE_PX) {
    area.scrollTop = target;
  }
}

/** Fire once the area's native smooth scroll has stopped. Cancel stops motion. */
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
    globalThis.clearTimeout(tid);
    onDone();
  };
  area.addEventListener("scrollend", fire, { once: true });
  const tid = globalThis.setTimeout(fire, fallbackMs);
  return () => {
    if (done) return;
    done = true;
    area.removeEventListener("scrollend", fire);
    globalThis.clearTimeout(tid);
    stopAreaScroll(area);
  };
}

export interface AnimateJumpOptions {
  getTarget?: () => number;
  reducedMotion?: boolean;
}

/**
 * Native smooth scroll to the padding edge. At settle or watchdog timeout,
 * recompute the target and apply one instant correction. Cancel stops
 * browser motion and skips that correction.
 */
export function animateJumpToLatest(
  area: HTMLElement,
  onDone?: () => void,
  options?: AnimateJumpOptions,
): () => void {
  const getTarget = options?.getTarget ?? (() => latestScrollTop(area));
  const reduced = options?.reducedMotion ?? prefersReducedMotion();
  if (reduced) {
    placeAtLatest(area, getTarget);
    onDone?.();
    return () => {};
  }
  const to = getTarget();
  if (Math.abs(area.scrollTop - to) <= JUMP_PLACEMENT_TOLERANCE_PX) {
    placeAtLatest(area, getTarget);
    onDone?.();
    return () => {};
  }
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    placeAtLatest(area, getTarget);
    onDone?.();
  };
  const cancelSettle = whenAreaScrollSettles(area, finish);
  area.scrollTo({ top: to, behavior: "smooth" });
  return () => {
    if (finished) return;
    finished = true;
    cancelSettle();
  };
}

export function defaultScrollerKey(sessionId: string, background: boolean): string {
  return background ? `peer:${sessionId}` : sessionId;
}

/** True only when the incoming session is this draft's adopted identity. */
export function isProvisionalSessionBind(
  fromKey: string | null | undefined,
  fromSid: string | null | undefined,
  toKey: string | null | undefined,
  toSid: string | null | undefined,
): boolean {
  if (!fromKey?.startsWith("local_") || !toKey || !toSid) return false;
  return fromKey === toKey || fromKey === toSid || (!!fromSid && fromSid === toSid);
}

export type TakeLatestNote = {
  sessionId: string;
  scrollerKey: string;
  turnSeed: string;
  generation: number;
};

type TakeLatestListener = (note: TakeLatestNote) => void;

const takeLatestNotes = new Map<string, TakeLatestNote>();
const takeLatestSettled = new Map<string, number>();
const takeLatestLive = new Map<string, number>();
const takeLatestListeners = new Set<TakeLatestListener>();
let takeLatestGeneration = 0;
let pruneTakeLatestQueued = false;

/** Idle completed records kept after session delete / viewport release. */
export const TAKE_LATEST_IDLE_LIMIT = 32;

function takeLatestMapKey(sessionId: string, scrollerKey: string): string {
  return `${sessionId}\0${scrollerKey}`;
}

export function noteTakeLatest(
  note: Omit<TakeLatestNote, "generation">,
): TakeLatestNote {
  takeLatestGeneration += 1;
  const stamped: TakeLatestNote = {
    sessionId: note.sessionId,
    scrollerKey: note.scrollerKey,
    turnSeed: note.turnSeed,
    generation: takeLatestGeneration,
  };
  takeLatestNotes.set(takeLatestMapKey(note.sessionId, note.scrollerKey), stamped);
  pruneTakeLatest();
  for (const listener of takeLatestListeners) listener(stamped);
  return stamped;
}

export function peekTakeLatest(
  sessionId: string,
  scrollerKey: string,
): TakeLatestNote | null {
  return takeLatestNotes.get(takeLatestMapKey(sessionId, scrollerKey)) ?? null;
}

export function lastSettledTakeLatest(
  sessionId: string,
  scrollerKey: string,
): number {
  return takeLatestSettled.get(takeLatestMapKey(sessionId, scrollerKey)) ?? 0;
}

/** Record completion or cancellation. Does not delete the note. */
export function settleTakeLatest(
  sessionId: string,
  scrollerKey: string,
  generation: number,
): void {
  const key = takeLatestMapKey(sessionId, scrollerKey);
  const prev = takeLatestSettled.get(key) ?? 0;
  if (generation > prev) takeLatestSettled.set(key, generation);
  pruneTakeLatest();
}

export function retainTakeLatestViewport(
  sessionId: string,
  scrollerKey: string,
): () => void {
  const key = takeLatestMapKey(sessionId, scrollerKey);
  takeLatestLive.set(key, (takeLatestLive.get(key) ?? 0) + 1);
  return () => {
    const n = (takeLatestLive.get(key) ?? 1) - 1;
    if (n <= 0) takeLatestLive.delete(key);
    else takeLatestLive.set(key, n);
    schedulePruneTakeLatest();
  };
}

export function forgetTakeLatestSession(sessionId: string): void {
  if (!sessionId) return;
  const prefix = `${sessionId}\0`;
  for (const key of [...takeLatestNotes.keys()]) {
    if (key.startsWith(prefix)) takeLatestNotes.delete(key);
  }
  for (const key of [...takeLatestSettled.keys()]) {
    if (key.startsWith(prefix)) takeLatestSettled.delete(key);
  }
}

function isWaitingTakeLatest(key: string): boolean {
  const note = takeLatestNotes.get(key);
  const settled = takeLatestSettled.get(key) ?? 0;
  return !!note && note.generation > settled;
}

function pruneTakeLatest(): void {
  const idle: string[] = [];
  for (const key of new Set([...takeLatestNotes.keys(), ...takeLatestSettled.keys()])) {
    if (isWaitingTakeLatest(key)) continue;
    if ((takeLatestLive.get(key) ?? 0) > 0) continue;
    idle.push(key);
  }
  const excess = idle.length - TAKE_LATEST_IDLE_LIMIT;
  if (excess <= 0) return;
  for (const key of idle.slice(0, excess)) {
    takeLatestNotes.delete(key);
    takeLatestSettled.delete(key);
  }
}

function schedulePruneTakeLatest(): void {
  if (pruneTakeLatestQueued) return;
  pruneTakeLatestQueued = true;
  queueMicrotask(() => {
    pruneTakeLatestQueued = false;
    pruneTakeLatest();
  });
}

/** Move an open note to a new session/scroller without a new generation (provisional bind). */
export function relocateTakeLatest(
  fromSessionId: string,
  fromScrollerKey: string,
  toSessionId: string,
  toScrollerKey: string,
): TakeLatestNote | null {
  const from = takeLatestMapKey(fromSessionId, fromScrollerKey);
  const note = takeLatestNotes.get(from);
  if (!note) return null;
  const moved: TakeLatestNote = {
    ...note,
    sessionId: toSessionId,
    scrollerKey: toScrollerKey,
  };
  takeLatestNotes.delete(from);
  takeLatestNotes.set(takeLatestMapKey(toSessionId, toScrollerKey), moved);
  const settled = takeLatestSettled.get(from);
  if (settled != null) {
    takeLatestSettled.delete(from);
    takeLatestSettled.set(takeLatestMapKey(toSessionId, toScrollerKey), settled);
  }
  pruneTakeLatest();
  for (const listener of takeLatestListeners) listener(moved);
  return moved;
}

export function subscribeTakeLatest(listener: TakeLatestListener): () => void {
  takeLatestListeners.add(listener);
  return () => {
    takeLatestListeners.delete(listener);
  };
}

/** Main overlay is `--main-composer-height`. Peer overlay is the composer root. */
export function readComposerOverlay(
  area: HTMLElement | null,
  composerRoot?: HTMLElement | null,
): number {
  if (composerRoot && composerRoot.isConnected) {
    const node = composerRoot.offsetHeight > 0
      ? composerRoot
      : composerRoot.firstElementChild;
    const h = node && typeof (node as HTMLElement).offsetHeight === "number"
      ? (node as HTMLElement).offsetHeight
      : 0;
    return Number.isFinite(h) && h > 0 ? h : 0;
  }
  if (area) {
    const peer = area.closest(".peer-session-pane")?.querySelector(
      ".peer-session-composer-host > *",
    );
    if (peer instanceof HTMLElement) {
      const h = peer.offsetHeight;
      if (Number.isFinite(h) && h > 0) return h;
    }
  }
  return readComposerHeight();
}
