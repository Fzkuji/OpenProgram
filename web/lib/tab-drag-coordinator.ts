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

/** Dwell-to-merge: hovering the center of a tab this long upgrades the
 *  reorder intent into a merge (Chrome-style stay-to-merge). */
export const MERGE_DWELL_MS = 300;
/** Central fraction of the target slot that counts as the merge zone. */
export const MERGE_DWELL_CENTER_FRACTION = 0.4;

/** True when the cursor sits in the central merge zone of a slot. */
export function isInMergeZone(
  rect: Pick<DOMRect, "left" | "width">,
  clientX: number,
): boolean {
  const progress = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
  const half = MERGE_DWELL_CENTER_FRACTION / 2;
  return progress >= 0.5 - half && progress <= 0.5 + half;
}

/** Position intent is Chrome-style midpoint-only: left of the target's
 *  midpoint → before, right → after. Merge is never positional — it is
 *  a dwell upgrade owned by the strip's onDragOver. */
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
