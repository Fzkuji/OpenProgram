/**
 * Canonical Tailwind class strings for ALL popover selection menus — the
 * single source of truth so every dropdown / context menu in the app is
 * visually identical: topbar pickers (channel / branch / project /
 * agent), the sidebar Recents context menu, and any future one.
 *
 * ── Design spec ──────────────────────────────────────────────────────
 *  Panel (MENU_PANEL): 10px radius · 1px `--border` · `--bg-tertiary` ·
 *    4px padding (the inset gap between rows and the frame) ·
 *    `--shadow-popover` · 1px gap between rows · scrolls past 60vh.
 *  Row (itemCls):      min 28px tall · 6px radius · 8px/4px padding ·
 *    13px/18px text · 7px icon↔label gap · hover & active = `--bg-hover`
 *    + `--text-bright`. danger = red text + faint red hover. One tier
 *    shorter / smaller than the sidebar nav rows, matching Claude's
 *    compact pickers.
 *  Section label (GROUP_LABEL) · key hint (SHORTCUT) · divider
 *    (MENU_SEPARATOR, full-bleed) · trailing check (CHECK).
 *
 *  The radix PopoverContent wrapper MUST be transparent
 *  (`border-0 bg-transparent p-0 shadow-none`) — the frame is always
 *  MENU_PANEL, never the wrapper, so every menu shares one frame.
 */

export const MENU_PANEL =
  "flex max-h-[60vh] flex-col gap-px overflow-y-auto rounded-[10px] " +
  "border border-[var(--border)] bg-bg-tertiary p-[4px] shadow-(--shadow-popover)";

export const GROUP_LABEL =
  "flex items-center gap-[6px] px-[8px] pb-[2px] pt-[5px] " +
  "text-[11px] text-text-muted";

export const CHECK = "shrink-0 text-[var(--accent-blue)]";

/** Right-aligned single-key shortcut hint (e.g. the R / P / C / A / D in
 *  the Recents context menu). Stays muted — doesn't brighten with the
 *  row on hover. */
export const SHORTCUT = "shrink-0 text-[11px] text-text-muted";

/** Full-bleed divider between menu groups. The negative inline margin
 *  cancels MENU_PANEL's 4px padding so the line spans edge to edge. */
export const MENU_SEPARATOR = "-mx-[4px] my-[4px] h-px bg-[var(--border)]";

/** A selectable menu row — `active` swaps the resting / hover colours;
 *  `danger` makes it a destructive (red) action. ~28px tall (one tier
 *  shorter than the sidebar nav rows). */
export function itemCls(active: boolean, danger = false): string {
  // Fixed 26px height (NOT min-height) so a row's inner content — model
  // capability icons, context-size labels, checkmarks — can never stretch
  // it taller than its neighbours. Every selection row in every dropdown
  // is therefore exactly the same height, matching a compact small menu.
  const base =
    "flex h-[26px] shrink-0 cursor-pointer items-center gap-[8px] rounded-[6px] " +
    "px-[8px] text-[13px] leading-[18px] transition-colors duration-75 ";
  if (danger) {
    return (
      base +
      "text-[var(--accent-red)] hover:text-[var(--accent-red)] " +
      "hover:bg-[color-mix(in_srgb,var(--accent-red)_15%,transparent)]"
    );
  }
  return (
    base +
    (active
      ? "bg-bg-hover text-text-bright"
      : "text-text-primary hover:bg-bg-hover hover:text-text-bright")
  );
}
