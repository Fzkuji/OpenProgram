"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "framer-motion";
import type { AnimatedNavIconHandle } from "@/components/animated-icons";

/** Drive the shared icon from its entire button, including keyboard focus. */
export function useActionIconAnimation(disabled = false) {
  const ref = useRef<AnimatedNavIconHandle>(null);
  const hovered = useRef(false);
  const focused = useRef(false);
  const reducedMotion = useReducedMotion();
  const start = () => {
    if (!disabled && !reducedMotion) ref.current?.startAnimation?.();
  };
  const stopIfIdle = () => {
    if (!hovered.current && !focused.current) ref.current?.stopAnimation?.();
  };
  useEffect(() => {
    if (disabled) { hovered.current = false; focused.current = false; }
    if (disabled || reducedMotion) ref.current?.stopAnimation?.();
  }, [disabled, reducedMotion]);

  return {
    ref,
    handlers: {
      onMouseEnter: () => { hovered.current = true; start(); },
      onMouseLeave: () => { hovered.current = false; stopIfIdle(); },
      onFocus: () => { focused.current = true; start(); },
      onBlur: () => { focused.current = false; stopIfIdle(); },
    },
  };
}
