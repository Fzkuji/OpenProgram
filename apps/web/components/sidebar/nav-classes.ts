/**
 * Shared Tailwind class strings for sidebar nav rows + the toggle
 * button. Used by both the left `<Sidebar />` and the right
 * `<RightSidebar />` so they stay visually identical.
 *
 * The matching legacy CSS lived in `app/styles/02-sidebar.css` under
 * `.sidebar-toggle / .sidebar-nav-item / .sidebar-nav-icon /
 * .sidebar-nav-label / .sidebar-nav-action`. After migration those
 * global rules are gone — every consumer must reach for these
 * constants instead.
 *
 * Pixel-y values are explicit (`gap-[12px]`, `px-[8px]`) because
 * this project's `html { font-size: 14px }` makes Tailwind's
 * rem-based scale 0.875× off. Height + corner come from the LIST
 * set in docs/design/ui/surface-system.md
 * (--ui-list-h, --ui-list-radius) so future size adjustments
 * happen in one place.
 */

/** Header button — toggle / collapse the rail. 32×32 round-corner. */
export const sidebarToggleClass = [
  // Legacy global class — used by .sidebar.collapsed CSS selectors
  // in app/styles/base.css. Without it those rules silently miss
  // the React-rendered DOM. Keep the descriptive name + the Tailwind
  // utilities side by side.
  "sidebar-toggle",
  "flex h-[var(--ui-list-h)] w-[var(--ui-list-h)] shrink-0 cursor-pointer items-center justify-center",
  "rounded-[var(--ui-list-radius)] border-none bg-transparent p-0",
  "text-nav-color",
  "transition-colors duration-150 ease-out",
  "hover:bg-bg-hover hover:text-nav-color-hover",
  "active:bg-[rgba(0,0,0,0.2)]",
].join(" ");

/**
 * Main nav row — `<NewChat />`, `<Functions />`, `<Memory />`, `<Chats />`
 * on the left; `<History />`, `<Execution Detail />` on the right.
 * The `group` class lets the inner icon's `group-hover:scale-[1.12]`
 * fire when the row is hovered.
 */
export const sidebarNavItemClass = [
  // `ui-list-item` (global, app/styles/base.css) is the single source for
  // the row box — height, corner, padding, gap, colour-transition, and the
  // hover --bg-hover tint. Only sidebar-specific bits live here.
  // `sidebar-nav-item` stays for the legacy `.sidebar.collapsed
  // .sidebar-nav-item` rules in base.css that force a 32×32 centered square
  // when collapsed.
  "ui-list-item sidebar-nav-item",
  "group w-full shrink-0",
  "font-normal text-nav-color no-underline",
  "hover:text-nav-color-hover",
  "active:bg-[rgba(0,0,0,0.15)]",
].join(" ");

/** Active variant — appended to `.sidebarNavItemClass` when current route matches. */
export const sidebarNavItemActiveClass = "bg-bg-hover text-nav-color-hover";

/** 16-wide icon container, 20-tall. Holds a 20×20 SVG centered, so
 *  the SVG overflows ±2px horizontally without pushing the text.
 *  This matches Claude's sidebar pattern: every icon's visual center
 *  lands at the same x regardless of SVG size (16×16 New-chat plus and
 *  20×20 nav glyphs both center on the same gridline; text starts at a
 *  fixed offset). */
export const sidebarNavIconClass = [
  "sidebar-nav-icon",
  "flex w-[16px] h-[20px] shrink-0 items-center justify-center",
  "overflow-visible text-nav-color",
  "transition-colors duration-75",
  "group-hover:text-nav-color-hover group-[.active]:text-nav-color-hover",
].join(" ");

/**
 * Class applied to the `<svg>` *inside* `sidebarNavIconClass` so
 * Heroicons-style icons get a uniform spring-out scale on hover.
 */
export const sidebarNavIconSvgClass = [
  // SVG stays 20×20 — overflows the 16-wide container by 2px on each
  // side, centered, to align with smaller icons (like the New-chat
  // 16×16 plus) on the same optical centerline.
  "size-[20px] shrink-0",
  "transition-transform duration-[220ms] ease-[cubic-bezier(0.34,1.56,0.64,1)]",
  "group-hover:scale-[1.12]",
].join(" ");

/** Text label inside a nav row. */
export const sidebarNavLabelClass = [
  "sidebar-nav-label",
  "flex-1 truncate leading-[20px]",
].join(" ");

/**
 * Trailing action icon (e.g. the refresh button on the Functions row).
 * Hidden at rest, fades in on parent hover.
 */
export const sidebarNavActionClass = [
  "sidebar-nav-action",
  "ml-auto opacity-0",
  "transition-opacity duration-150 ease-out",
  "group-hover:opacity-60",
  "hover:!opacity-100",
].join(" ");
