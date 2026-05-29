"use client";

/**
 * SectionHeader — the one collapsible section header used everywhere in
 * the sidebar: the Recents date/group buckets (Today / Yesterday /
 * Working / project…) in `SessionsList` AND the Favorites section in
 * `Sidebar`. Having a single component is the point — the label
 * typography and the collapse chevron can never drift between sections.
 *
 *   label  ⌄                            [right-side actions]
 *
 * - Label: 12px / 400 / text-secondary @ 80%, brightening to white on
 *   section hover (matches Claude's section labels).
 * - Chevron: a tight-viewBox inline chevron whose RENDERED height is the
 *   visible mark (unlike a lucide icon, which buries a small mark inside
 *   a big padded box). It's sized to the text's main body height (~9px,
 *   the cap-top→baseline band of the 12px label) and vertically centred
 *   on that band, so it reads as flush / level with the letters. Hidden
 *   until the section is hovered, brightens to white with the label,
 *   rotates to › when collapsed.
 *
 * Hover reveal relies on the SECTION WRAPPER carrying Tailwind's
 * `group/sec` marker, so hovering anywhere in the section (header or
 * body) reveals the chevron. Callers must wrap the section in a
 * `className="group/sec"` element.
 */

import { type ReactNode } from "react";

export function SectionHeader({
  name,
  collapsible,
  collapsed,
  onToggle,
  actions,
  className,
}: {
  name: string;
  collapsible: boolean;
  collapsed: boolean;
  onToggle: () => void;
  /** Right-aligned controls (e.g. the Recents filter button). Clicks
   *  inside are stopped from toggling the section. */
  actions?: ReactNode;
  /** Extra classes for the header row — used to tune per-section
   *  padding. Defaults to the bucket rhythm (px-8 / pt-10 / pb-2). */
  className?: string;
}) {
  return (
    <div
      onClick={collapsible ? onToggle : undefined}
      className={
        "flex select-none items-center gap-1 px-[8px] pt-[10px] pb-[2px]" +
        (collapsible ? " cursor-pointer" : "") +
        (className ? " " + className : "")
      }
    >
      {/* Label — 12px / 400 / text-secondary @ 80%, matching Claude's
          section labels. Brightens to white together with the chevron
          when the section is hovered. */}
      <span
        className="truncate text-[12px] font-normal text-[var(--text-secondary)]
          opacity-80 transition-colors group-hover/sec:text-[var(--text-bright)]
          group-hover/sec:opacity-100"
      >
        {name}
      </span>
      {collapsible ? (
        // Tight-viewBox chevron: the rendered box IS the visible mark, so
        // its height equals the text's main body height (cap-top→baseline,
        // ~9px for the 12px label) and it sits level with the letters.
        // `items-center` centres it on the label; a tiny `top` corrects
        // for the descender so its centre matches the cap-band centre.
        // Hidden until section hover, brightens with the label, rotates
        // to › when collapsed (rotate via `transform`; vertical nudge via
        // `top`, so they don't clash).
        <svg
          aria-hidden="true"
          width="15"
          height="9"
          viewBox="0 0 15 9"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="relative top-[-1px] shrink-0 text-[var(--text-secondary)] opacity-0
            transition-[opacity,color] duration-150
            group-hover/sec:opacity-100 group-hover/sec:text-[var(--text-bright)]"
          style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)" }}
        >
          <path d="M1.5 2 L7.5 7 L13.5 2" />
        </svg>
      ) : null}
      {actions ? (
        <span
          className="ml-auto flex items-center"
          onClick={(e) => e.stopPropagation()}
        >
          {actions}
        </span>
      ) : null}
    </div>
  );
}
