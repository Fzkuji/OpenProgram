"use client";

import type { Transition, Variants } from "framer-motion";
import { motion, useAnimation } from "framer-motion";
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react";

import { cn } from "@/lib/utils";

import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "./_shared";

// ─── hammer (function-card icon choice) ────────────────────────────────────────────
const HAMMER_HAMMER_VARIANTS: Variants = {
  normal: {
    rotate: 0,
    transition: {
      duration: 0.3,
      ease: "easeOut",
    },
  },
  animate: {
    rotate: [0, -20, 25, 0],
    transition: {
      duration: 0.8,
      times: [0, 0.6, 0.8, 1],
      ease: ["easeInOut", "easeOut", "easeOut"],
    },
  },
};

export const HammerIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
          style={{ transformOrigin: "0% 100%", transformBox: "fill-box" }}
          variants={HAMMER_HAMMER_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="m15 12-9.373 9.373a1 1 0 0 1-3.001-3L12 9" />
          <path d="m18 15 4-4" />
          <path d="m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172v-.344a2 2 0 0 0-.586-1.414l-1.657-1.657A6 6 0 0 0 12.516 3H9l1.243 1.243A6 6 0 0 1 12 8.485V10l2 2h1.172a2 2 0 0 1 1.414.586L18.5 14.5" />
        </motion.svg>
      </div>
    );
  }
);

HammerIcon.displayName = "HammerIcon";

// ─── key (function-card icon choice) ───────────────────────────────────────────────
export const KeyIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
          style={{ originX: 0.3, originY: 0.7 }}
          variants={{
            normal: {
              rotate: 0,
              transition: {
                type: "spring",
                stiffness: 120,
                damping: 14,
                duration: 0.8,
              },
            },
            animate: {
              rotate: [-3, -33, -25, -28],
              transition: {
                duration: 0.6,
                times: [0, 0.6, 0.8, 1],
                ease: "easeInOut",
              },
            },
          }}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4" />
          <path d="m21 2-9.6 9.6" />
          <circle cx="7.5" cy="15.5" r="5.5" />
        </motion.svg>
      </div>
    );
  }
);

KeyIcon.displayName = "KeyIcon";

// ─── languages (function-card icon choice) ─────────────────────────────────────────
const LANGUAGES_PATH_VARIANTS: Variants = {
  normal: { opacity: 1, pathLength: 1, pathOffset: 0 },
  animate: (custom: number) => ({
    opacity: [0, 1],
    pathLength: [0, 1],
    pathOffset: [1, 0],
    transition: {
      opacity: { duration: 0.01, delay: custom * 0.1 },
      pathLength: {
        type: "spring",
        duration: 0.5,
        bounce: 0,
        delay: custom * 0.1,
      },
    },
  }),
};

const LANGUAGES_SVG_VARIANTS: Variants = {
  normal: { opacity: 1 },
  animate: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1,
      delayChildren: 0.2,
    },
  },
};

export const LanguagesIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const svgControls = useAnimation();
    const pathControls = useAnimation();

    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;

      return {
        startAnimation: () => {
          svgControls.start("animate");
          pathControls.start("animate");
        },
        stopAnimation: () => {
          svgControls.start("normal");
          pathControls.start("normal");
        },
      };
    });

    const handleMouseEnter = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          svgControls.start("animate");
          pathControls.start("animate");
        }
      },
      [onMouseEnter, pathControls, svgControls]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          svgControls.start("normal");
          pathControls.start("normal");
        }
      },
      [svgControls, pathControls, onMouseLeave]
    );

    return (
      <div
        className={cn("inline-flex", className)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        {...props}
      >
        <motion.svg
          animate={svgControls}
          fill="none"
          height={size}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          variants={LANGUAGES_SVG_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <motion.path
            animate={pathControls}
            custom={3}
            d="m5 8 6 6"
            variants={LANGUAGES_PATH_VARIANTS}
          />
          <motion.path
            animate={pathControls}
            custom={2}
            d="m4 14 6-6 3-3"
            variants={LANGUAGES_PATH_VARIANTS}
          />
          <motion.path
            animate={pathControls}
            custom={1}
            d="M2 5h12"
            variants={LANGUAGES_PATH_VARIANTS}
          />
          <motion.path
            animate={pathControls}
            custom={0}
            d="M7 2h1"
            variants={LANGUAGES_PATH_VARIANTS}
          />
          <motion.path
            animate={pathControls}
            custom={3}
            d="m22 22-5-10-5 10"
            variants={LANGUAGES_PATH_VARIANTS}
          />
          <motion.path
            animate={pathControls}
            custom={3}
            d="M14 18h6"
            variants={LANGUAGES_PATH_VARIANTS}
          />
        </motion.svg>
      </div>
    );
  }
);

