/**
 * Composer tool / plus-menu icons.
 *
 * Functional (line / stroke) icons come from **lucide-react** — the
 * project's standard line-icon set (used across ~11 other components).
 * Sourcing them from one library guarantees the toolbar reads as a
 * single visual family (matched grid, stroke weight, corner radius)
 * instead of the hand-rolled SVGs that drifted out of sync (the
 * colourful ⚡ emoji being the worst offender). Per project rule, we
 * do not hand-author icon SVGs.
 *
 * Send / Stop stay local: they're the send-button glyphs, filled and
 * coloured by CSS rather than stroked, so they're not part of the
 * line-icon family.
 */
"use client";

import { Globe, Plus, Wrench, Zap } from "lucide-react";

// Thin wrappers preserve the existing ``{ size }`` prop API so call
// sites don't change. strokeWidth 1.75 matches the prior toolbar
// weight; lucide's default is 2.
export function PlusIcon({ size = 16 }: { size?: number }) {
  return <Plus size={size} strokeWidth={1.75} />;
}

export function ToolsIcon({ size = 20 }: { size?: number }) {
  return <Wrench size={size} strokeWidth={1.75} />;
}

export function WebSearchIcon({ size = 20 }: { size?: number }) {
  return <Globe size={size} strokeWidth={1.75} />;
}

export function FastIcon({ size = 20 }: { size?: number }) {
  return <Zap size={size} strokeWidth={1.75} />;
}

export function SendIcon() {
  return (
    <svg viewBox="0 0 24 24">
      <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
  );
}

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
