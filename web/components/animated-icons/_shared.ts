"use client";

// Shared types for the animated-icon set. Every icon exposes the same
// imperative start/stop API so a caller holds one ref type. Split out
// of the old 6000-line animated-icons.tsx; re-exported from ./index so
// `@/components/animated-icons` import paths are unchanged.

import type { HTMLAttributes } from "react";

export interface AnimatedNavIconHandle {
  startAnimation: () => void;
  stopAnimation: () => void;
}

export interface AnimatedNavIconProps extends HTMLAttributes<HTMLDivElement> {
  size?: number;
}
