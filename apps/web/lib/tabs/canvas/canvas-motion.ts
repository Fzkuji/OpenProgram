/** Whether the canvas's panes are moving: under a drag (canvas-drag.ts)
 *  or in an animated layout change (canvas-transition.ts, the full screen
 *  glide in canvas-fullscreen.ts). While they move, native web panes hold
 *  a snapshot and chats re-wrap only the rows in view. Free of the tab
 *  stores, so any of them can report motion without pulling those in. */
let dragging = false;
let animating = false;
const listeners = new Set<() => void>();

export function canvasPanesMoving() { return dragging || animating; }

function set(update: () => void) {
  const before = canvasPanesMoving();
  update();
  if (canvasPanesMoving() !== before) listeners.forEach((listener) => listener());
}

/** A drag holds the canvas. */
export function setCanvasDragFlag(value: boolean) {
  if (dragging !== value) set(() => { dragging = value; });
}

/** An animated layout change is moving the panes. */
export function setCanvasAnimating(value: boolean) {
  if (animating !== value) set(() => { animating = value; });
}

/** Called when the panes start or stop moving. */
export function onCanvasMotionChange(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
