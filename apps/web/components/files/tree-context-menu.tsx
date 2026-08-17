"use client";

/**
 * TreeContextMenu — the right-click menu for a FileTree row, in VS Code
 * item order. Presentational only: the Popover shell, target row and
 * every action live in FileTree. Uses the same MENU_PANEL / itemCls
 * primitives as ConvMenu so all context menus are pixel-identical.
 */
import { useTranslation } from "@/lib/i18n";
import {
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/chat/top-bar/menu-styles";

/** Cut/copy source, module-level so it survives menu close and spans
 *  tree instances. Cleared after a cut-paste lands. */
export const treeClipboard: {
  current: { op: "cut" | "copy"; path: string } | null;
} = { current: null };

export interface TreeContextMenuProps {
  canPaste: boolean;
  onReveal: () => void;
  onNewFile: () => void;
  onNewFolder: () => void;
  onCopyPath: () => void;
  onCopyRelativePath: () => void;
  onCut: () => void;
  onCopy: () => void;
  onPaste: () => void;
  onRename: () => void;
  onDelete: () => void;
  onClose: () => void;
}

export function TreeContextMenu(props: TreeContextMenuProps) {
  const { text } = useTranslation();
  const run = (fn: () => void) => {
    fn();
    props.onClose();
  };

  return (
    <div className={`${MENU_PANEL} min-w-[200px]`}>
      <Item
        label={text("Reveal in Finder", "在 Finder 中显示")}
        onClick={() => run(props.onReveal)}
      />
      <div className={MENU_SEPARATOR} />
      <Item label={text("New File", "新建文件")} onClick={() => run(props.onNewFile)} />
      <Item label={text("New Folder", "新建文件夹")} onClick={() => run(props.onNewFolder)} />
      <div className={MENU_SEPARATOR} />
      <Item label={text("Copy Path", "复制路径")} onClick={() => run(props.onCopyPath)} />
      <Item
        label={text("Copy Relative Path", "复制相对路径")}
        onClick={() => run(props.onCopyRelativePath)}
      />
      <div className={MENU_SEPARATOR} />
      <Item label={text("Cut", "剪切")} onClick={() => run(props.onCut)} />
      <Item label={text("Copy", "复制")} onClick={() => run(props.onCopy)} />
      <Item
        label={text("Paste", "粘贴")}
        disabled={!props.canPaste}
        onClick={() => run(props.onPaste)}
      />
      <div className={MENU_SEPARATOR} />
      <Item label={text("Rename", "重命名")} onClick={() => run(props.onRename)} />
      <Item label={text("Delete", "删除")} danger onClick={() => run(props.onDelete)} />
    </div>
  );
}

function Item({
  label,
  danger,
  disabled,
  onClick,
}: {
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={
        itemCls(false, danger) + (disabled ? " pointer-events-none opacity-40" : "")
      }
    >
      <span className="flex-1 text-left">{label}</span>
    </button>
  );
}
