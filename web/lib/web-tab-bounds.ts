export interface WebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function measureWebTabBounds(
  element: Pick<Element, "getBoundingClientRect">,
): WebTabBounds {
  const rect = element.getBoundingClientRect();
  return {
    x: rect.left,
    y: rect.top,
    width: rect.width,
    height: rect.height,
  };
}

export function overlayIntersectsWebTab(
  bounds: WebTabBounds,
  overlays: Iterable<Pick<Element, "getBoundingClientRect">>,
): boolean {
  const pageRight = bounds.x + bounds.width;
  const pageBottom = bounds.y + bounds.height;
  for (const overlay of overlays) {
    const rect = overlay.getBoundingClientRect();
    if (
      rect.width > 0
      && rect.height > 0
      && rect.left < pageRight
      && rect.left + rect.width > bounds.x
      && rect.top < pageBottom
      && rect.top + rect.height > bounds.y
    ) {
      return true;
    }
  }
  return false;
}
