/**
 * Shared Tailwind class strings for the topbar popover menus
 * (channel / branch). Replaces the legacy `.model-dropdown` /
 * `.model-dd-*` global CSS from `app/styles/08-dropdown.css`.
 */

export const MENU_PANEL =
  "flex max-h-[60vh] flex-col gap-px overflow-y-auto rounded-[10px] " +
  "border border-[var(--border)] bg-bg-tertiary p-[6px] shadow-(--shadow-popover)";

export const GROUP_LABEL =
  "flex items-center gap-[6px] px-[10px] pb-[4px] pt-[8px] " +
  "text-[12px] font-semibold tracking-[0.02em] text-text-muted";

export const CHECK = "shrink-0 text-[var(--accent-blue)]";

/** A selectable menu row — `active` swaps the resting / hover colours. */
export function itemCls(active: boolean): string {
  return (
    "flex min-h-[32px] cursor-pointer items-center gap-[8px] rounded-[8px] " +
    "px-[8px] py-[6px] text-[14px] leading-[20px] transition-colors duration-75 " +
    (active
      ? "bg-bg-hover text-text-bright"
      : "text-text-primary hover:bg-bg-hover hover:text-text-bright")
  );
}
