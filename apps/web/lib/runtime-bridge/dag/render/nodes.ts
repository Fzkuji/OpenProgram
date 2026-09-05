/**
 * Renderer: node SVG drawing.
 *
 * Each DAG node becomes a ``<g class="history-node">`` with a hit-area
 * circle, a coloured shape (diamond/circle/triangle/square), and — when
 * the node owns a folded call thread — a count on its shoulder.
 *
 * The count is the fold marker (dag/rendering.md §12): digits glued to
 * the glyph, not a shape of their own. Anything shaped would be read as
 * a node; a number riding the corner reads as what it is, "this many
 * calls behind this turn". Open, the count disappears — the calls are
 * on screen and countable.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, NODE_R } from "../types";
import {
  CAPSULE_RING,
  _branchColor,
  _buildShapeEl,
  _shapeFor,
  _svg,
} from "./shapes";
import { _summaryExpanded } from "../store/globals";
import type { ThreadModel } from "../passes/thread";

/** A turn that ended in ``error`` and is no longer on the HEAD chain:
 *  the retry forked off its predecessor and moved on, so this line is
 *  kept for the record and can never re-enter context (dag/rendering.md
 *  §10). ``status`` is the store's own terminal marker — the graph
 *  reads it, it never decides it. */
function _isArchivedFailure(
  node: GNode,
  onHead: boolean,
  isHead: boolean,
): boolean {
  const status = (node as Record<string, unknown>).status;
  if (status !== "error" && !node.is_error) return false;
  return !onHead && !isHead;
}

function applyStatusPaint(
  g: SVGElement,
  el: SVGElement | null,
  node: GNode,
): void {
  const status = (node as Record<string, unknown>).status as string | undefined;
  const onHead = !g.classList.contains("off-head");
  const isHead = g.classList.contains("is-head");
  const isFailed = _isArchivedFailure(node, onHead, isHead);
  const isSuperseded = !!(node as Record<string, unknown>).superseded_summary;
  const isErr = status === "error" || !!node.is_error;

  g.classList.toggle("is-running", status === "running");
  if (status === "cancelled" || status === "stopped") {
    g.setAttribute("opacity", "0.45");
  } else {
    g.removeAttribute("opacity");
  }
  g.classList.toggle("is-archived-failure", isFailed);
  g.setAttribute("data-failed", isFailed ? "1" : "0");

  if (el) {
    if (status === "running") el.setAttribute("stroke-dasharray", "4 3");
    else el.removeAttribute("stroke-dasharray");

    const base = el.getAttribute("data-base-stroke") || "";
    const baseW = el.getAttribute("data-base-stroke-width") || "";
    if (isFailed || isSuperseded) {
      el.setAttribute("stroke", "var(--dag-ghost, #c9c7bf)");
      el.setAttribute("stroke-width", "1.6");
    } else if (isErr) {
      el.setAttribute("stroke", "#e5534b");
      if (baseW) el.setAttribute("stroke-width", baseW);
    } else if (base) {
      el.setAttribute("stroke", base);
      if (baseW) el.setAttribute("stroke-width", baseW);
    }
  }

  const wantBang = !!(el && isErr);
  let bang = g.querySelector("[data-status-bang]");
  if (wantBang && !bang) {
    bang = _svg("text", {
      x: String(NODE_R + 2),
      y: String(-NODE_R),
      fill: "#e5534b",
      "font-size": "9",
      "font-weight": "700",
      "pointer-events": "none",
      "data-status-bang": "1",
    });
    bang.textContent = "!";
    g.appendChild(bang);
  } else if (!wantBang && bang) {
    bang.remove();
  }
}

/** In-place status / running / error marks. False if any drawn node is missing. */
export function patchHistoryStatus(host: HTMLElement, graph: GNode[]): boolean {
  const nodeG = host.querySelector("svg.history-svg g.history-nodes");
  if (!nodeG) return false;
  const drawn = nodeG.querySelectorAll(".history-node");
  if (!drawn.length) return false;
  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => { byId[m.id] = m; });
  for (let i = 0; i < drawn.length; i++) {
    const g = drawn[i] as SVGElement;
    const id = g.getAttribute("data-msg-id");
    const node = id ? byId[id] : undefined;
    if (!node) return false;
    const el = g.querySelector("[data-node-shape]") as SVGElement | null;
    if (!el) return false;
    applyStatusPaint(g, el, node);
    (g as any)._nodeData = node;
  }
  return true;
}

