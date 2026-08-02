/**
 * Pass: fold a sub-agent's branch behind its spawn root.
 *
 * A spawned agent runs a whole conversation of its own — in the case that
 * motivated this, two sub-agents contributed 280 of a session's 309 nodes.
 * Drawn in full they bury the main lane they were spawned from, and none
 * of it is what the parent turn actually carries: the parent sees the
 * sub-agent's result through the attach pointer, not its transcript.
 *
 * So the default view draws the spawn root as one capsule labelled with
 * the sub-agent's name and elides the branch behind it. Clicking the
 * capsule brings the whole lane back, clicking again folds it away —
 * ``_spawnExpanded``, the same view-only state the compaction fold uses
 * (dag/rendering.md §9/§12), never persisted.
 *
 * Shape-wise this reuses the compaction capsule (``shapes.ts``) and
 * distinguishes itself by tone: a double stroke and the sub-agent's name
 * rather than a covered count. Same reason the two folds live in separate
 * passes: a compaction capsule stands for a span of *this* chain, a spawn
 * capsule stands for a *different* chain that hangs off this one.
 */

import type { GNode } from "../types";
import { _spawnExpanded } from "../store/globals";

export interface SpawnFold {
  /** Visible graph — sub-branch nodes removed for every folded capsule. */
  visible: GNode[];
  /** ``spawnRootId → the branch's node ids`` for every spawn root, folded
   *  or not. The renderer needs it either way: folded it draws the count,
   *  expanded it still labels the capsule. */
  branchOf: Record<string, string[]>;
  /** ``spawnRootId → sub-agent display name``. Empty string when the
   *  branch was never named. */
  nameOf: Record<string, string>;
}

/** True for a spawn branch root: ``source=agent_spawn`` with no
 *  conversation predecessor (dag/overview.md §4). */
export function isSpawnRoot(n: GNode): boolean {
  return (
    (n as Record<string, unknown>).source === "agent_spawn" && !n.predecessor
  );
}

/** The sub-agent's name for a spawn root.
 *
 * Three sources, in the order they are trustworthy:
 *   1. ``spawned_from.label`` — what the backend resolved from the attach
 *      pointer the runner stamped. The authoritative one.
 *   2. the attach pointer's own ``attach_label``, found by matching its
 *      ``attach_ref`` into this branch. Same fact, resolved here, so a
 *      graph payload without the backend annotation still labels.
 *   3. ``branch_name`` — the name the session recorded for this branch,
 *      stamped by ``webui/graph_builder.py``. Sessions that ran before the
 *      runner started writing labels only have this one.
 *
 * Empty when none of the three exists; the capsule then says "sub-agent". */
function _spawnName(
  root: GNode,
  graph: GNode[],
  branchIds: string[],
  byId: Record<string, GNode>,
): string {
  const direct = (root as Record<string, unknown>).spawned_from as
    | { label?: string | null }
    | undefined;
  const fromRoot = (direct?.label || "").trim();
  if (fromRoot) return fromRoot;

  const inBranch = new Set(branchIds);
  for (const n of graph) {
    if (n.function !== "attach") continue;
    const ref = (n as Record<string, unknown>).attach_ref as string | undefined;
    if (!ref || !inBranch.has(ref)) continue;
    const label = ((n as Record<string, unknown>).attach_label as string) || "";
    if (label.trim()) return label.trim();
  }

  for (const id of [root.id, ...branchIds]) {
    const nm = ((byId[id] as Record<string, unknown> | undefined)
      ?.branch_name as string) || "";
    if (nm.trim()) return nm.trim();
  }
  return "";
}

/** Every node reachable from ``root`` along predecessor/caller edges,
 *  excluding the root itself. Stops at another spawn root so nested
 *  sub-agents keep their own capsule. */
function _branchIds(
  root: GNode,
  byId: Record<string, GNode>,
  kids: Record<string, string[]>,
): string[] {
  const out: string[] = [];
  const seen = new Set<string>([root.id]);
  const stack = (kids[root.id] || []).slice();
  while (stack.length) {
    const id = stack.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    const n = byId[id];
    // A nested spawn root is the head of its own capsule, not part of
    // this branch's body — leave it (and its subtree) to its own fold.
    if (!n || isSpawnRoot(n)) continue;
    out.push(id);
    for (const k of kids[id] || []) stack.push(k);
  }
  return out;
}

/** ``headId`` keeps its own branch drawn: checking out a sub-agent's lane
 *  and then finding it folded away would hide where you are standing. */
export function _foldSpawnBranches(
  graph: GNode[],
  headId?: string | null,
): SpawnFold {
  const branchOf: Record<string, string[]> = Object.create(null);
  const nameOf: Record<string, string> = Object.create(null);
  const roots = graph.filter(isSpawnRoot);
  if (!roots.length) return { visible: graph, branchOf, nameOf };

  // One child index for every root: the branches are disjoint, and a
  // session with two sub-agents has two roots over hundreds of nodes.
  const byId: Record<string, GNode> = Object.create(null);
  const kids: Record<string, string[]> = Object.create(null);
  for (const n of graph) {
    byId[n.id] = n;
    const p = n.predecessor;
    if (p) (kids[p] = kids[p] || []).push(n.id);
    const c = (n as Record<string, unknown>).caller as string | undefined;
    if (c && c !== p && c !== "ROOT") (kids[c] = kids[c] || []).push(n.id);
  }

  for (const root of roots) {
    const ids = _branchIds(root, byId, kids);
    branchOf[root.id] = ids;
    nameOf[root.id] = _spawnName(root, graph, ids, byId);
  }

  const hidden: Record<string, boolean> = Object.create(null);
  for (const rid of Object.keys(branchOf)) {
    if (_spawnExpanded[rid]) continue;
    if (headId && (rid === headId || branchOf[rid].includes(headId))) continue;
    for (const id of branchOf[rid]) hidden[id] = true;
  }
  // A spawn root is never hidden by another root's branch: it is the only
  // handle back to the turns behind it.
  for (const rid of Object.keys(branchOf)) delete hidden[rid];

  return { visible: graph.filter((m) => !hidden[m.id]), branchOf, nameOf };
}
