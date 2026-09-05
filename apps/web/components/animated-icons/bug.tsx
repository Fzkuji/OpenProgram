"use client";

import { Bug } from "lucide-react";
import { motion, useAnimation, useReducedMotion } from "framer-motion";
import { forwardRef, useImperativeHandle } from "react";
import { cn } from "@/lib/utils";
import type { AnimatedNavIconHandle, AnimatedNavIconProps } from "./_shared";

/** Lucide supplies the glyph; sidebar rows drive the shared motion handle. */
export const BugIcon = forwardRef<AnimatedNavIconHandle, AnimatedNavIconProps>(
  ({ size = 28, className, ...props }, ref) => {
    const controls = useAnimation();
    const reducedMotion = useReducedMotion();
    useImperativeHandle(ref, () => ({
      startAnimation: () => {
        if (!reducedMotion) void controls.start({ rotate: [0, -12, 12, -8, 0], transition: { duration: 0.5 } });
      },
      stopAnimation: () => { void controls.start({ rotate: 0, transition: { duration: 0.15 } }); },
    }), [controls, reducedMotion]);
    return <div className={cn("inline-flex", className)} {...props}>
      <motion.div className="inline-flex" animate={controls}>
        <Bug size={size} aria-hidden="true" />
      </motion.div>
    </div>;
  },
);
BugIcon.displayName = "BugIcon";
