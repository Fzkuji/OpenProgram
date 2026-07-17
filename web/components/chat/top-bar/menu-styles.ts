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
  "flex max-h-[60vh] flex-col overflow-y-auto rounded-[12px] " +
  "bg-[var(--surface-popover)] p-[6px] shadow-(--shadow-popover)";

export const GROUP_LABEL =
  "flex items-center gap-[6px] px-[10px] py-[8px] " +
  "text-[12px] text-text-muted";

export const CHECK = "shrink-0 text-[var(--accent-blue)]";

/** Right-aligned single-key shortcut hint (e.g. the R / P / C / A / D in
 *  the Recents context menu). Stays muted — doesn't brighten with the
 *  row on hover. */
export const SHORTCUT = "shrink-0 text-[12px] text-text-muted";

/** Full-bleed divider between menu groups. The negative inline margin
 *  cancels MENU_PANEL's 6px padding so the line spans edge to edge. */
export const MENU_SEPARATOR = "-mx-[6px] my-[6px] h-px shrink-0 bg-[var(--border)]";

/** A selectable menu row — `active` swaps the resting / hover colours;
 *  `danger` makes it a destructive (red) action. Min 34px tall —
 *  Claude's generous picker rows. */
export function itemCls(active: boolean, danger = false): string {
  const base =
    // 32 = --ui-list-h：菜单项与侧栏行同一刻度（全局高度刻度表：
    // 20 控件 / 24 图标钮 / 28 tab·二级行 / 32 列表行·菜单项 / 40 工具栏 / 44 输入行）
    "flex min-h-[32px] shrink-0 cursor-pointer items-center gap-[8px] rounded-[8px] " +
    "px-[10px] text-[14px] leading-[20px] transition-colors duration-75 ";
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
