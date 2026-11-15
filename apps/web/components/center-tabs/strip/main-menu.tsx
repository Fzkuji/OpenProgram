"use client";

/**
 * MainMenu — the Chrome ⋮ at the right end of the tab strip. One flat
 * level, no submenus: tab/window creation and app settings. Browser-only
 * history/bookmark actions live in the Browser menu inside web tabs.
 *
 * Radix supplies keyboard roving, typeahead, Esc and outside-click, so
 * this file owns no listeners. The look is entirely menu-styles.ts —
 * the same MENU_PANEL / itemCls every other menu in the app uses.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { MoreVertical } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  itemCls,
  MENU_ITEM_STATES,
  MENU_PANEL,
  MENU_SEPARATOR,
} from "@/components/ui/menu/menu-styles";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import { activeThemeId } from "@/lib/prefs/appearance/theme-pref";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./tab-strip.module.css";

export function MainMenu() {
  const router = useRouter();
  const { text } = useTranslation();
  const openNewTabPage = useCenterTabs((s) => s.openNewTabPage);
  // ponytail: the desktop shell has no "blank new window" IPC — only
  // detach-a-tab-into-one. Rather than ship a dead row there, the entry
  // is browser-only, where window.open is the native answer. Add an
  // Electron row once main.js exposes a create-window channel.
  const canOpenWindow = !desktopBridge();
  const label = text("Main menu", "主菜单");

  // Desktop shell: the menu is a top-layer WebContentsView (covers native
  // web tabs a DOM menu can't). Same actions as the Radix rows below,
  // routed back through onAction.
  const mainMenu = desktopBridge()?.mainMenu;
  useEffect(() => {
    if (!mainMenu) return;
    return mainMenu.onAction((id) => {
      switch (id) {
        case "new-tab":
          openNewTabPage();
          break;
        case "settings":
          router.push("/settings/general");
          break;
      }
    });
  }, [mainMenu, openNewTabPage, router]);

  if (mainMenu) {
    return (
      <HoverTip label={label} side="bottom">
      <button
        type="button"
        className={styles.menuBtn}
        aria-label={label}
        onClick={(e) => {
          const trigger = e.currentTarget.getBoundingClientRect();
          mainMenu.open({
            anchor: {
              rightInset: Math.round(window.innerWidth - trigger.right),
              top: Math.round(trigger.bottom + 4),
              vw: window.innerWidth,
            },
            theme: activeThemeId(),
          });
        }}
      >
        <MoreVertical size={16} />
      </button>
      </HoverTip>
    );
  }

  return (
    <DropdownMenu>
      <HoverTip label={label} side="bottom">
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={styles.menuBtn}
            aria-label={label}
          >
            <MoreVertical size={16} />
          </button>
        </DropdownMenuTrigger>
      </HoverTip>
      <DropdownMenuContent className={MENU_PANEL}>
        <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => openNewTabPage()}>
          <MenuRow glyph="add-square" label={text("New tab", "新标签页")} keys={{ key: "T", mod: true }} />
        </DropdownMenuItem>
        {canOpenWindow ? (
          <DropdownMenuItem
            className={cn(itemCls(false), MENU_ITEM_STATES)}
            onSelect={() => window.open(window.location.origin, "_blank")}
          >
            <MenuRow glyph="widget-add" label={text("New window", "新窗口")} keys={{ key: "N", mod: true }} />
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        <DropdownMenuItem
          className={cn(itemCls(false), MENU_ITEM_STATES)}
          onSelect={() => router.push("/settings/general")}
        >
          <MenuRow glyph="settings" label={text("Settings", "设置")} keys={{ key: ",", mod: true }} />
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
