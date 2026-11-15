/** Sideways movement in the Activity panel, as a phone does it: what moves
 *  follows the fingers on the trackpad, and when they lift it comes to rest
 *  on a spring that starts at the speed they left it with. Wheel events carry
 *  no phases, so wheel-gestures (the library Embla pages with, and the one the
 *  Launchpad uses) rebuilds began / momentum / ended from them. */
import { animate } from "framer-motion";
import { WheelGestures } from "wheel-gestures";

/** The Launchpad's settle, so every page in the App comes to rest the same way. */
const SPRING = { type: "spring", stiffness: 320, damping: 38, mass: 0.9, restDelta: 0.5, restSpeed: 8 } as const;
/** The fastest a lift is taken to be, in px a second: a flick lands, it does not bounce. */
const MAX_SPEED = 2400;
/** How far a pull past an end can move what it pulls, however long the pull. */
const END_GIVE = 44;
/** A mouse wheel's notch arrives as one big step; a trackpad never starts that hard. */
const NOTCH = 40;
/** Sideways travel that makes a gesture a sideways one. */
const TAKES = 6;

/** No animation: the reader asked for none, or there is no screen (the node tests). */
export const stillMotion = (): boolean => typeof matchMedia !== "function" || matchMedia("(prefers-reduced-motion: reduce)").matches;

export const rubberBand = (pull: number): number => Math.sign(pull) * (Math.abs(pull) * END_GIVE) / (Math.abs(pull) + END_GIVE);

/** A swipe turns on distance, or on a flick: short, and still brisk at the lift. */
export const swipeTurns = (moved: number, speed: number, far: number): boolean =>
  Math.abs(moved) >= far || (Math.abs(moved) >= 24 && Math.abs(speed) >= 500 && Math.sign(speed) === Math.sign(moved));

/** Travel from one value to another on the spring, starting at `speed` px a
 *  second. Returns what stops it where it is. */
export function springTo(from: number, to: number, speed: number, onUpdate: (value: number) => void, onDone?: () => void): () => void {
  if (stillMotion() || Math.abs(to - from) < 0.5) {
    onUpdate(to);
    onDone?.();
    return () => {};
  }
  let stopped = false;
  const controls = animate(from, to, {
    ...SPRING, velocity: Math.max(-MAX_SPEED, Math.min(MAX_SPEED, speed)),
    onUpdate: value => { if (!stopped) onUpdate(value); },
    onComplete: () => { if (!stopped) onDone?.(); },
  });
  return () => { stopped = true; controls.stop(); };
}

/** The App's standard curve (--ease-standard in app/styles/design/motion.css). */
const EASE_STANDARD = [0.25, 0.1, 0.25, 1] as const;

/** Travel from one value to another in `ms` on the standard curve: one move
 *  that ends when it says, with no spring's tail. Returns what stops it where it is. */
export function tweenTo(from: number, to: number, ms: number, onUpdate: (value: number) => void, onDone?: () => void): () => void {
  if (stillMotion() || Math.abs(to - from) < 0.5 || ms <= 0) {
    onUpdate(to);
    onDone?.();
    return () => {};
  }
  let stopped = false;
  const controls = animate(from, to, {
    duration: ms / 1000, ease: EASE_STANDARD,
    onUpdate: value => { if (!stopped) onUpdate(value); },
    onComplete: () => { if (!stopped) onDone?.(); },
  });
  return () => { stopped = true; controls.stop(); };
}

export interface SwipeHandlers {
  /** A sideways gesture starts, heading `direction` (1: content travels
   *  left, towards what is further right). False leaves it to the browser. */
  begin: (direction: number) => boolean;
  /** How far the fingers have gone since `begin`, signed like `direction`. */
  move: (moved: number) => void;
  /** The fingers lifted, moving at `speed` px a second. */
  end: (moved: number, speed: number) => void;
  /** One step of a mouse wheel turned sideways. */
  notch?: (direction: number) => void;
}

/** Watch an element for sideways trackpad gestures. With `own`, the element
 *  never scrolls sideways by itself: every sideways wheel event is this
 *  hook's. Without it only a gesture `begin` accepted is taken from the
 *  browser. Up-and-down scrolling is never touched. */
export function observeSwipe(element: HTMLElement, handlers: SwipeHandlers, own: boolean): () => void {
  const gestures = WheelGestures({ reverseSign: false, preventWheelAction: false });
  // "open": nothing sideways yet; "follow": ours until the fingers lift;
  // "spent": the rest of this gesture, momentum included, moves nothing.
  let phase: "open" | "follow" | "spent" | "left" = "left";
  let run = 0;
  let base = 0;
  const off = gestures.on("wheel", state => {
    if (state.isStart) { phase = "open"; run = 0; }
    const [x] = state.axisMovement;
    if (phase === "follow") {
      if (state.isMomentum || state.isEnding) { phase = "spent"; handlers.end(x - base, state.axisVelocity[0] * 1000); return; }
      handlers.move(x - base);
      return;
    }
    if (phase !== "open" || state.isEnding) return;
    // Inertia from an up-and-down scroll never starts a sideways move.
    if (state.isMomentum) { run = 0; return; }
    const [dx, dy] = state.axisDelta;
    if (Math.abs(dx) <= Math.abs(dy)) { run = 0; return; }
    if (Math.abs(dx) >= NOTCH && handlers.notch) { phase = "spent"; handlers.notch(Math.sign(dx)); return; }
    run += dx;
    if (Math.abs(run) < TAKES) return;
    if (!handlers.begin(Math.sign(run))) { phase = "left"; return; }
    phase = "follow";
    base = x - run;
    handlers.move(run);
  });
  gestures.observe(element);
  // Added after the observer, so the phase is already this event's.
  const onWheel = (event: WheelEvent) => {
    const sideways = Math.abs(event.deltaX) > Math.abs(event.deltaY);
    if (phase === "follow" || phase === "spent" ? sideways || phase === "follow" : own && sideways) event.preventDefault();
  };
  element.addEventListener("wheel", onWheel, { passive: false });
  return () => { off(); gestures.disconnect(); element.removeEventListener("wheel", onWheel); };
}
