"use client";

/**
 * MainMenu — the Chrome ⋮ at the right end of the tab strip. One flat
 * level, no submenus: tab/window creation, the two built-in library
 * pages (bookmarks / history, which open as center tabs), and settings.
 *
 * Radix supplies keyboard roving, typeahead, Esc and outside-click, so
 * this file owns no listeners. The look is entirely menu-styles.ts —
 * the same MENU_PANEL / itemCls every other menu in the app uses.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Bookmark, History, MoreVertical, Plus, Settings, SquarePlus } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  itemCls,
  MENU_PANEL,
  MENU_SEPARATOR,
  SHORTCUT,
} from "@/components/chat/top-bar/menu-styles";
import { desktopBridge } from "@/lib/desktop-bridge";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";

export function MainMenu() {
  const router = useRouter();
  const { text } = useTranslation();
  const openNewTabPage = useCenterTabs((s) => s.openNewTabPage);
  const openBuiltinTab = useCenterTabs((s) => s.openBuiltinTab);
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
        case "bookmarks":
          openBuiltinTab("bookmarks");
          break;
        case "history":
          openBuiltinTab("history");
          break;
        case "settings":
          router.push("/settings");
          break;
      }
    });
  }, [mainMenu, openNewTabPage, openBuiltinTab, router]);

  if (mainMenu) {
    return (
      <button
        type="button"
        className={styles.menuBtn}
        title={label}
        aria-label={label}
        onClick={(e) => {
          // Anchor: panel right edge sits 8px from the window's right (the
          // same gutter the tab-strip buttons keep), and its top edge sits on
          // the tab-strip's bottom divider so it covers the content below.
          const strip = e.currentTarget.closest<HTMLElement>(
            `.${styles.strip}`,
          );
          const dividerY = strip
            ? Math.round(strip.getBoundingClientRect().bottom)
            : Math.round(e.currentTarget.getBoundingClientRect().bottom);
          const theme = document.documentElement.dataset.theme;
          mainMenu.open({
            anchor: { rightInset: 8, top: dividerY, vw: window.innerWidth },
            theme: theme === "dark" || theme === "light" ? theme : undefined,
          });
        }}
      >
        <MoreVertical size={16} />
      </button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={styles.menuBtn}
          title={label}
          aria-label={label}
        >
          <MoreVertical size={16} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className={MENU_PANEL}>
        <DropdownMenuItem className={itemCls(false)} onSelect={() => openNewTabPage()}>
          <Plus size={14} aria-hidden="true" />
          <span className="flex-1">{text("New tab", "新标签页")}</span>
          <span className={SHORTCUT}>⌘T</span>
        </DropdownMenuItem>
        {canOpenWindow ? (
          <DropdownMenuItem
            className={itemCls(false)}
            onSelect={() => window.open(window.location.origin, "_blank")}
          >
            <SquarePlus size={14} aria-hidden="true" />
            <span className="flex-1">{text("New window", "新窗口")}</span>
            <span className={SHORTCUT}>⌘N</span>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        <DropdownMenuItem
          className={itemCls(false)}
          onSelect={() => openBuiltinTab("bookmarks")}
        >
          <Bookmark size={14} aria-hidden="true" />
          <span className="flex-1">{text("Bookmarks", "书签")}</span>
        </DropdownMenuItem>
        <DropdownMenuItem
          className={itemCls(false)}
          onSelect={() => openBuiltinTab("history")}
        >
          <History size={14} aria-hidden="true" />
          <span className="flex-1">{text("Web history", "网页历史")}</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        <DropdownMenuItem
          className={itemCls(false)}
          onSelect={() => router.push("/settings")}
        >
          <Settings size={14} aria-hidden="true" />
          <span className="flex-1">{text("Settings", "设置")}</span>
          <span className={SHORTCUT}>⌘,</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
