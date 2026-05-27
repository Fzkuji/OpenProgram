/**
 * DAG renderer — public entry.
 *
 * Imported for side effects by ``AppShell`` (see
 * ``web/components/app-shell.tsx``). Wires the document-level click /
 * dblclick handlers, installs the ``HGW.*`` window bridges that the
 * legacy script entry points and WebSocket handlers use, and exports
 * the same surface the old ``history-graph.ts`` exported so existing
 * consumers don't change.
 *
 * See ``./README.md`` for the directory layout and the pass pipeline.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, HGW, type HighlightMode } from "./types";
import { render } from "./pipeline";
import { _recomputeVisibility } from "./render/visibility";
import { _installInteractionHandlers } from "./render/interaction";
import {
  _contextSet,
  _highlightMode,
  _lastGraph,
  _lastHeadId,
  setContextSet,
  setHighlightMode,
  setLastGraph,
  setLastSignature,
} from "./store/globals";

// Install the document-level click + dblclick listeners exactly once,
// at module load. The handler needs a re-render callback so the panel
// rebuilds after a collapse toggle.
_installInteractionHandlers(() => {
  if (_lastGraph) render(_lastGraph, _lastHeadId);
});

export function renderHistoryGraph(graph: GNode[], headId: string | null): void {
  setLastGraph(graph, headId);
  render(graph, headId);
}

export function repaintBranchTags(): void {
  if (_lastGraph) render(_lastGraph, _lastHeadId);
}

export function setHistoryContextRange(ids: string[] | null): void {
  if (!ids || !ids.length) {
    setContextSet(null);
  } else {
    const m: Record<string, boolean> = Object.create(null);
    for (let i = 0; i < ids.length; i++) m[ids[i]] = true;
    setContextSet(m);
  }
  if (_lastGraph) {
    setLastSignature(null);
    render(_lastGraph, _lastHeadId);
  }
  void _contextSet;
}

export function refreshHistoryContextRange(sessionId: string | null): void {
  if (!sessionId) {
    setHistoryContextRange(null);
    return;
  }
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/context-range")
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j) setHistoryContextRange(j.node_ids || []);
    })
    .catch(() => {
      /* leave undimmed on failure */
    });
}

export function recomputeHistoryVisibility(): void {
  _recomputeVisibility();
}

export function getHistoryHighlightMode(): HighlightMode {
  return _highlightMode;
}

export function setHistoryHighlightMode(mode: HighlightMode): void {
  if (mode !== "viewport" && mode !== "context") return;
  if (_highlightMode === mode) return;
  setHighlightMode(mode);
  if (mode === "context") {
    const sid = HGW.currentSessionId;
    if (sid) refreshHistoryContextRange(sid);
  }
  _recomputeVisibility();
}

/* ===== window bridges ============================================ */

HGW.renderHistoryGraph = renderHistoryGraph;
HGW.repaintBranchTags = repaintBranchTags;
HGW.setHistoryContextRange = setHistoryContextRange;
HGW.refreshHistoryContextRange = refreshHistoryContextRange;
HGW.recomputeHistoryVisibility = recomputeHistoryVisibility;
HGW.setHistoryHighlightMode = setHistoryHighlightMode;
HGW.getHistoryHighlightMode = getHistoryHighlightMode;
