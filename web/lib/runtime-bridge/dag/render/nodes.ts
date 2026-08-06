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
import { translateText } from "@/lib/i18n";
import {
  CAPSULE_HH,
  CAPSULE_HW,
  _branchColor,
  _buildShapeEl,
  _shapeFor,
  _svg,
} from "../shapes";
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
      node.function === "task" ||
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
    // Rolling compaction: a newer summary absorbed this one. Grey
    // capsule, nothing to fold (backend strips its covers_ids).
    const isSuperseded =
      !!(node as Record<string, unknown>).superseded_summary;
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
      // The interaction layer routes a capsule click to the fold toggle
      // and labels the inspector's coverage row from these two.
      "data-summary": isCapsule ? String(covered.length) : "",
      "data-summary-open": capsuleOpen ? "1" : "0",
      // Same pair for the call thread: count and open state. Chain
      // turns and spawn heads use the identical vocabulary — a spawn
      // head's thread is the agent's own calls, one level down.
      "data-thread": threadCount ? String(threadCount) : "",
      "data-thread-open": threadOpen ? "1" : "0",
      "data-spawn-name": thread.nameOf[id] || "",
      "data-ghost": isGhost ? "1" : "0",
      "data-failed": isFailed ? "1" : "0",
    });
    // The compaction capsule is wider than the r=7 hit circle, so give it
    // a hit rect that actually covers the glyph — otherwise clicking the
    // ends of the pill misses and the fold "does nothing".
    const hit = isCapsule
      ? _svg("rect", {
        x: String(-CAPSULE_HW), y: String(-CAPSULE_HH),
        width: String(CAPSULE_HW * 2), height: String(CAPSULE_HH * 2),
        rx: String(CAPSULE_HH),
        fill: "transparent",
        "pointer-events": "all",
      })
      : _svg("circle", {
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
    // ── status 画在节点自己的描边上（rendering.md 第四节，废除占位框） ──
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
    // ── ghost 描边（rendering.md §9/§10）─────────────────────────
    // Two nodes read the same way and for the same reason: they are on
    // disk and readable, and they can never enter the next request.
    if (el && (isGhost || isFailed || isSuperseded)) {
      el.setAttribute("stroke", "var(--dag-ghost, #c9c7bf)");
      el.setAttribute("stroke-width", "1.6");
    }
    // ── 褶皱：折叠的覆盖区间收成递缩叠影（rendering.md §9）────────
    if (isCapsule && !capsuleOpen) {
      const pleats = Math.min(3, covered.length);
      for (let i = 0; i < pleats; i++) {
        const x = CAPSULE_HW + 2 + i * 5;
        const hh = CAPSULE_HH * (1 - i * 0.22);
        g.appendChild(_svg("rect", {
          x: String(x), y: String(-hh),
          width: "3.5", height: String(hh * 2),
          rx: "1.7",
          fill: "transparent",
          stroke: "var(--dag-ghost, #c9c7bf)",
          "stroke-width": String(1.2 - i * 0.1),
          "stroke-opacity": String(1 - i * 0.28),
          "pointer-events": "none",
        }));
      }
    }
    if (isSuperseded) {
      const cap = _svg("text", {
        x: String(CAPSULE_HW + 10),
        y: String(3.5),
        class: "history-summary-label",
        "pointer-events": "none",
      });
      cap.textContent = translateText("Superseded summary", "已被新摘要取代");
      g.appendChild(cap);
    }
    // ── 覆盖标注：胶囊旁写清它替掉了多少轮 ───────────────────────
    if (isCapsule) {
      // Clear of the pleats (they end at CAPSULE_HW + 2 + 2*5 + 3.5).
      const cap = _svg("text", {
        x: String(CAPSULE_HW + 22),
        y: String(3.5),
        class: "history-summary-label",
        "pointer-events": "none",
      });
      cap.textContent = capsuleOpen
        ? translateText(`Expanded · ${covered.length} turns`,
          `展开中 · ${covered.length} 轮`)
        : translateText(`Compacted · ${covered.length} turns`,
          `已压缩 · ${covered.length} 轮`);
      g.appendChild(cap);
    }
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
