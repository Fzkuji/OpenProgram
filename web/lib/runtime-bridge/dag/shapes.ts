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

export function _shapeFor(node: GNode): string {
  if (node.display === "root") return "diamond";
  // Compaction summary → capsule (dag/rendering.md §9). It is a real
  // llm turn, but a turn that stands in for a whole span of turns, and
  // the rounded box is what says "this one is wider than it looks".
  // Keyed on ``covers_ids`` (the backend resolves it from
  // ``metadata.covers``) so the shape and the fold read one fact.
  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    return "capsule";
  }
  // Sub-agent branch root → a double ring, small (dag/rendering.md §12).
  // Not a shape from the role vocabulary at all: what hangs off it is
  // another agent's whole conversation, and a glyph that reads as one of
  // this chain's turns would say the opposite. The name rides beside it
  // as a caption, so the mark itself stays a point on the grid.
  if ((node as Record<string, unknown>).source === "agent_spawn"
      && !node.predecessor) {
    return "agent_dot";
  }
  const role = node.role;
  const fn = node.function;
  // merge 节点：实心带孔圆 ◉，落在 base 分支 lane 上（rendering.md
  // 场景 8）。attach 指针不画节点（drawNodes 里跳过）；task 是普通执行层
  // 方块——占位虚框（square_outline）已废除（规则② 推论）。
  if (fn === "merge") return "merge_dot";
  if (fn === "attach") return "square";
  // display=runtime nodes are function calls (square), not user msgs
  if (node.display === "runtime") return "square";
  // tool = function call → square
  if (role === "tool") return "square";
  // assistant with a function = RuntimeBlock placeholder → square
  if (role === "assistant" && fn) return "square";
  // assistant without function = LLM reply → triangle
  if (role === "assistant") return "triangle";
  // user = real user message → circle
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
    // The capsule is a rect too, but a wide one — re-squaring it here
    // (this runs on every white-fill flip) would snap it back to a
    // square the moment coverage changed. Its own geometry is set once,
    // at build time, and marked so this pass leaves it alone.
    if (shape.getAttribute("data-shape") === "capsule") return;
    const s = R * SQR_SCALE;
    shape.setAttribute("x", String(-s));
    shape.setAttribute("y", String(-s));
    shape.setAttribute("width", String(s * 2));
    shape.setAttribute("height", String(s * 2));
  }
}

// Capsule half-extents. Wide enough to read as a different shape from
// the square at a glance, short enough to sit on one grid row.
export const CAPSULE_HW = (NODE_R + 1.8) * 2.1;
export const CAPSULE_HH = (NODE_R + 1.8) * 0.86;

// ── Sub-agent mark: a double ring, with its name beside it (§12) ─────
// The head of a spawned agent's chain. Small on purpose: the thing that
// matters about it is WHOSE chain it heads, and that is what the caption
// says. Sizing a glyph to its own label is what the pill this replaced
// did, and it made the layout answer to text metrics — two names a lane
// apart printed through each other, and the pass that stopped them had
// to move whole lanes around to do it. A point on the grid has no width
// to collide with.
/** Outer ring radius. */
export const AGENT_DOT_R = 7;
/** Inner ring radius — the second circle is what says "another agent's
 *  chain" rather than "a node of this one". */
export const AGENT_DOT_INNER_R = 3.2;
/** Where the caption starts, right of the dot's centre. */
export const AGENT_CAPTION_DX = 14;
/** Names longer than this are ellipsised: a caption is a label, not a
 *  paragraph laid across the branch beside it. */
const AGENT_NAME_MAX_TEXT = 190;

// 量文字实际像素宽（复用一个 canvas）。按 label.length*6 估对中文
// （字宽≈字号）严重低估，背景比文字短、文字溢出，实测才中英文都贴合。
let _measureCtx: CanvasRenderingContext2D | null = null;
export function _textWidth(s: string, font: string): number {
  if (typeof document === "undefined") return s.length * 8;
  if (!_measureCtx) {
    _measureCtx = document.createElement("canvas").getContext("2d");
  }
  if (!_measureCtx) return s.length * 8; // 拿不到 canvas 时保守按 8px/字
  _measureCtx.font = font;
  return _measureCtx.measureText(s).width;
}

