/**
 * Where a drawn group sits, in world units.
 *
 * Every node, agent token and branch tag is a ``<g>`` placed by a
 * ``translate(x,y)`` on its ``transform`` (render/nodes.ts, agents.ts).
 * The passes that run after drawing — captions, agent markers, the
 * camera, label culling, the minimap — read positions back from there
 * instead of carrying the layout around, so they share this one reader.
 */

const TRANSLATE = /translate\(\s*([-\d.]+)[ ,]+([-\d.]+)/;

export interface Seat { x: number; y: number }

export function seatOf(el: Element | null | undefined): Seat | null {
  const m = TRANSLATE.exec(el?.getAttribute("transform") || "");
  return m ? { x: Number(m[1]), y: Number(m[2]) } : null;
}
