/**
 * Renderer: node SVG drawing.
 *
 * Each DAG node becomes a ``<g class="history-node">`` with a hit-area
 * circle, a coloured shape (diamond/circle/triangle/square), and an
 * optional fold badge ("+N" / "−").
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, NODE_R } from "../types";
import {
  CAPSULE_HH,
  CAPSULE_HW,
  _branchColor,
  _buildShapeEl,
  _shapeFor,
  _svg,
} from "../shapes";
import { _spawnExpanded, _summaryExpanded } from "../store/globals";

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
  cinfo: {
    isCollapsible: (m: GNode) => boolean;
    hiddenCount: Record<string, number>;
  },
  collapsed: Record<string, boolean>,
  internalSet: Record<string, boolean>,
  internalOwner: Record<string, string>,
  contextSet: Record<string, boolean> | null,
  coverageSet: Record<string, { aged: boolean; spilled: boolean }> | null,
  coversOf: Record<string, string[]>,
  spawnFold: {
    branchOf: Record<string, string[]>;
    nameOf: Record<string, string>;
  },
): void {
  // Every id any capsule covers — folded or expanded. Expanded, those
  // nodes draw as ghosts so "readable" and "in context" stay visibly
  // separate (dag/rendering.md §9).
  const coveredBy: Record<string, string> = Object.create(null);
  for (const sid in coversOf) {
    for (const cid of coversOf[sid]) coveredBy[cid] = sid;
  }
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
    const covered = coversOf[id];
    const isCapsule = !!covered;
    const capsuleOpen = isCapsule && !!_summaryExpanded[id];
    // Sub-agent capsule (dag/rendering.md §12) — same fold vocabulary as
    // the compaction one, keyed off the spawn pass rather than covers_ids.
    const spawnBranch = spawnFold.branchOf[id];
    const isSpawnCapsule = !!spawnBranch;
    const spawnOpen = isSpawnCapsule && !!_spawnExpanded[id];
    const spawnName = spawnFold.nameOf[id] || "";
    const isAnyCapsule = isCapsule || isSpawnCapsule;
    const isGhost = !!coveredBy[id];
    const isFailed = _isArchivedFailure(node, onHead, isHead);
    const g = _svg("g", {
      class:
        "history-node" +
        (isHead ? " is-head" : "") +
        (onHead ? "" : " off-head") +
        (isCollapsible ? " is-collapsible" : "") +
        (oocFlag ? " out-of-context" : "") +
        (isCapsule ? " is-summary" : "") +
        (isSpawnCapsule ? " is-subagent" : "") +
        (isGhost ? " is-ghost" : "") +
        (isFailed ? " is-archived-failure" : ""),
      transform: "translate(" + p.x + "," + p.y + ")",
      "data-msg-id": id,
      "data-collapsible": isCollapsible ? "1" : "0",
      "data-collapsed": folded ? "1" : "0",
      "data-internal": internalSet[id] ? "1" : "0",
      "data-owner": internalOwner[id] || "",
      // The interaction layer routes a capsule click to the fold toggle
      // instead of the collapse toggle, and labels the inspector's
      // coverage row from these two.
      "data-summary": isCapsule ? String(covered.length) : "",
      "data-summary-open": capsuleOpen ? "1" : "0",
      // Same three for the sub-agent fold: count, open state, and the
      // name the inspector and tooltip title themselves from.
      "data-spawn": isSpawnCapsule ? String(spawnBranch.length) : "",
      "data-spawn-open": spawnOpen ? "1" : "0",
      "data-spawn-name": spawnName,
      "data-ghost": isGhost ? "1" : "0",
      "data-failed": isFailed ? "1" : "0",
    });
    // The capsule is wider than the r=7 hit circle, so give it a hit
    // rect that actually covers the glyph — otherwise clicking the ends
    // of the pill misses and the fold "does nothing".
    const hit = isAnyCapsule
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
    const r = NODE_R + 1.8;
    const el = _buildShapeEl(_shapeFor(node), color, r);
    if (el) {
      el.setAttribute("pointer-events", "none");
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
    // A covered turn, because its capsule speaks for it; an archived
    // failure, because the retry forked past it. Grey outline, no fill.
    //
    // The failure case deliberately overwrites the red ! stroke above:
    // red says "this needs your attention now", and an archived line
    // does not — the retry already happened. It keeps the ! glyph so
    // *why* the line ended is still legible.
    if (el && (isGhost || isFailed)) {
      el.setAttribute("stroke", "var(--dag-ghost, #c9c7bf)");
      el.setAttribute("stroke-width", "1.6");
    }
    // ── 褶皱：折叠的覆盖区间收成递缩叠影（rendering.md §9）────────
    // Three receding pleats off the capsule's right edge. They are the
    // whole reason the capsule can hide N turns without the graph
    // lying about it: the pill says "one node", the pleats say "and a
    // stack behind it". Expanded, they go away — the range is drawn.
    if ((isCapsule && !capsuleOpen)
        || (isSpawnCapsule && !spawnOpen && spawnBranch.length)) {
      const pleats = Math.min(3, (covered || spawnBranch).length);
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
    // ── 覆盖标注：胶囊旁写清它替掉了多少轮 ───────────────────────
    // Without the count the capsule is just an odd-looking turn; with
    // it the fold is self-describing and needs no legend lookup.
    if (isCapsule) {
      const cap = _svg("text", {
        x: String(CAPSULE_HW + (capsuleOpen ? 6 : 22)),
        y: String(3.5),
        class: "history-summary-label",
        "pointer-events": "none",
      });
      cap.textContent = capsuleOpen
        ? `▾ ${covered.length}`
        : `▸ ${covered.length}`;
      g.appendChild(cap);
    }
    // The sub-agent capsule is labelled by NAME, not by count: "which
    // agent is this" is the question the fold has to answer, and the
    // count of its turns is not an answer. The count still rides along
    // in parentheses so the fold stays self-describing.
    if (isSpawnCapsule) {
      const cap = _svg("text", {
        x: String(CAPSULE_HW + (spawnOpen ? 6 : 22)),
        y: String(3.5),
        class: "history-summary-label history-subagent-label",
        "pointer-events": "none",
      });
      const arrow = spawnOpen ? "▾" : "▸";
      const nm = spawnName || "sub-agent";
      cap.textContent = spawnBranch.length
        ? `${arrow} ${nm} (${spawnBranch.length})`
        : `${arrow} ${nm}`;
      g.appendChild(cap);
    }
    // ── 覆盖态的两级衰减（rendering.md 第八节）──
    // aged：结果被 aging 折成一行残根，节点还在上下文里但内容已残——
    // 描边调暗读作"在，但只剩梗概"。用 stroke-opacity 而不是整体
    // opacity：白点是 _applyVisibility 写的 fill，整体透明会把
    // "在上下文中"这个信号一起淡掉，两件事就分不开了。
    const cov = coverageSet ? coverageSet[id] : undefined;
    if (el && cov && cov.aged) {
      el.setAttribute("stroke-opacity", "0.4");
      g.classList.add("is-aged");
    }
    // spilled：大结果已外溢成盘上文件，正文只留引用。▤ 画在左上角
    // ——右上角归 ! 和 ↗，右下角归折叠徽标。用 text 而不是 rect，
    // 免得 _applyVisibility 找主形状时把它当成节点本体。
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
    if (isCollapsible) {
      const hc = cinfo.hiddenCount[id] || 0;
      // 对话层节点折叠的是它的执行子树 → ⚒N（rendering.md 第〇节）；
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
