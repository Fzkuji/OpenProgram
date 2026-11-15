/**
 * Canvas keyboard (dag/rendering.md §13): the camera keys, Delete,
 * Esc and ⌘Z. Active only while the graph is on screen and the keyboard
 * is not in a text field — typing "v" in the composer is a "v".
 *
 *   F                camera to the next working agent (else HEAD)
 *   M                hide / show the minimap
 *   Shift+1 / Shift+0  fit the graph / zoom back to 100%
 *   Shift+2          fit the selection
 *   + / −            zoom in / out one step
 *   Delete / ⌫       delete the selection
 *   Esc              clear the selection (the key sheet is a guide and
 *                    stays until toggled); with none, the key falls
 *                    through to leave full screen (tabs/canvas/canvas-fullscreen.ts)
 *   ⌘Z / Ctrl+Z      undo the last delete in this session
 *   ?                open / close the key sheet
 */

import { clearSelection, dagVisible, isEditableFocus, selectedIds } from "./selection";
import { fitCanvas, focusAgent, frameSelection, resetZoom, zoomStep } from "../view/canvas";
import { canUndoDeletion, deleteSelection, undoDeletion } from "./node-delete";
import { keySheetOpen, setKeySheet } from "./key-sheet";
import { toggleMinimap } from "../view/minimap";

function onKeyDown(e: KeyboardEvent): void {
  if (e.defaultPrevented || isEditableFocus() || !dagVisible()) return;
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "z") {
    if (!canUndoDeletion()) return;
    e.preventDefault();
    undoDeletion();
    return;
  }
  if (mod || e.altKey) return;
  if (e.key === "Delete" || e.key === "Backspace") {
    if (!selectedIds().length) return;
    e.preventDefault();
    deleteSelection();
    return;
  }
  if (e.key === "?") {
    e.preventDefault();
    setKeySheet(!keySheetOpen());
    return;
  }
  if (e.key === "Escape") {
    if (selectedIds().length) {
      e.preventDefault();
      clearSelection();
    }
    return;
  }
  // View keys, as design tools have them. By physical key, so Shift+1
  // is Shift+1 on every layout ("!" on US, something else elsewhere).
  const view = e.shiftKey && e.code === "Digit1" ? fitCanvas
    : e.shiftKey && e.code === "Digit0" ? resetZoom
    : e.shiftKey && e.code === "Digit2" && selectedIds().length ? () => frameSelection(selectedIds())
    : e.key === "+" || e.key === "=" ? () => zoomStep(1)
    : e.key === "-" || e.key === "_" ? () => zoomStep(-1)
    : null;
  if (view) {
    e.preventDefault();
    view();
    return;
  }
  if (e.shiftKey || e.repeat) return;
  if (e.key.toLowerCase() === "f") {
    e.preventDefault();
    focusAgent();
    return;
  }
  if (e.key.toLowerCase() === "m") {
    e.preventDefault();
    toggleMinimap();
  }
}

if (typeof document !== "undefined") {
  document.addEventListener("keydown", onKeyDown);
}
