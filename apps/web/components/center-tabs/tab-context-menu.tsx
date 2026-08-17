"use client";

/**
 * In-DOM tab context menu (browser mode). The desktop shell renders the
 * same actions through its top-layer overlay instead — see `useTabMenu`.
 */
import { findCenterTabGroup, type CenterTabGroup } from "@/lib/state/center-tab-groups";
import type { CenterTab } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import type { TabMenuState } from "./use-tab-menu";
import styles from "./center-tabs.module.css";

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
      className={styles.tabMenu}
      role="menu"
      aria-label={text("Tab actions", "标签操作")}
      style={{ left: tabMenu.left, top: tabMenu.top }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={!canMoveMenuTab(tabMenu.tabId, -1)}
        onClick={() => moveMenuTab(tabMenu.tabId, -1)}
      >
        {text("Move left", "向左移动")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={!canMoveMenuTab(tabMenu.tabId, 1)}
        onClick={() => moveMenuTab(tabMenu.tabId, 1)}
      >
        {text("Move right", "向右移动")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        onClick={() => closeMenuTab(tabMenu.tabId)}
      >
        {text("Close tab", "关闭标签页")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={closableTabsAround(tabMenu.tabId, false).length === 0}
        onClick={() => closeMenuTabs(tabMenu.tabId, false)}
      >
        {text("Close other tabs", "关闭其他标签页")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={closableTabsAround(tabMenu.tabId, true).length === 0}
        onClick={() => closeMenuTabs(tabMenu.tabId, true)}
      >
        {text("Close tabs to the right", "关闭右侧标签页")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={!canOpenSplitPicker(tabMenu.tabId)}
        onClick={() => openSplitPicker(tabMenu.tabId)}
      >
        {text("New split view with this tab", "与此标签页新建分屏")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={!findCenterTabGroup(groups, tabMenu.tabId)}
        onClick={() => removeMenuTabFromGroup(tabMenu.tabId)}
      >
        {text("Remove from group", "移出分组")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={styles.tabMenuItem}
        disabled={!canMoveToNewWindow}
        onClick={() => moveMenuTabToNewWindow(tabMenu.tabId)}
      >
        {text("Move to new window", "移至新窗口")}
      </button>
    </div>
  );
}
