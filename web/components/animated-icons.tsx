"use client";

/**
 * Animated icons — from pqoqubbw/icons ("Lucide Animated", MIT,
 * https://github.com/pqoqubbw/icons), the copy-paste (shadcn-style)
 * animated-icon set the project standardised on app-wide (left/right
 * sidebars, composer, …). Line (Lucide) glyphs animated with Framer
 * Motion. Carried verbatim from upstream except:
 *   - import from ``framer-motion`` (installed) not ``motion/react``.
 *   - the per-icon Handle / Props interfaces are merged into one shared
 *     ``AnimatedNavIconHandle`` / ``AnimatedNavIconProps`` — every icon
 *     exposes the same start/stop imperative API, so a caller holds a
 *     single ref type.
 * Everything else — path data, variants, transitions, the controlled-ref
 * pattern — is upstream's. Per project rule we never hand-author SVGs.
 *
 * Driven from the *container's* hover: the caller attaches a ref and
 * calls start/stopAnimation from the row's / button's onMouseEnter/Leave,
 * so the whole control is the hover target (claude.ai-style). Attaching a
 * ref flips each icon to "controlled" mode, so it stops self-animating on
 * its own hover and listens only to the container.
 */

import type { Transition, Variants } from "framer-motion";
import { motion, useAnimation } from "framer-motion";
import type { HTMLAttributes } from "react";
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react";

import { cn } from "@/lib/utils";

export interface AnimatedNavIconHandle {
  startAnimation: () => void;
  stopAnimation: () => void;
}

interface AnimatedNavIconProps extends HTMLAttributes<HTMLDivElement> {
  size?: number;
}

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

// ─── layers (MCP — was Heroicons RectangleStack) ─────────────────────
const LAYERS_TRANSITION: Transition = {
  type: "spring",
  stiffness: 100,
  damping: 14,
  mass: 1,
};

export const LayersIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;
      return {
        startAnimation: async () => {
          await controls.start("firstState");
          await controls.start("secondState");
        },
        stopAnimation: () => controls.start("normal"),
      };
    });

    const handleMouseEnter = useCallback(
      async (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) onMouseEnter?.(e);
        else {
          await controls.start("firstState");
          await controls.start("secondState");
        }
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
          <path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" />
          <motion.path
            animate={controls}
            d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"
            transition={LAYERS_TRANSITION}
            variants={{ normal: { y: 0 }, firstState: { y: -9 }, secondState: { y: 0 } }}
          />
          <motion.path
            animate={controls}
            d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"
            transition={LAYERS_TRANSITION}
            variants={{ normal: { y: 0 }, firstState: { y: -5 }, secondState: { y: 0 } }}
          />
        </svg>
      </div>
    );
  },
);
LayersIcon.displayName = "LayersIcon";

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

// ─── git-graph (RIGHT: History — the conversation DAG) ───────────────
const GIT_GRAPH_DURATION = 0.3;
const gitGraphDelay = (i: number) => (i === 0 ? 0.1 : i * GIT_GRAPH_DURATION + 0.1);

