export type Size = { width: number; height: number };
export type Rect = { x: number; y: number; width: number; height: number };
export type OperationPoint = { x: number; y: number; width?: number; height?: number };

/** Letterboxed contain-fit of an image inside a host rectangle. */
export function fittedImageRect(container: Size, image: Size): Rect {
  if (container.width <= 0 || container.height <= 0 || image.width <= 0 || image.height <= 0) {
    return { x: 0, y: 0, width: 0, height: 0 };
  }
  const scale = Math.min(container.width / image.width, container.height / image.height);
  const width = image.width * scale;
  const height = image.height * scale;
  return {
    x: (container.width - width) / 2,
    y: (container.height - height) / 2,
    width,
    height,
  };
}

/**
 * Map a pixel point in the original viewport onto the fitted screenshot.
 * `point.width/height` or `viewport` is the actual image/viewport size, never percents.
 */
export function mapOperationPoint(
  point: OperationPoint,
  fitted: Rect,
  viewport?: Size,
): { left: number; top: number } | null {
  const vw = point.width || viewport?.width;
  const vh = point.height || viewport?.height;
  if (!vw || !vh || vw <= 0 || vh <= 0 || fitted.width <= 0 || fitted.height <= 0) return null;
  const nx = point.x / vw;
  const ny = point.y / vh;
  if (nx < 0 || ny < 0 || nx > 1 || ny > 1) return null;
  return {
    left: fitted.x + nx * fitted.width,
    top: fitted.y + ny * fitted.height,
  };
}
