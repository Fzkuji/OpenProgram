import type { EditorView } from "@codemirror/view";
import { canvasPanesMoving, onCanvasMotionChange } from "@/lib/tabs/canvas/canvas-motion";
import { onRailMotionChange, railsMoving } from "@/lib/tabs/canvas/rail-motion";

type MeasureRequest = Parameters<EditorView["requestMeasure"]>[0];

/** What says the panes are resizing, and when that changes. */
export interface PaneMotion {
  moving(): boolean;
  onChange(listener: () => void): () => void;
}

/** The panes resize while a rail moves (rail-motion.ts) or the canvas moves
 *  or drags them (canvas-motion.ts). */
export const paneMotion: PaneMotion = {
  moving: () => railsMoving() || canvasPanesMoving(),
  onChange(listener) {
    const offRails = onRailMotionChange(listener);
    const offCanvas = onCanvasMotionChange(listener);
    return () => { offRails(); offCanvas(); };
  },
};

/** Holds an editor's measurements while the panes resize, and measures once
 *  after they stop.
 *
 *  CodeMirror watches its scroller with a ResizeObserver and, at most every
 *  50 ms, asks for a measurement, which it runs in an animation frame. While
 *  a sidebar or panel moves, the composer's input in each pane of a split
 *  view changes width in every frame, so every few frames that measurement
 *  read the editor's geometry before the frame's own layout, forcing style
 *  and layout early, about 7 ms (traced): long enough for a refresh to show
 *  a floating preview's native page ahead of its frame. Nothing visible
 *  needs it during the move: the browser wraps the text itself (the
 *  composer draws its caret and selection natively), and the measurement
 *  only brings the editor's own record of line heights and its viewport up
 *  to date. So a request made while the panes move is kept, not scheduled,
 *  and the kept requests are made in the frame after the move ends, when
 *  the layout is the one the panes rest at and nothing dirties it before
 *  the read. At rest every request goes through at once, as before. */
export function holdMeasureWhilePanesMove(
  view: Pick<EditorView, "requestMeasure">,
  motion: PaneMotion,
  frames: { request(callback: () => void): number; cancel(id: number): void } = {
    request: (callback) => requestAnimationFrame(callback),
    cancel: (id) => cancelAnimationFrame(id),
  },
): () => void {
  const own = view.requestMeasure;
  // The requests held, in order; `undefined` is a plain re-measure.
  let held: Set<MeasureRequest> | null = null;
  let frame = 0;
  const release = () => {
    const requests = held;
    held = null;
    requests?.forEach((request) => own.call(view, request));
  };
  view.requestMeasure = (request?: MeasureRequest) => {
    if (!motion.moving()) {
      own.call(view, request);
      return;
    }
    (held ??= new Set()).add(request);
  };
  const offMotion = motion.onChange(() => {
    if (motion.moving() || !held || frame) return;
    frame = frames.request(() => {
      frame = 0;
      if (!motion.moving()) release();
    });
  });
  return () => {
    offMotion();
    if (frame) frames.cancel(frame);
    frame = 0;
    held = null;
    view.requestMeasure = own;
  };
}