export const GitGraphIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.circle
            animate={controls}
            cx="5"
            cy="6"
            r="3"
            transition={{ duration: GIT_GRAPH_DURATION, delay: gitGraphDelay(0), opacity: { delay: gitGraphDelay(0) } }}
            variants={{
              normal: { pathLength: 1, opacity: 1, transition: { delay: 0 } },
              animate: { pathLength: [0, 1], opacity: [0, 1] },
            }}
          />
          <motion.path
            animate={controls}
            d="M5 9v6"
            transition={{ duration: GIT_GRAPH_DURATION, delay: gitGraphDelay(1), opacity: { delay: gitGraphDelay(1) } }}
            variants={{
              normal: { pathLength: 1, pathOffset: 0, opacity: 1, transition: { delay: 0 } },
              animate: { pathLength: [0, 1], opacity: [0, 1], pathOffset: [1, 0] },
            }}
          />
          <motion.circle
            animate={controls}
            cx="5"
            cy="18"
            r="3"
            transition={{ duration: GIT_GRAPH_DURATION, delay: gitGraphDelay(2), opacity: { delay: gitGraphDelay(2) } }}
            variants={{
              normal: { pathLength: 1, opacity: 1, transition: { delay: 0 } },
              animate: { pathLength: [0, 1], opacity: [0, 1] },
            }}
          />
          <motion.path
            animate={controls}
            d="M12 3v18"
            transition={{ duration: GIT_GRAPH_DURATION, delay: gitGraphDelay(1), opacity: { delay: gitGraphDelay(1) } }}
            variants={{
              normal: { pathLength: 1, pathOffset: 0, opacity: 1, transition: { delay: 0 } },
              animate: { pathLength: [0, 1], opacity: [0, 1], pathOffset: [1, 0] },
            }}
          />
          <motion.circle
            animate={controls}
            cx="19"
            cy="6"
            r="3"
            transition={{ duration: GIT_GRAPH_DURATION, delay: gitGraphDelay(2), opacity: { delay: gitGraphDelay(2) } }}
            variants={{
              normal: { pathLength: 1, opacity: 1, transition: { delay: 0 } },
              animate: { pathLength: [0, 1], opacity: [0, 1] },
            }}
          />
          <motion.path
            animate={controls}
            d="M16 15.7A9 9 0 0 0 19 9"
            transition={{ duration: GIT_GRAPH_DURATION, delay: gitGraphDelay(1), opacity: { delay: gitGraphDelay(1) } }}
            variants={{
              normal: { pathLength: 1, pathOffset: 0, opacity: 1, transition: { delay: 0 } },
              animate: { pathLength: [0, 1], opacity: [0, 1], pathOffset: [1, 0] },
            }}
          />
        </svg>
      </div>
    );
  },
);
GitGraphIcon.displayName = "GitGraphIcon";

// ─── align-left (RIGHT: Context) ─────────────────────────────────────
const ALIGN_TRANSITION: Transition = {
  type: "spring",
  stiffness: 150,
  damping: 15,
  mass: 0.3,
};

export const AlignLeftIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.line animate={controls} transition={ALIGN_TRANSITION} variants={{ normal: { x2: 21 }, animate: { x2: 21 } }} x1="3" x2="21" y1="6" y2="6" />
          <motion.line animate={controls} transition={ALIGN_TRANSITION} variants={{ normal: { x2: 15 }, animate: { x2: 19 } }} x1="3" x2="15" y1="12" y2="12" />
          <motion.line animate={controls} transition={ALIGN_TRANSITION} variants={{ normal: { x2: 17 }, animate: { x2: 12 } }} x1="3" x2="17" y1="18" y2="18" />
        </svg>
      </div>
    );
  },
);
AlignLeftIcon.displayName = "AlignLeftIcon";

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

// ─── wrench (Composer: Tools) ────────────────────────────────────────
const WRENCH_VARIANTS: Variants = {
  normal: { rotate: 0, transition: { duration: 0.25, ease: "easeOut" } },
  animate: {
    rotate: [0, 12, -14, 4, 0],
    transition: {
      duration: 1.05,
      times: [0, 0.42, 0.68, 0.88, 1],
      ease: ["easeInOut", "easeInOut", "easeOut", "easeOut"],
    },
  },
};

export const WrenchIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          style={{ transformOrigin: "90% 10%", transformBox: "fill-box" }}
          variants={WRENCH_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z" />
        </motion.svg>
      </div>
    );
  },
);
WrenchIcon.displayName = "WrenchIcon";

// ─── chrome (Composer: Web Search) ───────────────────────────────────
const CHROME_TRANSITION: Transition = { duration: 0.3, opacity: { delay: 0.15 } };
const CHROME_VARIANTS: Variants = {
  normal: { pathLength: 1, opacity: 1 },
  animate: (custom: number) => ({
    pathLength: [0, 1],
    opacity: [0, 1],
    transition: { ...CHROME_TRANSITION, delay: 0.1 * custom },
  }),
};

export const ChromeIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <circle cx="12" cy="12" r="10" />
          <motion.circle animate={controls} custom={0} cx="12" cy="12" r="4" variants={CHROME_VARIANTS} />
          <motion.line animate={controls} custom={3} variants={CHROME_VARIANTS} x1="21.17" x2="12" y1="8" y2="8" />
          <motion.line animate={controls} custom={3} variants={CHROME_VARIANTS} x1="3.95" x2="8.54" y1="6.06" y2="14" />
          <motion.line animate={controls} custom={3} variants={CHROME_VARIANTS} x1="10.88" x2="15.46" y1="21.94" y2="14" />
        </svg>
      </div>
    );
  },
);
ChromeIcon.displayName = "ChromeIcon";

