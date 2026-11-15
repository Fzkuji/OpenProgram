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

import { type GNode } from "../types";
import { _branchColor, _svg } from "./shapes";
import { TILE_R, buildTile, spawnName, tileFor } from "./tiles";
import { agentOf, paintAgents } from "./agents";
import { subAgentShape } from "./agent-look";
import { paintBelts } from "./edges";
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

  // Status is a CSS state of the tile (app/styles/dag/graph/nodes.css): the
  // running comet, the grey of a turn kept only for the record. An error
  // keeps its tone and gets a red ``!``: a red tile changed what the node
  // looked like, not just how it ended. A cancelled turn fades as a whole.
  g.classList.toggle("is-running", status === "running");
  g.classList.toggle("is-error", isErr && !isFailed && !isSuperseded);
  g.classList.toggle("is-faded", isFailed || isSuperseded);
  if (status === "cancelled" || status === "stopped") {
    g.setAttribute("opacity", "0.45");
  } else {
    g.removeAttribute("opacity");
  }
  g.classList.toggle("is-archived-failure", isFailed);
  g.setAttribute("data-failed", isFailed ? "1" : "0");

  const wantBang = !!(el && isErr);
  let bang = g.querySelector("[data-status-bang]");
  if (wantBang && !bang) {
    // The Activity panel's red badge, ``!`` in place of a count, on the
    // icon's upper-right corner (app/styles/dag/graph/nodes.css).
    bang = _svg("g", {
      class: "dag-status-badge",
      transform: `translate(${TILE_R - 3},${-TILE_R + 5})`,
      "pointer-events": "none",
      "data-status-bang": "1",
    });
    bang.appendChild(_svg("circle", { r: 6.5 }));
    const mark = _svg("text", { "text-anchor": "middle", "dominant-baseline": "central", y: "0.5" });
    mark.textContent = "!";
    bang.appendChild(mark);
    g.appendChild(bang);
  } else if (!wantBang && bang) {
    bang.remove();
  }
  seatShoulderCount(g);
}

/** The fold count shares the upper-right shoulder with the corner
 *  marks (`!` error, `↗` cross-session spawn). Glued on, the two read
 *  as one token ("!18"), so the count steps right past whichever mark
 *  is there; with none, it sits against the glyph. */
function seatShoulderCount(g: Element): void {
  const marked = g.querySelector("[data-status-bang], [data-corner-mark]");
  g.querySelectorAll(".history-thread-count").forEach((cnt) => {
    cnt.setAttribute("x", String(TILE_R + (marked ? 10 : 3)));
  });
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
  // The belts into working nodes run, and the agents move with them.
  const svg = host.querySelector("svg.history-svg");
  if (svg) paintBelts(svg, graph);
  paintAgents(nodeG, graph);
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
    const hit = _svg("rect", {
      x: -TILE_R - 3, y: -TILE_R - 3, width: TILE_R * 2 + 6, height: TILE_R * 2 + 6,
      fill: "transparent", class: "dag-hit",
      "pointer-events": "all",
    });
    g.appendChild(hit);
    (g as SVGGraphicsElement).style.cursor = "pointer";
    let shown = tileFor(node);
    // A sub-agent's start and its replies wear the sub-agent's character,
    // by the name its tag shows; the main agent's replies keep the
    // conversation Agent's.
    if (shown.bot) {
      const owner = agentOf(node, tree.byId);
      if (owner) shown = { ...shown, bot: subAgentShape(thread.nameOf[owner] || spawnName(tree.byId[owner])) };
    }
    const tile = buildTile(shown);
    // Your message and an agent's character wear their branch's colour, so
    // a branch reads by its messages and its agent as well as by its line.
    if (shown.tone === "user" || shown.bot) (tile as SVGElement).style.setProperty("--tone", _branchColor(node));
    const el = tile.querySelector("[data-node-shape]") as SVGElement | null;
    // HEAD is said by a breathing glow round its own chip (§4), in the
    // chip's colour: light rides the shape at every zoom.
    if (isHead && el) el.setAttribute("data-head", "1");
    g.appendChild(tile);
    applyStatusPaint(g, el, node);
    // ── 折叠数：肩上的数字，与调用线程的折叠数同一套语言（§12）──
    // 数字贴在右上角说"后面收着这么多节点"；展开后消失，因为那时
    // 它们都在屏上。
    if (isCapsule && !capsuleOpen && !isInert) {
      const cnt = _svg("text", {
        x: String(TILE_R + 3),
        y: String(-TILE_R + 2),
        class: "history-thread-count",
        // Part of its node: the whole box takes the hand and the click
        // (fold / unfold), not just the strokes of the digits.
        "pointer-events": "bounding-box",
      });
      cnt.textContent = `+${covered.length}`;
      g.appendChild(cnt);
    }
    // 胶囊不带文字注记：形状（双圈）说明它是什么，肩上数字说明收了
    // 多少，展开时幽灵就在屏上。详情在 tooltip 和检查器里。
    // ── 覆盖态的两级衰减（rendering.md 第八节）──
    const cov = coverageSet ? coverageSet[id] : undefined;
    if (el && cov && cov.aged) {
      g.classList.add("is-aged");
    }
    if (cov && cov.spilled) {
      const spill = _svg("text", {
        x: String(-TILE_R - 11),
        y: String(-TILE_R + 3),
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
        x: String(TILE_R + 2),
        y: String(-TILE_R + 3),
        fill: color,
        "font-size": "10",
        "font-weight": "700",
        "pointer-events": "none",
        "data-corner-mark": "1",
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
        x: String(TILE_R + 3),
        y: String(-TILE_R + 2),
        class: "history-thread-count",
        // Part of its node: the whole box takes the hand and the click
        // (fold / unfold), not just the strokes of the digits.
        "pointer-events": "bounding-box",
      });
      cnt.textContent = `+${threadCount}`;
      g.appendChild(cnt);
      seatShoulderCount(g);
    }
    (g as any)._nodeData = node;
    nodeG.appendChild(g);
  });
}