LanguagesIcon.displayName = "LanguagesIcon";

// ─── mic (function-card icon choice) ───────────────────────────────────────────────
const MIC_CAPSULE_VARIANTS: Variants = {
  normal: { y: 0 },
  animate: {
    y: [0, -3, 0, -2, 0],
    transition: {
      duration: 0.6,
      ease: "easeInOut",
    },
  },
};

export const MicIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
          overflow="visible"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M12 19v3" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
          <motion.rect
            animate={controls}
            height="13"
            rx="3"
            variants={MIC_CAPSULE_VARIANTS}
            width="6"
            x="9"
            y="2"
          />
        </svg>
      </div>
    );
  }
);

MicIcon.displayName = "MicIcon";

// ─── pen-tool (function-card icon choice) ──────────────────────────────────────────
const PEN_TOOL_SVG_VARIANTS: Variants = {
  normal: { rotate: 0, translateX: 0, translateY: 0 },
  animate: {
    rotate: [0, 0, 8, -3, 8, 0],
    translateY: [0, 2, 0, -1, 0],
  },
};

const PEN_TOOL_PATH_VARIANTS: Variants = {
  normal: { pathLength: 1, opacity: 1, pathOffset: 0 },
  animate: {
    pathLength: [0, 0, 1],
    opacity: [0, 1],
    pathOffset: [0, 1, 0],
  },
};

export const PenToolIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
          transition={{
            duration: 1,
          }}
          variants={PEN_TOOL_SVG_VARIANTS}
          viewBox="0 0 24 24"
          width={size}
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M15.707 21.293a1 1 0 0 1-1.414 0l-1.586-1.586a1 1 0 0 1 0-1.414l5.586-5.586a1 1 0 0 1 1.414 0l1.586 1.586a1 1 0 0 1 0 1.414z" />
          <path d="m18 13-1.375-6.874a1 1 0 0 0-.746-.776L3.235 2.028a1 1 0 0 0-1.207 1.207L5.35 15.879a1 1 0 0 0 .776.746L13 18" />
          <motion.path
            animate={controls}
            d="m2.3 2.3 7.286 7.286"
            transition={{
              duration: 0.8,
            }}
            variants={PEN_TOOL_PATH_VARIANTS}
          />
          <circle cx="11" cy="11" r="2" />
        </motion.svg>
      </div>
    );
  }
);

PenToolIcon.displayName = "PenToolIcon";

// ─── route (function-card icon choice) ─────────────────────────────────────────────
const ROUTE_CIRCLE_TRANSITION: Transition = {
  duration: 0.3,
  delay: 0.1,
  opacity: { delay: 0.15 },
};

const ROUTE_CIRCLE_VARIANTS: Variants = {
  normal: {
    pathLength: 1,
    opacity: 1,
  },
  animate: {
    pathLength: [0, 1],
    opacity: [0, 1],
  },
};

export const RouteIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
            cx="6"
            cy="19"
            r="3"
            transition={ROUTE_CIRCLE_TRANSITION}
            variants={ROUTE_CIRCLE_VARIANTS}
          />
          <motion.path
            animate={controls}
            d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"
            transition={{ duration: 0.7, delay: 0.5, opacity: { delay: 0.5 } }}
            variants={{
              normal: {
                pathLength: 1,
                opacity: 1,
                pathOffset: 0,
              },
              animate: {
                pathLength: [0, 1],
                opacity: [0, 1],
                pathOffset: [1, 0],
              },
            }}
          />
          <motion.circle
            animate={controls}
            cx="18"
            cy="5"
            r="3"
            transition={ROUTE_CIRCLE_TRANSITION}
            variants={ROUTE_CIRCLE_VARIANTS}
          />
        </svg>
      </div>
    );
  }
);

RouteIcon.displayName = "RouteIcon";

