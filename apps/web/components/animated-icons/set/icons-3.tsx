"use client";

import type { Transition, Variants } from "framer-motion";
import { motion, useAnimation } from "framer-motion";
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react";

import { cn } from "@/lib/utils";

import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "../_shared";

// ─── monitor (Status chip — "Local" / this machine / channel) ────────
// Lucide's official `monitor` glyph (pqoqubbw ships no monitor / laptop),
// wrapped in the same controlled-ref Framer-Motion pattern as the rest of
// this file. The screen does a soft blink on hover.
const MONITOR_VARIANTS: Variants = {
  normal: { opacity: 1 },
  animate: {
    opacity: [1, 0.4, 1],
    transition: { duration: 0.6, ease: "easeInOut" },
  },
};

export const MonitorIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <svg
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <motion.rect animate={controls} variants={MONITOR_VARIANTS} width="20" height="14" x="2" y="3" rx="2" />
          <line x1="8" x2="16" y1="21" y2="21" />
          <line x1="12" x2="12" y1="17" y2="21" />
        </svg>
      </div>
    );
  },
);
MonitorIcon.displayName = "MonitorIcon";

// ─── chevron-right (Effort pill — expand affordance) ─────────────────
// Carried verbatim from pqoqubbw/icons (icons/chevron-right.tsx): the
// chevron nudges right on hover.
const CHEVRON_RIGHT_TRANSITION: Transition = {
  times: [0, 0.4, 1],
  duration: 0.5,
};

export const ChevronRightIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <svg
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <motion.path
            animate={controls}
            d="m9 18 6-6-6-6"
            transition={CHEVRON_RIGHT_TRANSITION}
            variants={{
              normal: { x: 0 },
              animate: { x: [0, 2, 0] },
            }}
          />
        </svg>
      </div>
    );
  },
);
ChevronRightIcon.displayName = "ChevronRightIcon";

// ─── folder-open (Project chip — working folder) ────────────────────
// Carried from pqoqubbw/icons (icons/folder-open.tsx): the folder lid
// wobbles on hover.
const FOLDER_OPEN_VARIANTS: Variants = {
  normal: { rotate: 0 },
  animate: {
    rotate: [0, -8, 6, -4, 0],
    transition: { duration: 0.6, ease: "easeInOut" },
  },
};

export const FolderOpenIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <svg
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <motion.path
            animate={controls}
            variants={FOLDER_OPEN_VARIANTS}
            style={{ transformOrigin: "12px 12px" }}
            d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"
          />
        </svg>
      </div>
    );
  },
);
FolderOpenIcon.displayName = "FolderOpenIcon";

// ─── search ───────────────────────────────────────────────────
export const SearchIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <motion.svg
          animate={controls}
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          transition={{ duration: 1, bounce: 0.3 }}
          variants={{ normal: { x: 0, y: 0 }, animate: { x: [0, 0, -3, 0], y: [0, -4, 0, 0] } }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </motion.svg>
      </div>
    );
  },
);
SearchIcon.displayName = "SearchIcon";

// ─── book-text (user menu: Docs) ─────────────────────────────────────
export const BookTextIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <motion.svg
          animate={controls}
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          variants={{
            animate: { scale: [1, 1.04, 1], rotate: [0, -8, 8, -8, 0], y: [0, -2, 0], transition: { duration: 0.6, ease: "easeInOut", times: [0, 0.2, 0.5, 0.8, 1] } },
            normal: { scale: 1, rotate: 0, y: 0 },
          }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20" />
          <path d="M8 11h8" />
          <path d="M8 7h6" />
        </motion.svg>
      </div>
    );
  },
);
BookTextIcon.displayName = "BookTextIcon";

// ─── arrow-up-right (chat cards: switch-to-branch / open peer) ───────
const ARROW_UP_RIGHT_VARIANTS: Variants = {
  normal: { scale: 1, translateX: 0, translateY: 0 },
  animate: {
    scale: [1, 0.85, 1],
    translateX: [0, -4, 0],
    translateY: [0, 4, 0],
    originX: 1,
    originY: 0,
    transition: { duration: 0.5, ease: "easeInOut" },
  },
};

export const ArrowUpRightIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <svg
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <motion.g animate={controls} variants={ARROW_UP_RIGHT_VARIANTS}>
            <path d="M7 7H17" />
            <path d="M17 7V17" />
            <path d="M7 17L17 7" />
          </motion.g>
        </svg>
      </div>
    );
  },
);
ArrowUpRightIcon.displayName = "ArrowUpRightIcon";

// ─── file-text (Memory → Wiki tab) ───────────────────────────────────
export const FileTextIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: () => controls.start("animate"),
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else controls.start("animate");
      },
      [controls, onMouseEnter],
    );
    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseLeave?.(e);
        else controls.start("normal");
      },
      [controls, onMouseLeave],
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <motion.svg
          animate={controls}
          fill="none"
          height={size}
          initial="normal"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          variants={{
            normal: { scale: 1 },
            animate: {
              scale: 1.05,
              transition: { duration: 0.3, ease: "easeOut" },
            },
          }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
          <path d="M14 2v4a2 2 0 0 0 2 2h4" />
          <motion.path
            d="M10 9H8"
            stroke="currentColor"
            strokeWidth="2"
            variants={{
              normal: { pathLength: 1, x1: 8, x2: 10 },
              animate: {
                pathLength: [1, 0, 1],
                x1: [8, 10, 8],
                x2: [10, 10, 10],
                transition: { duration: 0.7, delay: 0.3 },
              },
            }}
          />
          <motion.path
            d="M16 13H8"
            stroke="currentColor"
            strokeWidth="2"
            variants={{
              normal: { pathLength: 1, x1: 8, x2: 16 },
              animate: {
                pathLength: [1, 0, 1],
                x1: [8, 16, 8],
                x2: [16, 16, 16],
                transition: { duration: 0.7, delay: 0.5 },
              },
            }}
          />
          <motion.path
            d="M16 17H8"
            stroke="currentColor"
            strokeWidth="2"
            variants={{
              normal: { pathLength: 1, x1: 8, x2: 16 },
              animate: {
                pathLength: [1, 0, 1],
                x1: [8, 16, 8],
                x2: [16, 16, 16],
                transition: { duration: 0.7, delay: 0.7 },
              },
            }}
          />
        </motion.svg>
      </div>
    );
  },
);
FileTextIcon.displayName = "FileTextIcon";
