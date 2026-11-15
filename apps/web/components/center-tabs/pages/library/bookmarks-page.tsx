"use client";

/** The Bookmarks manager: search header, folder rail and the current folder's list. */
import { useEffect, useState } from "react";
import { Bookmark, Check, ChevronDown, ChevronRight, Folder, MoreVertical, X } from "lucide-react";
import { bookmarkFaviconSources } from "@/lib/tabs/browser/bookmarks";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { TabFavicon } from "../../strip/tab-favicon";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { BOOKMARKS_ROOT_ID, createFolder, deleteNode, findNode, moveNode, readBookmarkTree, renameNode, subscribeBookmarks, type BookmarkFolder, type BookmarkNode } from "@/lib/tabs/browser/bookmarks";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { SearchInput } from "@/components/ui/search-input";
import styles from "../pages.module.css";

/** Folder navigation keeps ancestors visible while searching. */
function matchesQuery(node: BookmarkNode, needle: string): boolean {
  if (!needle) return true;
  if (node.title.toLowerCase().includes(needle)) return true;
  return node.kind === "folder"
    ? node.children.some((child) => matchesQuery(child, needle))
    : node.url.toLowerCase().includes(needle);
}

function bookmarkSearchResults(folder: BookmarkFolder, needle: string): BookmarkNode[] {
  const results: BookmarkNode[] = [];
  const visit = (node: BookmarkNode) => {
    const ownMatch = node.title.toLowerCase().includes(needle)
      || (node.kind === "bookmark" && node.url.toLowerCase().includes(needle));
    if (ownMatch) results.push(node);
    if (node.kind === "folder") node.children.forEach(visit);
  };
  folder.children.forEach(visit);
  return results;
}

function BookmarkFavicon({ node }: { node: BookmarkNode }) {
  return node.kind === "folder"
    ? <Folder size={16} aria-hidden="true" />
    : <TabFavicon {...bookmarkFaviconSources(node)} />;
}

