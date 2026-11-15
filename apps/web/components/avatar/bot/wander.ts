/**
 * Where a live character looks: around, on its own.
 *
 * The renderer's own idle wander moves the eyes about a pixel at icon size,
 * and every character of the same shape moves in step, so it reads as
 * staring ahead. Here each character gets its own glances instead: a look
 * target (`lookAt`) that the renderer turns the eyes and the body toward,
 * held for a while, then the next one. Every character draws its own
 * targets and pauses, so no two look the same way at the same moment.
 *
 * Nothing here reads layout or runs per frame: one timer per character,
 * firing once per glance.
 */

export interface Look {
  x: number;
  y: number;
}

/** How long one look is held, in ms. */
export const HOLD_MS = { min: 900, max: 2600 } as const;
/** How far a glance goes, each axis -1..1 (x right, y down). Sideways
 *  glances go most of the way; up and down less, like a head turning. */
export const REACH = { x: 0.95, y: 0.6 } as const;
/** Share of glances that come back to straight ahead. */
export const AHEAD_SHARE = 0.25;

export interface WanderClock {
  random: () => number;
  setTimeout: (run: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
}

const browserClock: WanderClock = {
  random: Math.random,
  setTimeout: (run, ms) => globalThis.setTimeout(run, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/** The next look: straight ahead (null) now and then, else a point off to
 *  one side, at least a third of the way out so the turn shows. */
export function nextLook(random: () => number): Look | null {
  if (random() < AHEAD_SHARE) return null;
  const side = random() < 0.5 ? -1 : 1;
  const x = side * REACH.x * (0.35 + 0.65 * random());
  const y = REACH.y * (random() * 2 - 1);
  return { x, y };
}

/** Make a character look around: `listener` gets each new look. Returns the
 *  function that stops it. The first glance comes after a random pause, so
 *  characters mounted together do not turn together. */
export function startWander(listener: (look: Look | null) => void, clock: WanderClock = browserClock): () => void {
  let handle: unknown = null;
  let stopped = false;
  const hold = () => HOLD_MS.min + (HOLD_MS.max - HOLD_MS.min) * clock.random();
  const glance = () => {
    if (stopped) return;
    listener(nextLook(clock.random));
    handle = clock.setTimeout(glance, hold());
  };
  handle = clock.setTimeout(glance, hold() * clock.random());
  return () => {
    stopped = true;
    if (handle !== null) clock.clearTimeout(handle);
    handle = null;
  };
}
