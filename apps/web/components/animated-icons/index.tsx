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

export type { AnimatedNavIconHandle, AnimatedNavIconProps } from "./_shared";
export * from "./icons-1";
export * from "./icons-2";
export * from "./icons-3";
export * from "./icons-4";
export * from "./icons-5";
export * from "./icons-6";
