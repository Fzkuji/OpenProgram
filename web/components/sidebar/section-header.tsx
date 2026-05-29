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
 * - Chevron: the shared animated icon (pqoqubbw / framer-motion),
 *   hidden until the section is hovered, bounces on hover, brightens to
 *   white with the label, rotates to › when collapsed. The row is
 *   `items-center`, so the chevron is centred on the LABEL — identical
 *   on every section regardless of line height (the first section also
 *   carries the 22px filter button, which would otherwise stretch the
 *   line). A small `top` nudge centres it on the text's cap-band so it
 *   reads level with the letters; `top`/`relative` don't touch
 *   `transform`, so the rotate + bounce stay intact.
 *
 * Hover reveal relies on the SECTION WRAPPER carrying Tailwind's
 * `group/sec` marker, so hovering anywhere in the section (header or
 * body) reveals the chevron. Callers must wrap the section in a
 * `className="group/sec"` element.
 */

import { useRef, type ReactNode } from "react";

import {
  ChevronDownIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";

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
  // Drive the chevron's bounce from the header hover (controlled mode).
  const chevronRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      onClick={collapsible ? onToggle : undefined}
      onMouseEnter={() => chevronRef.current?.startAnimation()}
      onMouseLeave={() => chevronRef.current?.stopAnimation()}
      className={
        // Vertical rhythm for the sidebar section headers: 16px above
        // the label (section break) and 5px below it before the first
        // row. (Claude uses ~16 / 8; tuned a touch tighter below.)
        "flex select-none items-center gap-1 px-[8px] pt-[16px] pb-[5px]" +
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
        // Shared animated chevron. Centred on the label (items-center)
        // with a 1px upward nudge so it sits level with the text's
        // cap-band, hidden until section hover, brightens with the label,
        // rotates to › when collapsed.
        <ChevronDownIcon
          ref={chevronRef}
          size={16}
          className="relative top-[1px] shrink-0 text-[var(--text-secondary)] opacity-0
            transition-[opacity,color] duration-150
            group-hover/sec:opacity-100 group-hover/sec:text-[var(--text-bright)]"
          style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)" }}
        />
      ) : null}
      {actions ? (
        // The filter button is 22px — taller than the 18px label line.
        // `-my-[2px]` absorbs the overflow so it doesn't stretch this
        // header taller than the button-less sections; the button keeps
        // its full 22px click target (centred on the label).
        <span
          className="-my-[2px] ml-auto flex items-center"
          onClick={(e) => e.stopPropagation()}
        >
          {actions}
        </span>
      ) : null}
    </div>
  );
}
