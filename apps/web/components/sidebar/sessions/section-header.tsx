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
 * - Chevron: the transcript's still 14px disclosure chevron, hidden
 *   until the section is hovered, brightens with the label, › when
 *   collapsed and ⌄ when open. The row is
 *   `items-center`, so the chevron is centred on the LABEL — identical
 *   on every section regardless of line height (the first section also
 *   carries the 22px filter button, which would otherwise stretch the
 *   line). `transform` carries the quarter turn.
 *
 * Hover reveal relies on the SECTION WRAPPER carrying Tailwind's
 * `group/sec` marker, so hovering anywhere in the section (header or
 * body) reveals the chevron. Callers must wrap the section in a
 * `className="group/sec"` element.
 */

import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { activateOnKey } from "@/lib/utils";

export function SectionHeader({
  name,
  collapsible,
  collapsed,
  onToggle,
  actions,
  className,
}: {
  /** The label; a section may add a muted figure after its name. */
  name: ReactNode;
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
      className={
        // One vertical rhythm in both sidebars: 16px from whatever is
        // above (a nav item, the previous header, the previous section's
        // last row) to the label, and 6px from the label to its first
        // row. A collapsed header has no rows, so it keeps no space below
        // and the next label is again 16px away.
        "group/hdr flex select-none items-center gap-1 px-[8px] pt-[16px]" +
        (collapsible && collapsed ? "" : " pb-[6px]") +
        (className ? " " + className : "")
      }
    >
      {/* Collapse target = the label/chevron PLUS the empty gap, stretching
          (flex-1) right up to the action button's left edge — but no
          further. So the area to the RIGHT of an action button (e.g. the
          Recents filter) is never a surprise collapse target. */}
      <div
        onClick={collapsible ? onToggle : undefined}
        /* Only the collapsible variant is interactive — a plain header
           must not become a tab stop that does nothing. */
        role={collapsible ? "button" : undefined}
        tabIndex={collapsible ? 0 : undefined}
        aria-expanded={collapsible ? !collapsed : undefined}
        onKeyDown={collapsible ? activateOnKey(onToggle) : undefined}
        className={
          "flex min-w-0 flex-1 items-center gap-2" +
          (collapsible ? " cursor-pointer" : "")
        }
      >
        {/* Label — 12px / 400 / text-secondary @ 80%, matching Claude's
            section labels. Brightens to white together with the chevron
            when the section is hovered. */}
        <span
          className="truncate text-fs-base font-normal leading-[20px] text-text-secondary
            opacity-80 transition-colors group-hover/hdr:text-text-primary
            group-hover/hdr:opacity-100"
        >
          {name}
        </span>
        {collapsible ? (
          // The transcript's disclosure chevron (execution-strip.css): a
          // still 14px ChevronRight, 8px after the label and centred on its
          // line (items-center), › when collapsed, turned to ⌄ when open.
          // Hidden until section hover, brightens with the label.
          <ChevronRight
            size={14}
            aria-hidden="true"
            className="shrink-0 text-text-secondary opacity-0
              group-hover/hdr:opacity-100 group-hover/hdr:text-text-primary"
            style={{
              transform: collapsed ? "none" : "rotate(90deg)",
              transition: "transform var(--motion-medium) var(--ease-standard), opacity var(--motion-fast) var(--ease-standard), color var(--motion-fast) var(--ease-standard)",
            }}
          />
        ) : null}
      </div>
      {actions ? (
        // Action button (e.g. Recents filter) has a slightly larger box
        // than the 20px label line; `-my-[2px]` lets it overlap the line
        // symmetrically instead of stretching the header taller than
        // button-less sections (it stays vertically centred on the label).
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
