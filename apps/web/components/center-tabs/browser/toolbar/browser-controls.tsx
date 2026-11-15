"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Library, MoreVertical } from "lucide-react";

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
} from "@/components/ui/menu/menu-styles";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { activeThemeId } from "@/lib/prefs/appearance/theme-pref";
import {
  requestBrowserImport,
  setShowBookmarksBar,
  showBookmarksBar,
} from "@/lib/browser/browser-prefs";
import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import type { DesktopContextMenuItem } from "@/lib/desktop/desktop-bridge-types";
import {
  browserActionPrefix,
  browserResponsiveMenuItems,
  ownedActionId,
} from "@/lib/browser/browser-layout";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import styles from "../browser.module.css";
import { webToolbarButton } from "./toolbar-button";
import { ToolbarTip } from "./browser-chrome";
import { useBookmarksBarPreference } from "../bookmark-bar";

type BrowserMenuActions = {
  home(): void;
  forward?: () => void;
  openExternal(): void;
  find?: () => void;
  comment?: () => void;
  regionShot?: () => void;
  zoomIn?: () => void;
  zoomOut?: () => void;
  resetZoom?: () => void;
  print?: () => void;
  collapseToPip?: () => void;
};

function runBrowserAction(
  id: string,
  router: ReturnType<typeof useRouter>,
  actions: BrowserMenuActions,
) {
  const tabs = useCenterTabs.getState();
  switch (id) {
    case "new-tab":
      tabs.openBuiltinTab("browser");
      break;
    case "collapse-to-pip":
      actions.collapseToPip?.();
      break;
    case "home":
      actions.home();
      break;
    case "forward":
      actions.forward?.();
      break;
    case "open-external":
      actions.openExternal();
      break;
    case "find":
      actions.find?.();
      break;
    case "comment":
      actions.comment?.();
      break;
    case "region-shot":
      actions.regionShot?.();
      break;
    case "zoom-in":
      actions.zoomIn?.();
      break;
    case "zoom-out":
      actions.zoomOut?.();
      break;
    case "reset-zoom":
      actions.resetZoom?.();
      break;
    case "print":
      actions.print?.();
      break;
    case "bookmarks":
      tabs.openBuiltinTab("bookmarks");
      break;
    case "history":
      tabs.openBuiltinTab("history");
      break;
    case "downloads":
      tabs.openBuiltinTab("downloads");
      break;
    case "toggle-bookmarks-bar":
      setShowBookmarksBar(!showBookmarksBar());
      break;
    case "import":
      requestBrowserImport();
      tabs.openBuiltinTab("browser");
      break;
    case "clear-data":
      router.push("/settings/browser#clear-data");
      break;
    case "settings":
      router.push("/settings/browser");
      break;
  }
}

