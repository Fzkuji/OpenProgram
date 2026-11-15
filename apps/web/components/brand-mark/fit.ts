/**
 * The one sizing rule for every brand mark (provider logos, channel logos,
 * Agent characters in a control).
 *
 * A mark is sized by its ink, the part of the drawing that is painted, not
 * by its view box: the logos leave different margins around their marks.
 * The ink fills the box: scaled until its longer side is the box's side,
 * with its centre on the box's centre. So every mark is a 16 x 16 rectangle
 * in a 16 px box, and its ink touches two opposite edges of it: a square
 * mark is 16 x 16, a wide one 16 wide, a tall one 16 high.
 */

/** Where a drawing's ink sits, in the drawing's own view-box units. */
export interface InkBounds {
  x: number;
  y: number;
  width: number;
  height: number;
  /** The drawing's view box: [min-x, min-y, width, height]. */
  viewBox: readonly [number, number, number, number] | readonly number[];
}

/** Where the whole drawing goes so its ink fits a box: its size and
 *  top-left corner in px, relative to the box. */
export interface InkFit {
  /** px per view-box unit. */
  scale: number;
  left: number;
  top: number;
  width: number;
  height: number;
}

const tidy = (value: number) => Math.round(value * 100) / 100;

export function fitInk(ink: InkBounds, size: number): InkFit {
  const [minX, minY, boxWidth, boxHeight] = ink.viewBox;
  const scale = size / Math.max(ink.width, ink.height);
  const centreX = ink.x - minX + ink.width / 2;
  const centreY = ink.y - minY + ink.height / 2;
  return {
    scale,
    left: tidy(size / 2 - centreX * scale),
    top: tidy(size / 2 - centreY * scale),
    width: tidy(boxWidth * scale),
    height: tidy(boxHeight * scale),
  };
}

/** The ink of a drawing whose ink fills its whole view box. */
export function wholeViewBox(width = 24, height = 24): InkBounds {
  return { x: 0, y: 0, width, height, viewBox: [0, 0, width, height] };
}
