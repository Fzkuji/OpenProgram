/**
 * DAG renderer — module-level state.
 *
 * Centralises the mutable singletons that the old monolithic
 * ``history-graph.ts`` carried at the top of the file. Kept as
 * exported ``let`` bindings with explicit setters so the pass
 * / layout / render / interaction modules can read and write
 * the same singletons without circular re-export gymnastics.
 *
 * Zero behaviour change vs the pre-split implementation — this is
 * the same set of variables, just collected in one file.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { GNode, HighlightMode } from "../types";

// ── HEAD / context / highlight mode ───────────────────────────────
export let _currentHead: string | null = null;
export function setCurrentHead(v: string | null): void { _currentHead = v; }

export let _contextSet: Record<string, boolean> | null = null;
export function setContextSet(v: Record<string, boolean> | null): void {
  _contextSet = v;
}

// Per-node degradations the context pipeline applied to the covered
// set, straight off ``/context-range``'s ``nodes`` (dag/rendering.md
// §8). ``aged`` dims the node's stroke, ``spilled`` adds the ▤ mark.
// Never derived here — the backend owns both semantics.
export let _coverageSet: Record<string, { aged: boolean; spilled: boolean }>
  | null = null;
export function setCoverageSet(
  v: Record<string, { aged: boolean; spilled: boolean }> | null,
): void {
  _coverageSet = v;
}

export let _highlightMode: HighlightMode = "viewport";
export function setHighlightMode(v: HighlightMode): void { _highlightMode = v; }

// ── visibility / ancestry / internal sets ─────────────────────────
export let _visibleIds: Record<string, boolean> = Object.create(null);
export function setVisibleIds(v: Record<string, boolean>): void {
  _visibleIds = v;
}

export let _headAncestorSet: Record<string, boolean> = Object.create(null);
export function setHeadAncestorSet(v: Record<string, boolean>): void {
  _headAncestorSet = v;
}

export let _internalSet: Record<string, boolean> = Object.create(null);
export function setInternalSet(v: Record<string, boolean>): void {
  _internalSet = v;
}

export let _internalOwner: Record<string, string> = Object.create(null);
export function setInternalOwner(v: Record<string, string>): void {
  _internalOwner = v;
}

export let _parentOf: Record<string, string> = Object.create(null);
export function setParentOf(v: Record<string, string>): void { _parentOf = v; }

// ── render signature + leaf cache + collapse ──────────────────────
export let _lastSignature: string | null = null;
export function setLastSignature(v: string | null): void {
  _lastSignature = v;
}

export let _leafOfNode: Record<string, string> = Object.create(null);
export function setLeafOfNode(v: Record<string, string>): void {
  _leafOfNode = v;
}

// Which call threads the user clicked open (dag/rendering.md §12).
// Keyed by the anchor node — a chain turn or a spawn root. View state,
// never persisted; absent = folded, which every session starts at.
// Reset on session switch so one session's expansions don't leak into
// the next graph.
export let _threadOpen: Record<string, boolean> = Object.create(null);
export function toggleThreadOpen(id: string): void {
  if (_threadOpen[id]) delete _threadOpen[id];
  else _threadOpen[id] = true;
}
export function setThreadOpen(v: Record<string, boolean>): void {
  _threadOpen = v;
}

export let _threadSession: string | null = null;
export function setThreadSession(v: string | null): void {
  _threadSession = v;
}

// Which compaction capsules the user clicked open (dag/rendering.md §9).
// View state, never persisted: it records how you are looking at the
// graph, not what the graph is. Absent = folded. Reset on session
// switch (pipeline.ts) so one session's expansions don't leak into
// the next graph.
export let _summaryExpanded: Record<string, boolean> = Object.create(null);
export function toggleSummaryExpanded(id: string): void {
  if (_summaryExpanded[id]) delete _summaryExpanded[id];
  else _summaryExpanded[id] = true;
}
export function setSummaryExpanded(v: Record<string, boolean>): void {
  _summaryExpanded = v;
}


// ── infinite-canvas view (see ../interaction/canvas.ts) ──────────
// Where the user has panned and zoomed to. Module state rather than
// per-render state because the graph repaints on every capture and the
// camera must not move when it does. ``_viewSession`` is what tells a
// repaint apart from an arrival at a different graph, which is the one
// case that re-fits.
export let _viewTx = 0;
export let _viewTy = 0;
export let _viewScale = 1;
export function setView(tx: number, ty: number, scale: number): void {
  _viewTx = tx;
  _viewTy = ty;
  _viewScale = scale;
}

export let _viewSession: string | null | undefined;
export function setViewSession(v: string | null): void { _viewSession = v; }

// ── last graph cache (for re-render after collapse toggle / resize) ──
export let _lastGraph: GNode[] | null = null;
export let _lastHeadId: string | null = null;
export function setLastGraph(g: GNode[] | null, h: string | null): void {
  _lastGraph = g;
  _lastHeadId = h;
}

// ── chat-sync wiring latches ──────────────────────────────────────
export let _chatScrollWired = false;
export function setChatScrollWired(v: boolean): void { _chatScrollWired = v; }

export let _chatMutationWired = false;
export function setChatMutationWired(v: boolean): void {
  _chatMutationWired = v;
}

export let _chatMutationObserver: MutationObserver | null = null;
export function setChatMutationObserver(v: MutationObserver | null): void {
  _chatMutationObserver = v;
}
