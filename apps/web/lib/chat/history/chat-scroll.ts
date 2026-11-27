export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export function remainingScroll(area: ScrollMetrics): number {
  return area.scrollHeight - area.scrollTop - area.clientHeight;
}

/** Remaining scroll at or under this is the bottom itself: the view follows
 *  only from here, so a reader who stops short of the bottom is never pulled
 *  the rest of the way. It allows for fractional scroll positions. */
export const AT_LATEST_PX = 2;

/** Jump to latest is hidden within this distance of the latest. It decides
 *  the button only, never where the view sits. */
export const JUMP_HIDE_DISTANCE_PX = 70;

/** Jump to latest shows only once the reader is this far above the latest. */
export const JUMP_SHOW_DISTANCE_PX = 240;

/** A scroll event leaves the latest only when the reader moved up.
 *  Content that grows or re-wraps beneath a reader parked at the latest
 *  (a pane made narrower, an image that loads) fires scroll with the top
 *  where it was, and must not count as scrolling away. */
export function scrolledAwayFromLatest(previousTop: number, top: number): boolean {
  return top < previousTop - 1;
}

/** Whether the view follows the latest after a scroll event. The reader
 *  moving up stops it; reaching the bottom itself starts it again. */
export function followAfterScroll(state: {
  following: boolean;
  latestWindow: boolean;
  remaining: number;
  movedUp: boolean;
}): boolean {
  if (!state.latestWindow) return false;
  if (state.remaining <= AT_LATEST_PX) return true;
  return state.following && !state.movedUp;
}

/** Whether the view follows the latest after the content changed height.
 *  Growth alone never stops it, and content that shrank to leave the view
 *  at the latest starts it. Growth the reader caused (a block opened by a
 *  click, a held pointer) is left where it is, and stops following once it
 *  has put the latest a clear distance below. */
export function followAfterGrowth(state: {
  following: boolean;
  latestWindow: boolean;
  remaining: number;
  readerActive: boolean;
}): boolean {
  if (!state.latestWindow) return false;
  if (state.remaining <= AT_LATEST_PX) return true;
  if (!state.following) return false;
  return !(state.readerActive && state.remaining > JUMP_SHOW_DISTANCE_PX);
}

/** Whether Jump to latest is drawn. Never while the view follows or sits
 *  at the latest; between the two distances it keeps what it had, so it
 *  neither appears on a small move nor blinks at one edge. */
export function jumpVisible(
  was: boolean,
  state: { following: boolean; hasNewer: boolean; remaining: number },
): boolean {
  if (state.hasNewer) return true;
  if (state.following || state.remaining <= JUMP_HIDE_DISTANCE_PX) return false;
  if (state.remaining > JUMP_SHOW_DISTANCE_PX) return true;
  return was;
}

/** A seek started outside the scroll area (the Goal chip sits in the
 *  composer). The area hears a click or a wheel inside it and stops pulling
 *  the view to the latest; a seek from elsewhere says so with this event,
 *  or its smooth scroll is undone on the first frame. It bubbles from the
 *  element being scrolled to. */
export const CHAT_READER_SEEK_EVENT = "chat-reader-seek";

export function announceReaderSeek(target: Element): void {
  target.dispatchEvent(new Event(CHAT_READER_SEEK_EVENT, { bubbles: true }));
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
  /** Frame scheduler and clock; tests pass their own. */
  requestFrame?: (cb: (time: number) => void) => number;
  cancelFrame?: (id: number) => void;
  now?: () => number;
}

/** Length of the Jump to latest ride, whatever the distance. Chromium's
 *  own smooth scroll grows with distance (up to ~1.5 s) and could not be
 *  timed; a fixed ride driven here can. */
export const JUMP_RIDE_MS = 480;

/** A ride starts at most this many screens above the latest: a longer
 *  distance hops to there first, so each frame moves a readable amount. */
export const JUMP_RIDE_MAX_SCREENS = 1.5;

/** Sine ease-in-out: the gentlest peak speed (pi/2 of the average) of
 *  the common curves, so the middle frames stay readable. */
export function easeInOutSine(t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  return -(Math.cos(Math.PI * t) - 1) / 2;
}

/** Where a ride starts: the reader's top, or at most
 *  JUMP_RIDE_MAX_SCREENS screens away from the target. */
