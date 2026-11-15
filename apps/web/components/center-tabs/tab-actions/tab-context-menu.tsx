"use client";

/**
 * In-DOM tab context menu (browser mode). The desktop shell renders the
 * same actions through its top-layer overlay instead — see `useTabMenu`.
 */
import { findCenterTabGroup, type CenterTabGroup } from "@/lib/tabs/center-tab-groups";
import type { CenterTab } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import type { TabMenuState } from "./use-tab-menu";
import { cn } from "@/lib/utils";
import { MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import styles from "../strip/tab-strip.module.css";

export interface TabContextMenuProps {
  tabMenu: TabMenuState;
  groups: CenterTabGroup[];
  canMoveToNewWindow: boolean;
  canMoveMenuTab(tabId: string, direction: -1 | 1): boolean;
  moveMenuTab(tabId: string, direction: -1 | 1): void;
  closeMenuTab(tabId: string): void;
  closableTabsAround(tabId: string, after: boolean): CenterTab[];
  closeMenuTabs(tabId: string, after: boolean): void;
  canOpenSplitPicker(tabId: string): boolean;
  openSplitPicker(tabId: string): void;
  removeMenuTabFromGroup(tabId: string): void;
  moveMenuTabToNewWindow(tabId: string): void;
}

export function TabContextMenu({
  tabMenu,
  groups,
  canMoveToNewWindow,
  canMoveMenuTab,
  moveMenuTab,
  closeMenuTab,
  closableTabsAround,
  closeMenuTabs,
  canOpenSplitPicker,
  openSplitPicker,
  removeMenuTabFromGroup,
  moveMenuTabToNewWindow,
}: TabContextMenuProps) {
  const { text } = useTranslation();
  return (
    <div
      className={cn(MENU_PANEL, styles.tabMenu)}
      role="menu"
      aria-label={text("Tab actions", "标签操作")}
      style={{ left: tabMenu.left, top: tabMenu.top }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={!canMoveMenuTab(tabMenu.tabId, -1)}
        onClick={() => moveMenuTab(tabMenu.tabId, -1)}
      >
        <MenuRow glyph="square-arrow-left" label={text("Move left", "向左移动")} />
      </button>
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={!canMoveMenuTab(tabMenu.tabId, 1)}
        onClick={() => moveMenuTab(tabMenu.tabId, 1)}
      >
        <MenuRow glyph="square-arrow-right" label={text("Move right", "向右移动")} />
      </button>
      <div className={MENU_SEPARATOR} role="separator" />
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        onClick={() => closeMenuTab(tabMenu.tabId)}
      >
        <MenuRow glyph="close-square" label={text("Close tab", "关闭标签页")} keys={{ key: "W", mod: true }} />
      </button>
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={closableTabsAround(tabMenu.tabId, false).length === 0}
        onClick={() => closeMenuTabs(tabMenu.tabId, false)}
      >
        <MenuRow glyph="close-circle" label={text("Close other tabs", "关闭其他标签页")} />
      </button>
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={closableTabsAround(tabMenu.tabId, true).length === 0}
        onClick={() => closeMenuTabs(tabMenu.tabId, true)}
      >
        <MenuRow glyph="arrow-right" label={text("Close tabs to the right", "关闭右侧标签页")} />
      </button>
      <div className={MENU_SEPARATOR} role="separator" />
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={!canOpenSplitPicker(tabMenu.tabId)}
        onClick={() => openSplitPicker(tabMenu.tabId)}
      >
        <MenuRow glyph="widget-2" label={text("New split view with this tab", "与此标签页新建分屏")} />
      </button>
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={!findCenterTabGroup(groups, tabMenu.tabId)}
        onClick={() => removeMenuTabFromGroup(tabMenu.tabId)}
      >
        <MenuRow glyph="exit" label={text("Remove from group", "移出分组")} />
      </button>
      <button
        type="button"
        role="menuitem"
        className={cn(itemCls(false), styles.tabMenuItem)}
        disabled={!canMoveToNewWindow}
        onClick={() => moveMenuTabToNewWindow(tabMenu.tabId)}
      >
        <MenuRow glyph="window-frame" label={text("Move to new window", "移至新窗口")} />
      </button>
    </div>
  );
}
