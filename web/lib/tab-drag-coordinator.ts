import type { CenterTabGroup } from "@/lib/state/center-tab-groups";

export type TabDragSubject =
  | { kind: "tab"; tabIds: [string] }
  | {
      kind: "segment";
      tabIds: [string];
      sourceGroup: CenterTabGroup;
      memberIndex: number;
    }
  | { kind: "group"; tabIds: string[]; sourceGroup: CenterTabGroup };

export type TabDropIntent =
  | { mode: "before"; targetTabId: string }
  | {
      mode: "merge";
      targetTabId: string;
      groupId?: string;
      memberIndex?: number;
    }
  | { mode: "after"; targetTabId: string };

export interface PreparedTabDrag {
  subject: TabDragSubject;
  transferToken?: string;
  started: boolean;
  cancelled: boolean;
  committed: boolean;
}

export function createTabDragCoordinator(): {
  prepare(prepared: PreparedTabDrag): void;
  current(): PreparedTabDrag | null;
  start(): PreparedTabDrag | null;
  cancel(): PreparedTabDrag | null;
  commit(): PreparedTabDrag | null;
  clear(): void;
} {
  let prepared: PreparedTabDrag | null = null;

  return {
    prepare(next) {
      prepared = next;
      return next;
    },
    current() {
      return prepared;
    },
    start() {
      if (!prepared || prepared.started || prepared.cancelled || prepared.committed) {
        return null;
      }
      prepared.started = true;
      return prepared;
    },
    cancel() {
      if (!prepared || prepared.cancelled || prepared.committed) return null;
      const cancelled = prepared;
      cancelled.cancelled = true;
      prepared = null;
      return cancelled;
    },
    commit() {
      if (!prepared || prepared.cancelled || prepared.committed) return null;
      const committed = prepared;
      committed.committed = true;
      prepared = null;
      return committed;
    },
    clear() {
      prepared = null;
    },
  };
}

/** Pointer travel (px) before a pressed tab becomes a drag. */
export const DRAG_START_THRESHOLD_PX = 4;
/** Vertical pointer distance (px) from the press point that flips a
 *  pointer drag into detach-to-new-window mode. */
export const DETACH_DISTANCE_PX = 48;

/** Fraction of a slot at EACH end that merges instead of reordering. */
export const MERGE_EDGE_FRACTION = 0.25;

/** Hold time over the CENTER PANE area before a release there merges
 *  with the active tab. Tabs in the strip do not use a dwell — their
 *  merge zone is positional (see isInMergeZone). */
export const PANE_MERGE_DWELL_MS = 300;

/** True when the dragged center sits in either EDGE quarter of the
 *  target: [0, 0.25] or [0.75, 1] merge, the middle half reorders.
 *  Fixed slot geometry — no direction, no dwell, no state — so the two
 *  travel directions are symmetric by construction and a drag returning
 *  over a neighbour hits the same zones it hit on the way out. */
export function isInMergeZone(
  rect: Pick<DOMRect, "left" | "width">,
  centerX: number,
): boolean {
  if (rect.width <= 0) return false;
  const progress = (centerX - rect.left) / rect.width;
  if (progress < 0 || progress > 1) return false;
  return progress <= MERGE_EDGE_FRACTION || progress >= 1 - MERGE_EDGE_FRACTION;
}

/** Position intent is Chrome-style midpoint-only: left of the target's
 *  midpoint → before, right → after. Merge is decided separately by
 *  isInMergeZone and takes precedence in the strip's pointer path. */
export function resolveTabDropIntent(
  rect: Pick<DOMRect, "left" | "width">,
  clientX: number,
  target: { tabId: string; groupId?: string; memberIndex?: number },
): TabDropIntent {
  const progress = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
  if (progress < 0.5) return { mode: "before", targetTabId: target.tabId };
  return { mode: "after", targetTabId: target.tabId };
}

export const dragCoordinator = createTabDragCoordinator();
