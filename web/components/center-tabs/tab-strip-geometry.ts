import type { CSSProperties } from "react";

import { centerTabStripEntries } from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import type { TabDropIntent } from "@/lib/tab-drag-coordinator";

/** Flex gap between strip entries — keep in sync with .strip/.tabsFlow gap. */
export const STRIP_GAP = 8;

/**
 * Chrome-style live reorder: while a drag hovers a before/after zone,
 * every entry between the dragged unit and the assumed insertion point
 * slides one drag-width aside (transform only — layout never changes,
 * so hit targets stay stable). Internal compound reorders keep their
 * own FLIP.
 */
export function computeLiveShifts(
  entries: ReturnType<typeof centerTabStripEntries>,
  draggedIds: ReadonlySet<string>,
  marker: TabDropIntent | null,
  dragWidth: number,
): Map<string, number> {
  const shifts = new Map<string, number>();
  if (!marker || marker.mode === "merge" || dragWidth <= 0) return shifts;
  // ponytail: the merge guard is vestigial for drags (they only ever
  // produce before/after now) but keeps the helper total for any caller.
  const targetIndex = entries.findIndex((entry) =>
    entry.kind === "group"
      ? entry.group.memberIds.includes(marker.targetTabId)
      : entry.tabId === marker.targetTabId,
  );
  if (targetIndex < 0) return shifts;
  const sourceIndex = entries.findIndex((entry) =>
    entry.kind === "group"
      ? entry.group.memberIds.every((tabId) => draggedIds.has(tabId))
      : draggedIds.has(entry.tabId),
  );
  const targetEntry = entries[targetIndex];
  if (
    sourceIndex < 0
    && targetEntry.kind === "group"
    && targetEntry.group.memberIds.some((tabId) => draggedIds.has(tabId))
  ) {
    // Segment dragged over its own compound — internal FLIP territory.
    return shifts;
  }
  const step = dragWidth + STRIP_GAP;
  const insertion = targetIndex + (marker.mode === "after" ? 1 : 0);
  if (sourceIndex >= 0) {
    if (insertion === sourceIndex || insertion === sourceIndex + 1) return shifts;
    if (insertion > sourceIndex) {
      for (let i = sourceIndex + 1; i < insertion; i++) {
        shifts.set(entries[i].id, -step);
      }
    } else {
      for (let i = insertion; i < sourceIndex; i++) {
        shifts.set(entries[i].id, step);
      }
    }
  } else {
    // No same-strip source (cross-window drag or segment leaving its
    // group) — open a gap at the insertion point.
    for (let i = insertion; i < entries.length; i++) {
      shifts.set(entries[i].id, step);
    }
  }
  return shifts;
}

/**
 * Detach-in-progress geometry: the dragged unit is on its way out of the
 * strip, so everything after it slides one drag-width left and the slot it
 * came from closes. Same transform-only mechanism as computeLiveShifts, so
 * the existing `transform 160ms ease` on .tab animates it.
 */
export function closeGapShifts(
  entries: ReturnType<typeof centerTabStripEntries>,
  draggedIds: ReadonlySet<string>,
  dragWidth: number,
): Map<string, number> {
  const shifts = new Map<string, number>();
  if (dragWidth <= 0) return shifts;
  const sourceIndex = entries.findIndex((entry) =>
    entry.kind === "group"
      ? entry.group.memberIds.every((tabId) => draggedIds.has(tabId))
      : draggedIds.has(entry.tabId),
  );
  if (sourceIndex < 0) return shifts;
  const step = dragWidth + STRIP_GAP;
  for (let i = sourceIndex + 1; i < entries.length; i++) {
    shifts.set(entries[i].id, -step);
  }
  return shifts;
}

/** Static slot geometry captured at drag start — hit tests always run
 *  against these unshifted rects, so slid-aside bystanders can never
 *  oscillate under the dragged tab (Chrome's stability property). */
export interface PointerDropTarget {
  tabId: string;
  groupId?: string;
  memberIndex?: number;
  left: number;
  width: number;
}

