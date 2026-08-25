/**
 * Pass: the call-thread model (dag/rendering.md §12).
 *
 * One triangle = everything the model did from one reply until the next
 * user message. Three consequences, all computed here:
 *
 *   * A ``job_followup`` reply — the turn an agent's return triggers —
 *     is not a chain node. A function's return does not get a new node
 *     when the model keeps talking, and an agent's return is the same
 *     event at a different scale. The reply merges into its ANCHOR: the
 *     turn found by climbing ``predecessor`` past every followup.
 *
 *   * A spawned agent's internal turns are not chain nodes either. The
 *     spawn root IS the agent: one glyph on the caller's thread, its
 *     own calls behind it. Everything in the agent's lane merges into
 *     the root.
 *
 *   * What a turn did — every function call, every agent it spawned —
 *     is one time-ordered event sequence, the turn's THREAD. Folded,
 *     the thread is a count on the node's shoulder and nothing else.
 *     Open, every event is a real node hanging on a dotted line beside
 *     the turn; an agent on the line opens recursively into its own
 *     thread, one column further right.
 *
 * View state (which threads are open) lives in ``_threadOpen`` — the
 * same never-persisted contract as the compaction fold.
 */

import type { GNode } from "../types";
import { _threadOpen } from "../store/globals";

export interface ThreadEvent {
  t: number;
  kind: "exec" | "spawn";
  id: string;
}

export interface ThreadModel {
  /** Graph the layout sees: chain nodes + the open threads' items. */
  visible: GNode[];
  /** ``anchorId → ordered events`` — the anchor's whole thread. */
  events: Record<string, ThreadEvent[]>;
  /** ``spawnRootId → the anchor whose thread carries it``. */
  spawnOwnerOf: Record<string, string>;
  /** ``spawnRootId → agent display name`` (tooltip / inspector only —
   *  the canvas draws no captions). */
  nameOf: Record<string, string>;
  /** True while every thread from the chain down to ``id`` is open. */
  isOpen: (id: string) => boolean;
  /** The chain node ``id`` merged into (itself when it didn't). Used to
   *  re-seat HEAD when it points at a merged followup reply. */
  anchorOf: (id: string) => string;
}

function _isRootRef(id: string | null | undefined): boolean {
  return !id || id === "ROOT";
}

/** A spawn branch root: ``source=agent_spawn`` with no conversation
 *  predecessor (dag/overview.md §4). The ROOT sentinel is the same as
 *  empty — a composer-run fallback used to write predecessor=ROOT. */
export function isSpawnRoot(n: GNode): boolean {
  return (
    (n as Record<string, unknown>).source === "agent_spawn"
    && _isRootRef(n.predecessor)
  );
}

function _hangsOnRoot(n: GNode): boolean {
  return _isRootRef(n.caller) && _isRootRef(n.predecessor);
}

/** A conversation-layer node: something the chain itself is made of.
 *  ``merge`` stays on the chain (it is a chain operation); everything
 *  with a caller-tree pedigree (runtime rows, tools, run nodes,
 *  function placeholders) is execution — except a composer-launched
 *  function hung on ROOT, which is the user's explicit action and
 *  sits as a main-lane square under the diamond. */
export function isChainNode(n: GNode): boolean {
  if (n.display === "root") return true;
  if (isSpawnRoot(n)) return false;
  // An agent-internal turn surfaced as a THREAD item (stamped by the
  // thread pass on its visible clone): it keeps its own shape and
  // colour but lays out on the agent's thread, not as a lane.
  if ((n as Record<string, unknown>)._agentTurn) return false;
  if (n.function === "merge") return true;
  if ((n.role === "tool" || n._runNode) && _hangsOnRoot(n)
      && (n.function || n.name)) {
    return true;
  }
  return (
    (n.role === "user" || n.role === "assistant")
    && n.display !== "runtime"
    && !n._runNode
    && !n.function
  );
}

/** The reply a followup chain hangs off: climb ``predecessor`` past
 *  every ``job_followup`` reply. Scarred data — a followup whose
 *  predecessor an old bug rewound — resolves to the same anchor, which
 *  is why the scar stops rendering as a fork. */
function isFollowup(n: GNode | undefined): boolean {
  return !!n
    && (n as Record<string, unknown>).source === "job_followup"
    && n.role === "assistant";
}

