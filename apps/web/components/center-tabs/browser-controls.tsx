"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bookmark,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Download,
  Folder,
  House,
  Import,
  Library,
  MoreVertical,
  PictureInPicture2,
  Plus,
  Printer,
  RotateCcw,
  Search,
  Settings,
  Trash2,
  ZoomIn,
  ZoomOut,
  ExternalLink,
} from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  itemCls,
  MENU_PANEL,
  MENU_SEPARATOR,
} from "@/components/chat/top-bar/menu-styles";
import {
  bookmarkBarLayout,
  readBookmarkTree,
  subscribeBookmarks,
  type BookmarkFolder,
  type BookmarkNode,
} from "@/lib/bookmarks";
import { activeThemeId } from "@/lib/prefs/theme-pref";
import {
  requestBrowserImport,
  setShowBookmarksBar,
  showBookmarksBar,
  subscribeBrowserPrefs,
} from "@/lib/browser-prefs";
import { desktopBridge } from "@/lib/desktop-bridge";
import type { DesktopContextMenuItem } from "@/lib/desktop-bridge-types";
import {
  bookmarkFolderActionPrefix,
  browserActionPrefix,
  browserResponsiveMenuItems,
  ownedActionId,
} from "@/lib/browser-layout";
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
  find?: () => void;
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
            theme: activeThemeId(),
            items: [
              item("new-tab", "New browser tab", "新建浏览器标签页"),
              ...(actions.collapseToPip
                ? [item("collapse-to-pip", "Collapse to floating window", "收起到悬浮窗")]
                : []),
              ...(responsive.home ? [item("home", "Home", "主页", { disabled: !canGoHome })] : []),
              ...(responsive.forward ? [item("forward", "Forward", "前进", { disabled: !canGoForward })] : []),
              ...(responsive.openExternal ? [item("open-external", "Open in browser", "在浏览器中打开", { disabled: !canOpenExternal })] : []),
              item("find", "Find in page", "在页面中查找", { separatorBefore: true, disabled: !actions.find }),
              item("zoom-in", "Zoom in", "放大", { disabled: !actions.zoomIn }),
              item("zoom-out", "Zoom out", "缩小", { disabled: !actions.zoomOut }),
              item("reset-zoom", "Reset zoom", "重置缩放", { disabled: !actions.resetZoom }),
              item("print", "Print", "打印", { disabled: !actions.print }),
              item("bookmarks", "Bookmarks", "书签", { separatorBefore: true }),
              item("history", "History", "历史"),
              item("downloads", "Downloads", "下载内容"),
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
        {actions.collapseToPip
          ? row("collapse-to-pip", <PictureInPicture2 size={14} />, "Collapse to floating window", "收起到悬浮窗")
          : null}
        {responsive.home ? row("home", <House size={14} />, "Home", "主页", false, !canGoHome) : null}
        {responsive.forward ? row("forward", <ArrowRight size={14} />, "Forward", "前进", false, !canGoForward) : null}
        {responsive.openExternal ? row("open-external", <ExternalLink size={14} />, "Open in browser", "在浏览器中打开", false, !canOpenExternal) : null}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("find", <Search size={14} />, "Find in page", "在页面中查找", false, !actions.find)}
        {row("zoom-in", <ZoomIn size={14} />, "Zoom in", "放大", false, !actions.zoomIn)}
        {row("zoom-out", <ZoomOut size={14} />, "Zoom out", "缩小", false, !actions.zoomOut)}
        {row("reset-zoom", <RotateCcw size={14} />, "Reset zoom", "重置缩放", false, !actions.resetZoom)}
        {row("print", <Printer size={14} />, "Print", "打印", false, !actions.print)}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        {row("bookmarks", <Bookmark size={14} />, "Bookmarks", "书签")}
        {row("history", <Clock3 size={14} />, "History", "历史")}
        {row("downloads", <Download size={14} />, "Downloads", "下载内容")}
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

