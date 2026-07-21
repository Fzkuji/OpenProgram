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
/** Hysteresis band (px) around the tab strip's vertical edges. Detach begins
 *  the instant the cursor leaves the strip's rectangle by this much (Chrome
 *  has no distance dead-zone), and only comes home when the cursor is back
 *  this far inside — so a cursor resting on the edge does not thrash. */
export const DETACH_HYSTERESIS_PX = 8;

/** Share of a neighbour the dragged tab must cover before they swap.
 *  Overlap ÷ neighbour width, so unequal widths behave correctly; for
 *  equal widths this is exactly "the leading edge passed its midpoint". */
export const SWAP_OVERLAP_RATIO = 0.5;

/** Chrome-style midpoint reorder: left of the target's midpoint → before,
 *  right → after. Dragging in the strip only ever reorders — merging into
 *  a split is a separate, explicit action from the tab context menu, never
 *  a drag outcome. (The "merge" intent mode survives for that menu path.) */
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
