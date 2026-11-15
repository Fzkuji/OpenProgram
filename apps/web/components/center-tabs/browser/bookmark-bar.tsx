"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import { ChevronRight, ChevronsRight, Folder } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { itemCls, MENU_PANEL } from "@/components/ui/menu/menu-styles";
import { bookmarkBarLayout, bookmarkFaviconSources, readBookmarkTree, subscribeBookmarks, type BookmarkFolder, type BookmarkNode } from "@/lib/tabs/browser/bookmarks";
import { activeThemeId } from "@/lib/prefs/appearance/theme-pref";
import { showBookmarksBar, subscribeBrowserPrefs } from "@/lib/browser/browser-prefs";
import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import type { DesktopContextMenuItem } from "@/lib/desktop/desktop-bridge-types";
import { bookmarkFolderActionPrefix, ownedActionId } from "@/lib/browser/browser-layout";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { TabFavicon } from "../strip/tab-favicon";
import { ToolbarTip } from "./toolbar/browser-chrome";
import styles from "./browser.module.css";

export function useBookmarksBarPreference() {
  const [visible, setVisible] = useState(showBookmarksBar);
  useEffect(() => subscribeBrowserPrefs(() => setVisible(showBookmarksBar())), []);
  return visible;
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
    iconUrl: bookmarkFaviconSources(node).url,
    iconFallbackUrl: bookmarkFaviconSources(node).fallbackUrl,
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
  const { text } = useTranslation();
  if (nodes.length === 0) {
    return <DropdownMenuItem className={itemCls(false)} disabled>{text("Empty folder", "空文件夹")}</DropdownMenuItem>;
  }
  return nodes.map((node) => node.kind === "folder" ? (
    <DropdownMenuSub key={node.id}>
      <DropdownMenuSubTrigger
        className={`${itemCls(false)} w-full min-w-0 outline-none data-[highlighted]:bg-accent data-[highlighted]:text-text-bright data-[state=open]:bg-accent data-[state=open]:text-text-bright`}
      >
        <Folder size={14} fill="currentColor" className="shrink-0" />
        <span className="min-w-0 flex-1 truncate text-left">{node.title || "Folder"}</span>
        <ChevronRight size={14} className="ml-auto shrink-0" aria-hidden="true" />
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className={`${MENU_PANEL} w-[280px] max-w-[calc(100vw-16px)]`}>
        <BookmarkMenuNodes nodes={node.children} onNavigate={onNavigate} />
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  ) : (
    <DropdownMenuItem
      key={node.id}
      className={`${itemCls(false)} w-full min-w-0 outline-none data-[highlighted]:bg-accent data-[highlighted]:text-text-bright`}
      onSelect={() => onNavigate(node.url)}
      title={node.title || node.url}
    >
      <BookmarkFavicon node={node} />
      <span className="min-w-0 flex-1 truncate">{node.title || node.url}</span>
    </DropdownMenuItem>
  ));
}

function BookmarkFavicon({ node }: { node: Extract<BookmarkNode, { kind: "bookmark" }> }) {
  return <TabFavicon {...bookmarkFaviconSources(node)} />;
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
    <ChevronsRight size={14} />
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

  // Folder buttons show their name; only the icon-only overflow needs a tip.
  const withTip = (control: ReactElement) => (
    appearance === "overflow" ? <ToolbarTip label={buttonLabel}>{control}</ToolbarTip> : control
  );

  if (mainMenu) {
    return withTip(
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
        aria-label={buttonLabel}
        aria-hidden={hidden || undefined}
        tabIndex={hidden ? -1 : undefined}
      >
        {buttonContent}
      </button>,
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
      {withTip(<DropdownMenuTrigger asChild>
        <button
          type="button"
          className={buttonClass}
          aria-expanded={open}
          onMouseEnter={() => {
            if (armed && !open) onArm();
          }}
          aria-label={buttonLabel}
          aria-hidden={hidden || undefined}
          tabIndex={hidden ? -1 : undefined}
        >
          {buttonContent}
        </button>
      </DropdownMenuTrigger>)}
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
    <ToolbarTip label={node.url}>
      <button
        type="button"
        className={`${styles.bookmarkBarItem}${hidden ? ` ${styles.bookmarkBarOverflowed}` : ""}`}
        onClick={() => onNavigate(node.url)}
        aria-hidden={hidden || undefined}
        tabIndex={hidden ? -1 : undefined}
      >
        <BookmarkFavicon node={node} />
        <span>{node.title || node.url}</span>
      </button>
    </ToolbarTip>
  );
}

export function BookmarkBar({ ownerId, onNavigate }: { ownerId: string; onNavigate(url: string): void }) {
  const { text } = useTranslation();
  const visible = useBookmarksBarPreference();
  const [tree, setTree] = useState(readBookmarkTree);
  const { items } = bookmarkBarLayout(tree);
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
    title: text("Hidden bookmarks", "隐藏的书签"),
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
      <div ref={itemsRef} className="flex-1 min-w-0 relative flex items-center gap-[2px] overflow-hidden">
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
      <span className={styles.bookmarkBarDivider} aria-hidden="true" />
      <button
        type="button"
        className={styles.bookmarkBarItem}
        onClick={() => {
          desktopBridge()?.mainMenu?.close();
          useCenterTabs.getState().openBuiltinTab("bookmarks");
        }}
        aria-label={text("All bookmarks", "所有书签")}
      >
        <Folder size={14} />
        <span>{text("All bookmarks", "所有书签")}</span>
      </button>
    </div>
  );
}
