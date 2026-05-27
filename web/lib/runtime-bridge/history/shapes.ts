/**
 * History DAG renderer — SVG primitives + per-node shape / colour helpers.
 *
 * Pure functions, no module-level state. Shapes encode the node kind
 * defined in ``docs/design/dag-node-model.md`` (3 kinds × function class
 * → 6 visual shapes).
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

/** Per-branch colour driven directly by ``_lane``. Lane 0 (the trunk)
 *  is always LANE_COLORS[0]; lane k uses LANE_COLORS[1 + (k-1) % N].
 *  Driving from the lane index (rather than a leaf-id hash) means
 *  every node on the same lane gets the same fill — tool calls on
 *  a sub branch never split into a second colour, even when the
 *  trunk walk happened to seize the sub branch's tip as its leaf
 *  label after a HEAD switch.
 *
 *  ``leafOfNode`` is kept in the signature for callers that still
 *  pass it (no-op now). */
export function _branchColor(
  node: GNode,
  _leafOfNode?: Record<string, string>,
): string {
  void _leafOfNode;
  const lane = node._lane || 0;
  if (lane === 0) return LANE_COLORS[0];
  return LANE_COLORS[1 + ((lane - 1) % (LANE_COLORS.length - 1))];
}

/**
 * Map a node to its shape token (consumed by ``_buildShapeEl``).
 *
 * Visual taxonomy (docs/design/dag-node-model.md):
 * | shape token      | node kind                          |
 * |------------------|------------------------------------|
 * | circle           | user_msg                           |
 * | triangle         | llm_reply                          |
 * | square           | inline tool (bash, read, ...) +    |
 * |                  | runtime-display rows               |
 * | square_outline   | branch-referencing (attach, merge) |
 * | diamond          | branch-creating (task spawn)       |
 */
// Functions whose semantics is "this node's real content lives on
// another branch" — task spawns one, attach pulls from one, merge
// folds one in. They all share the dashed-outline shape so the user
// can tell at a glance "this isn't a self-contained tool execution,
// it's a branch operation".
const BRANCH_OP_FUNCTIONS = new Set([
  "attach",
  "merge",
  "task",
]);

export function _shapeFor(node: GNode): string {
  const role = node.role;
  const fn = node.function;
  if (fn && BRANCH_OP_FUNCTIONS.has(fn)) return "square_outline";
  // Function-call runtime row (LLM-driven OR fn-form triggered) —
  // the node represents a tool/function invocation, so render as a
  // square regardless of role mapping. Without this both kinds of
  // runtime card render as triangles (because the persisted role
  // mapped to "assistant"), which misleads the reader into thinking
  // there's another LLM turn happening at that step. Guarded by
  // ``function`` being set + not a branch op so it doesn't catch
  // sub-agent user-msgs (those have display=runtime but no
  // ``function`` field).
  if (
    node.display === "runtime"
    && fn
    && !BRANCH_OP_FUNCTIONS.has(fn)
  ) {
    return "square";
  }
  // role drives the shape unconditionally — display=runtime used to
  // force square here, which mis-rendered sub-agent user_msgs
  // (role=user, display=runtime) as squares instead of circles.
  // runtime visibility belongs to the chat panel, not the DAG.
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
    const s = r - 0.2;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 0.8, ry: 0.8, fill: color,
    });
  } else if (shape === "square_outline") {
    // Branch-op nodes (task / attach / merge) — dashed outline, dark
    // fill so the node reads as "reference, not a new turn".
    const s = r - 0.2;
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 1.2, ry: 1.2,
      fill: "var(--bg-secondary, #1a1a1a)",
      stroke: color, "stroke-width": "1.5",
      "stroke-dasharray": "3 2",
    });
  } else if (shape === "diamond") {
    // Branch-creating function_call (task spawn) — rotated square /
    // diamond marks the fork point.
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
