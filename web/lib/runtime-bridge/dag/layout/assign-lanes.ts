/**
 * Layout step: assign ``_lane`` (column index) to every node + map
 * each node to its branch leaf (used for colouring and click-to-checkout).
 *
 * Strongly prefers backend-supplied ``_lane`` values from
 * ``webui/_graph_layout.py``. The legacy "pin head's ancestry to lane 0"
 * + "leaf-based fallback" paths remain ONLY for the no-backend-layout
 * case; they would otherwise flatten retry siblings that sit on a
 * head-ancestor onto column 0.
 *
 * ``leafOfNode`` is always built locally because the click handlers
 * need a leaf id per node for the checkout target.
 */

import { type GNode, layoutParent } from "../types";

export function _headAncestors(
  byId: Record<string, GNode>,
  headId: string | null,
): string[] {
  const out: string[] = [];
  let cur = headId;
  while (cur && byId[cur]) {
    out.push(cur);
    cur = layoutParent(byId[cur]) || null;
  }
  return out;
}

export function _assignLanes(
  byId: Record<string, GNode>,
  headId: string | null,
): { leaves: GNode[]; laneCount: number; leafOfNode: Record<string, string> } {
  const leaves: GNode[] = [];
  Object.keys(byId).forEach((id) => {
    if (!byId[id].children!.length) leaves.push(byId[id]);
  });

  const leafOfNode: Record<string, string> = Object.create(null);
  let laneCount = 1;

  const backendLanesPreset = Object.keys(byId).some(
    (id) => typeof byId[id]._lane === "number",
  );

  let trunkTip: GNode | null =
    headId && byId[headId] ? byId[headId] : null;
  if (!trunkTip) {
    trunkTip = leaves
      .slice()
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0] || null;
  }
  if (trunkTip) {
    let cur: GNode | null = trunkTip;
    while (cur) {
      if (!backendLanesPreset) cur._lane = 0;
      leafOfNode[cur.id] = trunkTip.id;
      const pp = layoutParent(cur);
      cur = pp ? byId[pp] : null;
    }
  }

  leaves.forEach((leaf) => {
    let cur: GNode | null = leaf;
    while (cur && !(cur.id in leafOfNode)) {
      leafOfNode[cur.id] = leaf.id;
      const pp = layoutParent(cur);
      cur = pp ? byId[pp] : null;
    }
  });

  const backendLanes = Object.keys(byId).some(
    (id) => typeof byId[id]._lane === "number",
  );
  if (backendLanes) {
    Object.keys(byId).forEach((id) => {
      const n = byId[id];
      if (typeof n._lane !== "number") n._lane = 0;
      if ((n._lane as number) + 1 > laneCount) laneCount = (n._lane as number) + 1;
    });
  } else {
    const leafLane: Record<string, number> = Object.create(null);
    const trunkLeafId = trunkTip?.id || null;
    if (trunkLeafId) leafLane[trunkLeafId] = 0;
    leaves
      .filter((l) => l.id !== trunkLeafId)
      .sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
      .forEach((leaf, i) => {
        leafLane[leaf.id] = i + 1;
      });
    laneCount = Math.max(laneCount, Object.keys(leafLane).length);
    Object.keys(byId).forEach((id) => {
      const lid = leafOfNode[id];
      const lane = lid && lid in leafLane ? leafLane[lid] : 0;
      byId[id]._lane = lane;
      if (lane + 1 > laneCount) laneCount = lane + 1;
    });
  }

  Object.keys(byId).forEach((id) => {
    if (byId[id]._lane === undefined) byId[id]._lane = 0;
    if (!(id in leafOfNode)) leafOfNode[id] = id;
  });

  return { leaves, laneCount, leafOfNode };
}
