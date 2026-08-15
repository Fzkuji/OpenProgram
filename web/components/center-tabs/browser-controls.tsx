"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bookmark,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Folder,
  House,
  Import,
  Library,
  MoreVertical,
  Plus,
  Settings,
  Trash2,
  ExternalLink,
} from "lucide-react";

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
} from "@/components/chat/top-bar/menu-styles";
import {
  flattenBookmarks,
  readBookmarkTree,
  subscribeBookmarks,
  type BookmarkFolder,
  type BookmarkNode,
} from "@/lib/bookmarks";
import {
  requestBrowserImport,
  setShowBookmarksBar,
  showBookmarksBar,
  subscribeBrowserPrefs,
} from "@/lib/browser-prefs";
import { desktopBridge, type DesktopContextMenuItem } from "@/lib/desktop-bridge";
import { browserResponsiveMenuItems } from "@/lib/browser-layout";
import { useTranslation } from "@/lib/i18n";
import { faviconUrl } from "@/lib/ntp-shortcuts";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

function useBookmarksBarPreference() {
  const [visible, setVisible] = useState(showBookmarksBar);
  useEffect(() => subscribeBrowserPrefs(() => setVisible(showBookmarksBar())), []);
  return visible;
}

type BrowserMenuActions = {
  home(): void;
  forward?: () => void;
  openExternal(): void;
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
    case "home":
      actions.home();
      break;
    case "forward":
      actions.forward?.();
      break;
    case "open-external":
      actions.openExternal();
      break;
    case "bookmarks":
      tabs.openBuiltinTab("bookmarks");
      break;
    case "history":
      tabs.openBuiltinTab("history");
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
  tabId,
  actions,
  canGoForward = true,
}: {
  tabId: string;
  actions: BrowserMenuActions;
  canGoForward?: boolean;
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
  const actionPrefix = `browsermenu:${tabId}:`;

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
      if (!id.startsWith(actionPrefix)) return;
      runBrowserAction(id.slice(actionPrefix.length), router, actionsRef.current);
    });
  }, [actionPrefix, mainMenu, router]);

  if (mainMenu) {
    return (
      <button
        ref={triggerRef}
        type="button"
        className={styles.webToolbarBtn}
        title={label}
        aria-label={label}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const item = (
            id: string,
            english: string,
            chinese: string,
            extra?: Pick<DesktopContextMenuItem, "separatorBefore" | "checked" | "disabled">,
          ): DesktopContextMenuItem => ({
            id: `${actionPrefix}${id}`,
            label: text(english, chinese),
            ...extra,
          });
          mainMenu.open({
            anchor: { right: rect.right, y: rect.bottom + 4, align: "end", vw: innerWidth, vh: innerHeight },
            theme: document.documentElement.dataset.theme as "light" | "dark" | undefined,
            items: [
              item("new-tab", "New browser tab", "新建浏览器标签页"),
              ...(responsive.home ? [item("home", "Home", "主页")] : []),
              ...(responsive.forward ? [item("forward", "Forward", "前进", { disabled: !canGoForward })] : []),
              ...(responsive.openExternal ? [item("open-external", "Open in browser", "在浏览器中打开")] : []),
              item("bookmarks", "Bookmarks", "书签", { separatorBefore: true }),
              item("history", "History", "历史"),
              item("toggle-bookmarks-bar", "Show bookmarks bar", "显示书签栏", { checked: visible }),
              item("import", "Import browser data", "导入浏览器资料", { separatorBefore: true, disabled: !canImport }),
              item("clear-data", "Clear browsing data", "清除浏览数据"),
              item("settings", "Browser settings", "浏览器设置", { separatorBefore: true }),
            ],
          });
        }}
      >
        <MoreVertical size={15} />
      </button>
    );
  }

  const row = (
    id: string,
    icon: React.ReactNode,
    english: string,
    chinese: string,
    checked = false,
    disabled = false,
  ) => (
    <DropdownMenuItem
      className={itemCls(false)}
      disabled={disabled}
      onSelect={() => runBrowserAction(id, router, actions)}
    >
      {checked ? <Check size={14} aria-hidden="true" /> : icon}
      <span className="flex-1">{text(english, chinese)}</span>
    </DropdownMenuItem>
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button ref={triggerRef} type="button" className={styles.webToolbarBtn} title={label} aria-label={label}>
          <MoreVertical size={15} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className={MENU_PANEL}>
        {row("new-tab", <Plus size={14} />, "New browser tab", "新建浏览器标签页")}
        {responsive.home ? row("home", <House size={14} />, "Home", "主页") : null}
        {responsive.forward ? row("forward", <ArrowRight size={14} />, "Forward", "前进", false, !canGoForward) : null}
        {responsive.openExternal ? row("open-external", <ExternalLink size={14} />, "Open in browser", "在浏览器中打开") : null}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("bookmarks", <Bookmark size={14} />, "Bookmarks", "书签")}
        {row("history", <Clock3 size={14} />, "History", "历史")}
        {row("toggle-bookmarks-bar", <span className="w-[14px]" />, "Show bookmarks bar", "显示书签栏", visible)}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {canImport ? row("import", <Import size={14} />, "Import browser data", "导入浏览器资料") : null}
        {row("clear-data", <Trash2 size={14} />, "Clear browsing data", "清除浏览数据")}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("settings", <Settings size={14} />, "Browser settings", "浏览器设置")}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function BookmarksLibraryButton() {
  const { text } = useTranslation();
  const openBuiltinTab = useCenterTabs((state) => state.openBuiltinTab);
  const label = text("Bookmarks", "书签");
  return (
    <button
      type="button"
      className={`${styles.webToolbarBtn} ${styles.webToolbarMedium}`}
      onClick={() => openBuiltinTab("bookmarks")}
      title={label}
      aria-label={label}
    >
      <Library size={14} />
    </button>
  );
}

