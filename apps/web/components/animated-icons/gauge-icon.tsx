"use client";

import { motion, useReducedMotion } from "framer-motion";
import { forwardRef, useImperativeHandle, useState } from "react";
import { cn } from "@/lib/utils";
import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "./_shared";

/** User-supplied gauge geometry and spring, with a persistent active state. */
export const GaugeIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps & { active?: boolean }>(
  ({ size = 28, active = false, className, onMouseEnter, onMouseLeave, ...props }, ref) => {
    const [hovered, setHovered] = useState(false);
    const reduced = useReducedMotion();
    useImperativeHandle(ref, () => ({
      startAnimation: () => setHovered(true),
      stopAnimation: () => setHovered(false),
    }), []);
    return <div {...props} className={cn(className)}
      onMouseEnter={(e) => { setHovered(true); onMouseEnter?.(e); }}
      onMouseLeave={(e) => { setHovered(false); onMouseLeave?.(e); }}>
      <svg fill="none" height={size} width={size} stroke="currentColor" strokeLinecap="round"
        strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true"
        style={{ color: active ? "var(--accent-red)" : undefined }}>
        <motion.path d="m12 14 4-4" initial={false}
          style={{ originX: 0, originY: 1 }}
          animate={active || hovered ? { rotate: 72 } : { rotate: 0 }}
          transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 160, damping: 17, mass: 1 }} />
        <path d="M3.34 19a10 10 0 1 1 17.32 0" />
      </svg>
    </div>;
  },
);
GaugeIcon.displayName = "GaugeIcon";
