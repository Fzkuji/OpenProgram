"use client";

/**
 * A row that tucks behind the input box (use-tucked-controls.ts): it
 * slides into the box (the row above down, the row below up) and fades,
 * keeping its height so the box never moves; untucked it slides out
 * again. Tucked, it is inert so focus and clicks skip it.
 */
import type { ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";

/** The rows' slide; the mask above the box moves on the same spring
 *  (use-tuck-backdrop.ts). */
export const TUCK_SLIDE = { type: "spring", stiffness: 420, damping: 32 } as const;

export function TuckRow({ tucked, side, onSettled, children }: {
  tucked: boolean; side: "above" | "below";
  /** Called when a slide ends, so the box can stop covering the rows. */
  onSettled?: () => void;
  children: ReactNode;
}) {
  const reduced = useReducedMotion();
  return <motion.div
    initial={false}
    animate={tucked
      ? { y: side === "above" ? "100%" : "-100%", opacity: 0, scale: 0.96 }
      : { y: 0, opacity: 1, scale: 1 }}
    transition={reduced ? { duration: 0 } : { ...TUCK_SLIDE, opacity: { duration: 0.18 } }}
    onAnimationComplete={onSettled}
    inert={tucked}
    data-tucked={tucked ? "" : undefined}
    data-tuck-side={side}
  >
    {children}
  </motion.div>;
}
