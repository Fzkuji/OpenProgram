/**
 * Canonical Tailwind class strings for ALL popover selection menus — the
 * single source of truth so every dropdown / context menu / Select in the
 * app is visually identical: topbar pickers (channel / branch / project /
 * agent), the sidebar Recents context menu, ui/select, and any future one.
 *
 * ── Design spec (shadcn radix-nova DropdownMenu geometry) ───────────
 *  Values are Nova's own, the style ui.shadcn.com's docs use, on the
 *  app's tiers (styles/design/): 14px rows text, a 28px row.
 *  Panel (MENU_PANEL): rounded-lg · p-1 · width hugs content,
 *    min-w-32 up to 320px · scrolls past 60vh. Material is Nova's own:
 *    solid bg-popover, ring-1 + shadow from the --popover-ring /
 *    --popover-shadow / --popover-shadow-hover tokens (the files card's
 *    frame in light themes: no hairline, its shadow deepening under the
 *    pointer; lifted in dark ones, app/globals.css) — the same surface
 *    as the Select popup.
 *  Row (itemCls): min-h-(--ui-button-h-sm) (28px) · rounded-md · px-1.5 py-1 · text-fs-base ·
 *    gap-1.5 · icons size-4 ·
 *    hover = `--accent` fill (the ink at 9%), the ONLY row tint (a
 *    selected row shows a right-aligned accent check, never a fill).
 *    danger = red text + faint red hover. Radix items add
 *    MENU_ITEM_STATES so keyboard highlight and the disabled look
 *    follow the same rule.
 *  Two-line row (ITEM_TWO_LINE): title over a text-fs-xs description.
 *  Section label (GROUP_LABEL): px-1.5 py-1 · text-fs-xs font-medium ·
 *    `--text-muted`.
 *  Note (MENU_NOTE): a sentence under a label or below the rows (a
 *    fixed-choice explanation, an empty state, an error) · px-1.5 pb-1 ·
 *    text-fs-xs · `--text-muted` · wraps within max-w-64, so its text lines
 *    up with the label's. An error swaps only the colour.
 *  Separator (MENU_SEPARATOR): full-bleed (-mx-1) · my-1 · 1px
 *    `--border`. Trailing column (MENU_TRAILING): after the
 *    content, check then key hint (SHORTCUT, text-fs-xs tracking-widest) ·
 *    check (MenuCheck, accent) with CHECK_SLOT_PAD reserving the column
 *    on unselected rows.
 *  A lit row (hover, keyboard highlight, open submenu, active) lights
 *    its parts with it: the muted icon, key badge and chevron turn
 *    bright and the icon pops to 1.12 like the composer's icons
 *    (`.menu-row` rules in app/styles/chat/composer/top-bar-chips.css).
 *
 *  The radix PopoverContent wrapper MUST be transparent
 *  (`border-0 bg-transparent p-0 shadow-none`) — the frame is always
 *  MENU_PANEL, never the wrapper, so every menu shares one frame.
 *  DropdownMenuContent carries no frame of its own, so MENU_PANEL goes
 *  straight on it.
 */

/** The panel material alone (fill, ring, shadow): floating surfaces that are
 *  not menus, such as the recording bar, wear it so they share the frame. */
export const POPOVER_SURFACE =
  "bg-popover text-popover-foreground shadow-(--popover-shadow) transition-shadow hover:shadow-(--popover-shadow-hover) ring-1 ring-(color:--popover-ring)";

export const MENU_PANEL =
  // 只有内容本身需要固定宽度的菜单（git 的分支列表 + 搜索框）才自带 w-[…]。
  "flex max-h-[60vh] min-w-32 max-w-[320px] flex-col overflow-y-auto rounded-lg p-1 " + POPOVER_SURFACE;

export const GROUP_LABEL =
  "flex items-center gap-1.5 px-1.5 py-1 text-fs-xs font-medium text-text-muted";

export const MENU_NOTE =
  "max-w-64 px-1.5 pb-1 text-fs-xs whitespace-normal text-text-muted";

/** MENU_NOTE in the warning colour, for a menu's error line. */
export const MENU_NOTE_ERROR = "text-[var(--accent-orange)]";

export const CHECK = "shrink-0 text-[var(--accent-blue)]";

/** Grammar-A selection check — the pattern for single-select menus:
 *  the SELECTED row shows a right-aligned Check (MenuCheck) in the
 *  accent; selection is NEVER a filled/shaded row (hover is the only
 *  bg tint). Any muted metadata (shortcut digit, badge) sits right-
 *  aligned BEFORE this check. Non-selected rows render CHECK_SLOT_PAD so
 *  right-side metadata stays column-aligned across rows. */