// ─── zap (Composer: Fast) ────────────────────────────────────────────
const ZAP_PATH_VARIANTS: Variants = {
  normal: { opacity: 1, pathLength: 1, transition: { duration: 0.6, opacity: { duration: 0.1 } } },
  animate: { opacity: [0, 1], pathLength: [0, 1], transition: { duration: 0.6, opacity: { duration: 0.1 } } },
};

export const ZapIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
            d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"
            initial="normal"
            variants={ZAP_PATH_VARIANTS}
          />
        </svg>
      </div>
    );
  },
);
ZapIcon.displayName = "ZapIcon";

// ─── plus (Composer: + menu trigger) ─────────────────────────────────
const PLUS_TRANSITION: Transition = { type: "spring", stiffness: 100, damping: 15 };
const PLUS_VARIANTS: Variants = { normal: { rotate: 0 }, animate: { rotate: 180 } };

export const PlusIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          transition={PLUS_TRANSITION}
          variants={PLUS_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M5 12h14" />
          <path d="M12 5v14" />
        </motion.svg>
      </div>
    );
  },
);
PlusIcon.displayName = "PlusIcon";

// ─── git-branch (top-bar: branch chip) ───────────────────────────────
const GIT_BRANCH_DURATION = 0.3;
const gitBranchDelay = (i: number) => (i === 0 ? 0.1 : i * GIT_BRANCH_DURATION + 0.1);

export const GitBranchIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.circle animate={controls} cx="18" cy="6" r="3" transition={{ duration: GIT_BRANCH_DURATION, delay: gitBranchDelay(0), opacity: { delay: gitBranchDelay(0) } }} variants={{ normal: { pathLength: 1, opacity: 1, transition: { delay: 0 } }, animate: { pathLength: [0, 1], opacity: [0, 1] } }} />
          <motion.line animate={controls} transition={{ duration: GIT_BRANCH_DURATION, delay: gitBranchDelay(1), opacity: { delay: gitBranchDelay(1) } }} variants={{ normal: { pathLength: 1, pathOffset: 0, opacity: 1, transition: { delay: 0 } }, animate: { pathLength: [0, 1], opacity: [0, 1], pathOffset: [1, 0] } }} x1="6" x2="6" y1="3" y2="15" />
          <motion.circle animate={controls} cx="6" cy="18" r="3" transition={{ duration: GIT_BRANCH_DURATION, delay: gitBranchDelay(2), opacity: { delay: gitBranchDelay(2) } }} variants={{ normal: { pathLength: 1, opacity: 1, transition: { delay: 0 } }, animate: { pathLength: [0, 1], opacity: [0, 1] } }} />
          <motion.path animate={controls} d="M18 9a9 9 0 0 1-9 9" transition={{ duration: GIT_BRANCH_DURATION, delay: gitBranchDelay(1), opacity: { delay: gitBranchDelay(1) } }} variants={{ normal: { pathLength: 1, pathOffset: 0, opacity: 1, transition: { delay: 0 } }, animate: { pathLength: [0, 1], opacity: [0, 1], pathOffset: [1, 0] } }} />
        </svg>
      </div>
    );
  },
);
GitBranchIcon.displayName = "GitBranchIcon";

// ─── copy (chat message action) ──────────────────────────────────────
const COPY_TRANSITION: Transition = { type: "spring", stiffness: 160, damping: 17, mass: 1 };

export const CopyIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.rect animate={controls} height="14" rx="2" ry="2" transition={COPY_TRANSITION} variants={{ normal: { translateY: 0, translateX: 0 }, animate: { translateY: -3, translateX: -3 } }} width="14" x="8" y="8" />
          <motion.path animate={controls} d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" transition={COPY_TRANSITION} variants={{ normal: { x: 0, y: 0 }, animate: { x: 3, y: 3 } }} />
        </svg>
      </div>
    );
  },
);
CopyIcon.displayName = "CopyIcon";

