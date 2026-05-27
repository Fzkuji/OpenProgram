/**
 * History DAG renderer — SVG primitives + per-node shape / colour helpers.
 *
 * Pure functions, no module-level state. Shapes encode the node kind
 * defined in ``docs/design/dag-node-model.md`` (3 kinds × function class
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

export function _applyShapeSize(shape: SVGElement, isCurrent: boolean): void {
  const r = isCurrent ? NODE_R + 1.8 : NODE_R;
  if (shape.tagName === "circle") {
    shape.setAttribute("r", String(r));
  } else if (shape.tagName === "polygon") {
    const t = r * 1.5;
    const COS30 = 0.8660254;
    shape.setAttribute(
      "points",
      "0," + -t + " " + t * COS30 + "," + t * 0.5 + " " + -t * COS30 + "," + t * 0.5,
    );
  } else if (shape.tagName === "rect") {
    const s = r - 0.2;
    shape.setAttribute("x", String(-s));
    shape.setAttribute("y", String(-s));
    shape.setAttribute("width", String(s * 2));
    shape.setAttribute("height", String(s * 2));
  }
}

export function _buildShapeEl(
  shape: string,
  color: string,
  r: number,
): SVGElement | null {
  if (shape === "circle") {
    return _svg("circle", { r, fill: color });
  } else if (shape === "triangle") {
    const t = r * 1.5;
    const COS30 = 0.8660254;
    return _svg("polygon", {
      points:
        "0," + -t + " " + t * COS30 + "," + t * 0.5 + " " + -t * COS30 + "," + t * 0.5,
      fill: color,
    });
  } else if (shape === "square") {
    // Sharp corners (rx=0) so a small function-call square reads as
    // clearly DIFFERENT from a user-msg circle at the tight scale
    // mini-DAG renders at — a 13×13 rect with rx=0.8 looked too round
    // and users mistook collapsed function-call nodes for user dots.
    const s = r - 0.2;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 0, ry: 0, fill: color,
    });
  } else if (shape === "square_outline") {
    const s = r - 0.2;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 1.2, ry: 1.2,
      fill: "var(--bg-secondary, #1a1a1a)",
      stroke: color, "stroke-width": "1.5",
      "stroke-dasharray": "3 2",
    });
  } else if (shape === "diamond") {
    const t = r * 1.15;
    return _svg("polygon", {
      points: "0," + -t + " " + t + ",0 0," + t + " " + -t + ",0",
      fill: color,
    });
  }
  return null;
}

export function _shapeTypeFromTag(tagName: string): string {
  if (tagName === "polygon") return "triangle";
  if (tagName === "rect") return "square";
  return "circle";
}
