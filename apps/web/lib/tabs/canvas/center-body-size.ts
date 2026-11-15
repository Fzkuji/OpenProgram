import { create } from "zustand";
import { canvasGeometry, canvasViewport, type LayoutNode } from "./canvas-layout";
import { CANVAS_FULLSCREEN_EVENT, canvasCoversWindow } from "./canvas-fullscreen";
import { createSplitLayoutMeasureScheduler } from "./split-layout";

/** The centre body's measured size. A rail that slides changes it on every
 *  frame, so it lives here rather than in the shell's state: readers
 *  subscribe to what they decide from it (whether split fits, which panes are
 *  too small to show), and those answers change only at their thresholds, so
 *  the shell and its subtree do not render again for each frame of the move.
 *  Code that needs the pixels at the moment of an event reads `getState()`. */
export const useCenterBodySize = create<{ width: number; height: number }>(() => ({ width: 0, height: 0 }));

export function setCenterBodySize(width: number, height: number) {
  const current = useCenterBodySize.getState();
  if (current.width !== width || current.height !== height) useCenterBodySize.setState({ width, height });
}

/** Below this a pane shows only its zoom button: there is no room for content. */
export const tooSmallForPane = (rect: { width: number; height: number }) => rect.width < 150 || rect.height < 110;

/** The panes of `root` too small to show at this size, as a stable key (ids
 *  joined by spaces, "viewport" when the whole canvas is), so a selector that
 *  returns it changes only when a pane crosses the threshold. */
export function smallCanvasPanes(root: LayoutNode, width: number, height: number): string {
  const viewport = canvasViewport(width, height);
  const small = [...canvasGeometry(root, viewport).panes].filter(([, rect]) => tooSmallForPane(rect)).map(([id]) => id);
  if (tooSmallForPane(viewport)) small.push("viewport");
  return small.join(" ");
}

/** Keeps the store at `body`'s size. The size comes from the
 *  ResizeObserver's entries, which the browser hands over after its layout
 *  is done, so nothing here reads layout while a rail moves. It used to read
 *  `getBoundingClientRect()` in an animation frame (or a zero timer) queued
 *  from the observer and from every transitionend under the shell: in the
 *  next frame of a sidebar or panel move that read ran before the frame's
 *  own layout and forced it, 3 to 5.6 ms (traced). Full screen lifts the
 *  body out to the window and back; nothing lays out by that size meanwhile
 *  (the canvas controls hide), so its sizes are skipped, and when full
 *  screen ends the body is measured once, at rest. */
export function watchCenterBodySize(body: Element): () => void {
  const measureAtRest = createSplitLayoutMeasureScheduler(() => {
    if (canvasCoversWindow()) return;
    const box = body.getBoundingClientRect();
    setCenterBodySize(box.width, box.height);
  });
  const observer = new ResizeObserver((entries) => {
    if (canvasCoversWindow()) return;
    const entry = entries[entries.length - 1];
    if (!entry) return;
    const size = borderBox(entry);
    setCenterBodySize(size.width, size.height);
  });
  observer.observe(body, { box: "border-box" });
  window.addEventListener(CANVAS_FULLSCREEN_EVENT, measureAtRest.schedule);
  return () => {
    observer.disconnect();
    window.removeEventListener(CANVAS_FULLSCREEN_EVENT, measureAtRest.schedule);
    measureAtRest.cancel();
  };
}

/** An entry's border box, the size `getBoundingClientRect()` gives an
 *  untransformed element. */
function borderBox(entry: ResizeObserverEntry): { width: number; height: number } {
  const box = entry.borderBoxSize?.[0];
  return box ? { width: box.inlineSize, height: box.blockSize } : { width: entry.contentRect.width, height: entry.contentRect.height };
}