export function jumpRideStart(top: number, target: number, clientHeight: number): number {
  const lead = Math.max(1, clientHeight) * JUMP_RIDE_MAX_SCREENS;
  if (target - top > lead) return target - lead;
  if (top - target > lead) return target + lead;
  return top;
}

/**
 * Ride to the latest over JUMP_RIDE_MS, one scrollTop write per frame.
 * Every frame reads the target again, so a reply that grows or a page
 * that arrives during the ride is absorbed and the ride ends on the
 * current latest with no correction jump. Cancel stops at the current
 * frame and skips onDone.
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
  const first = getTarget();
  if (Math.abs(area.scrollTop - first) <= JUMP_PLACEMENT_TOLERANCE_PX) {
    placeAtLatest(area, getTarget);
    onDone?.();
    return () => {};
  }
  const requestFrame = options?.requestFrame
    ?? ((cb: (time: number) => void) => globalThis.requestAnimationFrame(cb));
  const cancelFrame = options?.cancelFrame
    ?? ((id: number) => globalThis.cancelAnimationFrame(id));
  const now = options?.now ?? (() => performance.now());
  // Any native smooth scroll still running would fight the frames.
  stopAreaScroll(area);
  const startTop = jumpRideStart(area.scrollTop, first, area.clientHeight);
  if (startTop !== area.scrollTop) area.scrollTop = startTop;
  const t0 = now();
  let finished = false;
  let frameId = 0;
  const step = () => {
    if (finished) return;
    const t = Math.min(1, (now() - t0) / JUMP_RIDE_MS);
    const target = getTarget();
    if (t < 1) {
      area.scrollTop = startTop + (target - startTop) * easeInOutSine(t);
    } else {
      finished = true;
      area.scrollTop = target;
      onDone?.();
      return;
    }
    frameId = requestFrame(step);
  };
  frameId = requestFrame(step);
  return () => {
    if (finished) return;
    finished = true;
    cancelFrame(frameId);
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

/** How far the composer's mask starts below its top edge while its rows
 *  are tucked behind the input box (use-tuck-backdrop.ts writes it on the
 *  composer root). COMPOSER_TUCK_SHIFT_EVENT fires on the root whenever
 *  the covered height settles at a new value: a tuck shift, or the end of
 *  a slide (COMPOSER_SLIDING_ATTR). */
export const COMPOSER_TUCK_SHIFT_VAR = "--composer-tuck-shift";
export const COMPOSER_TUCK_SHIFT_EVENT = "composer-tuck-shift";

/** The part of the composer that covers the transcript: its box less the
 *  strip a tucked row leaves empty above the input. */
export function visibleComposerHeight(root: HTMLElement): number {
  const shift = parseFloat(root.style?.getPropertyValue(COMPOSER_TUCK_SHIFT_VAR) ?? "");
  return Math.max(0, root.offsetHeight - (Number.isFinite(shift) ? shift : 0));
}

/** On the composer root while its covered height slides to a new value:
 *  a tuck toggle (use-tuck-backdrop.ts) or the function form growing or
 *  shrinking (use-fn-form-wrapper.ts). The height publishers wait
 *  (composerSliding) and publish once COMPOSER_TUCK_SHIFT_EVENT fires at
 *  the end, so no frame of the slide restyles or lays out the transcript. */
export const COMPOSER_SLIDING_ATTR = "data-composer-sliding";

/** Whether this composer root's covered height is sliding; its height
 *  publishers write the reserve when the slide ends. */
export function composerSliding(root: Element): boolean {
  return root.hasAttribute(COMPOSER_SLIDING_ATTR);
}

/** Fired (bubbling) from the composer root when a slide starts: the
 *  covered height will change by `-by` once the slide ends, when
 *  COMPOSER_TUCK_SHIFT_EVENT fires. A transcript following the latest
 *  rides `by` meanwhile, on the slide's own timing, so the step its
 *  reserve takes at the end does not show. */
export const COMPOSER_RESERVE_SLIDE_EVENT = "composer-reserve-slide";

export interface ReserveSlide {
  /** How far the transcript's latest row moves down (px). */
  by: number;
  duration: number;
  easing: string;
}

/** The pane a scroller or a composer belongs to. */
export function chatPaneOf(element: Element): Element | null {
  return element.closest("#chatView, .peer-session-pane");
}
