/**
 * Projection + viewport bridge for the xyflow center canvas.
 * Imperative pipeline publishes here; React subscribes.
 */

import {
  EMPTY_PROJECTION,
  type CanvasProjection,
  type ProjectionNode,
} from "./types";

type Listener = () => void;

let _projection: CanvasProjection = EMPTY_PROJECTION;
const _listeners = new Set<Listener>();

export type ViewportApi = {
  fitView: (opts?: { padding?: number; duration?: number }) => void;
  zoomIn: (opts?: { duration?: number }) => void;
  zoomOut: (opts?: { duration?: number }) => void;
  setZoom: (zoom: number) => void;
  getZoom: () => number;
};

let _viewportApi: ViewportApi | null = null;

export function getProjection(): CanvasProjection {
  return _projection;
}

export function subscribeProjection(listener: Listener): () => void {
  _listeners.add(listener);
  return () => { _listeners.delete(listener); };
}

function emit(): void {
  for (const l of _listeners) l();
}

export function publishProjection(next: CanvasProjection): void {
  _projection = next;
  emit();
}

export function publishSkeleton(sessionId: string | null): void {
  _projection = {
    ...EMPTY_PROJECTION,
    sessionId,
    skeleton: true,
    revision: _projection.revision + 1,
  };
  emit();
}

export function publishEmpty(sessionId: string | null): void {
  _projection = {
    ...EMPTY_PROJECTION,
    sessionId,
    empty: true,
    revision: _projection.revision + 1,
  };
  emit();
}

/** Patch coverage / in-context flags without a full layout rebuild. */
export function patchProjectionCoverage(
  contextSet: Record<string, boolean> | null,
  coverageSet: Record<string, { aged: boolean; spilled: boolean }> | null,
): void {
  if (_projection.skeleton || _projection.empty || !_projection.nodes.length) {
    return;
  }
  const nodes: ProjectionNode[] = _projection.nodes.map((n) => {
    const cov = coverageSet ? coverageSet[n.id] : undefined;
    return {
      ...n,
      inContext: !!(contextSet && contextSet[n.id]),
      aged: !!(cov && cov.aged),
      spilled: !!(cov && cov.spilled),
    };
  });
  _projection = {
    ..._projection,
    nodes,
    revision: _projection.revision + 1,
  };
  emit();
}

export function registerViewportApi(api: ViewportApi | null): void {
  _viewportApi = api;
}

export function fitXyflow(): void {
  _viewportApi?.fitView({ padding: 0.18, duration: 200 });
}

export function zoomXyflow(dir: 1 | -1): void {
  if (!_viewportApi) return;
  if (dir > 0) _viewportApi.zoomIn({ duration: 120 });
  else _viewportApi.zoomOut({ duration: 120 });
}

export function resetXyflowZoom(): void {
  if (!_viewportApi) return;
  _viewportApi.setZoom(1);
}

export function writeZoomReadout(zoom: number): void {
  if (typeof document === "undefined") return;
  const el = document.querySelector(".dag-hud-zoom");
  if (el) el.textContent = Math.round(zoom * 100) + "%";
}