function folderItems(
  folder: BookmarkFolder,
  ownerId: string,
  rootFolderId = folder.id,
): DesktopContextMenuItem[] {
  const prefix = bookmarkFolderActionPrefix(ownerId, rootFolderId);
  return folder.children.map((node) => node.kind === "folder" ? {
    id: `${prefix}folder:${node.id}`,
    label: node.title || "Folder",
    icon: "folder",
    children: node.children.length > 0
      ? folderItems(node, ownerId, rootFolderId)
      : [{ id: `${prefix}empty:${node.id}`, label: "Empty folder", disabled: true }],
  } : {
    id: `${prefix}bookmark:${node.id}`,
    label: node.title || node.url,
    iconUrl: node.faviconUrl || faviconUrl(node.url),
  });
}

function bookmarkUrlById(folder: BookmarkFolder, id: string): string | null {
  for (const node of folder.children) {
    if (node.kind === "bookmark" && node.id === id) return node.url;
    if (node.kind === "folder") {
      const nested = bookmarkUrlById(node, id);
      if (nested) return nested;
    }
  }
  return null;
}

function BookmarkMenuNodes({
  nodes,
  onNavigate,
}: {
  nodes: BookmarkNode[];
  onNavigate(url: string): void;
}) {
  if (nodes.length === 0) {
    return <DropdownMenuItem className={itemCls(false)} disabled>Empty folder</DropdownMenuItem>;
  }
  return nodes.map((node) => node.kind === "folder" ? (
    <DropdownMenuSub key={node.id}>
      <DropdownMenuSubTrigger
        className={`${itemCls(false)} w-full min-w-0 outline-none data-[highlighted]:bg-bg-hover data-[highlighted]:text-text-bright data-[state=open]:bg-bg-hover data-[state=open]:text-text-bright`}
      >
        <Folder size={13} fill="currentColor" className="shrink-0" />
        <span className="min-w-0 flex-1 truncate text-left">{node.title || "Folder"}</span>
        <ChevronRight size={13} className="ml-auto shrink-0" aria-hidden="true" />
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className={`${MENU_PANEL} w-[280px] max-w-[calc(100vw-16px)]`}>
        <BookmarkMenuNodes nodes={node.children} onNavigate={onNavigate} />
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  ) : (
    <DropdownMenuItem
      key={node.id}
      className={`${itemCls(false)} w-full min-w-0 outline-none data-[highlighted]:bg-bg-hover data-[highlighted]:text-text-bright`}
      onSelect={() => onNavigate(node.url)}
      title={node.title || node.url}
    >
      <BookmarkFavicon node={node} />
      <span className="min-w-0 flex-1 truncate">{node.title || node.url}</span>
    </DropdownMenuItem>
  ));
}

function BookmarkFavicon({ node }: { node: Extract<BookmarkNode, { kind: "bookmark" }> }) {
  const [broken, setBroken] = useState(false);
  const icon = node.faviconUrl || faviconUrl(node.url);
  return !broken && icon ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={icon} alt="" width={14} height={14} className="shrink-0" onError={() => setBroken(true)} />
  ) : <Bookmark size={14} className="shrink-0" />;
}

