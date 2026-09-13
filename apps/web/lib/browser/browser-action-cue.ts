export type CuePoint = { x: number; y: number };

export const FLOAT_DRAG_THRESHOLD = 6;
export const FLOAT_CONTROL_SIZE = 36;
export const ACTION_CUE_ICON_SIZE = 28;

export function controlSurfaceVisible(state: string | undefined): boolean {
  return state !== "idle" && state !== "closed" && !!state;
}

export function clampFloatPosition(
  left: number,
  top: number,
  size: { width: number; height: number },
  viewport: { width: number; height: number },
): { left: number; top: number } {
  const maxLeft = Math.max(4, viewport.width - size.width - 4);
  const maxTop = Math.max(4, viewport.height - size.height - 4);
  return {
    left: Math.min(Math.max(4, left), maxLeft),
    top: Math.min(Math.max(4, top), maxTop),
  };
}

export function defaultFloatPosition(
  size: { width: number; height: number },
  viewport: { width: number; height: number },
): { left: number; top: number } {
  return clampFloatPosition(
    viewport.width - size.width - 12,
    viewport.height - size.height - 12,
    size,
    viewport,
  );
}

export function nextCueTravel(
  last: CuePoint | null,
  next: CuePoint,
  reducedMotion: boolean,
): { from: CuePoint | null; to: CuePoint; animateMove: boolean } {
  if (!last || reducedMotion) {
    return { from: null, to: next, animateMove: false };
  }
  if (last.x === next.x && last.y === next.y) {
    return { from: last, to: next, animateMove: false };
  }
  return { from: last, to: next, animateMove: true };
}

export function cueTravelDurationMs(from: CuePoint, to: CuePoint): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy);
  return Math.min(280, Math.max(80, Math.round(dist * 0.4)));
}

export function lerpCuePoint(from: CuePoint, to: CuePoint, t: number): CuePoint {
  const ease = t * (2 - t);
  return {
    x: from.x + (to.x - from.x) * ease,
    y: from.y + (to.y - from.y) * ease,
  };
}

export function cueCancelKey(parts: {
  resourceId: string;
  generation: number;
  geometryRevision?: number;
  navKey?: string;
}): string {
  return [
    parts.resourceId,
    String(parts.generation),
    parts.geometryRevision == null ? "" : String(parts.geometryRevision),
    parts.navKey || "",
  ].join(":");
}

export function prefersCueReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export type CueIdentity = {
  resourceId: string;
  generation: number;
  sequence: number;
};

export function isStaleCueIdentity(last: CueIdentity | null, next: CueIdentity): boolean {
  if (!last) return false;
  if (next.resourceId && last.resourceId && next.resourceId !== last.resourceId) {
    return false;
  }
  if (next.generation < last.generation) return true;
  if (next.generation > last.generation) return false;
  return next.sequence <= last.sequence;
}
