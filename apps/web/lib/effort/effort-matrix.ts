/** Effort slider geometry shared with the Radix thumb. */
export const THUMB_WIDTH = 16;
/** Track (and thumb) height, px. */
export const TRACK_HEIGHT = 20;

/** X of the thumb's centre for option `index` of `count` on a track
 *  `trackWidth` px wide — Radix's own thumb placement. */
export function thumbCenterX(index: number, count: number, trackWidth: number): number {
  if (count <= 1) return trackWidth / 2;
  const ratio = Math.min(1, Math.max(0, index / (count - 1)));
  return ratio * (trackWidth - THUMB_WIDTH) + THUMB_WIDTH / 2;
}

/** How the "Recommended" caption hangs under its option: the first option
 *  aligns with the track's left edge, the last with its right edge, and
 *  anything between is centred on the thumb position. */
export function captionAlignment(index: number, count: number): "start" | "center" | "end" {
  if (count <= 1) return "center";
  if (index <= 0) return "start";
  if (index >= count - 1) return "end";
  return "center";
}