/** The agent's name for a spawn root — tooltip/inspector material.
 *  ``spawned_from.label`` when the runner stamped it, else the branch
 *  name the session recorded. */
function _spawnName(
  root: GNode,
  laneFirstName: Record<number, { id: string; name: string }>,
): string {
  const direct = (root as Record<string, unknown>).spawned_from as
    | { label?: string | null }
    | undefined;
  const fromRoot = (direct?.label || "").trim();
  if (fromRoot) return fromRoot;
  const own = ((root as Record<string, unknown>).branch_name as string) || "";
  if (own.trim()) return own.trim();
  const hit = laneFirstName[root._lane || 0];
  return hit && hit.id !== root.id ? hit.name : "";
}

function _composerBefore(
  composerFns: GNode[],
  spawn: GNode,
): GNode | undefined {
  const t = spawn.created_at || 0;
  let lo = 0;
  let hi = composerFns.length - 1;
  let idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((composerFns[mid].created_at || 0) <= t) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (idx < 0) return undefined;
  const bestT = composerFns[idx].created_at || 0;
  while (idx > 0 && (composerFns[idx - 1].created_at || 0) === bestT) idx--;
  return composerFns[idx];
}

export function buildThreadModel(graph: GNode[]): ThreadModel {
  const byId: Record<string, GNode> = Object.create(null);
  const laneSpawn: Record<number, string> = Object.create(null);
  const laneFirstName: Record<number, { id: string; name: string }> =
    Object.create(null);
  const composerFns: GNode[] = [];
  graph.forEach((n) => {
    byId[n.id] = n;
    if (isSpawnRoot(n)) laneSpawn[n._lane || 0] = n.id;
    const nm = ((n as Record<string, unknown>).branch_name as string) || "";
    const trimmed = nm.trim();
    if (trimmed && !laneFirstName[n._lane || 0]) {
      laneFirstName[n._lane || 0] = { id: n.id, name: trimmed };
    }
    if (
      isChainNode(n)
      && n.display !== "root"
      && (n.role === "tool" || n._runNode)
    ) {
      composerFns.push(n);
    }
  });
  composerFns.sort((a, b) => (a.created_at || 0) - (b.created_at || 0));

  // ── anchor resolution ──
  // agent-internal chain node → its lane's spawn root; followup reply →
  // climb predecessors. Composed, so a followup inside an agent still
  // lands on the agent's root.
  const anchorCache: Record<string, string> = Object.create(null);
  function anchorOf(id: string): string {
    if (anchorCache[id]) return anchorCache[id];
    let cur = byId[id];
    let hops = 0;
    while (cur && hops < 200) {
      if (isSpawnRoot(cur)) break;
      const ls = laneSpawn[cur._lane || 0];
      if (ls && cur.id !== ls) { cur = byId[ls]; hops++; continue; }
      if (isFollowup(cur) && cur.predecessor && byId[cur.predecessor]) {
        cur = byId[cur.predecessor]; hops++; continue;
      }
      break;
    }
    const out = cur ? cur.id : id;
    anchorCache[id] = out;
    return out;
  }

  // ── event attribution ──
  // Execution nodes climb predecessor/caller to the first chain node or
  // spawn root; that node's anchor owns the event. Spawn roots climb
  // their caller the same way.
  function ownerOf(n: GNode): string | null {
    let cur: GNode | undefined = n;
    let hops = 0;
    while (cur && hops < 200) {
      const pid: string =
        (cur.caller as string) || (cur.predecessor as string) || "";
      const p: GNode | undefined = pid ? byId[pid] : undefined;
      if (!p) return null;
      if (p.display === "root") {
        // Scarred clean-spawn: caller/predecessor stamped ROOT instead
        // of the composer-launched function. Attach to that function
        // so the diamond is not four fork lanes.
        if (isSpawnRoot(n) || (n as Record<string, unknown>).source === "agent_spawn") {
          const fn = _composerBefore(composerFns, n);
          if (fn) return anchorOf(fn.id);
        }
        return null;
      }
      if (isChainNode(p) || isSpawnRoot(p)) return anchorOf(p.id);
      cur = p;
      hops++;
    }
    return null;
  }

  const events: Record<string, ThreadEvent[]> = Object.create(null);
  const spawnOwnerOf: Record<string, string> = Object.create(null);
  const nameOf: Record<string, string> = Object.create(null);
  graph.forEach((n) => {
    if (n.display === "root") return;
    if (isSpawnRoot(n)) {
      const o = ownerOf(n);
      if (o) {
        spawnOwnerOf[n.id] = o;
        (events[o] = events[o] || []).push(
          { t: n.created_at || 0, kind: "spawn", id: n.id });
      }
      nameOf[n.id] = _spawnName(n, laneFirstName);
      return;
    }
    if (isChainNode(n)) {
      // An agent-internal turn (its lane merged into a spawn root) is
      // the agent's own activity: an event on the AGENT's thread. Once
      // the square opens, its replies come back as triangles in the
      // agent's colour — the conversation is in the graph, one level
      // down, not deleted. Followup replies on the main chain keep
      // merging invisibly (their anchor is a chain turn, not a spawn).
      const a = anchorOf(n.id);
      if (a !== n.id && byId[a] && isSpawnRoot(byId[a])) {
        (events[a] = events[a] || []).push(
          { t: n.created_at || 0, kind: "exec", id: n.id });
      }
      return;
    }
    const o = ownerOf(n);
    if (o) {
      (events[o] = events[o] || []).push(
        { t: n.created_at || 0, kind: "exec", id: n.id });
    }
  });
  Object.values(events).forEach((evs) => evs.sort((a, b) => a.t - b.t));

  // ── one spawn, one glyph ──
  // A dispatch call (``agent`` / ``send_message``) and the spawn root
  // it opened are the same act; drawing both puts two squares on the
  // thread for one event. The spawn root — the node that expands into
  // the agent's own activity — is the one that stays; the dispatch
  // call folds into it (its arguments live on in the spawn's tooltip
  // material). A dispatch that opened NO spawn (the call failed)
  // keeps its own square: that failure is worth a glyph.
  const isDispatchCall = (n: GNode): boolean =>
    n.name === "agent" || n.name === "send_message"
    || n.function === "agent" || n.function === "send_message";
  const dispatchHidden = new Set<string>();
  Object.keys(events).forEach((anchor) => {
    const evs = events[anchor];
    if (!evs.some((e) => e.kind === "spawn")) return;
    events[anchor] = evs.filter((e) => {
      if (e.kind !== "exec") return true;
      const n = byId[e.id];
      if (!n || !isDispatchCall(n)) return true;
      dispatchHidden.add(e.id);
      return false;
    });
  });

  // ── visibility ──
  // A spawn root shows only while every thread above it is open; its
  // items likewise. Chain nodes show unless they merged into an anchor.
  const openCache: Record<string, boolean> = Object.create(null);
  function chainOpen(id: string): boolean {
    if (id in openCache) return openCache[id];
    let ok = !!_threadOpen[id];
    if (ok) {
      const owner = spawnOwnerOf[id];
      if (owner) ok = chainOpen(owner);
      // a chain anchor is always reachable — nothing above it folds it
    }
    openCache[id] = ok;
    return ok;
  }
  const isOpen = (id: string): boolean => chainOpen(id);

  const spawnVisible = (id: string): boolean => {
    const owner = spawnOwnerOf[id];
    return !owner || chainOpen(owner);
  };

  const visible = graph.filter((n) => {
    if (n.display === "root") return true;
    if (isSpawnRoot(n)) return spawnVisible(n.id);
    if (isChainNode(n)) {
      const a = anchorOf(n.id);
      if (a === n.id) return true;
      // Agent-internal turn: a thread item while the agent is open.
      return !!byId[a] && isSpawnRoot(byId[a])
        && chainOpen(a) && spawnVisible(a);
    }
    // execution node: on screen only while its anchor's thread is open
    if (dispatchHidden.has(n.id)) return false;
    const o = ownerOf(n);
    return !!o && chainOpen(o) && (spawnOwnerOf[o] ? spawnVisible(o) : true);
  }).map((n) => {
    // Stamp the surfaced agent turns so downstream passes lay them out
    // as thread items (isChainNode above keys on the stamp). View-only
    // clones — the graph rows themselves stay untouched.
    if (!isChainNode(n) || n.display === "root") return n;
    const a = anchorOf(n.id);
    if (a !== n.id && byId[a] && isSpawnRoot(byId[a])) {
      return { ...n, _agentTurn: true } as GNode;
    }
    return n;
  });

  return { visible, events, spawnOwnerOf, nameOf, isOpen, anchorOf };
}
