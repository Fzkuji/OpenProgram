"use client";

/**
 * PaneDropMerge — drop a dragged tab anywhere on the center pane area
 * to merge it with the ACTIVE tab (same result as dropping on the
 * active tab's strip button at its merge zone).
 *
 * Same-window drags reuse the store's groupTab/mergeGroup paths (so
 * self-merge and four-member rejections behave exactly like the
 * strip); cross-window drags reuse stageIncomingTransfer with a merge
 * placement targeting the active tab.
 *
 * Only reacts to our own drag payloads (coordinator subject or the
 * cross-window transfer MIME) — external file drops keep their
 * existing composer behavior untouched.
 *
 * Known limit: in the desktop shell a web tab's page body is a native
 * WebContentsView, which never delivers DOM drag events. Session /
 * NTP / file panes accept drops across their full area; for a web
 * pane the DOM drop surface is its toolbar row (and the tab strip).
 */
import { useState } from "react";

import {
  desktopBridge,
  placementForDropIntent,
  stageIncomingTransfer,
} from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import {
  findCenterTabGroup,
  MAX_CENTER_TAB_GROUP_MEMBERS,
} from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  dragCoordinator,
  type TabDragSubject,
} from "@/lib/tab-drag-coordinator";

// Keep in sync with center-tab-strip.tsx.
const TAB_TRANSFER_MIME = "application/x-openprogram-tab-transfer";

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

export function usePaneDropMerge() {
  const { text } = useTranslation();
  const [highlight, setHighlight] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  function isTabDrag(e: React.DragEvent) {
    if (dragCoordinator.current()?.started) return true;
    return e.dataTransfer.types.includes(TAB_TRANSFER_MIME);
  }

  function onDragOver(e: React.DragEvent<HTMLDivElement>) {
    if (!isTabDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setHighlight(true);
  }

  function onDragLeave(e: React.DragEvent<HTMLDivElement>) {
    if (
      e.relatedTarget instanceof Node
      && e.currentTarget.contains(e.relatedTarget)
    ) {
      return;
    }
    setHighlight(false);
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    if (!isTabDrag(e)) return;
    e.preventDefault();
    setHighlight(false);
    const targetId = useCenterTabs.getState().activeId;
    if (!targetId) return;
    const prepared = dragCoordinator.current();
    if (!prepared?.started) {
      // Cross-window: another window prepared this token; stage it
      // here as a merge onto the active tab.
      const bridge = desktopBridge();
      const token = e.dataTransfer.getData(TAB_TRANSFER_MIME);
      if (bridge && token) {
        void stageIncomingTransfer(
          bridge,
          token,
          placementForDropIntent({ mode: "merge", targetTabId: targetId }),
        );
        setAnnouncement(text("Tab moved", "标签已移动"));
      }
      return;
    }
    const result = mergeSubjectIntoTab(prepared.subject, targetId);
    if (result === "ok") {
      const committed = dragCoordinator.commit();
      if (committed?.transferToken) {
        // Same-window move — release the unused main-process token.
        void desktopBridge()?.tabTransfer.cancel(committed.transferToken);
      }
      setAnnouncement(
        text("Tabs merged into split view", "标签已合并到分屏"),
      );
      return;
    }
    const cancelled = dragCoordinator.cancel();
    if (cancelled?.transferToken) {
      void desktopBridge()?.tabTransfer.cancel(cancelled.transferToken);
    }
    setAnnouncement(
      result === "full"
        ? text("Split supports up to three tabs", "分屏最多支持三个标签")
        : text("Tab move cancelled", "标签移动已取消"),
    );
  }

  const overlay = (
    <>
      {highlight ? (
        <div
          data-testid="pane-drop-merge-overlay"
          aria-hidden="true"
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
      ) : null}
      <span
        role="status"
        aria-live="polite"
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          overflow: "hidden",
          clipPath: "inset(50%)",
        }}
      >
        {announcement}
      </span>
    </>
  );

  return { onDragOver, onDragLeave, onDrop, overlay };
}
