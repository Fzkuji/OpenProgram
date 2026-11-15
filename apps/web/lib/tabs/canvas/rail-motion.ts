/** Whether a sidebar or panel is moving: a rail's shell (`.rail-shell`,
 *  components/layout/use-resizable-rail.ts) changes its width on a CSS
 *  transition, and the centre column, its panes and everything in them
 *  resize with it on every frame of the move. Read from the transition's
 *  own events, which the browser fires as it starts and ends or cancels
 *  the width's transition, so asking costs no style or layout read.
 *  Unlike canvas-motion.ts this changes nothing about how panes draw
 *  while it moves (native pages keep following); it is for work that can
 *  wait for the move to end, such as an editor measuring its text. */

const RAIL_SHELL = "rail-shell";
/** Width transitions running, per rail shell. A reversal cancels one and
 *  starts another, so the count, not a flag, says whether one runs. */
const running = new Map<Element, number>();
const listeners = new Set<() => void>();
let installed = false;

function railOf(event: Event): Element | null {
  const target = event.target as Element | null;
  if ((event as TransitionEvent).propertyName !== "width") return null;
  return target?.classList?.contains(RAIL_SHELL) ? target : null;
}

/** A shell taken out of the document gets no end for its transition. */
function prune() {
  for (const shell of running.keys()) if (!shell.isConnected) running.delete(shell);
}

export function railsMoving(): boolean {
  install();
  prune();
  return running.size > 0;
}

function update(shell: Element, delta: 1 | -1) {
  const before = railsMoving();
  const next = (running.get(shell) ?? 0) + delta;
  if (next > 0) running.set(shell, next);
  else running.delete(shell);
  if (railsMoving() !== before) listeners.forEach((listener) => listener());
}

const onRun = (event: Event) => { const shell = railOf(event); if (shell) update(shell, 1); };
const onStop = (event: Event) => { const shell = railOf(event); if (shell) update(shell, -1); };

function install() {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("transitionrun", onRun, true);
  document.addEventListener("transitionend", onStop, true);
  document.addEventListener("transitioncancel", onStop, true);
}

/** Called when a rail starts moving or the last moving one stops. */
export function onRailMotionChange(listener: () => void): () => void {
  install();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
