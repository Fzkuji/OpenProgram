/**
 * Mouse wheel or trackpad (dag/rendering.md §2): a wheel zooms, the way a
 * map does, while two fingers on a trackpad pan, the way a design canvas
 * does — a pinch still zooms. Both arrive as ``wheel`` events, so the
 * kind is read from the event itself:
 *
 *   * a pinch carries ``ctrlKey`` (⌘ + wheel zooms as well);
 *   * a sideways delta is two fingers, no wheel moves sideways on its own;
 *   * Chromium (and so the App) reports a trackpad's legacy
 *     ``wheelDeltaY`` as exactly ``-3 × deltaY``, while a wheel's notches
 *     come as multiples of 120.
 *
 * A gesture is judged on its first event and keeps that verdict until the
 * events stop, so a trackpad's slow inertial tail never flips to zoom.
 */

export type WheelKind = "pinch" | "pan" | "zoom";

const GESTURE_GAP_MS = 180;
let _last = 0;
let _kind: WheelKind = "zoom";

function looksLikeTrackpad(e: WheelEvent): boolean {
  if (e.deltaMode !== 0) return false;
  if (e.deltaX !== 0) return true;
  const legacy = (e as WheelEvent & { wheelDeltaY?: number }).wheelDeltaY;
  return typeof legacy === "number" && legacy !== 0 && legacy === -3 * e.deltaY;
}

export function wheelKind(e: WheelEvent): WheelKind {
  if (e.ctrlKey || e.metaKey) return "pinch";
  const now = e.timeStamp || performance.now();
  if (now - _last > GESTURE_GAP_MS) _kind = looksLikeTrackpad(e) ? "pan" : "zoom";
  else if (_kind === "zoom" && e.deltaX !== 0) _kind = "pan";
  _last = now;
  return _kind;
}
