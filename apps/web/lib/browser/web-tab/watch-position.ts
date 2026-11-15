/** A box as the viewport sees it. */
export interface ViewportBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

const same = (a: ViewportBox, b: ViewportBox) =>
  a.left === b.left && a.top === b.top && a.width === b.width && a.height === b.height;

/**
 * Calls `onMove` with the element's new box whenever it moves or resizes in
 * the viewport, whatever moved it: a pane shifted by something above it, a
 * bar that went away, without any size changing. Nothing is measured to
 * find out. An IntersectionObserver's root is shrunk to the element's own
 * box (whole pixels), so the element reaches outside it, and the
 * intersection ratio drops below what it was, as soon as it moves; the
 * observer reports the new box, already laid out, and is set up again
 * around it. Moves under a pixel go unseen.
 */
export function watchPosition(
  element: Element,
  onMove: (box: ViewportBox) => void,
  start: ViewportBox = element.getBoundingClientRect(),
): () => void {
  if (typeof IntersectionObserver === "undefined") return () => {};
  let observer: IntersectionObserver | null = null;
  let disposed = false;

  const arm = (box: ViewportBox, threshold: number, tuned = false) => {
    observer?.disconnect();
    observer = null;
    if (disposed) return;
    const inset = (value: number) => `${-Math.floor(value)}px`;
    // An empty or unseen element has no box to shrink to: any change in
    // whether it shows is reported, and it is watched again from there.
    const empty = box.width <= 0 || box.height <= 0;
    const rootMargin = empty ? "0px" : [
      inset(box.top),
      inset(window.innerWidth - (box.left + box.width)),
      inset(window.innerHeight - (box.top + box.height)),
      inset(box.left),
    ].join(" ");
    let first = true;
    observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (!entry) return;
      const next = entry.boundingClientRect;
      const moved = !same(next, box);
      const ratio = entry.intersectionRatio;
      if (moved) onMove({ left: next.left, top: next.top, width: next.width, height: next.height });
      if (moved) {
        arm(next, 1);
        return;
      }
      if (first && !tuned && !empty && ratio !== threshold) {
        // Partly clipped (by the window edge or a scrolled ancestor), it
        // starts below a ratio of 1; that ratio is the one to leave.
        arm(next, Math.max(ratio, 1e-7), true);
        return;
      }
      first = false;
    }, { rootMargin, threshold: empty ? 0 : threshold });
    observer.observe(element);
  };

  arm(start, 1);
  return () => {
    disposed = true;
    observer?.disconnect();
    observer = null;
  };
}
