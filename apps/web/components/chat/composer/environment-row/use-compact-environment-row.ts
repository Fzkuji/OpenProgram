"use client";

import { useLayoutEffect, type RefObject } from "react";

const COMPACT_HYSTERESIS = 12;
/** 0 full · 1 long labels capped, Local icon-only · 2 git branch names hidden
 *  · 3 every label hidden and the diff badge reduced to a dot (compact). */
const MAX_LEVEL = 3;

/**
 * Squeeze the environment row one level at a time instead of switching
 * between "all labels" and "all icons".
 *
 * When the content changes the row is laid out at each level with
 * transitions off and chips at their natural width (`data-measuring`),
 * synchronously, and each level's need (the last chip's right edge, with
 * the right-aligned chips' auto margins zeroed) is kept. Those needs depend
 * only on the content, so a resize just picks the first level whose need
 * fits the new width, and the row animates from its previous level to it.
 * Laying out every level on every frame of a pane resize made dragging
 * stutter. Moving to a
 * roomier level needs COMPACT_HYSTERESIS px of slack so the row doesn't
 * flap at the boundary. Level 3 also sets `data-compact="true"`, the
 * historical icon-only state other styles key off.
 *
 * A resize picks the level in the ResizeObserver callback, where layout is
 * already done and reading the row's width costs nothing; an animation
 * frame read it before the frame's own layout and forced one, every frame
 * a pane moved. Content changes measure in the next frame, once for a
 * burst of them.
 */
export function useCompactEnvironmentRow(ref: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const row = ref.current;
    if (!row) return;

    let frame = 0;
    let level = 0;
    let needs: number[] | null = null;

    // Writes only what differs: an attribute set to its own value still
    // reaches every MutationObserver on the page.
    const apply = (next: number) => {
      const squeeze = next > 0 ? String(next) : undefined;
      if (row.dataset.squeeze !== squeeze) {
        if (squeeze) row.dataset.squeeze = squeeze;
        else delete row.dataset.squeeze;
      }
      const compact = next >= MAX_LEVEL ? "true" : undefined;
      if (row.dataset.compact !== compact) {
        if (compact) row.dataset.compact = compact;
        else delete row.dataset.compact;
      }
    };

    // Width the chips actually need. scrollWidth can't tell: the right-
    // aligned web chip's auto margin makes the content fill the row
    // exactly, so a squeezed row never looked roomy enough to expand.
    // While measuring, CSS zeroes those auto margins and the need is the
    // right edge of the last laid-out chip.
    const needed = () => {
      const left = row.getBoundingClientRect().left - row.scrollLeft;
      let right = 0;
      for (const child of Array.from(row.children)) {
        // Hover tips render in place (unportaled), as a fixed-position
        // popper wrapper inside the row; a tip over the right-aligned web
        // chip reached past the row and squeezed it to icons. Only
        // in-flow chips count.
        const position = getComputedStyle(child).position;
        if (position === "fixed" || position === "absolute") continue;
        const box = child.getBoundingClientRect();
        if (box.width || box.height) right = Math.max(right, box.right - left);
      }
      // The row's right padding is shadow room, not room for chips.
      return right + parseFloat(getComputedStyle(row).paddingRight || "0");
    };

    const measureNeeds = () => {
      row.dataset.measuring = "true";
      const out: number[] = [];
      for (let candidate = 0; candidate < MAX_LEVEL; candidate += 1) {
        apply(candidate);
        out.push(Math.ceil(needed()));
      }
      apply(level);
      void row.offsetWidth; // settle the previous level before animating
      delete row.dataset.measuring;
      return out;
    };

    const choose = (available: number, levelNeeds: number[]) => {
      for (let candidate = 0; candidate < MAX_LEVEL; candidate += 1) {
        const slack = candidate < level ? COMPACT_HYSTERESIS : 0;
        if (levelNeeds[candidate] <= available + 1 - slack) return candidate;
      }
      return MAX_LEVEL;
    };

    const fit = () => {
      const available = row.clientWidth;
      if (available <= 0) return;
      needs ??= measureNeeds();
      const next = choose(available, needs);
      if (next === level) return;
      level = next;
      apply(level);
    };
    const later = () => {
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; fit(); });
    };
    const measure = () => {
      needs = null;
      later();
    };
    // Needs not measured yet (new content, or a row that had no width) are
    // measured in a frame; known needs are compared right here.
    const resized = () => {
      if (needs) fit();
      else later();
    };

    const resizeObserver =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resized);
    resizeObserver?.observe(row);

    const mutationObserver =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver((records) => {
            // Content only: the row's own squeeze attributes are not content.
            if (records.some((record) => record.type !== "attributes")) measure();
          });
    mutationObserver?.observe(row, {
      childList: true,
      characterData: true,
      subtree: true,
    });

    measure();
    return () => {
      cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      delete row.dataset.squeeze;
      delete row.dataset.measuring;
      delete row.dataset.compact;
    };
  }, [ref]);
}
