"use client";

import type { Transition, Variants } from "framer-motion";
import { motion, useAnimation, useReducedMotion } from "framer-motion";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";

import { cn } from "@/lib/utils";

import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "./_shared";

const CURSOR_TRANSITION: Transition = { duration: 1, ease: "easeInOut" };

const LINE_TRANSITION: Transition = {
  delay: 1.3,
  type: "spring",
  stiffness: 70,
  damping: 10,
  mass: 0.4,
};

const CURSOR_VARIANTS: Variants = {
  normal: { x: 0, y: 0 },
  animate: {
    x: [0, 0, -3, 0],
    y: [0, -4, 0, 0],
    transition: CURSOR_TRANSITION,
  },
};

const LINE_DIRECTIONS = [
  { x: 1, y: -1 },
  { x: -1, y: 0 },
  { x: -1, y: 1 },
  { x: 0, y: -1 },
] as const;

const LINE_VARIANTS: Variants = {
  normal: { x: 0, y: 0, opacity: 1 },
  animate: (custom: { x: number; y: number }) => ({
    x: [0, custom.x],
    y: [0, custom.y],
    opacity: [1, 0.2],
    transition: { ...LINE_TRANSITION, repeat: 1, repeatType: "reverse" },
  }),
};

export const CursorClickIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps & {
  play?: "hover" | "once" | "never";
  playSeq?: number;
}>(
  ({ onMouseEnter, onMouseLeave, className, size = 20, play = "hover", playSeq = 0, ...props }, ref) => {
    const controls = useAnimation();
    const reduced = useReducedMotion();
    const isControlledRef = useRef(false);

    const start = useCallback(() => {
      if (reduced) return;
      void Promise.resolve(controls.start("animate")).catch(() => {});
    }, [controls, reduced]);

    const stop = useCallback(() => {
      void controls.start("normal");
    }, [controls]);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return { startAnimation: start, stopAnimation: stop };
    });

    useEffect(() => {
      if (play === "once") start();
      if (play === "never") stop();
    }, [play, playSeq, start, stop]);

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else if (play === "hover") start();
      },
      [onMouseEnter, play, start],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else if (play === "hover") stop();
      },
      [onMouseLeave, play, stop],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <svg
          className="click-ico"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          width={size}
          height={size}
          aria-hidden="true"
          focusable="false"
        >
          <motion.g animate={controls} initial="normal" variants={CURSOR_VARIANTS}>
            <path d="M9.037 9.69a.498.498 0 0 1 .653-.653l11 4.5a.5.5 0 0 1-.074.949l-4.349 1.041a1 1 0 0 0-.74.739l-1.04 4.35a.5.5 0 0 1-.95.074z" />
          </motion.g>
          <g>
            <motion.path className="l0" animate={controls} custom={LINE_DIRECTIONS[0]} d="M14 4.1 12 6" initial="normal" variants={LINE_VARIANTS} />
            <motion.path className="l1" animate={controls} custom={LINE_DIRECTIONS[1]} d="m5.1 8-2.9-.8" initial="normal" variants={LINE_VARIANTS} />
            <motion.path className="l2" animate={controls} custom={LINE_DIRECTIONS[2]} d="m6 12-1.9 2" initial="normal" variants={LINE_VARIANTS} />
            <motion.path className="l3" animate={controls} custom={LINE_DIRECTIONS[3]} d="M7.2 2.2 8 5.1" initial="normal" variants={LINE_VARIANTS} />
          </g>
        </svg>
      </div>
    );
  },
);
CursorClickIcon.displayName = "CursorClickIcon";
