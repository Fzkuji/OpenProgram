"use client";

import type { Transition, Variants } from "framer-motion";
import { motion, useAnimation } from "framer-motion";
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react";

import { cn } from "@/lib/utils";

import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "./_shared";

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