export function drawNodes(
  nodeG: SVGElement,
  tree: { byId: Record<string, GNode> },
  pos: (n: GNode) => { x: number; y: number },
  headId: string | null,
  headAncestors: Record<string, boolean>,
  stableLeafOfNode: Record<string, string>,
  internalSet: Record<string, boolean>,
  internalOwner: Record<string, string>,
  contextSet: Record<string, boolean> | null,
  coverageSet: Record<string, { aged: boolean; spilled: boolean }> | null,
  coversOf: Record<string, string[]>,
  thread: ThreadModel,
): void {
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    const p = pos(node);
    const isHead = id === headId;
    const onHead = !!headAncestors[id];
    const color = _branchColor(node, stableLeafOfNode);
    const isBranchOp =
      node.function === "agent" ||
      node.function === "attach" ||
      node.function === "merge";
    const oocFlag = contextSet && !contextSet[id] && !isBranchOp;
    const covered = coversOf[id];
    const isCapsule = !!covered;
    const capsuleOpen = isCapsule && !!_summaryExpanded[id];
    // The node's call thread (dag/rendering.md §12): how many events
    // fold behind it, and whether they are on screen.
    const threadCount = (thread.events[id] || []).length;
    const threadOpen = threadCount > 0 && thread.isOpen(id);
    // Ghost = covered by the branch-applying summary AND expanded —
    // stamped per-branch by the fold pass (dag/rendering.md §9). On a
    // branch the summary does not apply to, these turns keep colour.
    const isGhost = !!(node as Record<string, unknown>)._ghost;
    // The active summary viewed from a branch it does not apply to:
    // the turns around it are live, the capsule is the inert thing.
    const isInert = !!(node as Record<string, unknown>)._summaryInert;
    const isFailed = _isArchivedFailure(node, onHead, isHead);
    const g = _svg("g", {
      class:
        "history-node" +
        (isHead ? " is-head" : "") +
        (onHead ? "" : " off-head") +
        (oocFlag ? " out-of-context" : "") +
        (isCapsule ? " is-summary" : "") +
        (threadCount ? " has-thread" : "") +
        (isGhost ? " is-ghost" : "") +
        (isFailed ? " is-archived-failure" : ""),
      transform: "translate(" + p.x + "," + p.y + ")",
      "data-msg-id": id,
      "data-internal": internalSet[id] ? "1" : "0",
      "data-owner": internalOwner[id] || "",
      // The interaction layer routes a capsule click to the fold
      // toggle and labels the inspector's coverage row from these two.
      // An INERT capsule (this branch does not carry the summary) has
      // nothing to fold — no data-summary, so a click is an ordinary
      // select and no fold affordance is advertised.
      "data-summary": isCapsule && !isInert ? String(covered.length) : "",
      "data-summary-open": capsuleOpen && !isInert ? "1" : "0",
      // Same pair for the call thread: count and open state. Chain
      // turns and spawn heads use the identical vocabulary — a spawn
      // head's thread is the agent's own calls, one level down.
      "data-thread": threadCount ? String(threadCount) : "",
      "data-thread-open": threadOpen ? "1" : "0",
      "data-spawn-name": thread.nameOf[id] || "",
      "data-ghost": isGhost ? "1" : "0",
      "data-failed": isFailed ? "1" : "0",
    });
    const hit = _svg("circle", {
      r: "7",
      fill: "transparent",
      "pointer-events": "all",
    });
    g.appendChild(hit);
    (g as SVGGraphicsElement).style.cursor = "pointer";
    const r = NODE_R + 3;
    const el = _buildShapeEl(_shapeFor(node), color, r);
    if (el) {
      el.setAttribute("pointer-events", "none");
      el.setAttribute("data-node-shape", "1");
      el.setAttribute("data-base-stroke", color);
      el.setAttribute(
        "data-base-stroke-width",
        el.getAttribute("stroke-width") || "2.2",
      );
      // HEAD is said by a breathing glow around its own glyph (§4):
      // light rides the shape at every zoom, where a fill changed what
      // the glyph looks like and a halo ring read as a second node.
      // ``color`` on the <g> feeds the CSS glow's ``currentColor``.
      if (isHead) {
        el.setAttribute("data-head", "1");
        (g as SVGGraphicsElement).style.color = color;
      }
      g.appendChild(el);
    }
    applyStatusPaint(g, el, node);
    // ── 内圈：双圈圆的里圈（rendering.md §9）────────────────────
    // 细一号的同心圆，跟外圈同色，在白填充（在上下文中）与镂空两种
    // 状态下都可见。
    if (isCapsule) {
      g.appendChild(_svg("circle", {
        r: String((NODE_R + 3) * CAPSULE_RING),
        fill: "none",
        stroke: color,
        "stroke-width": "1.2",
        "pointer-events": "none",
      }));
    }
    // ── 折叠数：肩上的数字，与调用线程的折叠数同一套语言（§12）──
    // 数字贴在右上角说"后面收着这么多节点"；展开后消失，因为那时
    // 它们都在屏上。
    if (isCapsule && !capsuleOpen && !isInert) {
      const cnt = _svg("text", {
        x: String(NODE_R + 5),
        y: String(-NODE_R - 1),
        class: "history-thread-count",
        "pointer-events": "none",
      });
      cnt.textContent = String(covered.length);
      g.appendChild(cnt);
    }
    // 胶囊不带文字注记：形状（双圈）说明它是什么，肩上数字说明收了
    // 多少，展开时幽灵就在屏上。详情在 tooltip 和检查器里。
    // ── 覆盖态的两级衰减（rendering.md 第八节）──
    const cov = coverageSet ? coverageSet[id] : undefined;
    if (el && cov && cov.aged) {
      el.setAttribute("stroke-opacity", "0.4");
      g.classList.add("is-aged");
    }
    if (cov && cov.spilled) {
      const spill = _svg("text", {
        x: String(-NODE_R - 9),
        y: String(-NODE_R + 1),
        fill: color,
        "font-size": "9",
        "pointer-events": "none",
      });
      spill.textContent = "▤";
      g.appendChild(spill);
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
    // ── 折叠数角标（rendering.md §12）────────────────────────────
    // 数字贴在字形右上角，是字形的标注，不依赖任何线是否存在——
    // 全是函数调用、没有 spawn 的轮次照样成立。函数调用和 spawn 是
    // 同一类事件，一并计入。展开后角标消失：调用变成线程上的真节点。
    if (threadCount && !threadOpen) {
      const cnt = _svg("text", {
        x: String(NODE_R + 5),
        y: String(-NODE_R - 1),
        class: "history-thread-count",
        "pointer-events": "none",
      });
      cnt.textContent = String(threadCount);
      g.appendChild(cnt);
    }
    (g as any)._nodeData = node;
    nodeG.appendChild(g);
  });
}
