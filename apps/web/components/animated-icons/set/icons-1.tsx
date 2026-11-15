"use client";

import type { Transition, Variants } from "framer-motion";
import { motion, useAnimation } from "framer-motion";
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react";

import { cn } from "@/lib/utils";

import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "../_shared";

// ─── workflow (Functions — runnable programs / pipelines) ────────────
const WORKFLOW_TRANSITION: Transition = {
  duration: 0.3,
  opacity: { delay: 0.15 },
};
const WORKFLOW_VARIANTS: Variants = {
  normal: { pathLength: 1, opacity: 1 },
  animate: (custom: number) => ({
    pathLength: [0, 1],
    opacity: [0, 1],
    transition: { ...WORKFLOW_TRANSITION, delay: 0.1 * custom },
  }),
};

export const WorkflowIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.rect animate={controls} custom={0} height="8" rx="2" variants={WORKFLOW_VARIANTS} width="8" x="3" y="3" />
          <motion.path animate={controls} custom={3} d="M7 11v4a2 2 0 0 0 2 2h4" variants={WORKFLOW_VARIANTS} />
          <motion.rect animate={controls} custom={0} height="8" rx="2" variants={WORKFLOW_VARIANTS} width="8" x="13" y="13" />
        </svg>
      </div>
    );
  },
);
WorkflowIcon.displayName = "WorkflowIcon";

// ─── chevron-down (collapsible section toggle) ───────────────────────
const CHEVRON_DOWN_TRANSITION: Transition = { times: [0, 0.4, 1], duration: 0.5 };
const CHEVRON_DOWN_VARIANTS: Variants = {
  normal: { y: 0 },
  animate: { y: [0, 2, 0] },
};
export const ChevronDownIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
            d="m6 9 6 6 6-6"
            transition={CHEVRON_DOWN_TRANSITION}
            variants={CHEVRON_DOWN_VARIANTS}
          />
        </svg>
      </div>
    );
  },
);
ChevronDownIcon.displayName = "ChevronDownIcon";

// ─── graduation-cap (Skills — abilities / mastery) ───────────────────
const CAP_VARIANTS: Variants = {
  normal: { rotate: 0 },
  animate: {
    y: [0, -2, 0],
    rotate: [0, -2, 2, 0],
    transition: { duration: 0.6, ease: "easeInOut" },
  },
};
const TASSEL_VARIANTS: Variants = {
  normal: { rotate: 0 },
  animate: {
    rotate: [0, 15, -10, 5, 0],
    transition: { duration: 0.8, ease: "easeInOut", delay: 0.1 },
  },
};

export const GraduationCapIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.g animate={controls} style={{ transformOrigin: "12px 12px" }} variants={CAP_VARIANTS}>
            <path d="M2 10l10-5 10 5-10 5z" />
            <path d="M6 12v5c3 3 9 3 12 0v-5" />
            <motion.path
              d="M22 10v6"
              style={{ transformBox: "fill-box", transformOrigin: "top center" }}
              variants={TASSEL_VARIANTS}
            />
          </motion.g>
        </svg>
      </div>
    );
  },
);
GraduationCapIcon.displayName = "GraduationCapIcon";

// ─── blocks (Plugins — was Heroicons PuzzlePiece) ────────────────────
const BLOCKS_VARIANTS: Variants = {
  normal: { translateX: 0, translateY: 0 },
  animate: { translateX: -4, translateY: 4 },
};

export const BlocksIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <path d="M10 21V8a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H3" />
          <motion.path animate={controls} d="M14 3h7v7h-7z" variants={BLOCKS_VARIANTS} />
        </svg>
      </div>
    );
  },
);
BlocksIcon.displayName = "BlocksIcon";

// ─── brain (Memory — was Heroicons QueueList) ────────────────────────
// Pulses continuously while hovered (repeat: mirror).
const BRAIN_STEM_VARIANTS: Variants = {
  normal: { pathLength: 1, pathOffset: 0 },
  animate: {
    pathLength: [1, 0.4, 1],
    pathOffset: [0, 0.25, 0],
    transition: { duration: 1.4, repeat: Number.POSITIVE_INFINITY, repeatType: "mirror", ease: "easeInOut" },
  },
};
const BRAIN_SIDE_VARIANTS: Variants = {
  normal: { pathLength: 1, pathOffset: 0 },
  animate: {
    pathLength: [1, 0.5, 1],
    pathOffset: [0, 0.25, 0],
    transition: { duration: 1.4, repeat: Number.POSITIVE_INFINITY, repeatType: "mirror", ease: "easeInOut" },
  },
};
const BRAIN_TOP_ARC_VARIANTS: Variants = {
  normal: { pathLength: 1, pathOffset: 0 },
  animate: {
    pathLength: [1, 0.8, 1],
    pathOffset: [0, 0.07, 0],
    transition: { duration: 1.4, repeat: Number.POSITIVE_INFINITY, repeatType: "mirror", ease: "easeInOut" },
  },
};
const BRAIN_LOWER_ARC_VARIANTS: Variants = {
  normal: { pathLength: 1, pathOffset: 0 },
  animate: {
    pathLength: [1, 0.8, 1],
    pathOffset: [0, 0.14, 0],
    transition: { duration: 1.4, repeat: Number.POSITIVE_INFINITY, repeatType: "mirror", ease: "easeInOut" },
  },
};