function folderItems(folder: BookmarkFolder, ownerId: string): DesktopContextMenuItem[] {
  return flattenBookmarks(folder).map((bookmark, index) => ({
    id: `bookmarkfolder:${ownerId}:${folder.id}:${index}`,
    label: bookmark.title || bookmark.url,
  }));
}

function BookmarkFolderButton({
  folder,
  ownerId,
  onNavigate,
}: {
  folder: BookmarkFolder;
  ownerId: string;
  onNavigate(url: string): void;
}) {
  const mainMenu = desktopBridge()?.mainMenu;
  const bookmarks = flattenBookmarks(folder);

  useEffect(() => {
    if (!mainMenu) return;
    return mainMenu.onAction((id) => {
      const prefix = `bookmarkfolder:${ownerId}:${folder.id}:`;
      if (!id.startsWith(prefix)) return;
      const bookmark = bookmarks[Number(id.slice(prefix.length))];
      if (bookmark) onNavigate(bookmark.url);
    });
  }, [bookmarks, folder.id, mainMenu, onNavigate, ownerId]);

  if (mainMenu) {
    return (
      <button
        type="button"
        className={styles.bookmarkBarItem}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          mainMenu.open({
            anchor: { x: rect.left, y: rect.bottom + 2, vw: innerWidth, vh: innerHeight },
            theme: document.documentElement.dataset.theme as "light" | "dark" | undefined,
            items: folderItems(folder, ownerId).length > 0
              ? folderItems(folder, ownerId)
              : [{ id: `bookmarkfolder:${ownerId}:${folder.id}:empty`, label: "Empty folder", disabled: true }],
          });
        }}
        title={folder.title}
      >
        <Folder size={13} fill="currentColor" />
        <span>{folder.title}</span>
      </button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className={styles.bookmarkBarItem} title={folder.title}>
          <Folder size={13} fill="currentColor" />
          <span>{folder.title}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className={MENU_PANEL} align="start">
        {bookmarks.length === 0 ? (
          <DropdownMenuItem className={itemCls(false)} disabled>Empty folder</DropdownMenuItem>
        ) : bookmarks.map((bookmark) => (
          <DropdownMenuItem
            key={bookmark.url}
            className={itemCls(false)}
            onSelect={() => onNavigate(bookmark.url)}
          >
            <Bookmark size={13} />
            <span className="max-w-[280px] truncate">{bookmark.title || bookmark.url}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function BookmarkLeafButton({ node, onNavigate }: { node: Extract<BookmarkNode, { kind: "bookmark" }>; onNavigate(url: string): void }) {
  const [broken, setBroken] = useState(false);
  const icon = node.faviconUrl || faviconUrl(node.url);
  return (
    <button type="button" className={styles.bookmarkBarItem} onClick={() => onNavigate(node.url)} title={node.url}>
      {!broken && icon ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={icon} alt="" width={13} height={13} onError={() => setBroken(true)} />
      ) : <Bookmark size={13} />}
      <span>{node.title || node.url}</span>
    </button>
  );
}

export function BookmarkBar({ tabId, onNavigate }: { tabId: string; onNavigate(url: string): void }) {
  const { text } = useTranslation();
  const visible = useBookmarksBarPreference();
  const [nodes, setNodes] = useState(() => readBookmarkTree().children);
  const openBuiltinTab = useCenterTabs((state) => state.openBuiltinTab);

  useEffect(() => subscribeBookmarks(() => setNodes(readBookmarkTree().children)), []);
  if (!visible) return null;

  return (
    <div className={styles.bookmarkBar} aria-label={text("Bookmarks bar", "书签栏")}>
      <div className={styles.bookmarkBarItems}>
        {nodes.map((node) => node.kind === "folder" ? (
          <BookmarkFolderButton key={node.id} folder={node} ownerId={tabId} onNavigate={onNavigate} />
        ) : (
          <BookmarkLeafButton key={node.id} node={node} onNavigate={onNavigate} />
        ))}
      </div>
      <button
        type="button"
        className={styles.bookmarkBarMore}
        onClick={() => openBuiltinTab("bookmarks")}
        title={text("Manage bookmarks", "管理书签")}
        aria-label={text("Manage bookmarks", "管理书签")}
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
}
