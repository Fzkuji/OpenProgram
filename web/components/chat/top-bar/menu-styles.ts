/**
 * Canonical Tailwind class strings for ALL popover selection menus — the
 * single source of truth so every dropdown / context menu in the app is
 * visually identical: topbar pickers (channel / branch / project /
 * agent), the sidebar Recents context menu, and any future one.
 *
 * ── Design spec (Claude-style elevated card) ─────────────────────────
 *  Panel (MENU_PANEL): 12px radius · `--surface-popover` (white in
 *    light, #30302e in dark) · 6px padding · `--shadow-popover` (which
 *    bakes in a 1px ring — no border class) · scrolls past 60vh.
 *  Row (itemCls):      min 34px tall · 8px radius · 0 10px padding ·
 *    14px/20px text · 8px icon↔label gap · hover & active = `--bg-hover`
 *    warm tint + `--text-bright`. danger = red text + faint red hover.
 *  Section label (GROUP_LABEL): 12px `--text-muted`, 8px 10px padding.
 *  Separator (MENU_SEPARATOR): 1px `--border`, 6px vertical margin,
 *    full-bleed. Key hint (SHORTCUT) · trailing check (CHECK).
 *
 *  The radix PopoverContent wrapper MUST be transparent
 *  (`border-0 bg-transparent p-0 shadow-none`) — the frame is always
 *  MENU_PANEL, never the wrapper, so every menu shares one frame.
 */

export const MENU_PANEL =
  // 10px 圆角 = 输入框同刻度（用户定的统一规则）；边缘走真 border
  // （--border-popover），shadow 只投影。竖衬 6 / 横衬 0 —— Claude
  // 实测：行通铺到面板左右边缘（Mode 菜单 pad "6px 0px"）。
  "flex max-h-[60vh] flex-col overflow-y-auto rounded-[10px] " +
  "border border-[var(--border-popover)] " +
  "bg-[var(--surface-popover)] py-[6px] shadow-(--shadow-popover)";

export const GROUP_LABEL =
  // Claude 实测：标题块整高 21px（12px 字、无上下 padding），底缘
  // 直接贴第一行——这 9px 就是"标题上下空白太多"的差值。
  "flex items-center gap-[6px] px-[12px] py-0 leading-[21px] " +
  "text-[12px] text-text-muted";

export const CHECK = "shrink-0 text-[var(--accent-blue)]";

/** Grammar-A selection check — the Claude pattern for single-select
 *  menus: the SELECTED row shows a right-aligned lucide Check (14px) in
 *  ink colour; selection is NEVER a filled/shaded row (hover is the only
 *  bg tint). Any muted metadata (shortcut digit, badge) sits right-
 *  aligned BEFORE this check. Non-selected rows render CHECK_SLOT_PAD so
 *  right-side metadata stays column-aligned across rows. */
export const CHECK_SLOT = "shrink-0 text-text-bright";
export const CHECK_SLOT_PAD = "w-[14px] shrink-0";

/** Right-aligned single-key shortcut hint (e.g. the R / P / C / A / D in
 *  the Recents context menu). Stays muted — doesn't brighten with the
 *  row on hover. */
export const SHORTCUT = "shrink-0 text-[12px] text-text-muted";

/** Full-bleed divider between menu groups — panel has zero horizontal
 *  padding now, so no negative margin needed. */
export const MENU_SEPARATOR = "my-[6px] h-px shrink-0 bg-[var(--border)]";

/** A selectable menu row — `active` swaps the resting / hover colours;
 *  `danger` makes it a destructive (red) action. Min 34px tall —
 *  Claude's generous picker rows. */
export function itemCls(active: boolean, danger = false): string {
  const base =
    // 24 / 13px / 18px / 通铺无圆角 = claude.ai/code 菜单行实测（repo
    // 与 Mode 菜单）：hover 底色从面板左缘铺到右缘。侧栏列表行仍是
    // 32（--ui-list-h）——菜单行是更紧的 Claude 刻度。
    "flex min-h-[24px] shrink-0 cursor-pointer items-center gap-[8px] " +
    "px-[12px] text-[13px] leading-[18px] transition-colors duration-75 ";
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
