"use client";

/**
 * A row that tucks behind the input box (use-tucked-controls.ts): it
 * slides into the box (the row above down, the row below up) and fades,
 * keeping its height so the box never moves; untucked it slides out
 * again. Tucked, it turns hidden when its slide ends, so focus, clicks
 * and assistive tech skip it (composer.module.css).
 *
 * The slide is a CSS transition of transform and opacity (.tuckRow in
 * composer.module.css, on the --composer-tuck-* spring), which the
 * compositor runs: a frame of it costs the page's main thread nothing.
 */
import { useEffect, useRef, useSyncExternalStore, type ReactNode, type TransitionEvent } from "react";
import { useReducedMotion } from "framer-motion";
import styles from "../composer.module.css";
import type { TuckStore } from "./use-tucked-controls";

export function TuckRow({ store, enabled, side, children }: {
  /** The composer's tuck state (use-tucked-controls.ts); a toggle
   *  re-renders this row alone, not its children or the composer. */
  store: TuckStore;
  /** Tucking applies (chat mode); otherwise the row stays out. */
  enabled: boolean;
  side: "above" | "below";
  children: ReactNode;
}) {
  const snapshot = () => enabled && store.get().tucked;
  const tucked = useSyncExternalStore(store.subscribe, snapshot, snapshot);
  const onSettled = store.settle;
  const reduced = useReducedMotion();
  const first = useRef(true);
  // Without motion no transition runs, so nothing ends: settle at once.
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (reduced) onSettled?.();
  }, [tucked, reduced, onSettled]);
  const onTransitionEnd = (event: TransitionEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget && event.propertyName === "transform") onSettled?.();
  };
  return <div
    className={styles.tuckRow}
    onTransitionEnd={onTransitionEnd}
    data-tucked={tucked ? "" : undefined}
    data-tuck-side={side}
  >
    {children}
  </div>;
}
