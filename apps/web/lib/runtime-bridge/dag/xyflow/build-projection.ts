/**
 * Build a CanvasProjection from the laid-out session graph.
 * Reuses existing geometry + shape helpers — no ELK rewrite.
 */

import type { GNode } from "../types";
import type { Geometry } from "../layout/geometry";
import type { ThreadModel } from "../passes/thread";
import { isChainNode, isSpawnRoot } from "../passes/thread";
import { _branchColor, _shapeFor } from "../render/shapes";
import { _summaryExpanded } from "../store/globals";
import type {
  CanvasProjection,
  DagShape,
  ProjectionEdge,
  ProjectionNode,
} from "./types";

function asShape(s: string): DagShape {
  if (s === "diamond" || s === "circle" || s === "triangle"
      || s === "square" || s === "capsule" || s === "merge_dot") {
    return s;
  }
  return "circle";
}

function previewSnippet(node: GNode, max = 12): string {
  const raw = node.preview ?? node.content ?? node.output ?? node.name ?? "";
  const s = typeof raw === "string" ? raw : String(raw);
  const one = s.replace(/\s+/g, " ").trim();
  if (!one) return "";
  return one.length > max ? one.slice(0, max) + "…" : one;
}

function rangeChipFor(
  node: GNode,
  covered: string[] | undefined,
  byId: Record<string, GNode>,
): string {
  if (!covered || !covered.length) return "";
  const first = byId[covered[0]] || null;
  const last = byId[covered[covered.length - 1]] || null;
  const a = first ? previewSnippet(first, 8) : "…";
  const b = last ? previewSnippet(last, 8) : "…";
  return `${covered.length} · ${a}→${b}`;
}

export function buildProjection(args: {
  sessionId: string | null;
  headId: string | null;
  byId: Record<string, GNode>;
  fullById: Record<string, GNode>;
  geom: Geometry;
  headAncestors: Record<string, boolean>;
  stableLeafOfNode: Record<string, string>;
  internalSet: Record<string, boolean>;
  internalOwner: Record<string, string>;
  contextSet: Record<string, boolean> | null;
  coverageSet: Record<string, { aged: boolean; spilled: boolean }> | null;
  coversOf: Record<string, string[]>;
  thread: ThreadModel;
  revision: number;
}): CanvasProjection {
  const {
    sessionId, headId, byId, fullById, geom, headAncestors,
    stableLeafOfNode, internalSet, internalOwner, contextSet,
    coverageSet, coversOf, thread, revision,
  } = args;

  const nodes: ProjectionNode[] = [];
  Object.keys(byId).forEach((id) => {
    const node = byId[id];
    const p = geom.pos[id] || { x: 0, y: 0 };
    const isHead = id === headId;
    const onHead = !!headAncestors[id];
    const color = _branchColor(node, stableLeafOfNode);
    const covered = coversOf[id];
    const isCapsule = !!covered;
    const isInert = !!(node as Record<string, unknown>)._summaryInert;
    const capsuleOpen = isCapsule && !!_summaryExpanded[id];
    const threadCount = (thread.events[id] || []).length;
    const threadOpen = threadCount > 0 && thread.isOpen(id);
    const cov = coverageSet ? coverageSet[id] : undefined;
    const isBranchOp =
      node.function === "agent"
      || node.function === "attach"
      || node.function === "merge";
    // Match SVG: branch-ops never count as out-of-context for paint.
    const inContext = contextSet
      ? (!!contextSet[id] || isBranchOp)
      : false;

    nodes.push({
      id,
      x: p.x,
      y: p.y,
      shape: asShape(_shapeFor(node)),
      color,
      isHead,
      onHead,
      inContext,
      aged: !!(cov && cov.aged),
      spilled: !!(cov && cov.spilled),
      isGhost: !!(node as Record<string, unknown>)._ghost,
      isInert,
      isSummary: isCapsule,
      summaryCount: isCapsule && !isInert ? covered.length : 0,
      summaryOpen: capsuleOpen && !isInert,
      threadCount,
      threadOpen,
      status: String((node as Record<string, unknown>).status || ""),
      isError: !!node.is_error
        || (node as Record<string, unknown>).status === "error",
      isInternal: !!internalSet[id],
      ownerId: internalOwner[id] || "",
      role: String(node.role || ""),
      display: String(node.display || ""),
      functionName: String(node.function || ""),
      rangeChip: rangeChipFor(node, covered, fullById),
      source: String((node as Record<string, unknown>).source || ""),
    });
  });

  const edges: ProjectionEdge[] = [];
  const seen = new Set<string>();

  Object.keys(byId).forEach((id) => {
    const node = byId[id];
    if (node.display === "root") return;
    if (isSpawnRoot(node) || !isChainNode(node, byId)) return;

    let pid = node.predecessor || node.caller;
    if (pid && !byId[pid]) {
      let cur: string | null | undefined = pid;
      let hops = 0;
      while (cur && !byId[cur] && hops < 50) {
        const pn = fullById[cur];
        cur = pn ? (pn.predecessor || pn.caller || null) : null;
        hops++;
      }
      if (cur && byId[cur]) pid = cur;
      else return;
    }
    if (!pid || !byId[pid]) return;

    const parent = byId[pid];
    const sameLane = (node._lane || 0) === (parent._lane || 0);
    const isGhost = !!(node as Record<string, unknown>)._ghost;
    const color = _branchColor(node, stableLeafOfNode);
    const eid = `e:${pid}->${id}`;
    if (seen.has(eid)) return;
    seen.add(eid);

    if (!sameLane) {
      edges.push({
        id: eid,
        source: pid,
        target: id,
        kind: "fork",
        color,
        dashed: true,
      });
      return;
    }

    edges.push({
      id: eid,
      source: pid,
      target: id,
      kind: isGhost ? "ghost" : "conv",
      color,
      dashed: isGhost,
    });
  });

  // Call-thread edges: faint dotted from anchor to each open thread item.
  Object.keys(thread.events).forEach((anchorId) => {
    if (!thread.isOpen(anchorId)) return;
    const items = thread.events[anchorId] || [];
    let prev = anchorId;
    items.forEach((ev) => {
      const tid = ev.id;
      if (!tid || !byId[tid]) return;
      const eid = `t:${prev}->${tid}`;
      if (seen.has(eid)) return;
      seen.add(eid);
      edges.push({
        id: eid,
        source: prev,
        target: tid,
        kind: "thread",
        color: "var(--text-muted, #8a8880)",
        dashed: true,
      });
      prev = tid;
    });
  });

  // Attach / merge reference edges (dashed) when both ends visible.
  Object.keys(byId).forEach((id) => {
    const node = byId[id];
    const ref = (node as { attach_ref?: string }).attach_ref;
    if (!ref || !byId[ref]) return;
    const eid = `a:${ref}->${id}`;
    if (seen.has(eid)) return;
    seen.add(eid);
    edges.push({
      id: eid,
      source: ref,
      target: id,
      kind: "attach",
      color: _branchColor(node, stableLeafOfNode),
      dashed: true,
    });
  });


  return {
    sessionId,
    headId,
    nodes,
    edges,
    empty: nodes.length === 0,
    skeleton: false,
    revision,
  };
}
