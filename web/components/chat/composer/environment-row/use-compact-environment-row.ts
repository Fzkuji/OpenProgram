"use client";

import { useLayoutEffect, type RefObject } from "react";

const COMPACT_HYSTERESIS = 12;

/** Switch the complete environment row between full labels and icons. */
export function useCompactEnvironmentRow(ref: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const row = ref.current;
    if (!row) return;

    let frame = 0;
    let expandedWidth = 0;

    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const available = row.clientWidth;
        if (available <= 0) return;

        if (row.dataset.compact !== "true") {
          expandedWidth = Math.ceil(row.scrollWidth);
          if (expandedWidth > available + 1) row.dataset.compact = "true";
          return;
        }

        if (available >= expandedWidth + COMPACT_HYSTERESIS) {
          delete row.dataset.compact;
          frame = requestAnimationFrame(measure);
        }
      });
    };

    const resizeObserver =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resizeObserver?.observe(row);

    const mutationObserver =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver((records) => {
            const isInside = (node: Node, selector: string) =>
              node instanceof Element
                ? Boolean(node.closest(selector))
                : Boolean(node.parentElement?.closest(selector));
            if (
              records.every(
                (record) =>
                  isInside(record.target, ".dag-hud-zoom") ||
                  isInside(record.target, ".dag-legend"),
              )
            ) {
              return;
            }
            expandedWidth = 0;
            delete row.dataset.compact;
            measure();
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
      delete row.dataset.compact;
    };
  }, [ref]);
}