export const BrainIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          strokeWidth={2}
          variants={{
            normal: { scale: 1, strokeWidth: 2 },
            animate: {
              scale: [1, 1.08, 1],
              strokeWidth: [2, 2.25, 2],
              transition: { duration: 1.4, repeat: Number.POSITIVE_INFINITY, repeatType: "mirror", ease: "easeInOut" },
            },
          }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <motion.path animate={controls} d="M12 18V5" variants={BRAIN_STEM_VARIANTS} />
          <motion.path animate={controls} d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4" variants={BRAIN_SIDE_VARIANTS} />
          <motion.path animate={controls} d="M12 5A3 3 0 1 1 17.598 6.5" variants={BRAIN_TOP_ARC_VARIANTS} />
          <motion.path animate={controls} d="M12 5A3 3 0 1 0 6.402 6.5" variants={BRAIN_TOP_ARC_VARIANTS} />
          <path d="M17.997 5.125a4 4 0 0 1 2.526 5.77" />
          <motion.path animate={controls} d="M18 18a4 4 0 0 0 2-7.464" variants={BRAIN_LOWER_ARC_VARIANTS} />
          <path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517" />
          <motion.path animate={controls} d="M6 18a4 4 0 0 1-2-7.464" variants={BRAIN_LOWER_ARC_VARIANTS} />
          <path d="M6.003 5.125a4 4 0 0 0-2.526 5.77" />
        </motion.svg>
      </div>
    );
  },
);
BrainIcon.displayName = "BrainIcon";

// ─── message-circle (Chats — was Heroicons ChatBubbleLeftRight) ──────
const MESSAGE_VARIANTS: Variants = {
  normal: { scale: 1, rotate: 0 },
  animate: {
    scale: 1.05,
    rotate: [0, -7, 7, 0],
    transition: {
      rotate: { duration: 0.5, ease: "easeInOut" },
      scale: { type: "spring", stiffness: 400, damping: 10 },
    },
  },
};

export const MessageCircleIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          variants={MESSAGE_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
        </motion.svg>
      </div>
    );
  },
);
MessageCircleIcon.displayName = "MessageCircleIcon";

// ─── panel-left-close / -open (sidebar collapse toggle) ──────────────
// State-aware in the sidebar: "close" (chevron ‹) shows when the rail is
// open, "open" (chevron ›) when collapsed. Hover nudges the chevron.
const PANEL_TRANSITION: Transition = { times: [0, 0.4, 1], duration: 0.5 };
const PANEL_CLOSE_VARIANTS: Variants = {
  normal: { x: 0 },
  animate: { x: [0, -1.5, 0] },
};
const PANEL_OPEN_VARIANTS: Variants = {
  normal: { x: 0 },
  animate: { x: [0, 1.5, 0] },
};

export const PanelLeftCloseIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <rect height="18" rx="2" width="18" x="3" y="3" />
          <path d="M9 3v18" />
          <motion.path animate={controls} d="m16 15-3-3 3-3" transition={PANEL_TRANSITION} variants={PANEL_CLOSE_VARIANTS} />
        </svg>
      </div>
    );
  },
);
PanelLeftCloseIcon.displayName = "PanelLeftCloseIcon";

export const PanelLeftOpenIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <rect height="18" rx="2" width="18" x="3" y="3" />
          <path d="M9 3v18" />
          <motion.path animate={controls} d="m14 9 3 3-3 3" transition={PANEL_TRANSITION} variants={PANEL_OPEN_VARIANTS} />
        </svg>
      </div>
    );
  },
);
PanelLeftOpenIcon.displayName = "PanelLeftOpenIcon";

// ─── activity (RIGHT: Execution Detail — the pulse line) ─────────────
const ACTIVITY_VARIANTS: Variants = {
  normal: {
    opacity: 1,
    pathLength: 1,
    pathOffset: 0,
    transition: { duration: 0.4, opacity: { duration: 0.1 } },
  },
  animate: {
    opacity: [0, 1],
    pathLength: [0, 1],
    pathOffset: [1, 0],
    transition: { duration: 0.6, ease: "linear", opacity: { duration: 0.1 } },
  },
};

export const ActivityIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
            d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"
            initial="normal"
            variants={ACTIVITY_VARIANTS}
          />
        </svg>
      </div>
    );
  },
);
ActivityIcon.displayName = "ActivityIcon";

