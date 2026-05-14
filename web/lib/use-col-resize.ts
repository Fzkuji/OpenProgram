"use client";

/**
 * Column-resize handle hook.
 *
 * Wraps a small chunk of pointer plumbing: grab `mousedown` on the
 * handle element, drag with `mousemove` to update the target's
 * `width`, stop on `mouseup`. While dragging we suppress text
 * selection + flip the page cursor so it doesn't flicker into the
 * default arrow over text.
 *
 * `direction`:
 *   `+1`  → handle sits to the RIGHT of the target (e.g. left sidebar
 *           — drag right grows it).
 *   `-1`  → handle sits to the LEFT  of the target (e.g. right
 *           detail panel — drag left grows it).
 *
 * Migrated from the IIFE at the bottom of `web/public/js/chat/init.js`.
 */

import { useEffect } from "react";

interface ColResizeOptions {
  /** id of the draggable handle element. */
  handleId: string;
  /** id of the element whose width should change while dragging. */
  targetId: string;
  /** `+1` if dragging right grows the target, `-1` if dragging left grows it. */
  direction: 1 | -1;
  /** Lower bound on the target's width (px). */
  minWidth: number;
}

export function useColResize(opts: ColResizeOptions): void {
  useEffect(() => {
    const handle = document.getElementById(opts.handleId);
    if (!handle) return;
    const onMouseDown = (ev: MouseEvent) =>
      startDrag(ev, handle, opts);
    handle.addEventListener("mousedown", onMouseDown);
    return () => handle.removeEventListener("mousedown", onMouseDown);
  }, [opts.handleId, opts.targetId, opts.direction, opts.minWidth, opts]);
}

function startDrag(
  ev: MouseEvent,
  handle: HTMLElement,
  { targetId, direction, minWidth }: ColResizeOptions,
): void {
  const node = document.getElementById(targetId);
  if (!node) return;
  // Capture as a non-null local so the nested handler closures get a
  // narrowed type without TS losing the narrowing across the closure
  // boundary.
  const target: HTMLElement = node;
  ev.preventDefault();

  const startX = ev.clientX;
  const startW = target.offsetWidth;
  applyDragChrome(handle, target);

  function onMove(move: MouseEvent) {
    const dx = move.clientX - startX;
    const next = Math.max(minWidth, startW + dx * direction);
    target.style.width = `${next}px`;
  }

  function onUp() {
    clearDragChrome(handle, target);
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  }

  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function applyDragChrome(handle: HTMLElement, target: HTMLElement): void {
  handle.classList.add("dragging");
  // Suppress the width-transition that normally smooths sidebar
  // collapse — during a manual drag we want pixel-accurate
  // tracking, not a lag.
  target.style.transition = "none";
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
}

function clearDragChrome(handle: HTMLElement, target: HTMLElement): void {
  handle.classList.remove("dragging");
  target.style.transition = "";
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
}
