/**
 * Get each character's shape ready before it is drawn.
 *
 * The renderer prepares a shape the first time it draws it: it infers the
 * shape's volume from its outline, 25 to 90 ms a shape, and keeps the
 * result for the page's life. A menu that shows seventeen Agents for the
 * first time asked for all of that in one frame, over half a second with
 * the page frozen. Here the shapes are prepared one per task instead: the
 * ones about to show first, the rest of the catalogue in idle time once
 * the renderer has loaded, so a list usually opens with every shape
 * already prepared. Until its shape is ready a character shows as its
 * flat glyph (./glyph.ts) in its colours, the same size and place.
 */
import { BOT_SHAPES } from "./catalog";
import type { BotRuntime } from "./runtime";

const ready = new Set<string>();
const listeners = new Set<() => void>();
/** Shapes waiting, the wanted ones first. */
let queue: string[] = [];
/** Shapes a character on the page is waiting for. */
const wanted = new Set<string>();
let scheduled = false;
let runtime: BotRuntime | null = null;

export function shapeReady(shape: string): boolean {
  return ready.has(shape);
}

export function watchShapes(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function prepareNext(): void {
  scheduled = false;
  const shape = queue.shift();
  if (!shape || !runtime) return;
  wanted.delete(shape);
  if (!ready.has(shape)) {
    try {
      runtime.prepare(shape);
    } catch {
      // A shape that cannot be prepared is drawn by the renderer's own
      // fallback; waiting on it would leave the glyph up for good.
    }
    ready.add(shape);
    for (const listener of [...listeners]) listener();
  }
  schedule();
}

type Idle = (run: () => void, options?: { timeout: number }) => number;

/** One shape per task: a wanted one in the next task, after whatever the
 *  page already queued; the rest in idle time. */
function schedule(): void {
  if (scheduled || !queue.length || !runtime) return;
  scheduled = true;
  const idle = (globalThis as { requestIdleCallback?: Idle }).requestIdleCallback;
  if (wanted.has(queue[0]) || !idle) setTimeout(prepareNext, 0);
  else idle(prepareNext, { timeout: 3000 });
}

/** Ask for `shape` before the shapes merely waiting. */
export function wantShape(shape: string): void {
  if (ready.has(shape)) return;
  wanted.add(shape);
  queue = [...queue.filter((queued) => wanted.has(queued) && queued !== shape), shape,
    ...queue.filter((queued) => !wanted.has(queued))];
  schedule();
}

/** Start preparing once the renderer is in: wanted shapes first, then the
 *  whole catalogue in idle time. */
export function startWarming(next: BotRuntime): void {
  if (runtime) return;
  runtime = next;
  queue = [...queue, ...BOT_SHAPES.filter((shape) => !queue.includes(shape))];
  schedule();
}

/** Test seam: forget what was prepared. */
export function resetWarming(): void {
  ready.clear();
  queue = [];
  wanted.clear();
  scheduled = false;
  runtime = null;
}