// ─── check (chat message: copied state) ──────────────────────────────
const CHECK_PATH_VARIANTS: Variants = {
  normal: { opacity: 1, pathLength: 1, scale: 1, transition: { duration: 0.3, opacity: { duration: 0.1 } } },
  animate: { opacity: [0, 1], pathLength: [0, 1], scale: [0.5, 1], transition: { duration: 0.4, opacity: { duration: 0.1 } } },
};

export const CheckIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.path animate={controls} d="M4 12 9 17L20 6" initial="normal" variants={CHECK_PATH_VARIANTS} />
        </svg>
      </div>
    );
  },
);
CheckIcon.displayName = "CheckIcon";

// ─── refresh-cw (chat message: retry) ─────────────────────────────────
const REFRESH_TRANSITION: Transition = { type: "spring", stiffness: 250, damping: 25 };

export const RefreshCwIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          transition={REFRESH_TRANSITION}
          variants={{ normal: { rotate: "0deg" }, animate: { rotate: "50deg" } }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
          <path d="M21 3v5h-5" />
          <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
          <path d="M8 16H3v5" />
        </motion.svg>
      </div>
    );
  },
);
RefreshCwIcon.displayName = "RefreshCwIcon";

// ─── square-pen (chat message: edit) ─────────────────────────────────
const PEN_VARIANTS: Variants = {
  normal: { rotate: 0, x: 0, y: 0 },
  animate: { rotate: [-0.5, 0.5, -0.5], x: [0, -1, 1.5, 0], y: [0, 1.5, -1, 0] },
};

export const SquarePenIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          style={{ overflow: "visible" }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <motion.path animate={controls} d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" variants={PEN_VARIANTS} />
        </svg>
      </div>
    );
  },
);
SquarePenIcon.displayName = "SquarePenIcon";

// ─── undo (chat message: revert) ─────────────────────────────────────
const UNDO_TRANSITION: Transition = { duration: 0.6, ease: "easeInOut" };

export const UndoIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.path animate={controls} d="M3 7v6h6" transition={UNDO_TRANSITION} variants={{ normal: { translateX: 0, translateY: 0, rotate: 0 }, animate: { translateX: [0, 2.1, 0], translateY: [0, -1.4, 0], rotate: [0, 12, 0] } }} />
          <motion.path animate={controls} d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13" transition={UNDO_TRANSITION} variants={{ normal: { pathLength: 1 }, animate: { pathLength: [1, 0.8, 1] } }} />
        </svg>
      </div>
    );
  },
);
UndoIcon.displayName = "UndoIcon";

// ─── settings (user menu: settings — gear spins) ─────────────────────
const SETTINGS_TRANSITION: Transition = { type: "spring", stiffness: 50, damping: 10 };
const SETTINGS_VARIANTS: Variants = { normal: { rotate: 0 }, animate: { rotate: 180 } };

export const SettingsIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          transition={SETTINGS_TRANSITION}
          variants={SETTINGS_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
          <circle cx="12" cy="12" r="3" />
        </motion.svg>
      </div>
    );
  },
);
SettingsIcon.displayName = "SettingsIcon";

// ─── circle-help (user menu: about / help — ? wiggles) ───────────────
const CIRCLE_HELP_TRANSITION: Transition = { duration: 0.5, ease: "easeInOut" };
const CIRCLE_HELP_VARIANTS: Variants = { normal: { rotate: 0 }, animate: { rotate: [0, -10, 10, -10, 0] } };

export const CircleHelpIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <circle cx="12" cy="12" r="10" />
          <motion.g animate={controls} transition={CIRCLE_HELP_TRANSITION} variants={CIRCLE_HELP_VARIANTS}>
            <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
            <path d="M12 17h.01" />
          </motion.g>
        </svg>
      </div>
    );
  },
);
CircleHelpIcon.displayName = "CircleHelpIcon";

// ─── x (close / remove — e.g. delete branch) ─────────────────────────
const X_PATH_VARIANTS: Variants = {
  normal: { opacity: 1, pathLength: 1 },
  animate: { opacity: [0, 1], pathLength: [0, 1] },
};