export const CHECK_SLOT = "shrink-0 text-[var(--accent-blue)]";
export const CHECK_SLOT_PAD = "size-4 shrink-0";

/** Right-aligned single-key shortcut hint. Stays muted. */
export const SHORTCUT = "shrink-0 text-fs-xs tracking-widest text-text-muted";
/** A row's key as a price tag (MenuKey): a menu-local letter (R) or a
 *  system shortcut (⌘T / Ctrl+T). The tag is a fixed 10px tip — the
 *  right end of Solar's tag-horizontal outline, mirrored — joined to the
 *  shadcn Kbd as its plate. Both paint --menu-key-bg (set with the lit
 *  state in app/styles/chat/composer/top-bar-chips.css), so one variable colours
 *  the whole tag. */
export const MENU_KEY =
  "menu-row-key inline-flex h-[18px] shrink-0 items-center";
/** The tip: 18px tall at the outline's own proportions; it overlaps the
 *  plate by half a pixel so no seam shows when scaled. */
export const MENU_KEY_TIP = "-mr-[0.5px] block h-[18px] w-[10.3px] -scale-x-100 fill-(--menu-key-bg)";
/** Kbd overrides for the plate: square where it meets the tip, 5px round
 *  on the right, 1px / 6px padding around at least 8px of text. */
export const MENU_KEY_PLATE =
  "box-content h-[18px] min-w-[8px] rounded-none rounded-r-xs bg-(--menu-key-bg) py-0 pr-[6px] pl-[1px] text-fs-2xs leading-none";

/** Right column of a row (check, then key hint) after the content, one
 *  title line tall so on a two-line row it sits on the title. */
export const MENU_TRAILING = "ms-3 flex h-5 shrink-0 items-center gap-1.5";

/** Divider between menu groups, full-bleed like Nova's. */
export const MENU_SEPARATOR = "-mx-1 my-1 h-px shrink-0 bg-[var(--border)]";

/** Two-line row — a title with a description under it — added to
 *  itemCls; the hover fill is still the row's own rounded rectangle and
 *  its MENU_TRAILING column sits on the title. */
export const ITEM_TWO_LINE = "items-start pb-2";
export const ITEM_TITLE = "block truncate text-text-bright";
export const ITEM_DESC =
  "block whitespace-normal text-fs-xs text-text-muted";

/** Inline metadata tag after a row's title ("Recommended", a channel
 *  account alias, git's "current" / "worktree") — the `.menu-tag` class
 *  in app/styles/chat/composer/top-bar-chips.css: Claude's xxs neutral badge,
 *  16px tall, 0 4px padding, 4px radius, 10px/500 secondary text on a
 *  5% foreground mix. One tag for every menu. */
export const MENU_TAG = "menu-tag";

/** The states a radix DropdownMenu / ContextMenu row exposes as data
 *  attributes, mapped onto the itemCls hover rule: keyboard highlight
 *  (radix sets `data-highlighted` on the focused item) and an open
 *  sub-trigger tint the same as hover; a disabled row dims, keeps
 *  pointer events so its `title` can still explain why, and shows the
 *  arrow like every disabled control (globals.css cursor rule). */
export const MENU_ITEM_STATES =
  "select-none outline-none " +
  "data-[highlighted]:bg-accent data-[highlighted]:text-text-bright " +
  "data-[state=open]:bg-accent data-[state=open]:text-text-bright " +
  "data-[disabled]:cursor-default data-[disabled]:opacity-50";

/** A selectable menu row — `active` swaps the resting / hover colours;
 *  `danger` makes it a destructive (red) action. */
export function itemCls(active: boolean, danger = false): string {
  const base =
    // Nova 的行：min-h-(--ui-button-h-sm)、rounded-md、px-1.5 py-1、text-sm、gap-1.5，行首
    // 图标 size-4（纯 svg、只包一个 svg 的 SolarIcon、或只包一个 SolarIcon
    // 的槽），不再左移——图标和文字一样留官方的 6px 行内边距。
    "menu-row flex min-h-(--ui-button-h-sm) shrink-0 cursor-pointer items-center gap-1.5 rounded-md " +
    "px-1.5 py-1 text-fs-base transition-colors duration-75 " +
    "[&>svg:first-child]:size-4 [&>:first-child:has(>svg:only-child)>svg]:size-4 " +
    "[&>:first-child:has(>:only-child>svg:only-child)>*>svg]:size-4 ";
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
      ? "menu-row-active bg-accent text-text-bright"
      : "text-text-primary hover:bg-accent hover:text-text-bright")
  );
}