export function BrowserMenu({
  ownerId,
  actions,
  canGoForward = true,
  canGoHome = true,
  canOpenExternal = true,
}: {
  ownerId: string;
  actions: BrowserMenuActions;
  canGoForward?: boolean;
  canGoHome?: boolean;
  canOpenExternal?: boolean;
}) {
  const router = useRouter();
  const { text } = useTranslation();
  const visible = useBookmarksBarPreference();
  const bridge = desktopBridge();
  const mainMenu = bridge?.mainMenu;
  const canImport = Boolean(bridge?.browserImport);
  const label = text("Browser menu", "浏览器菜单");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  const [paneWidth, setPaneWidth] = useState(Number.POSITIVE_INFINITY);
  const responsive = browserResponsiveMenuItems(paneWidth, {
    forward: Boolean(actions.forward),
  });
  const actionPrefix = browserActionPrefix(ownerId);

  useEffect(() => {
    const pane = triggerRef.current?.closest(`.${styles.webPane}`);
    if (!pane) return;
    const update = () => setPaneWidth(pane.getBoundingClientRect().width);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(pane);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!mainMenu) return;
    return mainMenu.onAction((id) => {
      const action = ownedActionId(id, actionPrefix);
      if (action === null) return;
      runBrowserAction(action, router, actionsRef.current);
    });
  }, [actionPrefix, mainMenu, router]);

  if (mainMenu) {
    return (
      <ToolbarTip label={label}>
      <button
        ref={triggerRef}
        type="button"
        className={webToolbarButton()}
        aria-label={label}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const item = (
            id: string,
            glyph: MenuGlyph,
            english: string,
            chinese: string,
            extra?: Pick<DesktopContextMenuItem, "separatorBefore" | "checked" | "disabled">,
          ): DesktopContextMenuItem => ({
            id: `${actionPrefix}${id}`,
            glyph,
            label: text(english, chinese),
            ...extra,
          });
          mainMenu.open({
            anchor: { right: rect.right, y: rect.bottom + 4, align: "end", vw: innerWidth, vh: innerHeight },
            theme: activeThemeId(),
            items: [
              item("new-tab", "add-square", "New browser tab", "新建浏览器标签页"),
              ...(actions.collapseToPip
                ? [item("collapse-to-pip", "pip-2", "Collapse to floating window", "收起到悬浮窗")]
                : []),
              ...(responsive.home ? [item("home", "home-2", "Home", "主页", { disabled: !canGoHome })] : []),
              ...(responsive.forward ? [item("forward", "arrow-right", "Forward", "前进", { disabled: !canGoForward })] : []),
              ...(responsive.openExternal ? [item("open-external", "square-arrow-right-up", "Open in browser", "在浏览器中打开", { disabled: !canOpenExternal })] : []),
              item("find", "magnifier", "Find in page", "在页面中查找", { separatorBefore: true, disabled: !actions.find }),
              ...(actions.comment ? [item("comment", "chat-round-dots", "Comment on the page…", "评论页面元素…")] : []),
              ...(actions.regionShot ? [item("region-shot", "crop", "Screenshot a region…", "区域截图…")] : []),
              item("zoom-in", "magnifer-zoom-in", "Zoom in", "放大", { disabled: !actions.zoomIn }),
              item("zoom-out", "magnifer-zoom-out", "Zoom out", "缩小", { disabled: !actions.zoomOut }),
              item("reset-zoom", "restart", "Reset zoom", "重置缩放", { disabled: !actions.resetZoom }),
              item("print", "printer", "Print", "打印", { disabled: !actions.print }),
              item("bookmarks", "bookmark", "Bookmarks", "书签", { separatorBefore: true }),
              item("history", "history", "History", "历史"),
              item("downloads", "download-minimalistic", "Downloads", "下载内容"),
              item("toggle-bookmarks-bar", "bookmark-square", "Show bookmarks bar", "显示书签栏", { checked: visible }),
              item("import", "import", "Import browser data", "导入浏览器资料", { separatorBefore: true, disabled: !canImport }),
              item("clear-data", "trash-bin-minimalistic", "Clear browsing data", "清除浏览数据"),
              item("settings", "settings", "Browser settings", "浏览器设置", { separatorBefore: true }),
            ],
          });
        }}
      >
        <MoreVertical size={16} />
      </button>
      </ToolbarTip>
    );
  }

  const row = (
    id: string,
    glyph: MenuGlyph,
    english: string,
    chinese: string,
    checked?: boolean,
    disabled = false,
  ) => (
    <DropdownMenuItem
      className={itemCls(false)}
      disabled={disabled}
      onSelect={() => runBrowserAction(id, router, actions)}
    >
      <MenuRow glyph={glyph} label={text(english, chinese)} checked={checked} />
    </DropdownMenuItem>
  );

  return (
    <DropdownMenu>
      <ToolbarTip label={label}>
        <DropdownMenuTrigger asChild>
          <button ref={triggerRef} type="button" className={webToolbarButton()} aria-label={label}>
            <MoreVertical size={16} />
          </button>
        </DropdownMenuTrigger>
      </ToolbarTip>
      <DropdownMenuContent className={MENU_PANEL}>
        {row("new-tab", "add-square", "New browser tab", "新建浏览器标签页")}
        {actions.collapseToPip
          ? row("collapse-to-pip", "pip-2", "Collapse to floating window", "收起到悬浮窗")
          : null}
        {responsive.home ? row("home", "home-2", "Home", "主页", undefined, !canGoHome) : null}
        {responsive.forward ? row("forward", "arrow-right", "Forward", "前进", undefined, !canGoForward) : null}
        {responsive.openExternal ? row("open-external", "square-arrow-right-up", "Open in browser", "在浏览器中打开", undefined, !canOpenExternal) : null}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("find", "magnifier", "Find in page", "在页面中查找", undefined, !actions.find)}
        {actions.comment ? row("comment", "chat-round-dots", "Comment on the page…", "评论页面元素…") : null}
        {actions.regionShot ? row("region-shot", "crop", "Screenshot a region…", "区域截图…") : null}
        {row("zoom-in", "magnifer-zoom-in", "Zoom in", "放大", undefined, !actions.zoomIn)}
        {row("zoom-out", "magnifer-zoom-out", "Zoom out", "缩小", undefined, !actions.zoomOut)}
        {row("reset-zoom", "restart", "Reset zoom", "重置缩放", undefined, !actions.resetZoom)}
        {row("print", "printer", "Print", "打印", undefined, !actions.print)}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("bookmarks", "bookmark", "Bookmarks", "书签")}
        {row("history", "history", "History", "历史")}
        {row("downloads", "download-minimalistic", "Downloads", "下载内容")}
        {row("toggle-bookmarks-bar", "bookmark-square", "Show bookmarks bar", "显示书签栏", visible)}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {canImport ? row("import", "import", "Import browser data", "导入浏览器资料") : null}
        {row("clear-data", "trash-bin-minimalistic", "Clear browsing data", "清除浏览数据")}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("settings", "settings", "Browser settings", "浏览器设置")}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function BookmarksLibraryButton() {
  const { text } = useTranslation();
  const openBuiltinTab = useCenterTabs((state) => state.openBuiltinTab);
  const label = text("Bookmarks", "书签");
  return (
    <ToolbarTip label={label}>
      <button
        type="button"
        className={webToolbarButton(styles.webToolbarMedium)}
        onClick={() => openBuiltinTab("bookmarks")}
        aria-label={label}
      >
        <Library size={14} />
      </button>
    </ToolbarTip>
  );
}
