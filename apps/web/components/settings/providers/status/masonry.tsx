"use client";

import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { placeMasonry } from "./model";

/** Below this width the list is one column (the grid's old `@max-[720px]`). */
const ONE_COLUMN_BELOW = 720;
const GAP = 12;

/**
 * The cards as a masonry: two columns (one below 720 px), each card at its
 * own height, placed in list order into the shortest column, directly under
 * the card above it with the grid's gap. Heights are measured (a
 * ResizeObserver, read again before each paint) and estimated until then,
 * so the first paint already uses the columns the cards keep.
 *
 * The cards stay in list order in the DOM, laid on a grid of 1 px rows: each
 * card spans its own height in its column. Placing into the shortest column
 * never puts a card above the one before it, so the keyboard reads them in
 * the order they are seen.
 */
export function Masonry<T>({
  items, keyOf, estimate, children,
}: {
  items: T[];
  keyOf: (item: T) => string;
  /** A card's height before it is measured. */
  estimate: (item: T) => number;
  children: (item: T) => ReactNode;
}) {
  const grid = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(2);
  const [measured, setMeasured] = useState<Record<string, number>>({});

  // Read every card's height and the grid's width; set state only on a change.
  const measure = useRef(() => {});
  measure.current = () => {
    const el = grid.current;
    if (!el) return;
    const width = el.clientWidth;
    if (width > 0) {
      const next = width < ONE_COLUMN_BELOW ? 1 : 2;
      setColumns((c) => (c === next ? c : next));
    }
    const seen: Record<string, number> = {};
    for (const cell of el.children) {
      const key = (cell as HTMLElement).dataset.masonryKey;
      const height = (cell as HTMLElement).getBoundingClientRect?.().height ?? 0;
      if (key && height > 0) seen[key] = height;
    }
    setMeasured((prev) => {
      const keys = Object.keys(seen);
      const same = keys.length === Object.keys(prev).length && keys.every((k) => Math.abs(prev[k] - seen[k]) < 0.5);
      return same ? prev : seen;
    });
  };

  // Before each paint: what this render changed is measured at once.
  useLayoutEffect(() => { measure.current(); });

  // After: a card or the pane that changes size on its own (a font, a quota
  // read, the window) is measured again.
  useLayoutEffect(() => {
    const el = grid.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => measure.current());
    observer.observe(el);
    for (const cell of el.children) observer.observe(cell);
    const cells = new MutationObserver(() => {
      observer.disconnect();
      observer.observe(el);
      for (const cell of el.children) observer.observe(cell);
    });
    cells.observe(el, { childList: true });
    return () => { observer.disconnect(); cells.disconnect(); };
  }, []);

  const heights = items.map((item) => measured[keyOf(item)] ?? estimate(item));
  const places = useMemo(() => placeMasonry(heights, columns, GAP), [heights.join(","), columns]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div
      ref={grid}
      data-masonry={columns}
      className="grid auto-rows-[1px] gap-x-[12px]"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {items.map((item, i) => (
        <div
          key={keyOf(item)}
          data-masonry-key={keyOf(item)}
          className="min-w-0 self-start"
          style={{ gridColumn: places[i].column + 1, gridRow: `${places[i].top + 1} / span ${Math.max(1, Math.ceil(heights[i]))}` }}
        >
          {children(item)}
        </div>
      ))}
    </div>
  );
}
