"use client";

/**
 * Committing a drop / keyboard move to the tabs store.
 *
 * `targetBeforeId` resolves an intent's target tab to the store's
 * insert-before id (group-aware: a group is moved as a unit). `applyDrop`
 * turns a `TabDropIntent` into the matching store action — merge into a
 * group, reorder inside one, or a plain strip move.
 */
import {
  findCenterTabGroup,
} from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import type {
  dragCoordinator,
  TabDropIntent,
} from "@/lib/tab-drag-coordinator";

export function useTabDropActions() {
  const moveTab = useCenterTabs((s) => s.moveTab);
  const moveGroup = useCenterTabs((s) => s.moveGroup);
  const moveGroupMember = useCenterTabs((s) => s.moveGroupMember);
  const groupTab = useCenterTabs((s) => s.groupTab);
  const mergeGroup = useCenterTabs((s) => s.mergeGroup);
  const ungroupTab = useCenterTabs((s) => s.ungroupTab);

  function targetBeforeId(targetTabId: string, after: boolean): string | null {
    const state = useCenterTabs.getState();
    const targetGroup = findCenterTabGroup(state.groups, targetTabId);
    if (!after) return targetGroup?.memberIds[0] ?? targetTabId;
    const lastTargetId = targetGroup?.memberIds.at(-1) ?? targetTabId;
    const targetIndex = state.tabs.findIndex((tab) => tab.id === lastTargetId);
    return state.tabs[targetIndex + 1]?.id ?? null;
  }

  function applyDrop(
    prepared: NonNullable<ReturnType<typeof dragCoordinator.current>>,
    intent: TabDropIntent,
  ) {
    const subject = prepared.subject;
    if (intent.mode === "merge") {
      if (subject.kind === "group") {
        return mergeGroup(
          subject.sourceGroup.id,
          intent.targetTabId,
          intent.memberIndex ?? 1,
        );
      }
      if (subject.kind === "segment" && intent.groupId === subject.sourceGroup.id) {
        const currentGroup = useCenterTabs.getState().groups.find(
          (group) => group.id === subject.sourceGroup.id,
        );
        if (!currentGroup) return false;
        let toIndex = intent.memberIndex ?? currentGroup.memberIds.length;
        const sourceIndex = currentGroup.memberIds.indexOf(subject.tabIds[0]);
        if (sourceIndex >= 0 && sourceIndex < toIndex) toIndex -= 1;
        moveGroupMember(subject.sourceGroup.id, subject.tabIds[0], toIndex);
        return true;
      }
      return groupTab(
        subject.tabIds[0],
        intent.targetTabId,
        intent.memberIndex ?? 1,
        intent.groupId,
      );
    }

    const beforeId = targetBeforeId(intent.targetTabId, intent.mode === "after");
    if (subject.kind === "group") {
      if (subject.tabIds.includes(intent.targetTabId)) return true;
      moveGroup(subject.sourceGroup.id, beforeId);
      return true;
    }
    if (subject.kind === "segment") {
      const targetGroup = findCenterTabGroup(
        useCenterTabs.getState().groups,
        intent.targetTabId,
      );
      if (targetGroup?.id === subject.sourceGroup.id) {
        const targetIndex = targetGroup.memberIds.indexOf(intent.targetTabId);
        let toIndex = targetIndex + (intent.mode === "after" ? 1 : 0);
        const sourceIndex = targetGroup.memberIds.indexOf(subject.tabIds[0]);
        if (sourceIndex >= 0 && sourceIndex < toIndex) toIndex -= 1;
        moveGroupMember(subject.sourceGroup.id, subject.tabIds[0], toIndex);
      } else {
        ungroupTab(subject.tabIds[0]);
        moveTab(subject.tabIds[0], beforeId);
      }
      return true;
    }
    moveTab(subject.tabIds[0], beforeId);
    return true;
  }

  return { targetBeforeId, applyDrop };
}
