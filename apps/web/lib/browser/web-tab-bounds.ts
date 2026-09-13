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

export function isWebTabOccluded(
  bounds: WebTabBounds,
  occluders: Iterable<Pick<Element, "getBoundingClientRect">>,
): boolean {
  const right = bounds.x + bounds.width;
  const bottom = bounds.y + bounds.height;
  for (const occluder of occluders) {
    const rect = occluder.getBoundingClientRect();
    if (
      rect.width > 0
      && rect.height > 0
      && Math.max(bounds.x, rect.left) < Math.min(right, rect.right)
      && Math.max(bounds.y, rect.top) < Math.min(bottom, rect.bottom)
    ) {
      return true;
    }
  }
  return false;
}
