/**
 * Composer tool / plus-menu icons.
 *
 * The composer area uses the filled Solar set (``@/components/solar-icons``,
 * Bold Duotone) rather than the app-wide pqoqubbw line icons. Each glyph
 * is wrapped as ``forwardRef`` so a parent button / row / chip can drive
 * the hover micro-motion imperatively (start / stop) — see
 * ./controls/menu-pieces. The + trigger button is icon-sized, so its own
 * hover suffices (uncontrolled). Per project rule we never hand-author
 * icon SVGs.
 *
 * Icons that reflect a toggle take the state and switch glyphs:
 * Unattended shows a closed eye once nobody is watching, the
 * while-running mode shows a forward arrow for Steer and a queue list
 * for Queue.
 *
 * Send is the Solar paper plane; Stop stays a local
 * filled square (the send button's stop glyph, not an icon).
 *
 * Every glyph here fills its 16 px box (SolarIcon fit="fill") and grows to
 * exactly 18 px on hover, the paper plane like the rest. The stop
 * square keeps its 8 px square in the 16 px box.
 */
"use client";

import { forwardRef } from "react";

import type { AnimatedNavIconHandle } from "@/components/animated-icons";
import { SolarIcon } from "@/components/solar-icons";

type IconProps = { size?: number };

// The composer's "+" trigger opens the tools/options menu; it's a
// tuning-sliders ("adjust options") glyph rather than a plus.
export const OptionsIcon = forwardRef<AnimatedNavIconHandle, IconProps>(
  function OptionsIcon({ size = 16 }, ref) {
    return <SolarIcon ref={ref} fit="fill" name="tuning-2" size={size} />;
  },
);

export const AttachIcon = forwardRef<AnimatedNavIconHandle, IconProps>(
  function AttachIcon({ size = 16 }, ref) {
    return <SolarIcon ref={ref} fit="fill" name="paperclip" size={size} />;
  },
);

export const RecordIcon = forwardRef<AnimatedNavIconHandle, IconProps>(
  function RecordIcon({ size = 16 }, ref) {
    return <SolarIcon ref={ref} fit="fill" name="record" size={size} />;
  },
);

export const WebSearchIcon = forwardRef<AnimatedNavIconHandle, IconProps>(
  function WebSearchIcon({ size = 16 }, ref) {
    return <SolarIcon ref={ref} fit="fill" name="global" size={size} />;
  },
);

export const SandboxIcon = forwardRef<AnimatedNavIconHandle, IconProps>(
  function SandboxIcon({ size = 16 }, ref) {
    return <SolarIcon ref={ref} fit="fill" name="box-minimalistic" size={size} />;
  },
);

// Unattended (no-one watching → agent won't ask questions): open eye
// while someone is attending, closed eye once the mode is on.
export const UnattendedIcon = forwardRef<AnimatedNavIconHandle, IconProps & { on?: boolean }>(
  function UnattendedIcon({ size = 16, on = false }, ref) {
    return <SolarIcon ref={ref} fit="fill" name={on ? "eye-closed" : "eye"} size={size} />;
  },
);

// Send glyph — paper plane, grown on the send button's hover via ref like the
// row's other glyphs (controlled): it does not glide. `.actionBtn svg` keeps it filled with currentColor.
export const SendIcon = forwardRef<AnimatedNavIconHandle>(function SendIcon(_props, ref) {
  return <SolarIcon ref={ref} fit="fill" name="plain-2" size={16} />;
});

export function StopIcon() {
  return (
    <svg viewBox="0 0 24 24">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
