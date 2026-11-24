/** One clock for every elapsed time, countdown and "x min ago" on screen.
 *
 *  A component says how its text follows time (a `Cadence`) and from which
 *  instant (`from`); it is woken exactly when that text would read
 *  differently, never in between. However many components show a time, one
 *  timer runs for the whole app: it is set for the earliest moment any of
 *  them changes, stops when none is mounted, and pauses while the document
 *  is hidden (a window shown again is brought up to date at once). */
import { useCallback, useMemo, useSyncExternalStore } from "react";

/** Given the milliseconds elapsed since `from` (negative before it), the
 *  elapsed milliseconds at which the shown text next changes, strictly
 *  later; `Infinity` once it never changes again. */
export type Cadence = (elapsed: number) => number;

/** Text that changes every `step` milliseconds counted from `from`. */
export function every(step: number): Cadence {
  return elapsed => (Math.floor(elapsed / step) + 1) * step;
}

/** "1m 40s", "0:42": changes each second. */
export const SECONDS = every(1000);
/** Changes each minute. */
export const MINUTES = every(60_000);
const HOURS = every(3_600_000);
const DAYS = every(86_400_000);

/** "just now", "5 min ago", "3 h ago", "2 d ago" counted down to the
 *  minute, hour or day: changes each minute for the first hour, each hour
 *  for the first day, then each day. */
export const AGO: Cadence = elapsed => (
  elapsed < 3_600_000 ? MINUTES(elapsed) : elapsed < 86_400_000 ? HOURS(elapsed) : DAYS(elapsed)
);

/** Whole seconds left until `from` (rounded up), resting at zero. */
export const COUNTDOWN: Cadence = elapsed => (elapsed >= 0 ? Infinity : SECONDS(elapsed));

/** The local calendar day, with `from` = 0 (wall-clock time). */
export const LOCAL_DAY: Cadence = time => {
  const day = new Date(time);
  return new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1).getTime();
};

/** The cadence of any text drawn from the elapsed time: `text(elapsed)`
 *  is compared, not shown, and must only move forward as time passes (a
 *  label that never comes back to an earlier value). The next change is
 *  found to the millisecond within `horizon`, beyond which it never wakes. */
export function whenChanges(text: (elapsed: number) => unknown, horizon = 400 * 86_400_000): Cadence {
  return elapsed => {
    const now = text(elapsed);
    let step = 1000;
    while (text(elapsed + step) === now) {
      if (step >= horizon) return Infinity;
      step *= 2;
    }
    let low = 0;
    let high = step;
    while (high - low > 1) {
      const middle = Math.floor((low + high) / 2);
      if (text(elapsed + middle) === now) low = middle;
      else high = middle;
    }
    return elapsed + high;
  };
}

const NEVER: Cadence = () => Infinity;

interface Slot {
  cadence: Cadence;
  anchors: readonly number[];
  /** The time it was last brought up to date: what it shows. */
  value: number;
  /** When its text next changes. */
  due: number;
  listeners: Set<() => void>;
}

const live = new Set<Slot>();
let timer: ReturnType<typeof setTimeout> | undefined;
let timerAt = Infinity;
let watchingVisibility = false;
/** setTimeout holds at most this long (about 24.8 days). */
const LONGEST_WAIT = 2 ** 31 - 1;

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function dueAfter(slot: Slot, now: number): number {
  let due = Infinity;
  for (const anchor of slot.anchors) {
    const next = anchor + slot.cadence(now - anchor);
    if (next < due) due = next;
  }
  return due;
}

/** Bring a slot up to date; true when what it shows changed. */
function refresh(slot: Slot, now: number): boolean {
  if (now < slot.due) return false;
  slot.value = now;
  slot.due = dueAfter(slot, now);
  return true;
}

function arm(): void {
  let next = Infinity;
  if (!hidden()) for (const slot of live) if (slot.due < next) next = slot.due;
  if (next === timerAt && (timer !== undefined || next === Infinity)) return;
  if (timer !== undefined) clearTimeout(timer);
  timer = undefined;
  timerAt = next;
  if (next === Infinity) return;
  timer = setTimeout(tick, Math.min(LONGEST_WAIT, Math.max(0, next - Date.now())));
}

function tick(): void {
  timer = undefined;
  timerAt = Infinity;
  const now = Date.now();
  const changed: Slot[] = [];
  for (const slot of live) if (refresh(slot, now)) changed.push(slot);
  arm();
  for (const slot of changed) for (const listener of [...slot.listeners]) listener();
}

function onVisibility(): void {
  if (hidden()) arm();
  else tick();
}

function makeSlot(cadence: Cadence, anchors: readonly number[]): Slot {
  const now = Date.now();
  const slot: Slot = { cadence, anchors: anchors.filter(Number.isFinite), value: now, due: 0, listeners: new Set() };
  slot.due = dueAfter(slot, now);
  return slot;
}

function attach(slot: Slot, listener: () => void): () => void {
  slot.listeners.add(listener);
  if (!live.has(slot)) {
    live.add(slot);
    if (!watchingVisibility && typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
      watchingVisibility = true;
    }
    arm();
  }
  return () => {
    slot.listeners.delete(listener);
    if (slot.listeners.size) return;
    live.delete(slot);
    arm();
    if (!live.size && watchingVisibility) {
      document.removeEventListener("visibilitychange", onVisibility);
      watchingVisibility = false;
    }
  };
}

/** Outside React: call `listener(now)` each time text with this cadence
 *  from `from` changes. Returns the unsubscribe. */
export function watchNow(cadence: Cadence, from: number | readonly number[], listener: (now: number) => void): () => void {
  const slot = makeSlot(cadence, typeof from === "number" ? [from] : [...from]);
  return attach(slot, () => listener(slot.value));
}

/** The current time in epoch milliseconds, renewed only when text with
 *  `cadence`, counted from `from` (an instant, or several: the earliest
 *  change of any of them), would read differently. A null cadence stops
 *  the clock: the time stays where it was. */
export function useNow(cadence: Cadence | null, from: number | readonly number[] = 0): number {
  const key = typeof from === "number" ? String(from) : from.join(",");
  // `key` stands for `from`, whose array identity changes every render.
  const slot = useMemo(() => makeSlot(cadence ?? NEVER, key ? key.split(",").map(Number) : []), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (cadence && slot.cadence !== cadence) {
    // Started again, or counted another way: renewed at the next read.
    slot.cadence = cadence;
    slot.due = -Infinity;
  }
  const subscribe = useCallback(
    (listener: () => void) => (cadence ? attach(slot, listener) : () => {}),
    [slot, cadence],
  );
  const snapshot = useCallback(() => {
    if (cadence) refresh(slot, Date.now());
    return slot.value;
  }, [slot, cadence]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Timers running for the clock (tests). */
export function clockTimersForTest(): { slots: number; armed: boolean } {
  return { slots: live.size, armed: timer !== undefined };
}
