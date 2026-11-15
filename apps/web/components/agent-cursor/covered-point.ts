/**
 * Whether a point of the control overlay lies under a window in front of
 * the controlled one. `occluders` are [x, y, width, height] rectangles in
 * the overlay's own coordinates; the agent cursor hides while its point is
 * under one, so it never shows on top of another app.
 */
export function coveredPoint(occluders: readonly (readonly number[])[], x: number, y: number): boolean {
  return occluders.some(([ox, oy, ow, oh]) => x >= ox && x < ox + ow && y >= oy && y < oy + oh);
}
