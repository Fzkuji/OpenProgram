/**
 * History DAG renderer — SVG primitives + per-node shape / colour helpers.
 *
 * Pure functions; the only module-level state is the text-width cache
 * and the canvas it measures on. The node tiles themselves are drawn by
 * ``./tiles``. See ``../README.md``.
 */

import { type GNode, LANE_COLORS } from "../types";

export function _svg(
  tag: string,
  attrs?: Record<string, string | number>,
): SVGElement {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (attrs) {
    Object.keys(attrs).forEach((k) => el.setAttribute(k, String(attrs[k])));
  }
  return el as SVGElement;
}

/** Smooth S-curve between two points (vertical adjacent → straight). */
export function _edgePath(x1: number, y1: number, x2: number, y2: number): string {
  if (x1 === x2) return "M" + x1 + "," + y1 + " L" + x2 + "," + y2;
  const my = (y1 + y2) / 2;
  return (
    "M" + x1 + "," + y1 + " C" + x1 + "," + my + " " + x2 + "," + my + " " + x2 + "," + y2
  );
}

export function _branchColor(
  node: GNode,
  _leafOfNode?: Record<string, string>,
): string {
  void _leafOfNode;
  // The road to HEAD wears HEAD's colour, shared turns included, so the
  // branch you are on reads as one line from the root (rendering.md §5).
  const lane = node._pathLane ?? node._lane ?? 0;
  if (lane === 0) return LANE_COLORS[0];
  return LANE_COLORS[1 + ((lane - 1) % (LANE_COLORS.length - 1))];
}

// 量文字实际像素宽（复用一个 canvas）。按 label.length*6 估对中文
// （字宽≈字号）严重低估，背景比文字短、文字溢出，实测才中英文都贴合。
// 注意 canvas 的 ctx.font 不认 CSS 变量——含 var() 的整串会被拒收，
// 静默退回默认 "10px sans-serif"，所以调用方必须写死字体栈。
let _measureCtx: CanvasRenderingContext2D | null = null;
let _measureFont = "";
const _widths = new Map<string, number>();

export function _textWidth(s: string, font: string): number {
  if (typeof document === "undefined") return s.length * 8;
  if (!_measureCtx) {
    _measureCtx = document.createElement("canvas").getContext("2d");
  }
  if (!_measureCtx) return s.length * 8; // 拿不到 canvas 时保守按 8px/字
  // Every capture re-measures every label (and a truncation measures
  // each shorter prefix), so widths are kept; setting ``font`` parses
  // it, so it is set only when it changes.
  const key = font + "\0" + s;
  let w = _widths.get(key);
  if (w === undefined) {
    if (_measureFont !== font) _measureCtx.font = _measureFont = font;
    w = _measureCtx.measureText(s).width;
    if (_widths.size >= 20000) _widths.clear();
    _widths.set(key, w);
  }
  return w;
}
