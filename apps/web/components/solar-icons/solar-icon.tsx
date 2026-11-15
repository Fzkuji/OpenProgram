"use client";

/**
 * Solar icon — one component for the two-tone (Bold Duotone) Solar
 * glyphs the composer area uses. Bodies come from ./bodies.ts
 * (regenerate with scripts/icons/fetch-solar.mjs); the glyph is inline
 * SVG filled with currentColor, so it takes the surrounding text colour
 * like every other icon in the app.
 *
 * Exposes the same imperative ``AnimatedNavIconHandle`` (start / stop)
 * as the pqoqubbw line icons in ``@/components/animated-icons``, so the
 * existing hover plumbing keeps working unchanged: a parent button /
 * row / chip attaches a ref and drives the motion from its own hover
 * ("controlled"). Without a ref the icon finds its nearest clickable
 * ancestor (HOVER_HOST in @/components/ui/hover-pop) and animates on that element's hover, so every
 * button behaves the same whether or not it wires a ref. Purely
 * decorative glyphs (menu checks, warnings, capability marks) pass
 * `motionPreset="none"`.
 * The motion is deliberately small and uniform — a filled glyph doesn't
 * redraw itself the way a line icon can, so it just pops, flies or
 * nudges, and `pulse` plays a one-shot pop-in for state changes.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, type HTMLAttributes, type MouseEvent } from "react";
import { motion, useAnimation, useReducedMotion, type Transition, type Variants } from "framer-motion";

import { cn } from "@/lib/utils";
import type { AnimatedNavIconHandle } from "@/components/animated-icons/_shared";
import { HOVER_FILL_SCALE, HOVER_POP, HOVER_POP_FILL, HOVER_SPRING, followHostHover } from "@/components/ui/hover-pop";
import { SOLAR_BODIES, SOLAR_VIEWBOXES, type SolarIconName } from "./bodies";
import { SOLAR_INK } from "./ink-bounds";

export type SolarMotionPreset = "pop" | "nudge" | "pulse" | "none";

const PRESETS: Record<Exclude<SolarMotionPreset, "none">, Variants> = {
  pop: HOVER_POP,
  nudge: { normal: { x: 0 }, animate: { x: 1.5 } },
  pulse: { normal: { scale: 1 }, animate: { scale: [0.5, 1.15, 1] } },
};

/** The motions of a filled glyph (fit="fill"): a pop grows the
 *  box to exactly 18 px (HOVER_FILL_SCALE) instead of 1.12 times. */
const FILL_PRESETS: Record<Exclude<SolarMotionPreset, "none">, Variants> = {
  ...PRESETS,
  pop: HOVER_POP_FILL,
};

/** How a glyph sits in its box. "viewBox" (the default) draws the whole
 *  view box, so a Solar glyph's ink is 20/24 of the box. "fill" crops the
 *  view box to a square around the glyph's ink (ink-bounds.ts, generated),
 *  so its longer side is the box's side, centred; the box is `size` px
 *  whatever CSS the surrounding control sets for svgs. Only the composer
 *  and the popups it opens use "fill". */
export type SolarIconFit = "viewBox" | "fill";

/** The view box that makes a glyph's ink fill a square box. */
export function solarFillViewBox(name: SolarIconName): string {
  const ink = SOLAR_INK[name];
  if (!ink) return SOLAR_VIEWBOXES[name] ?? "0 0 24 24";
  const side = Math.max(ink.width, ink.height);
  const round = (value: number) => Math.round(value * 1000) / 1000;
  return [round(ink.x + ink.width / 2 - side / 2), round(ink.y + ink.height / 2 - side / 2), round(side), round(side)].join(" ");
}

const SPRING: Transition = HOVER_SPRING;
const PULSE: Transition = { duration: 0.32, ease: "easeOut" };

export interface SolarIconProps extends HTMLAttributes<HTMLSpanElement> {
  name: SolarIconName;
  size?: number;
  /** Hover micro-motion. Defaults to `pop`. */
  motionPreset?: SolarMotionPreset;
  /** How the glyph sits in its box; see SolarIconFit. */
  fit?: SolarIconFit;
}

export const SolarIcon = forwardRef<AnimatedNavIconHandle, SolarIconProps>(function SolarIcon(
  { name, size = 16, motionPreset = "pop", fit = "viewBox", className, onMouseEnter, onMouseLeave, ...props },
  ref,
) {
  const controls = useAnimation();
  const isControlledRef = useRef(false);
  const reduced = useReducedMotion();
  const animated = motionPreset !== "none" && !reduced;
  const filled = fit === "fill";

  useImperativeHandle(ref, () => {
    isControlledRef.current = true;
    return {
      startAnimation: () => { if (animated) void controls.start("animate"); },
      stopAnimation: () => { if (animated) void controls.start("normal"); },
    };
  }, [animated, controls]);

  // Uncontrolled: follow the hover of the nearest clickable ancestor
  // (or the icon itself when it has none).
  const hostRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const self = hostRef.current;
    if (!self || !animated || isControlledRef.current) return;
    return followHostHover(self, () => void controls.start("animate"), () => void controls.start("normal"));
  }, [animated, controls]);

  const handleMouseEnter = useCallback((e: MouseEvent<HTMLSpanElement>) => {
    if (isControlledRef.current) onMouseEnter?.(e);
  }, [onMouseEnter]);
  const handleMouseLeave = useCallback((e: MouseEvent<HTMLSpanElement>) => {
    if (isControlledRef.current) onMouseLeave?.(e);
  }, [onMouseLeave]);

  return (
    <span
      {...props}
      ref={hostRef}
      className={cn("inline-flex shrink-0", className)}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <motion.svg
        width={size}
        height={size}
        viewBox={filled ? solarFillViewBox(name) : SOLAR_VIEWBOXES[name] ?? "0 0 24 24"}
        fill="currentColor"
        aria-hidden="true"
        xmlns="http://www.w3.org/2000/svg"
        style={filled ? { display: "block", flexShrink: 0, width: size, height: size, overflow: "visible" } : { display: "block", flexShrink: 0 }}
        data-icon-fit={filled ? "fill" : undefined}
        initial="normal"
        animate={controls}
        variants={motionPreset === "none" ? undefined : (filled ? FILL_PRESETS : PRESETS)[motionPreset]}
        transition={motionPreset === "pulse" ? PULSE : SPRING}
        dangerouslySetInnerHTML={{ __html: SOLAR_BODIES[name] }}
      />
    </span>
  );
});
