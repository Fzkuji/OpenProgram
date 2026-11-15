"use client";

/**
 * The right-click menu of every terminal view: Copy, Paste where the view
 * takes input, and "Add selection to chat", as Codex's terminal has.
 */
import { useState, type ReactNode } from "react";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
// Terminal copy and paste are ⌘C / ⌘V on macOS and Ctrl+Shift+C / V
// elsewhere, where Ctrl+C is the interrupt (terminal-page.tsx).
import { isMacPlatform, MenuRow } from "@/components/ui/menu/menu-parts";
import type { Terminal } from "@xterm/xterm";

import { appendToComposer } from "@/components/chat/messages/editing/quote-to-chat";
import { targetChat } from "@/lib/chat/resources/target-chat";
import { terminalSelectionText } from "@/lib/chat/resources/terminal-selection";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";

export function TerminalContextMenu({ terminal, onPaste, canPaste = true, onClose, children }: {
  terminal: () => Terminal | null | undefined;
  /** Omitted for read-only views. */
  onPaste?: () => void;
  canPaste?: boolean;
  onClose?: () => void;
  children: ReactNode;
}) {
  const { text } = useTranslation();
  const [selection, setSelection] = useState("");

  const copy = () => {
    if (selection) void navigator.clipboard.writeText(selection).catch(() => showToast(text("Clipboard copy failed", "复制到剪贴板失败")));
  };
  const addToChat = () => {
    if (!selection.trim()) return;
    const chat = targetChat();
    if (!chat) {
      showToast(text("Open a conversation to add the selection to", "先打开一个对话，再加入选中内容"));
      return;
    }
    appendToComposer(chat.key, terminalSelectionText(selection), false);
    showToast(chat.title
      ? text(`Selection added to the message in “${chat.title}”`, `选中内容已加入“${chat.title}”的消息`)
      : text("Selection added to your message", "选中内容已加入消息"));
  };

  return (
    <ContextMenu onOpenChange={(open) => { if (!open) onClose?.(); }}>
      <ContextMenuTrigger asChild>
        <div style={{ display: "contents" }} onContextMenu={() => {
          // xterm has already applied its right-click word selection by now.
          setSelection(terminal()?.getSelection() ?? "");
        }}>
          {children}
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent onCloseAutoFocus={(event) => event.preventDefault()} className="min-w-[200px]">
        <ContextMenuItem disabled={!selection} onSelect={copy}>
          <MenuRow glyph="copy" label={text("Copy", "复制")} keys={{ key: "C", mod: true, shift: !isMacPlatform() }} />
        </ContextMenuItem>
        {onPaste && (
          <ContextMenuItem disabled={!canPaste} onSelect={onPaste}>
            <MenuRow glyph="clipboard-text" label={text("Paste", "粘贴")} keys={{ key: "V", mod: true, shift: !isMacPlatform() }} />
          </ContextMenuItem>
        )}
        <ContextMenuSeparator />
        <ContextMenuItem disabled={!selection.trim()} onSelect={addToChat}>
          <MenuRow glyph="chat-round-dots" label={text("Add selection to chat", "把选中内容加入对话")} />
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
