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

export function resolveTabDropIntent(
  rect: Pick<DOMRect, "left" | "width">,
  clientX: number,
  target: { tabId: string; groupId?: string; memberIndex?: number },
): TabDropIntent {
  const progress = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
  if (progress < 0.25) return { mode: "before", targetTabId: target.tabId };
  if (progress < 0.75) {
    const intent: TabDropIntent = {
      mode: "merge",
      targetTabId: target.tabId,
    };
    if (target.groupId !== undefined) intent.groupId = target.groupId;
    if (target.memberIndex !== undefined) intent.memberIndex = target.memberIndex;
    return intent;
  }
  return { mode: "after", targetTabId: target.tabId };
}

export const dragCoordinator = createTabDragCoordinator();