export function BookmarksPage() {
  const { text } = useTranslation();
  const [tree, setTree] = useState<BookmarkFolder>(readBookmarkTree);
  const [query, setQuery] = useState("");
  const [selectedFolderId, setSelectedFolderId] = useState(BOOKMARKS_ROOT_ID);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const [dropAt, setDropAt] = useState<{ parentId: string; index: number } | null>(null);

  useEffect(() => {
    const refresh = () => setTree(readBookmarkTree());
    refresh();
    return subscribeBookmarks(refresh);
  }, []);

  const needle = query.trim().toLowerCase();
  const selectedNode = findNode(tree, selectedFolderId);
  const selectedFolder = selectedNode?.kind === "folder" ? selectedNode : tree;
  const visibleNodes = needle
    ? bookmarkSearchResults(tree, needle)
    : selectedFolder.children;

  useEffect(() => {
    if (selectedNode?.kind !== "folder") setSelectedFolderId(BOOKMARKS_ROOT_ID);
  }, [selectedNode]);

  function saveRename(id: string) {
    renameNode(id, draftTitle);
    setEditingId(null);
  }

  function toggleCollapsed(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearDrag() {
    setDragId(null);
    setDropId(null);
    setDropAt(null);
  }

  function handleDrop(parentId: string) {
    if (dragId) moveNode(dragId, parentId);
    clearDrag();
  }

  /** Drop on the gap between two rows reorders among siblings. The
   *  dragged node is spliced out before being re-inserted, so an index
   *  after its own old slot shifts down by one. */
  function handleReorderDrop(parentId: string, index: number) {
    if (dragId) {
      const siblings = findNode(tree, parentId);
      const from =
        siblings && siblings.kind === "folder"
          ? siblings.children.findIndex((child) => child.id === dragId)
          : -1;
      moveNode(dragId, parentId, from >= 0 && from < index ? index - 1 : index);
    }
    clearDrag();
  }

  function dropZone(parentId: string, index: number) {
    const active = dropAt?.parentId === parentId && dropAt.index === index;
    return (
      <div
        className={styles.bookmarkManagerDropZone}
        data-drop-target={active ? "true" : undefined}
        onDragOver={(event) => {
          if (!dragId) return;
          event.preventDefault();
          event.stopPropagation();
          event.dataTransfer.dropEffect = "move";
          setDropId(null);
          setDropAt({ parentId, index });
        }}
        onDragLeave={() => setDropAt(null)}
        onDrop={(event) => {
          event.preventDefault();
          event.stopPropagation();
          handleReorderDrop(parentId, index);
        }}
      />
    );
  }

  const saveLabel = text("Save bookmark title", "保存书签标题");
  const cancelLabel = text("Cancel editing", "取消编辑");
  const editLabel = text("Edit bookmark title", "编辑书签标题");
  const deleteLabel = text("Delete bookmark", "删除书签");
  const newFolderLabel = text("New folder", "新建文件夹");
  const expandLabel = text("Expand folder", "展开文件夹");
  const collapseLabel = text("Collapse folder", "折叠文件夹");
  const pageActionsLabel = text("Bookmark actions", "书签操作");
  const allBookmarksLabel = text("All bookmarks", "全部书签");

  function renderFolder(folder: BookmarkFolder, depth: number): React.ReactNode {
    const childFolders = folder.children.filter(
      (node): node is BookmarkFolder => node.kind === "folder" && matchesQuery(node, needle),
    );
    const open = folder.id === BOOKMARKS_ROOT_ID || !collapsed.has(folder.id) || Boolean(needle);
    const selected = selectedFolder.id === folder.id;
    const label = folder.id === BOOKMARKS_ROOT_ID ? allBookmarksLabel : folder.title;
    return (
      <div key={folder.id}>
        <div
          className={styles.bookmarkFolderRow + (selected ? ` ${styles.bookmarkFolderSelected}` : "")}
          style={{ paddingInlineStart: 8 + depth * 16 }}
          data-drop-target={dropId === folder.id ? "true" : undefined}
          onDragOver={(event) => {
            if (!dragId || dragId === folder.id) return;
            event.preventDefault();
            event.stopPropagation();
            setDropId(folder.id);
          }}
          onDragLeave={() => setDropId(null)}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            handleDrop(folder.id);
          }}
        >
          {childFolders.length > 0 && folder.id !== BOOKMARKS_ROOT_ID ? (
            <button
              type="button"
              className={styles.bookmarkFolderTwisty}
              onClick={() => toggleCollapsed(folder.id)}
              title={open ? collapseLabel : expandLabel}
              aria-label={open ? collapseLabel : expandLabel}
            >
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : (
            <span className={styles.bookmarkFolderTwisty} />
          )}
          <Folder size={16} aria-hidden="true" />
          <button
            type="button"
            className={styles.bookmarkFolderTitle}
            onClick={() => setSelectedFolderId(folder.id)}
            title={label}
          >
            {label}
          </button>
        </div>
        {open ? childFolders.map((child) => renderFolder(child, depth + 1)) : null}
      </div>
    );
  }

  function openNode(node: BookmarkNode) {
    if (node.kind === "folder") {
      setSelectedFolderId(node.id);
      setQuery("");
    } else {
      useCenterTabs.getState().openWebTab(node.url);
    }
  }

  function renderContentNode(node: BookmarkNode): React.ReactNode {
    const editing = editingId === node.id;
    const isFolder = node.kind === "folder";
    return (
      <div
        key={node.id}
        className={styles.bookmarkContentRow}
        data-drop-target={dropId === node.id ? "true" : undefined}
        draggable={!editing && !needle}
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = "move";
          setDragId(node.id);
        }}
        onDragEnd={clearDrag}
        onDragOver={isFolder && dragId && dragId !== node.id ? (event) => {
          event.preventDefault();
          event.stopPropagation();
          setDropId(node.id);
        } : undefined}
        onDragLeave={isFolder ? () => setDropId(null) : undefined}
        onDrop={isFolder ? (event) => {
          event.preventDefault();
          event.stopPropagation();
          handleDrop(node.id);
        } : undefined}
      >
        <span className={styles.bookmarkContentIcon}>
          <BookmarkFavicon node={node} />
        </span>
        {editing ? (
          <input
            className={styles.bookmarkContentTitleInput}
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") saveRename(node.id);
              if (event.key === "Escape") setEditingId(null);
            }}
            aria-label={editLabel}
            autoFocus
          />
        ) : (
          <button
            type="button"
            className={styles.bookmarkContentTitle}
            onClick={() => openNode(node)}
            title={node.title}
          >
            {node.title || (node.kind === "bookmark" ? node.url : newFolderLabel)}
          </button>
        )}
        {editing ? (
          <div className={styles.bookmarkContentEditActions}>
            <Button type="button" variant="ghost" size="icon-sm" onClick={() => saveRename(node.id)} title={saveLabel} aria-label={saveLabel}>
              <Check className="size-4" />
            </Button>
            <Button type="button" variant="ghost" size="icon-sm" onClick={() => setEditingId(null)} title={cancelLabel} aria-label={cancelLabel}>
              <X className="size-4" />
            </Button>
          </div>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                title={`${pageActionsLabel}: ${node.title}`}
                aria-label={`${pageActionsLabel}: ${node.title}`}
              >
                <MoreVertical className="size-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={MENU_PANEL + " min-w-[180px]"}>
              <DropdownMenuItem
                className={`${itemCls(false)} ${MENU_ITEM_STATES}`}
                onSelect={() => {
                  setEditingId(node.id);
                  setDraftTitle(node.title);
                }}
              >
                <MenuRow glyph="pen" label={editLabel} />
              </DropdownMenuItem>
              <DropdownMenuSeparator className={MENU_SEPARATOR} />
              <DropdownMenuItem className={cn(itemCls(false, true), MENU_ITEM_STATES, "data-[highlighted]:bg-[color-mix(in_srgb,var(--accent-red)_15%,transparent)] data-[highlighted]:text-accent-red")} onSelect={() => deleteNode(node.id)}>
                <MenuRow glyph="trash-bin-trash" label={deleteLabel} danger />
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    );
  }

  const contentRows: React.ReactNode[] = [];
  visibleNodes.forEach((node, index) => {
    if (!needle) {
      contentRows.push(
        <div key={`zone-${selectedFolder.id}-${index}`}>
          {dropZone(selectedFolder.id, index)}
        </div>,
      );
    }
    contentRows.push(renderContentNode(node));
  });
  if (!needle && visibleNodes.length > 0) {
    contentRows.push(
      <div key={`zone-${selectedFolder.id}-end`}>
        {dropZone(selectedFolder.id, selectedFolder.children.length)}
      </div>,
    );
  }

  return (
    <div className={styles.bookmarkManager}>
      <header className={styles.bookmarkManagerHeader}>
        <div className={styles.bookmarkManagerBrand}>
          <Bookmark size={20} aria-hidden="true" />
          <h1>{text("Bookmarks", "书签")}</h1>
        </div>
        <SearchInput
          className={styles.bookmarkManagerSearch}
          value={query}
          onChange={setQuery}
          placeholder={text("Search bookmarks", "搜索书签")}
          aria-label={text("Search bookmarks", "搜索书签")}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title={pageActionsLabel}
              aria-label={pageActionsLabel}
            >
              <MoreVertical className="size-5" aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={MENU_PANEL + " min-w-[180px]"}>
            <DropdownMenuItem
              className={`${itemCls(false)} ${MENU_ITEM_STATES}`}
              onSelect={() => createFolder(newFolderLabel, selectedFolder.id)}
            >
              <MenuRow glyph="add-folder" label={newFolderLabel} />
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>
      <div className={styles.bookmarkManagerBody}>
        <nav className={styles.bookmarkFolderTree} aria-label={text("Bookmark folders", "书签文件夹")}>
          {renderFolder(tree, 0)}
        </nav>
        <main
          className={styles.bookmarkManagerContent}
          onDragOver={(event) => {
            if (dragId && !needle) event.preventDefault();
          }}
          onDrop={() => {
            if (!needle) handleDrop(selectedFolder.id);
          }}
        >
          <div className={styles.bookmarkContentPanel}>
            {tree.children.length === 0 ? (
              <div className={styles.bookmarkContentEmpty}>
                {text("No bookmarks yet", "还没有书签")}
              </div>
            ) : visibleNodes.length === 0 ? (
              <div className={styles.bookmarkContentEmpty}>
                {needle
                  ? text("No matching bookmarks", "没有匹配的书签")
                  : text("This folder is empty", "此文件夹为空")}
              </div>
            ) : (
              <div className={styles.bookmarkContentList}>{contentRows}</div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