export const XIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.path animate={controls} d="M18 6 6 18" variants={X_PATH_VARIANTS} />
          <motion.path animate={controls} d="m6 6 12 12" transition={{ delay: 0.2 }} variants={X_PATH_VARIANTS} />
        </svg>
      </div>
    );
  },
);
XIcon.displayName = "XIcon";

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

// ─── terminal (Exec agent chip — command / tool runtime) ────────────
// Carried verbatim from pqoqubbw/icons (icons/terminal.tsx): the prompt
// line blinks while hovered; the chevron cursor stays put.
const TERMINAL_LINE_VARIANTS: Variants = {
  normal: { opacity: 1 },
  animate: {
    opacity: [1, 0, 1],
    transition: {
      duration: 0.8,
      repeat: Number.POSITIVE_INFINITY,
      ease: "linear",
    },
  },
};

export const TerminalIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <polyline points="4 17 10 11 4 5" />
          <motion.line
            animate={controls}
            initial="normal"
            variants={TERMINAL_LINE_VARIANTS}
            x1="12"
            x2="20"
            y1="19"
            y2="19"
          />
        </svg>
      </div>
    );
  },
);
TerminalIcon.displayName = "TerminalIcon";

// ─── sliders-horizontal (Composer: tools / options trigger) ──────────
const SLIDERS_TRANSITION: Transition = {
  type: "spring",
  stiffness: 100,
  damping: 12,
  mass: 0.4,
};

export const SlidersHorizontalIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.line animate={controls} initial={false} transition={SLIDERS_TRANSITION} variants={{ normal: { x2: 14 }, animate: { x2: 10 } }} x1="21" x2="14" y1="4" y2="4" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x1: 10 }, animate: { x1: 5 } }} x1="10" x2="3" y1="4" y2="4" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x2: 12 }, animate: { x2: 18 } }} x1="21" x2="12" y1="12" y2="12" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x1: 8 }, animate: { x1: 13 } }} x1="8" x2="3" y1="12" y2="12" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x2: 12 }, animate: { x2: 4 } }} x1="3" x2="12" y1="20" y2="20" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x1: 16 }, animate: { x1: 8 } }} x1="16" x2="21" y1="20" y2="20" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x1: 14, x2: 14 }, animate: { x1: 9, x2: 9 } }} x1="14" x2="14" y1="2" y2="6" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x1: 8, x2: 8 }, animate: { x1: 14, x2: 14 } }} x1="8" x2="8" y1="10" y2="14" />
          <motion.line animate={controls} transition={SLIDERS_TRANSITION} variants={{ normal: { x1: 16, x2: 16 }, animate: { x1: 8, x2: 8 } }} x1="16" x2="16" y1="18" y2="22" />
        </svg>
      </div>
    );
  },
);
SlidersHorizontalIcon.displayName = "SlidersHorizontalIcon";

// ─── search (Welcome: research_agent) ────────────────────────────────
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

// ─── book-text (Welcome: wiki_agent) ─────────────────────────────────
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

// ─── frame (Welcome: extract_pdf_figures) ────────────────────────────
const FRAME_TRANSITION: Transition = { type: "spring", stiffness: 160, damping: 17, mass: 1 };
export const FrameIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
          <motion.line animate={controls} transition={FRAME_TRANSITION} variants={{ animate: { translateY: -4 }, normal: { translateX: 0, rotate: 0, translateY: 0 } }} x1={22} x2={2} y1={6} y2={6} />
          <motion.line animate={controls} transition={FRAME_TRANSITION} variants={{ animate: { translateY: 4 }, normal: { translateX: 0, rotate: 0, translateY: 0 } }} x1={22} x2={2} y1={18} y2={18} />
          <motion.line animate={controls} transition={FRAME_TRANSITION} variants={{ animate: { translateX: -4 }, normal: { translateX: 0, rotate: 0, translateY: 0 } }} x1={6} x2={6} y1={2} y2={22} />
          <motion.line animate={controls} transition={FRAME_TRANSITION} variants={{ animate: { translateX: 4 }, normal: { translateX: 0, rotate: 0, translateY: 0 } }} x1={18} x2={18} y1={2} y2={22} />
        </svg>
      </div>
    );
  },
);
FrameIcon.displayName = "FrameIcon";

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
