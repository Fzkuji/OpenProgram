"use client";

/**
 * TreeContextMenu — the right-click menu for a FileTree row, in VS Code
 * item order: the content of the shadcn ContextMenu whose root and
 * trigger (the tree), target row and every action live in FileTree.
 */
import { useTranslation } from "@/lib/i18n";
import {
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from "@/components/ui/context-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";

/** Cut/copy source, module-level so it survives menu close and spans
 *  tree instances. Cleared after a cut-paste lands. */
export const treeClipboard: {
  current: { op: "cut" | "copy"; projectId: string; paths: string[] } | null;
} = { current: null };

export interface TreeContextMenuProps {
  canPaste: boolean;
  /** Rows the copy, cut and delete items act on; rename needs exactly one. */
  count?: number;
  revealLabel: string;
  onInfo: () => void;
  onReveal: () => void;
  onNewFile: () => void;
  onNewFolder: () => void;
  onCopyPath: () => void;
  onCopyRelativePath: () => void;
  onCut: () => void;
  onCopy: () => void;
  onPaste: () => void;
  onMoveTo: () => void;
  onRename: () => void;
  onDelete: () => void;
}

export function TreeContextMenu(props: TreeContextMenuProps) {
  const { text } = useTranslation();
  return (
    // Rename / New File mount an input that takes focus; returning focus
    // to the row on close would steal it back.
    <ContextMenuContent className="min-w-[200px]" onCloseAutoFocus={(e) => e.preventDefault()}>
      <ContextMenuItem onSelect={props.onInfo}><MenuRow glyph="info-circle" label={text("Get Info", "查看详细信息")} /></ContextMenuItem>
      <ContextMenuItem onSelect={props.onReveal}><MenuRow glyph="folder-path-connect" label={props.revealLabel} /></ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem onSelect={props.onNewFile}><MenuRow glyph="document-add" label={text("New File", "新建文件")} /></ContextMenuItem>
      <ContextMenuItem onSelect={props.onNewFolder}><MenuRow glyph="add-folder" label={text("New Folder", "新建文件夹")} /></ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem onSelect={props.onCopyPath}><MenuRow glyph="copy" label={text("Copy Path", "复制路径")} /></ContextMenuItem>
      <ContextMenuItem onSelect={props.onCopyRelativePath}><MenuRow glyph="copy" label={text("Copy Relative Path", "复制相对路径")} /></ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem onSelect={props.onCut}><MenuRow glyph="scissors" label={text("Cut", "剪切")} /></ContextMenuItem>
      <ContextMenuItem onSelect={props.onCopy}><MenuRow glyph="copy" label={text("Copy", "复制")} /></ContextMenuItem>
      <ContextMenuItem disabled={!props.canPaste} onSelect={props.onPaste}><MenuRow glyph="clipboard-text" label={text("Paste", "粘贴")} /></ContextMenuItem>
      <ContextMenuItem onSelect={props.onMoveTo}><MenuRow glyph="folder-open" label={(props.count ?? 1) > 1 ? text(`Move ${props.count} Items to…`, `将 ${props.count} 项移动到…`) : text("Move to…", "移动到…")} keys={{ key: "M", mod: true, shift: true }} /></ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem disabled={(props.count ?? 1) > 1} onSelect={props.onRename}><MenuRow glyph="pen" label={text("Rename", "重命名")} /></ContextMenuItem>
      <ContextMenuItem variant="destructive" onSelect={props.onDelete}><MenuRow glyph="trash-bin-trash" label={(props.count ?? 1) > 1 ? text(`Delete ${props.count} Items`, `删除 ${props.count} 项`) : text("Delete", "删除")} danger /></ContextMenuItem>
    </ContextMenuContent>
  );
}