// canvas 的 ctx.font 不认 CSS 变量——含 var() 的整串会被拒收，静默退回
// 默认 "10px sans-serif"。这里写死与 --font-sans 相同的字体栈，量的才是
// 真正渲染的 Inter。
const AGENT_FONT =
  '400 10px "Inter Variable", "Inter", -apple-system, "PingFang SC", sans-serif';

/** The caption beside a sub-agent's dot: its name, and the size of the
 *  chain behind it when that chain is folded away.
 *
 *  Expanded there is no count — the turns are on screen and countable.
 *  Folded there is, because the dot alone would not say how much it
 *  stands for. */
export function agentCaption(
  name: string,
  count: number,
  open: boolean,
): string {
  const nm = name || "sub-agent";
  return !open && count ? `${nm} (${count})` : nm;
}

/** ``caption`` truncated with an ellipsis so it stays a label. */
export function agentCaptionText(caption: string): string {
  if (_textWidth(caption, AGENT_FONT) <= AGENT_NAME_MAX_TEXT) return caption;
  let s = caption;
  while (s.length > 1
    && _textWidth(s + "…", AGENT_FONT) > AGENT_NAME_MAX_TEXT) {
    s = s.slice(0, -1);
  }
  return s + "…";
}

/** How far the caption's ink reaches right of the dot's centre. The
 *  canvas is sized from it; nothing in the layout is. */
export function agentCaptionReach(caption: string): number {
  return AGENT_CAPTION_DX
    + Math.min(_textWidth(caption, AGENT_FONT), AGENT_NAME_MAX_TEXT);
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

/** ``solid`` fills the glyph with its branch colour instead of leaving
 *  it hollow. That is what HEAD looks like (§4): the one node you are
 *  standing on, marked by weight rather than by a ring around it — a
 *  halo is a second shape orbiting the first, and at this glyph size it
 *  read as its own node. */
export function _buildShapeEl(
  shape: string,
  color: string,
  r: number,
  solid?: boolean,
): SVGElement | null {
  const common = {
    fill: solid ? color : "transparent",
    stroke: color,
    "stroke-width": String(STROKE_W),
  };

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
  } else if (shape === "capsule") {
    // Compaction summary (dag/rendering.md §9): a rounded box on the
    // trunk, wider than every other shape because it speaks for more
    // than one turn. ``data-shape`` keeps ``_applyShapeSize`` from
    // re-squaring it on the next coverage flip.
    return _svg("rect", {
      x: -CAPSULE_HW, y: -CAPSULE_HH,
      width: CAPSULE_HW * 2, height: CAPSULE_HH * 2,
      rx: CAPSULE_HH, ry: CAPSULE_HH,
      "data-shape": "capsule",
      ...common,
    });
  } else if (shape === "agent_dot") {
    // Sub-agent head (dag/rendering.md §12): two concentric rings, and
    // nothing else. The name is a caption the drawer hangs beside it,
    // not part of the glyph, so this mark occupies exactly one grid
    // point however long the agent's name is.
    const g = _svg("g");
    g.appendChild(_svg("circle", {
      r: AGENT_DOT_R,
      fill: solid ? color : "transparent",
      stroke: color,
      "stroke-width": "2",
    }));
    g.appendChild(_svg("circle", {
      r: AGENT_DOT_INNER_R,
      fill: "none", stroke: solid ? "var(--bg-secondary, #1a1a1a)" : color,
      "stroke-width": "1.2",
      "stroke-opacity": "0.8",
    }));
    return g;
  } else if (shape === "merge_dot") {
    // ◉ 实心带孔圆（rendering.md 第四节图例）：外圈实心 + 中心
    // 挖孔，读作"多条分支在此汇为一点"。
    const g = _svg("g");
    g.appendChild(_svg("circle", { r, fill: color }));
    g.appendChild(_svg("circle", {
      r: r * 0.42,
      fill: "var(--bg-secondary, #1a1a1a)",
    }));
    return g;
  } else if (shape === "diamond") {
    const s = r * SQR_SCALE;
    // ROOT diamond is filled (white) — it's the session anchor, drawn
    // solid so it reads as the root, not just another outline node.
    return _svg("rect", {
      x: -s, y: -s, width: s * 2, height: s * 2,
      rx: 0.6, ry: 0.6,
      transform: "rotate(45)",
      "stroke-linejoin": "round",
      ...common,
      fill: "#ffffff",
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
