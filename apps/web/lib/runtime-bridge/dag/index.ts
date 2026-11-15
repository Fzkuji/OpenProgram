/**
 * DAG renderer — public entry.
 *
 * Imported for side effects by ``AppShell`` (see
 * ``apps/web/components/app-shell.tsx``). Wires the document-level click /
 * dblclick handlers and exports the same surface the old
 * ``history-graph.ts`` exported so existing consumers don't change.
 *
 * See ``./README.md`` for the directory layout and the pass pipeline.
 */

 

import { type GNode } from "./types";
import { runtimeState } from "../state";
import { flushPendingHistoryEmit, render } from "./pipeline";
import { _recomputeVisibility } from "./render/visibility";
import { setMainAgentShape } from "./render/agent-look";
import { _installInteractionHandlers } from "./interaction/node/nodes";
import {
  contextRangeUnchanged,
  type CoverageNode,
} from "./paint-gate";
import {
  _contextSet,
  _coverageSet,
  _lastGraph,
  _lastHeadId,
  setContextSet,
  setCoverageSet,
  setLastGraph,
  setLastSignature,
} from "./store/globals";

export type { CoverageNode } from "./paint-gate";

// Install the document-level click + dblclick listeners exactly once,
// at module load. The handler needs a re-render callback so the panel
// rebuilds after a collapse toggle.
_installInteractionHandlers(() => {
  if (_lastGraph) render(_lastGraph, _lastHeadId);
});

// A locale switch only re-renders React subscribers; this imperative
// layer bakes its strings into the SVG at draw time. ``lib/i18n``'s
// ``setLocale`` stamps ``<html lang>``, so that attribute is the change
// notification — repaint past the signature dedup when it flips.
if (typeof document !== "undefined"
    && typeof MutationObserver !== "undefined") {
  new MutationObserver(() => {
    if (!_lastGraph) return;
    setLastSignature(null);
    render(_lastGraph, _lastHeadId);
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["lang"],
  });
}

export function renderHistoryGraph(graph: GNode[], headId: string | null): void {
  setLastGraph(graph, headId);
  render(graph, headId);
}

/** The view reports the conversation's Agent character; its replies wear
 *  it (render/agent-look.ts). A change repaints the graph. */
export function setDagAgentLook(shape: string | null): void {
  if (!setMainAgentShape(shape)) return;
  if (_lastGraph) render(_lastGraph, _lastHeadId);
}

export function repaintBranchTags(): void {
  // Branch badges read runtimeState._branchesByConv at draw time, which is
  // NOT part of the render signature — force the repaint past the
  // signature dedup, or a badge-only change (branches fetched after
  // the first draw) silently no-ops and the DAG never shows names.
  setLastSignature(null);
  if (_lastGraph) render(_lastGraph, _lastHeadId);
}

export function setHistoryContextRange(
  ids: string[] | null,
  coverage?: CoverageNode[] | null,
): void {
  if (contextRangeUnchanged(_contextSet, _coverageSet, ids, coverage)) return;
  if (!ids || !ids.length) {
    setContextSet(null);
    setCoverageSet(null);
  } else {
    const m: Record<string, boolean> = Object.create(null);
    for (let i = 0; i < ids.length; i++) m[ids[i]] = true;
    setContextSet(m);
    if (coverage && coverage.length) {
      const c: Record<string, { aged: boolean; spilled: boolean }> =
        Object.create(null);
      for (const row of coverage) {
        if (!row || !row.node_id) continue;
        c[row.node_id] = { aged: !!row.aged, spilled: !!row.spilled };
      }
      setCoverageSet(c);
    } else {
      setCoverageSet(null);
    }
  }
  if (_lastGraph) {
    // Aged / spilled / membership are none of them part of the render
    // signature, so without busting it the repaint silently no-ops and
    // the graph keeps painting the previous coverage.
    setLastSignature(null);
    render(_lastGraph, _lastHeadId);
  }
}

// Opening a conversation asks for its range from several places at once.
const RANGE_READ_WINDOW_MS = 1500;
let lastRangeRead: { url: string; at: number } | null = null;

/** `changed`: the context just changed (a turn ended, a compaction), so a
 *  range read a moment ago no longer holds. */
export function refreshHistoryContextRange(
  sessionId: string | null,
  { changed = false }: { changed?: boolean } = {},
): void {
  if (!sessionId) {
    setHistoryContextRange(null);
    return;
  }
  // Pin the range to the head the GRAPH is rendered at: the fold pass
  // (ghosts, inert capsule) and the white fill must read one head, or
  // a race between checkout and repaint paints "out of the next
  // request" dashes under an in-context white fill.
  const head = _lastHeadId;
  const url = "/api/sessions/" + encodeURIComponent(sessionId) + "/context-range"
    + (head ? "?head_id=" + encodeURIComponent(head) : "");
  const now = Date.now();
  if (!changed && lastRangeRead?.url === url && now - lastRangeRead.at < RANGE_READ_WINDOW_MS) return;
  lastRangeRead = { url, at: now };
  fetch(url)
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j) setHistoryContextRange(j.node_ids || [], j.nodes || null);
    })
    .catch(() => {
      /* leave undimmed on failure */
    });
}

/**
 * The graph is showing: paint whatever was owed while it was hidden and
 * fetch the coverage it fills in. The graph owns its pane — there is
 * never a transcript beside it — so the fill means context coverage,
 * full stop (dag/rendering.md §8); it never follows a transcript's
 * scroll.
 *
 * Idempotent: safe to call on every render of the host.
 */
export function enterExclusiveCoverageMode(): void {
  flushPendingHistoryEmit();
  const sid = runtimeState.currentSessionId;
  if (sid) refreshHistoryContextRange(sid);
  else _recomputeVisibility();
}
