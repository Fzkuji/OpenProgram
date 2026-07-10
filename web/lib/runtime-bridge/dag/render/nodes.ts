/**
 * Renderer: node SVG drawing.
 *
 * Each DAG node becomes a ``<g class="history-node">`` with a hit-area
 * circle, a coloured shape (diamond/circle/triangle/square), and an
 * optional fold badge ("+N" / "−").
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, NODE_R } from "../types";
import { _branchColor, _buildShapeEl, _shapeFor, _svg } from "../shapes";

export function drawNodes(
  nodeG: SVGElement,
  tree: { byId: Record<string, GNode> },
  pos: (n: GNode) => { x: number; y: number },
  headId: string | null,
  headAncestors: Record<string, boolean>,
  stableLeafOfNode: Record<string, string>,
  cinfo: {
    isCollapsible: (m: GNode) => boolean;
    hiddenCount: Record<string, number>;
  },
  collapsed: Record<string, boolean>,
  internalSet: Record<string, boolean>,
  internalOwner: Record<string, string>,
  contextSet: Record<string, boolean> | null,
): void {
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    const p = pos(node);
    const isHead = id === headId;
    const onHead = !!headAncestors[id];
    const color = _branchColor(node, stableLeafOfNode);
    const isCollapsible = cinfo.isCollapsible(node);
    const folded = isCollapsible && !!collapsed[id];
    const isBranchOp =
      node.function === "task" ||
      node.function === "attach" ||
      node.function === "merge";
    const oocFlag = contextSet && !contextSet[id] && !isBranchOp;
    const g = _svg("g", {
      class:
        "history-node" +
        (isHead ? " is-head" : "") +
        (onHead ? "" : " off-head") +
        (isCollapsible ? " is-collapsible" : "") +
        (oocFlag ? " out-of-context" : ""),
      transform: "translate(" + p.x + "," + p.y + ")",
      "data-msg-id": id,
      "data-collapsible": isCollapsible ? "1" : "0",
      "data-collapsed": folded ? "1" : "0",
      "data-internal": internalSet[id] ? "1" : "0",
      "data-owner": internalOwner[id] || "",
    });
    const hit = _svg("circle", {
      r: "7",
      fill: "transparent",
      "pointer-events": "all",
    });
    g.appendChild(hit);
    (g as SVGGraphicsElement).style.cursor = "pointer";
    const r = NODE_R + 1.8;
    const el = _buildShapeEl(_shapeFor(node), color, r);
    if (el) {
      el.setAttribute("pointer-events", "none");
      g.appendChild(el);
    }
    // ── status 画在节点自己的描边上（dag-rendering.md 第四节，废除占位框） ──
    const status = (node as Record<string, unknown>).status as string | undefined;
    if (el && status === "running") {
      el.setAttribute("stroke-dasharray", "4 3");
      g.classList.add("is-running");
    } else if (el && (status === "error" || node.is_error)) {
      el.setAttribute("stroke", "#e5534b");
      const bang = _svg("text", {
        x: String(NODE_R + 2),
        y: String(-NODE_R),
        fill: "#e5534b",
        "font-size": "9",
        "font-weight": "700",
        "pointer-events": "none",
      });
      bang.textContent = "!";
      g.appendChild(bang);
    } else if (status === "cancelled" || status === "stopped") {
      g.setAttribute("opacity", "0.45");
    }
    // ↗ 跨会话 spawn 角标：两侧都标（spawn_remote=目标侧分支根，
    // spawn_out=源侧发起节点）。同会话 spawn 有点划线边，不加 ↗。
    if ((node as Record<string, unknown>).spawn_remote
        || (node as Record<string, unknown>).spawn_out) {
      const arrow = _svg("text", {
        x: String(NODE_R + 2),
        y: String(-NODE_R + 1),
        fill: color,
        "font-size": "10",
        "font-weight": "700",
        "pointer-events": "none",
      });
      arrow.textContent = "↗";
      g.appendChild(arrow);
    }
    if (isCollapsible) {
      const hc = cinfo.hiddenCount[id] || 0;
      // 对话层节点折叠的是它的执行子树 → ⚒N（dag-rendering.md 第〇节）；
      // 执行层内部的折叠沿用 +N。
      const isConvNode =
        (node.role === "user" || node.role === "assistant")
        && node.display !== "runtime" && !node._runNode;
      const label = folded
        ? (isConvNode ? "⚒" + hc : "+" + hc)
        : "−";
      // Transparent hit rect covering the badge glyph — the "+N" sits
      // ~13px off-centre, outside the r=7 node hit circle, so clicking
      // the glyph itself used to miss and the fold "did nothing". Width
      // grows with the digit count so "+12" stays fully clickable.
      const badgeW = 10 + Math.max(0, label.length - 2) * 6;
      const badgeHit = _svg("rect", {
        x: String(NODE_R + 1),
        y: String(NODE_R - 2),
        width: String(badgeW),
        height: "14",
        fill: "transparent",
        "pointer-events": "all",
      });
      g.appendChild(badgeHit);
      const badge = _svg("text", {
        x: String(NODE_R + 3),
        y: String(NODE_R + 5),
        class: "history-fold-badge",
        "pointer-events": "none",
      });
      badge.textContent = label;
      g.appendChild(badge);
    }
    (g as any)._nodeData = node;
    nodeG.appendChild(g);
  });
}
