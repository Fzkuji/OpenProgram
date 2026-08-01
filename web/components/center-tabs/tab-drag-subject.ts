import {
  dragCoordinator,
  type TabDragSubject,
  type TabDropIntent,
} from "@/lib/tab-drag-coordinator";
import { desktopBridge } from "@/lib/desktop-bridge";
import {
  findCenterTabGroup,
  MAX_CENTER_TAB_GROUP_MEMBERS,
} from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";

let removePreparedReleaseListener: (() => void) | null = null;

/** Remember the "cancel an unstarted prepare on pointerup" listener so a
 *  later prepare / teardown can drop it. Module state, like the coordinator
 *  it guards — a drag is a process-wide singleton. */
export function setPreparedReleaseListener(remove: (() => void) | null) {
  removePreparedReleaseListener = remove;
}

export function removeReleaseListener() {
  removePreparedReleaseListener?.();
  removePreparedReleaseListener = null;
}

/** Cancel the local coordinator AND its prepared main-process token. */
export function cancelCoordinator() {
  const cancelled = dragCoordinator.cancel();
  if (cancelled?.transferToken) {
    void desktopBridge()?.tabTransfer.cancel(cancelled.transferToken);
  }
  return cancelled;
}

export function snapshotTabDragSubject(subject: TabDragSubject): TabDragSubject {
  if (subject.kind === "tab") return { kind: "tab", tabIds: [subject.tabIds[0]] };
  if (subject.kind === "segment") {
    return {
      ...subject,
      tabIds: [subject.tabIds[0]],
      sourceGroup: {
        ...subject.sourceGroup,
        memberIds: [...subject.sourceGroup.memberIds],
        visibleIds: [...subject.sourceGroup.visibleIds],
      },
    };
  }
  return {
    ...subject,
    tabIds: [...subject.tabIds],
    sourceGroup: {
      ...subject.sourceGroup,
      memberIds: [...subject.sourceGroup.memberIds],
      visibleIds: [...subject.sourceGroup.visibleIds],
    },
  };
}

/** The drag subject a context-menu action operates on: a lone tab, or the
 *  segment it occupies inside its compound. */
export function menuDragSubject(tabId: string): TabDragSubject | null {
  const state = useCenterTabs.getState();
  if (!state.tabs.some((tab) => tab.id === tabId)) return null;
  const sourceGroup = findCenterTabGroup(state.groups, tabId);
  if (!sourceGroup) return { kind: "tab", tabIds: [tabId] };
  return snapshotTabDragSubject({
    kind: "segment",
    tabIds: [tabId],
    sourceGroup,
    memberIndex: sourceGroup.memberIds.indexOf(tabId),
  });
}

export function isFourthMemberRejection(
  subject: TabDragSubject,
  intent: TabDropIntent,
) {
  if (intent.mode !== "merge") return false;
  const targetGroup = findCenterTabGroup(
    useCenterTabs.getState().groups,
    intent.targetTabId,
  );
  if (subject.kind === "group") {
    if (targetGroup?.id === subject.sourceGroup.id) return false;
    return (targetGroup?.memberIds.length ?? 1) + subject.tabIds.length
      > MAX_CENTER_TAB_GROUP_MEMBERS;
  }
  if (targetGroup?.memberIds.includes(subject.tabIds[0])) return false;
  return (targetGroup?.memberIds.length ?? 1) + 1
    > MAX_CENTER_TAB_GROUP_MEMBERS;
}
