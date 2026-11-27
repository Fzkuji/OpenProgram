"use client";

/**
 * Renaming and deleting a chat on the phone ask with Framework7's own
 * dialogs (an alert with a field, an alert with a red button), as iOS
 * asks; the work itself is the chat menu's (session-actions-menu.tsx).
 */

import { f7 } from "framework7-react";

type Text = (en: string, zh: string) => string;

export function askRename(text: Text, current: string, rename: (next: string) => void): void {
  f7.dialog.prompt(
    text("A new name for this chat", "给这个对话起个新名字"),
    text("Rename chat", "重命名对话"),
    (value) => rename(String(value ?? "")),
    undefined,
    current,
  );
}

export function askDelete(text: Text, title: string, remove: () => void): void {
  f7.dialog.create({
    title: text("Delete chat", "删除对话"),
    text: text(`Delete "${title}"? This cannot be undone.`, `删除「${title}」？删除后无法恢复。`),
    buttons: [
      { text: text("Cancel", "取消") },
      { text: text("Delete", "删除"), color: "red", strong: true, onClick: remove },
    ],
  }).open();
}
