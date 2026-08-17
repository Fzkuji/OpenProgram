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

/** Chrome's close-with-the-mouse width freeze.
 *
 * Closing a tab by clicking its × normally reflows the survivors wider at
 * once, so the next × lands somewhere else and the user cannot keep
 * clicking in place. Chrome pins every remaining tab to the width it had
 * at the moment of the close; the survivors only slide left to fill the
 * gap. The lock is released when the cursor leaves the strip.
 *
 * The lock is written inline on each strip child, over the four
 * properties that decide a flex item's width: `width`, `flex-basis`,
 * `max-width` (the three the stylesheet sizes tabs with, so the lock
 * beats both the browser `flex: 0 1 200px` rule and the desktop 240px
 * override) and `flex-shrink`. Pinning the shrink factor is what makes
 * the freeze hold: a full strip shrinks every tab below its basis, so
 * closing one relieves the overflow and the survivors would shrink LESS
 * and still grow — the exact case the freeze exists to prevent.
 * Releasing clears the inline values and the CSS transition animates the
 * survivors back out.
 *
 * Closing tabs keep their natural width — the exit animation shrinks
 * them, and pinning that width would freeze the animation mid-collapse.
 */
export function freezeStripWidths(flow: HTMLElement | null) {
  if (!flow) return;
  // Read EVERY width before writing ANY pin. Pinning a child with
  // flex-shrink:0 changes the flex distribution, so an interleaved
  // measure-pin walk records a descending staircase — each later tab
  // measured after its predecessors left the flexible pool (observed:
  // 216 → 211 → 191 → 167 → 94 on a 6-tab desktop row) and the whole
  // strip visibly shrinks on the first ×.
  const measured: Array<[HTMLElement, number]> = [];
  for (const child of Array.from(flow.children) as HTMLElement[]) {
    if (child.dataset.tabClosing === "true") continue;
    const width = child.getBoundingClientRect().width;
    if (width > 0) measured.push([child, width]);
  }
  for (const [child, width] of measured) {
    const px = `${width}px`;
    child.style.width = px;
    child.style.flexBasis = px;
    child.style.maxWidth = px;
    child.style.flexShrink = "0";
    child.dataset.widthFrozen = "true";
  }
}

/** Clearing the inline widths and dropping the marker in one pass would
 *  remove the width transition (which is keyed on the marker) in the same
 *  frame the width changes, so the survivors would snap instead of easing
 *  back. The marker is therefore downgraded to "released" — still matched
 *  by the transition rule — and only removed once the ease has finished.
 *  A later freeze simply overwrites it. */
export function releaseStripWidths(flow: HTMLElement | null) {
  if (!flow) return;
  for (const child of Array.from(
    flow.querySelectorAll<HTMLElement>('[data-width-frozen="true"]'),
  )) {
    child.style.width = "";
    child.style.flexBasis = "";
    child.style.maxWidth = "";
    child.style.flexShrink = "";
    child.dataset.widthFrozen = "released";
    child.addEventListener(
      "transitionend",
      () => {
        if (child.dataset.widthFrozen === "released") {
          delete child.dataset.widthFrozen;
        }
      },
      { once: true },
    );
  }
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
