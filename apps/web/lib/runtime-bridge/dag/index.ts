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

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode } from "./types";
import { runtimeState } from "../state";
import { flushPendingHistoryEmit, render } from "./pipeline";
import { _recomputeVisibility } from "./render/visibility";
import { _installInteractionHandlers } from "./render/interaction";
import {
  contextRangeUnchanged,
  type CoverageNode,
} from "./paint-gate";
import {
  _contextSet,
  _coverageSet,
  _highlightMode,
  _lastGraph,
  _lastHeadId,
  setContextSet,
  setCoverageSet,
  setHighlightMode,
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

export function refreshHistoryContextRange(sessionId: string | null): void {
  if (!sessionId) {
    setHistoryContextRange(null);
    return;
  }
  // Pin the range to the head the GRAPH is rendered at: the fold pass
  // (ghosts, inert capsule) and the white fill must read one head, or
  // a race between checkout and repaint paints "out of the next
  // request" dashes under an in-context white fill.
  const head = _lastHeadId;
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/context-range"
    + (head ? "?head_id=" + encodeURIComponent(head) : ""))
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j) setHistoryContextRange(j.node_ids || [], j.nodes || null);
    })
    .catch(() => {
      /* leave undimmed on failure */
    });
}

export function recomputeHistoryVisibility(): void {
  _recomputeVisibility();
}

/**
 * The graph is showing, and it owns its pane — a lone session tab gets
 * the chat shell plus this graph, and two session tabs split into two
 * `PeerSessionPane`s where the shell is not rendered at all. There is
 * therefore never a transcript beside the graph, so "which bubbles are
 * on screen" has no reading and the white fill means context coverage,
 * full stop (dag/rendering.md §8). No mode to offer, so no switch.
 *
 * Idempotent: safe to call on every render of the host.
 */
export function enterExclusiveCoverageMode(): void {
  if (_highlightMode !== "context") setHighlightMode("context");
  flushPendingHistoryEmit();
  const sid = runtimeState.currentSessionId;
  if (sid) refreshHistoryContextRange(sid);
  else _recomputeVisibility();
}

