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
 * Pixel values are explicit (`h-[32px]`, `gap-[12px]`, `px-[8px]`,
 * `rounded-[6px]`) because this project's `html { font-size: 14px }`
 * makes Tailwind's rem-based scale 0.875× off.
 */

/** Header button — toggle / collapse the rail. 32×32 round-corner. */
export const sidebarToggleClass = [
  // Legacy global class — used by .sidebar.collapsed CSS selectors
  // in app/styles/base.css. Without it those rules silently miss
  // the React-rendered DOM. Keep the descriptive name + the Tailwind
  // utilities side by side.
  "sidebar-toggle",
  "flex size-[32px] shrink-0 cursor-pointer items-center justify-center",
  "rounded-[6px] border-none bg-transparent p-0",
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
  // Legacy global class — `.sidebar.collapsed .sidebar-nav-item`
  // rules in base.css force a 32×32 centered square in collapsed
  // state. Without this class string the rule misses and the
  // button renders 31×32 with the icon off-centered to the right.
  "sidebar-nav-item",
  "group inline-flex h-[32px] w-full shrink-0 cursor-pointer",
  "items-center gap-[12px] rounded-[6px] px-[8px] py-[6px]",
  "text-fs-base font-normal text-nav-color no-underline",
  "transition-colors duration-150 ease-out",
  "hover:bg-bg-hover hover:text-nav-color-hover",
  "active:bg-[rgba(0,0,0,0.15)]",
].join(" ");

/** Active variant — appended to `.sidebarNavItemClass` when current route matches. */
export const sidebarNavItemActiveClass = "bg-bg-hover text-nav-color-hover";

/** 16×16 icon container. Holds a 20×20 SVG that overflows visually. */
export const sidebarNavIconClass = [
  "sidebar-nav-icon",
  "flex size-[20px] shrink-0 items-center justify-center",
  "overflow-visible text-nav-color",
  "transition-colors duration-75",
  "group-hover:text-nav-color-hover group-[.active]:text-nav-color-hover",
].join(" ");

/**
 * Class applied to the `<svg>` *inside* `sidebarNavIconClass` so
 * Heroicons-style icons get a uniform spring-out scale on hover.
 */
export const sidebarNavIconSvgClass = [
  // Container + SVG both 20x20 — 16 was too small to read at a glance.
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