function BookmarkFolderButton({
  folder,
  ownerId,
  onNavigate,
  appearance = "folder",
  label,
  hidden = false,
  open,
  armed,
  wasJustClosed,
  onArm,
  onDisarm,
}: {
  folder: BookmarkFolder;
  ownerId: string;
  onNavigate(url: string): void;
  appearance?: "folder" | "overflow";
  label?: string;
  hidden?: boolean;
  open: boolean;
  armed: boolean;
  wasJustClosed(): boolean;
  onArm(): void;
  onDisarm(): void;
}) {
  const mainMenu = desktopBridge()?.mainMenu;
  const buttonLabel = label || folder.title;
  const buttonClass = `${appearance === "overflow" ? styles.bookmarkBarMore : styles.bookmarkBarItem}${hidden ? ` ${styles.bookmarkBarOverflowed}` : ""}`;
  const buttonContent = appearance === "overflow" ? (
    <ChevronRight size={14} />
  ) : (
    <><Folder size={14} fill="currentColor" /><span>{folder.title}</span></>
  );

  useEffect(() => {
    if (!mainMenu) return;
    return mainMenu.onAction((id) => {
      const action = ownedActionId(id, bookmarkFolderActionPrefix(ownerId, folder.id));
      if (action === null || !action.startsWith("bookmark:")) return;
      const url = bookmarkUrlById(folder, action.slice("bookmark:".length));
      if (url) onNavigate(url);
    });
  }, [folder, mainMenu, onNavigate, ownerId]);

  const openFolderMenu = (button: HTMLButtonElement) => {
    if (!mainMenu) return;
    const rect = button.getBoundingClientRect();
    const items = folderItems(folder, ownerId);
    mainMenu.open({
      anchor: { x: rect.left, y: rect.bottom + 2, vw: innerWidth, vh: innerHeight },
      theme: activeThemeId(),
      items: items.length > 0
        ? items
        : [{ id: `${bookmarkFolderActionPrefix(ownerId, folder.id)}empty`, label: "Empty folder", disabled: true }],
      cascade: true,
      width: 280,
    });
  };

  if (mainMenu) {
    return (
      <button
        type="button"
        className={buttonClass}
        aria-expanded={open}
        onClick={(event) => {
          if (open || wasJustClosed()) {
            mainMenu.close();
            onDisarm();
            return;
          }
          onArm();
          openFolderMenu(event.currentTarget);
        }}
        onMouseEnter={(event) => {
          mainMenu.cancelClose?.();
          if (!armed || open) return;
          onArm();
          openFolderMenu(event.currentTarget);
        }}
        onMouseLeave={() => mainMenu.scheduleClose?.(120)}
        title={buttonLabel}
        aria-label={buttonLabel}
        aria-hidden={hidden || undefined}
        tabIndex={hidden ? -1 : undefined}
      >
        {buttonContent}
      </button>
    );
  }

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(next) => {
        if (next) onArm();
        else onDisarm();
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={buttonClass}
          aria-expanded={open}
          onMouseEnter={() => {
            if (armed && !open) onArm();
          }}
          title={buttonLabel}
          aria-label={buttonLabel}
          aria-hidden={hidden || undefined}
          tabIndex={hidden ? -1 : undefined}
        >
          {buttonContent}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        className={`${MENU_PANEL} w-[280px] max-w-[calc(100vw-16px)]`}
        align="start"
      >
        <BookmarkMenuNodes nodes={folder.children} onNavigate={onNavigate} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function BookmarkLeafButton({
  node,
  onNavigate,
  hidden = false,
}: {
  node: Extract<BookmarkNode, { kind: "bookmark" }>;
  onNavigate(url: string): void;
  hidden?: boolean;
}) {
  return (
    <button
      type="button"
      className={`${styles.bookmarkBarItem}${hidden ? ` ${styles.bookmarkBarOverflowed}` : ""}`}
      onClick={() => onNavigate(node.url)}
      title={node.url}
      aria-hidden={hidden || undefined}
      tabIndex={hidden ? -1 : undefined}
    >
      <BookmarkFavicon node={node} />
      <span>{node.title || node.url}</span>
    </button>
  );
}

export function BookmarkBar({ ownerId, onNavigate }: { ownerId: string; onNavigate(url: string): void }) {
  const { text } = useTranslation();
  const visible = useBookmarksBarPreference();
  const [tree, setTree] = useState(readBookmarkTree);
  const { items, trailingFolders } = bookmarkBarLayout(tree);
  const itemsRef = useRef<HTMLDivElement>(null);
  const [overflowStart, setOverflowStart] = useState(items.length);
  const [openFolderId, setOpenFolderId] = useState<string | null>(null);
  const openFolderIdRef = useRef<string | null>(null);
  const closedAtRef = useRef(0);
  const closedKeyRef = useRef<string | null>(null);
  const armFolder = (id: string) => {
    openFolderIdRef.current = id;
    setOpenFolderId(id);
  };
  const disarmFolder = () => {
    closedKeyRef.current = openFolderIdRef.current;
    closedAtRef.current = performance.now();
    openFolderIdRef.current = null;
    setOpenFolderId(null);
  };
  useEffect(() => desktopBridge()?.mainMenu?.onClosed?.(() => {
    closedKeyRef.current = openFolderIdRef.current;
    closedAtRef.current = performance.now();
    openFolderIdRef.current = null;
    setOpenFolderId(null);
  }), []);
  const bookmarkOverflowFolder: BookmarkFolder = {
    kind: "folder",
    id: "bookmark-bar-overflow",
    title: text("All bookmarks", "所有书签"),
    children: items.slice(overflowStart),
  };

  useEffect(() => subscribeBookmarks(() => setTree(readBookmarkTree())), []);
  useEffect(() => {
    const container = itemsRef.current;
    if (!container) return;
    const updateOverflow = () => {
      const children = Array.from(container.children) as HTMLElement[];
      const gap = Number.parseFloat(getComputedStyle(container).columnGap) || 0;
      let used = 0;
      let firstHidden = items.length;
      for (let index = 0; index < children.length; index += 1) {
        const width = children[index].offsetWidth;
        const required = width + (index === 0 ? 0 : gap);
        if (used + required > container.clientWidth + 0.5) {
          firstHidden = index;
          break;
        }
        used += required;
      }
      setOverflowStart(firstHidden);
    };
    updateOverflow();
    const observer = new ResizeObserver(updateOverflow);
    observer.observe(container);
    return () => observer.disconnect();
  }, [items.length, tree, visible]);
  if (!visible) return null;

  return (
    <div className={styles.bookmarkBar} aria-label={text("Bookmarks bar", "书签栏")}>
      <div ref={itemsRef} className={styles.bookmarkBarItems}>
        {items.map((node, index) => node.kind === "folder" ? (
          <BookmarkFolderButton
            key={node.id}
            folder={node}
            ownerId={ownerId}
            onNavigate={onNavigate}
            hidden={index >= overflowStart}
            open={openFolderId === node.id}
            armed={openFolderId !== null}
            wasJustClosed={() =>
              performance.now() - closedAtRef.current < 120
              && closedKeyRef.current === node.id
            }
            onArm={() => armFolder(node.id)}
            onDisarm={disarmFolder}
          />
        ) : (
          <BookmarkLeafButton
            key={node.id}
            node={node}
            onNavigate={onNavigate}
            hidden={index >= overflowStart}
          />
        ))}
      </div>
      {trailingFolders.map((folder) => (
        <BookmarkFolderButton
          key={folder.id}
          folder={folder}
          ownerId={ownerId}
          onNavigate={onNavigate}
          open={openFolderId === folder.id}
          armed={openFolderId !== null}
          wasJustClosed={() =>
            performance.now() - closedAtRef.current < 120
            && closedKeyRef.current === folder.id
          }
          onArm={() => armFolder(folder.id)}
          onDisarm={disarmFolder}
        />
      ))}
      <span className={styles.bookmarkBarMoreSlot}>
        {overflowStart < items.length ? (
          <BookmarkFolderButton
            folder={bookmarkOverflowFolder}
            ownerId={ownerId}
            onNavigate={onNavigate}
            appearance="overflow"
            label={text("Show hidden bookmarks", "显示隐藏的书签")}
            open={openFolderId === bookmarkOverflowFolder.id}
            armed={openFolderId !== null}
            wasJustClosed={() =>
              performance.now() - closedAtRef.current < 120
              && closedKeyRef.current === bookmarkOverflowFolder.id
            }
            onArm={() => armFolder(bookmarkOverflowFolder.id)}
            onDisarm={disarmFolder}
          />
        ) : null}
      </span>
    </div>
  );
}