export function collectPointerDropTargets(flow: HTMLElement): PointerDropTarget[] {
  const state = useCenterTabs.getState();
  const entries = centerTabStripEntries({
    tabIds: state.tabs.map((tab) => tab.id),
    groups: state.groups,
  });
  const targets: PointerDropTarget[] = [];
  for (const entry of entries) {
    const memberIds = entry.kind === "group" ? entry.group.memberIds : [entry.tabId];
    memberIds.forEach((tabId, index) => {
      const inner = flow.querySelector<HTMLElement>(
        `[data-tab-id="${CSS.escape(tabId)}"]`,
      );
      const root = inner?.parentElement;
      if (!root) return;
      const rect = root.getBoundingClientRect();
      const target: PointerDropTarget = entry.kind === "group"
        ? {
            tabId,
            groupId: entry.group.id,
            memberIndex: index + 1,
            left: rect.left,
            width: rect.width,
          }
        : { tabId, left: rect.left, width: rect.width };
      targets.push(target);
    });
  }
  return targets;
}

/** Inline shift style for a bystander tab.
 *
 * Returns `undefined` — NOT `{ transform: "" }` — when the entry has no
 * shift, and the dragged tab never gets one. That matters: the dragged
 * tab's own transform is written imperatively every pointermove, and if
 * this prop ever emitted a transform for it, React would overwrite the
 * live drag offset on the next re-render (markers change several times
 * per drag), snapping the tab back to its slot for a frame. On a fast
 * flick that discarded offset is large, so the tab visibly flies. Keeping
 * the key absent leaves the imperative value untouched.
 */
export function shiftStyle(shiftX: number): CSSProperties | undefined {
  return shiftX ? { transform: `translateX(${shiftX}px)` } : undefined;
}

/** Visible horizontal span a dragged tab may occupy. Desktop uses the
 *  scrollable flow's own box (its client width, not the wider scrolled
 *  content); browser mode has no flow box (display:contents) so the
 *  strip's padded content box stands in. The "+" button sits outside the
 *  flow's max-width, so the flow's right edge already excludes it. */
export function visibleStripBounds(
  flow: HTMLElement | null,
  strip: HTMLElement | null,
): { left: number; right: number } | null {
  if (flow && flow.getClientRects().length > 0) {
    const rect = flow.getBoundingClientRect();
    return { left: rect.left, right: rect.left + flow.clientWidth };
  }
  if (!strip) return null;
  const rect = strip.getBoundingClientRect();
  const style = getComputedStyle(strip);
  return {
    left: rect.left + (Number.parseFloat(style.paddingLeft) || 0),
    right: rect.right - (Number.parseFloat(style.paddingRight) || 0),
  };
}

/** Fraction of `slot` covered by `dragged` — the reorder measure: once
 *  the dragged tab covers half of a neighbour, they swap. Using overlap
 *  (not the dragged centre) keeps it correct for unequal widths. */
export function slotOverlapRatio(
  slot: Pick<PointerDropTarget, "left" | "width">,
  dragged: { left: number; width: number },
): number {
  if (slot.width <= 0) return 0;
  const overlap =
    Math.min(slot.left + slot.width, dragged.left + dragged.width)
    - Math.max(slot.left, dragged.left);
  return overlap <= 0 ? 0 : Math.min(1, overlap / slot.width);
}

/** Nearest slot to the dragged tab's center (containment wins). */
export function pickPointerDropTarget(
  targets: PointerDropTarget[],
  centerX: number,
): PointerDropTarget | null {
  let best: PointerDropTarget | null = null;
  let bestDistance = Infinity;
  for (const target of targets) {
    const distance =
      centerX >= target.left && centerX <= target.left + target.width
        ? 0
        : Math.min(
            Math.abs(centerX - target.left),
            Math.abs(centerX - target.left - target.width),
          );
    if (distance < bestDistance) {
      bestDistance = distance;
      best = target;
    }
  }
  return best;
}
