/**
 * History DAG renderer — SVG primitives + per-node shape / colour helpers.
 *
 * Pure functions, no module-level state. Shapes encode the node kind
 * defined in ``docs/design/runtime/dag-node-model.md`` (3 kinds × function class
 * → 6 visual shapes).
 *
 * Moved from ``../history/shapes.ts``; see ``./README.md``.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, LANE_COLORS, NODE_R } from "./types";

export const CURSOR_R = NODE_R * 0.55;

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

export function _treeEdgePath(x1: number, y1: number, x2: number, y2: number): string {
  const nr = NODE_R + 4;
  const endX = x2 > x1 ? x2 - nr : x2 + nr;
  return "M" + x1 + "," + y1 + " L" + x1 + "," + y2 + " L" + endX + "," + y2;
}

export function _branchColor(
  node: GNode,
  _leafOfNode?: Record<string, string>,
): string {
  void _leafOfNode;
  const lane = node._lane || 0;
  if (lane === 0) return LANE_COLORS[0];
  return LANE_COLORS[1 + ((lane - 1) % (LANE_COLORS.length - 1))];
}

const BRANCH_OP_FUNCTIONS = new Set([
  "attach",
  "merge",
  "task",
]);

export function _shapeFor(node: GNode): string {
  if (node.display === "root") return "diamond";
  const role = node.role;
  const fn = node.function;
  if (fn && BRANCH_OP_FUNCTIONS.has(fn)) return "square_outline";
  if (
    node.display === "runtime"
    && fn
    && !BRANCH_OP_FUNCTIONS.has(fn)
  ) {
    return "square";
  }
  if (role === "tool") return "square";
  if (role === "assistant") return "triangle";
  if (role === "user") return "circle";
  return "circle";
}

export function _applyShapeSize(shape: SVGElement): void {
  const R = NODE_R + 1.8;
  if (shape.tagName === "circle") {
    shape.setAttribute("r", String(R));
  } else if (shape.tagName === "polygon") {
    shape.setAttribute("points", _regularPolygon(3, R * TRI_SCALE, -Math.PI / 2));
  } else if (shape.tagName === "rect") {
    const s = R * SQR_SCALE;
    shape.setAttribute("x", String(-s));
    shape.setAttribute("y", String(-s));
    shape.setAttribute("width", String(s * 2));
    shape.setAttribute("height", String(s * 2));
  }
}

// Shape sizing: all shapes share the same reference circle of radius R.
//   circle:   radius = R (the baseline)
//   square:   half-side = R (edges touch the circle, corners poke out to R√2)
//   triangle: circumradius = R * 1.35 (corners poke out, edges sit inside)
//   diamond:  same as square, rotated 45°
// This gives a balanced look: each shape's edges sit near the circle
// boundary, with corners slightly outside.
const STROKE_W = 3.0;
const TRI_SCALE = 1.35;
const SQR_SCALE = 1.0;

export function _buildShapeEl(
  shape: string,
  color: string,
  r: number,
): SVGElement | null {
  const common = { fill: "transparent", stroke: color, "stroke-width": String(STROKE_W) };

  if (shape === "circle") {
    return _svg("circle", { r, ...common });
  } else if (shape === "triangle") {
    const pts = _regularPolygon(3, r * TRI_SCALE, -Math.PI / 2);
    return _svg("polygon", { points: pts, "stroke-linejoin": "round", ...common });
  } else if (shape === "square") {
    const s = r * SQR_SCALE;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 0, ry: 0, ...common,
    });
  } else if (shape === "square_outline") {
    const s = r * SQR_SCALE;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 1.2, ry: 1.2,
      fill: "var(--bg-secondary, #1a1a1a)",
      stroke: color, "stroke-width": "1.5",
      "stroke-dasharray": "3 2",
    });
  } else if (shape === "diamond") {
    const s = r * SQR_SCALE;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 0.6, ry: 0.6,
      transform: "rotate(45)",
      "stroke-linejoin": "round",
      ...common,
    });
  }
  return null;
}

function _regularPolygon(sides: number, r: number, startAngle: number): string {
  const pts: string[] = [];
  for (let i = 0; i < sides; i++) {
    const a = startAngle + (2 * Math.PI * i) / sides;
    pts.push(+(r * Math.cos(a)).toFixed(2) + "," + +(r * Math.sin(a)).toFixed(2));
  }
  return pts.join(" ");
}

export function _shapeTypeFromTag(tagName: string): string {
  if (tagName === "polygon") return "triangle";
  if (tagName === "rect") return "square";
  return "circle";
}
