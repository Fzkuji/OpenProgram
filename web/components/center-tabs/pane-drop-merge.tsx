"use client";

/**
 * PaneDropMerge — release a pointer-dragged tab over the center pane
 * area to merge it with the ACTIVE tab (same result as dwelling on the
 * active tab's strip button at its merge zone).
 *
 * The strip's pointer drag engine owns the gesture: it hit-tests the
 * pointer against the registered pane surface, arms the merge after
 * MERGE_DWELL_MS, drives the highlight through setPaneMergeHighlight,
 * and commits via mergeSubjectIntoTab. This module only contributes
 * the merge semantics + the registered surface & highlight overlay.
 *
 * Known limit: in the desktop shell a web tab's page body is a native
 * WebContentsView; the registered surface rect still covers it, so the
 * pointer path works across the full pane area (unlike the old HTML5
 * path, which never received drag events over native views).
 */
import { useCallback, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import {
  findCenterTabGroup,
  MAX_CENTER_TAB_GROUP_MEMBERS,
} from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { type TabDragSubject } from "@/lib/tab-drag-coordinator";

type MergeResult = "ok" | "full" | "noop";

/** Merge the dragged subject into the active tab's group (or a new
 *  group of the two), mirroring the strip's merge-intent semantics. */
export function mergeSubjectIntoTab(
  subject: TabDragSubject,
  targetId: string,
): MergeResult {
  const state = useCenterTabs.getState();
  const targetGroup = findCenterTabGroup(state.groups, targetId);
  if (subject.kind === "group") {
    if (targetGroup?.id === subject.sourceGroup.id) return "noop";
    if (
      (targetGroup?.memberIds.length ?? 1) + subject.tabIds.length
        > MAX_CENTER_TAB_GROUP_MEMBERS
    ) {
      return "full";
    }
    return state.mergeGroup(subject.sourceGroup.id, targetId, 1)
      ? "ok"
      : "noop";
  }
  const sourceId = subject.tabIds[0];
  if (sourceId === targetId || targetGroup?.memberIds.includes(sourceId)) {
    return "noop";
  }
  if ((targetGroup?.memberIds.length ?? 1) + 1 > MAX_CENTER_TAB_GROUP_MEMBERS) {
    return "full";
  }
  return state.groupTab(sourceId, targetId, 1, targetGroup?.id)
    ? "ok"
    : "noop";
}

// ------------------------------------------------------ surface registry

let paneMergeSurface: {
  element: HTMLElement;
  setHighlight: (on: boolean) => void;
} | null = null;

/** True when the pointer sits inside the registered pane surface. */
export function paneMergeSurfaceContains(x: number, y: number): boolean {
  const element = paneMergeSurface?.element;
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

/** Toggle the pane merge highlight (driven by the strip's drag engine). */
export function setPaneMergeHighlight(on: boolean): void {
  paneMergeSurface?.setHighlight(on);
}

export function usePaneDropMerge() {
  const { text } = useTranslation();
  const [highlight, setHighlight] = useState(false);

  // Callback ref the app shell attaches to the center pane container.
  const surfaceRef = useCallback((element: HTMLDivElement | null) => {
    if (element) {
      paneMergeSurface = { element, setHighlight };
    } else if (paneMergeSurface?.setHighlight === setHighlight) {
      paneMergeSurface = null;
    }
  }, []);

  const overlay = highlight ? (
    <div
      data-testid="pane-drop-merge-overlay"
      aria-hidden="true"
      title={text("Merge into split view", "合并到分屏")}
      style={{
        position: "absolute",
        inset: 4,
        zIndex: 40,
        pointerEvents: "none",
        borderRadius: 8,
        outline: "2px solid var(--accent-blue)",
        outlineOffset: -2,
        background:
          "color-mix(in srgb, var(--accent-blue) 8%, transparent)",
      }}
    />
  ) : null;

  return { surfaceRef, overlay };
}
