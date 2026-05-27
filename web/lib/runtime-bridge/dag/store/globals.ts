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

export let _collapsed: Record<string, boolean> = Object.create(null);
export function setCollapsed(v: Record<string, boolean>): void {
  _collapsed = v;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export let _seenCollapsible: Record<string, boolean> = Object.create(null);
export function setSeenCollapsible(v: Record<string, boolean>): void {
  _seenCollapsible = v;
}

export let _collapseSession: string | null = null;
export function setCollapseSession(v: string | null): void {
  _collapseSession = v;
}

// ── last graph cache (for re-render after collapse toggle / resize) ──
export let _lastGraph: GNode[] | null = null;
export let _lastHeadId: string | null = null;
export function setLastGraph(g: GNode[] | null, h: string | null): void {
  _lastGraph = g;
  _lastHeadId = h;
}

// ── user-scroll latch (suppress auto-scroll during manual wheel) ──
export let _userScrolledGraph = false;
export let _userScrollTimer = 0;
export function setUserScrolledGraph(v: boolean): void {
  _userScrolledGraph = v;
}
export function setUserScrollTimer(v: number): void {
  _userScrollTimer = v;
}

// ── chat-sync wiring latches ──────────────────────────────────────
export let _chatScrollWired = false;
export function setChatScrollWired(v: boolean): void { _chatScrollWired = v; }

export let _chatMutationWired = false;
export function setChatMutationWired(v: boolean): void {
  _chatMutationWired = v;
}

export let _panelResizeWired = false;
export function setPanelResizeWired(v: boolean): void {
  _panelResizeWired = v;
}
