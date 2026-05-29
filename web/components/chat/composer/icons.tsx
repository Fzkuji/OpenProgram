/**
 * Composer tool / plus-menu icons.
 *
 * Tools / Web Search / Fast / Plus are the ANIMATED line glyphs from
 * pqoqubbw/icons (the app-wide set in ``@/components/animated-icons``),
 * wrapped as ``forwardRef`` so a parent button / row / chip can drive
 * the hover animation imperatively (start / stop) — see
 * ./controls/menu-pieces. The + trigger button is icon-sized, so its
 * own hover suffices (uncontrolled). Per project rule we never
 * hand-author icon SVGs.
 *
 * Send / Stop / Check / ChipClose / Caret stay local: they're the
 * send-button glyphs + tiny indicators (filled / CSS-coloured, or
 * micro-glyphs), not part of the animated line-icon family.
 */
"use client";

import { forwardRef, useImperativeHandle } from "react";
import { motion, useAnimation, type Variants } from "framer-motion";

import {
  type AnimatedNavIconHandle,
  ChromeIcon,
  SlidersHorizontalIcon,
  WrenchIcon,
  ZapIcon,
} from "@/components/animated-icons";

// The composer's "+" trigger opens the tools/options menu; it's a
// sliders-horizontal ("adjust options") glyph rather than a plus.
export const OptionsIcon = forwardRef<AnimatedNavIconHandle, { size?: number }>(
  function OptionsIcon({ size = 16 }, ref) {
    return <SlidersHorizontalIcon ref={ref} size={size} />;
  },
);

export const ToolsIcon = forwardRef<AnimatedNavIconHandle, { size?: number }>(
  function ToolsIcon({ size = 20 }, ref) {
    return <WrenchIcon ref={ref} size={size} />;
  },
);

export const WebSearchIcon = forwardRef<AnimatedNavIconHandle, { size?: number }>(
  function WebSearchIcon({ size = 20 }, ref) {
    return <ChromeIcon ref={ref} size={size} />;
  },
);

export const FastIcon = forwardRef<AnimatedNavIconHandle, { size?: number }>(
  function FastIcon({ size = 20 }, ref) {
    return <ZapIcon ref={ref} size={size} />;
  },
);

// Send glyph — claude.ai-style up-arrow (pqoqubbw arrow-up). Driven by
// the send button's hover via ref (controlled). Rendered as a bare
// <svg> (no wrapper div) so the existing `.actionBtn svg` sizing still
// applies; `.sendBtn svg { fill: none }` keeps it stroked, not filled.
const SEND_ARROW_VARIANTS: Variants = {
  normal: { d: "m5 12 7-7 7 7", translateY: 0 },
  animate: { d: "m5 12 7-7 7 7", translateY: [0, 3, 0], transition: { duration: 0.4 } },
};
const SEND_SHAFT_VARIANTS: Variants = {
  normal: { d: "M12 19V5" },
  animate: { d: ["M12 19V5", "M12 19V10", "M12 19V5"], transition: { duration: 0.4 } },
};

export const SendIcon = forwardRef<AnimatedNavIconHandle>((_props, ref) => {
  const controls = useAnimation();
  useImperativeHandle(ref, () => ({
    startAnimation: () => controls.start("animate"),
    stopAnimation: () => controls.start("normal"),
  }));
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      xmlns="http://www.w3.org/2000/svg"
    >
      <motion.path animate={controls} initial="normal" d="m5 12 7-7 7 7" variants={SEND_ARROW_VARIANTS} />
      <motion.path animate={controls} initial="normal" d="M12 19V5" variants={SEND_SHAFT_VARIANTS} />
    </svg>
  );
});
SendIcon.displayName = "SendIcon";

export function StopIcon() {
  return (
    <svg viewBox="0 0 24 24">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
      <path d="M15.188 5.11a.5.5 0 0 1 .752.626l-.056.084-7.5 9a.5.5 0 0 1-.738.033l-3.5-3.5-.064-.078a.501.501 0 0 1 .693-.693l.078.064 3.113 3.113 7.15-8.58z" />
    </svg>
  );
}

export function ChipCloseIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
    >
      <line x1="3" y1="3" x2="9" y2="9" />
      <line x1="9" y1="3" x2="3" y2="9" />
    </svg>
  );
}

export function CaretIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="10"
      height="10"
      viewBox="0 0 10 10"
    >
      <path
        d="M2 3.5L5 6.5L8 3.5"
        stroke="currentColor"
        strokeWidth="1.5"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
