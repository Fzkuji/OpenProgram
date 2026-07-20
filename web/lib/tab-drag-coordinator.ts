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

/** Fraction of the target slot, measured from the edge the drag first
 *  touches, that merges instead of reordering. */
export const MERGE_LEADING_FRACTION = 0.25;

/** Hold time over the CENTER PANE area before a release there merges
 *  with the active tab. Tabs in the strip do not use a dwell — their
 *  merge zone is positional (see isInMergeZone). */
export const PANE_MERGE_DWELL_MS = 300;

/** Drag direction along the strip: 1 = moving right, -1 = moving left. */
export type DragDirection = 1 | -1;

/** True when the dragged center sits in the target's LEADING quarter —
 *  the quarter the drag runs into first. Dragging right that is the
 *  target's left edge; dragging left, its right edge. Purely positional:
 *  slide into the near quarter and it merges immediately, slide past it
 *  and it is an ordinary reorder. No dwell timer. */
export function isInMergeZone(
  rect: Pick<DOMRect, "left" | "width">,
  centerX: number,
  direction: DragDirection,
): boolean {
  if (rect.width <= 0) return false;
  const progress = (centerX - rect.left) / rect.width;
  if (progress < 0 || progress > 1) return false;
  return direction > 0
    ? progress <= MERGE_LEADING_FRACTION
    : progress >= 1 - MERGE_LEADING_FRACTION;
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