// ─── satellite-dish (function-card icon choice) ────────────────────────────────────
const SATELLITE_DISH_SATELLITE_DISH_VARIANTS: Variants = {
  normal: {
    y: 0,
    rotate: 0,
  },
  animate: {
    y: [0, 1, 2, 0],
    rotate: [0, -15, 0],
    transition: {
      duration: 1.5,
      ease: "easeInOut",
    },
  },
};

const SATELLITE_DISH_PATH_VARIANTS: Variants = {
  normal: {
    opacity: 1,
    transition: {
      duration: 1.1,
    },
  },
  fadeOut: {
    opacity: 0,
    transition: { duration: 1.1 },
  },
  fadeIn: (i: number) => ({
    opacity: 1,
    transition: {
      type: "spring",
      stiffness: 300,
      damping: 20,
      delay: i * 0.1,
    },
  }),
};

export const SatelliteDishIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
  const svgControls = useAnimation();
  const pathControls = useAnimation();
  const isControlledRef = useRef(false);

  const runPathIntro = useCallback(async () => {
    await pathControls.start("fadeOut");
    pathControls.start("fadeIn");
  }, [pathControls]);

  useImperativeHandle(ref, () => {
    isControlledRef.current = ref != null;
    return {
      startAnimation: async () => {
        await Promise.all([svgControls.start("animate"), runPathIntro()]);
      },
      stopAnimation: () => {
        svgControls.start("normal");
        pathControls.start("normal");
      },
    };
  }, [pathControls, ref, runPathIntro, svgControls]);

  const handleMouseEnter = useCallback(
    async (e: React.MouseEvent<HTMLDivElement>) => {
      if (isControlledRef.current) {
        onMouseEnter?.(e);
      } else {
        await Promise.all([svgControls.start("animate"), runPathIntro()]);
      }
    },
    [onMouseEnter, runPathIntro, svgControls]
  );

  const handleMouseLeave = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (isControlledRef.current) {
        onMouseLeave?.(e);
      } else {
        svgControls.start("normal");
        pathControls.start("normal");
      }
    },
    [onMouseLeave, pathControls, svgControls]
  );

  return (
    <div
      className={cn("inline-flex", className)}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      {...props}
    >
      <motion.svg
        animate={svgControls}
        fill="none"
        height={size}
        initial="normal"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        variants={SATELLITE_DISH_SATELLITE_DISH_VARIANTS}
        viewBox="0 0 24 24"
        width={size}
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M4 10a7.31 7.31 0 0 0 10 10Z" />
        <path d="m9 15 3-3" />
        <motion.path
          animate={pathControls}
          custom={1}
          d="M17 13a6 6 0 0 0-6-6"
          initial={{ opacity: 1 }}
          variants={SATELLITE_DISH_PATH_VARIANTS}
        />
        <motion.path
          animate={pathControls}
          custom={2}
          d="M21 13A10 10 0 0 0 11 3"
          initial={{ opacity: 1 }}
          variants={SATELLITE_DISH_PATH_VARIANTS}
        />
      </motion.svg>
    </div>
  );
});

SatelliteDishIcon.displayName = "SatelliteDishIcon";

// ─── scan-text (function-card icon choice) ─────────────────────────────────────────
const SCAN_TEXT_FRAME_VARIANTS: Variants = {
  visible: { opacity: 1 },
  hidden: { opacity: 1 },
};

const SCAN_TEXT_LINE_VARIANTS: Variants = {
  visible: { pathLength: 1, opacity: 1 },
  hidden: { pathLength: 0, opacity: 0 },
};

