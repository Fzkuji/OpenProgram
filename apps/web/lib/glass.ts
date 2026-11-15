/**
 * Glass popover surface (iOS-style material) as Tailwind utilities, so a
 * consumer's own utilities (bg-transparent, shadow-none, rounded-*) still
 * override it through tailwind-merge. Tokens live in app/styles/themes/fallback.css
 * (--glass-*); CSS modules use those variables directly.
 */
export const GLASS_MATERIAL =
  "border-[0.5px] border-[var(--glass-ring)] " +
  "bg-[var(--glass-surface)] [-webkit-backdrop-filter:var(--glass-backdrop)] " +
  "[backdrop-filter:var(--glass-backdrop)] shadow-[var(--glass-shadow)]";

/** The material plus the glass corner radius, for popovers that are not menus. */
export const GLASS_SURFACE = GLASS_MATERIAL + " rounded-lg";