export const ScanTextIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ onMouseEnter, onMouseLeave, className, size = 28, ...props }, ref) => {
    const controls = useAnimation();
    const isControlledRef = useRef(false);

    useImperativeHandle(ref, () => {
      isControlledRef.current = true;

      return {
        startAnimation: async () => {
          await controls.start((i) => ({
            pathLength: 0,
            opacity: 0,
            transition: { delay: i * 0.1, duration: 0.3 },
          }));
          await controls.start((i) => ({
            pathLength: 1,
            opacity: 1,
            transition: { delay: i * 0.1, duration: 0.3 },
          }));
        },
        stopAnimation: () => controls.start("visible"),
      };
    });

    const handleMouseEnter = useCallback(
      async (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          await controls.start((i) => ({
            pathLength: 0,
            opacity: 0,
            transition: { delay: i * 0.1, duration: 0.3 },
          }));
          await controls.start((i) => ({
            pathLength: 1,
            opacity: 1,
            transition: { delay: i * 0.1, duration: 0.3 },
          }));
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("visible");
        }
      },
      [controls, onMouseLeave]
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
          <motion.path d="M3 7V5a2 2 0 0 1 2-2h2" variants={SCAN_TEXT_FRAME_VARIANTS} />
          <motion.path d="M17 3h2a2 2 0 0 1 2 2v2" variants={SCAN_TEXT_FRAME_VARIANTS} />
          <motion.path
            d="M21 17v2a2 2 0 0 1-2 2h-2"
            variants={SCAN_TEXT_FRAME_VARIANTS}
          />
          <motion.path d="M7 21H5a2 2 0 0 1-2-2v-2" variants={SCAN_TEXT_FRAME_VARIANTS} />
          <motion.path
            animate={controls}
            custom={0}
            d="M7 8h8"
            initial="visible"
            variants={SCAN_TEXT_LINE_VARIANTS}
          />
          <motion.path
            animate={controls}
            custom={1}
            d="M7 12h10"
            initial="visible"
            variants={SCAN_TEXT_LINE_VARIANTS}
          />
          <motion.path
            animate={controls}
            custom={2}
            d="M7 16h6"
            initial="visible"
            variants={SCAN_TEXT_LINE_VARIANTS}
          />
        </svg>
      </div>
    );
  }
);

ScanTextIcon.displayName = "ScanTextIcon";

// ─── shield-check (function-card icon choice) ──────────────────────────────────────
const SHIELD_CHECK_PATH_VARIANTS: Variants = {
  normal: {
    opacity: 1,
    pathLength: 1,
    scale: 1,
    transition: {
      duration: 0.3,
      opacity: { duration: 0.1 },
    },
  },
  animate: {
    opacity: [0, 1],
    pathLength: [0, 1],
    scale: [0.5, 1],
    transition: {
      duration: 0.4,
      opacity: { duration: 0.1 },
    },
  },
};

export const ShieldCheckIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
          <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
          <motion.path
            animate={controls}
            d="m9 12 2 2 4-4"
            initial="normal"
            variants={SHIELD_CHECK_PATH_VARIANTS}
          />
        </svg>
      </div>
    );
  }
);

ShieldCheckIcon.displayName = "ShieldCheckIcon";

// ─── timer (function-card icon choice) ─────────────────────────────────────────────
const TIMER_HAND_VARIANTS: Variants = {
  normal: {
    rotate: 0,
    originX: "0%",
    originY: "100%",
    transition: {
      duration: 0.6,
      ease: [0.4, 0, 0.2, 1],
    },
  },
  animate: {
    rotate: 300,
    originX: "0%",
    originY: "100%",
    transition: {
      delay: 0.1,
      duration: 0.6,
      ease: [0.4, 0, 0.2, 1],
    },
  },
};

const TIMER_BUTTON_VARIANTS: Variants = {
  normal: {
    scale: 1,
    y: 0,
  },
  animate: {
    scale: [0.9, 1],
    y: [0, 1, 0],
    transition: {
      duration: 0.3,
      ease: [0.4, 0, 0.2, 1],
    },
  },
};

export const TimerIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
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
        if (isControlledRef.current) {
          onMouseEnter?.(e);
        } else {
          controls.start("animate");
        }
      },
      [controls, onMouseEnter]
    );

    const handleMouseLeave = useCallback(
      (e: React.MouseEvent<HTMLDivElement>) => {
        if (isControlledRef.current) {
          onMouseLeave?.(e);
        } else {
          controls.start("normal");
        }
      },
      [controls, onMouseLeave]
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
          <motion.line
            animate={controls}
            variants={TIMER_BUTTON_VARIANTS}
            x1="10"
            x2="14"
            y1="2"
            y2="2"
          />
          <motion.line
            animate={controls}
            initial="normal"
            variants={TIMER_HAND_VARIANTS}
            x1="12"
            x2="15"
            y1="14"
            y2="11"
          />
          <circle cx="12" cy="14" r="8" />
        </svg>
      </div>
    );
  }
);

TimerIcon.displayName = "TimerIcon";

