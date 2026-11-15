"use client";

/**
 * The right-click menu of a file a chat names: a path in a reply, a file
 * card under it, a row of the turn's changed files. One list of items,
 * the same everywhere:
 *
 *   Open preview
 *   Copy ›  Absolute path / Relative path / Filename
 *   Reveal in Finder          (when the worker runs on this machine)
 *   Open in default app       (in the desktop app on this machine)
 *   ─
 *   Attach as context         (adds the file to the chat's message box)
 */
import type { ReactElement } from "react";

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { appendToComposer } from "@/components/chat/messages/editing/quote-to-chat";
import { copyText } from "@/lib/clipboard";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { canBrowseLocalFiles } from "@/lib/files/local-file-url";
import { fileName, relativeTo } from "@/lib/files/prose-file-paths";
import { canRevealFiles, revealAbsolutePath, revealLabel } from "@/lib/files/reveal-file";

export interface FileMenuProject { id: string; path: string }

/** The composer's `@` mention of a path, quoted when it has a space. */
export function fileMention(path: string): string {
  return /\s/.test(path) ? `@"${path}"` : `@${path}`;
}

/** Whether this window can hand a file to the system's default app. */
export function canOpenInDefaultApp(): boolean {
  return canBrowseLocalFiles() && typeof window.openprogramDesktop?.openFileInDefaultApp === "function";
}

export function FilePathMenuContent({ path, sessionId, project, onOpen }: {
  /** The file's absolute path. */
  path: string;
  sessionId?: string;
  project?: FileMenuProject | null;
  /** Open preview; left out where the file has nothing to preview. */
  onOpen?: () => void;
}) {
  const { text } = useTranslation();
  const relative = relativeTo(path, project?.path);
  const copy = (value: string) => {
    void copyText(value).then(ok => {
      if (!ok) showToast(text("Couldn't copy.", "无法复制。"), { tone: "error" });
    });
  };
  const reveal = async () => {
    if (!(await revealAbsolutePath(path, sessionId))) showToast(text("Couldn't show this file.", "无法显示该文件。"), { tone: "error" });
  };
  const openDefault = async () => {
    const opened = await window.openprogramDesktop?.openFileInDefaultApp?.(path).catch(() => false);
    if (!opened) showToast(text("Couldn't open this file.", "无法打开该文件。"), { tone: "error" });
  };
  return (
    <ContextMenuContent className="min-w-[200px]" onCloseAutoFocus={event => event.preventDefault()}>
      {onOpen ? <ContextMenuItem onSelect={onOpen}><MenuRow glyph="eye" label={text("Open preview", "打开预览")} /></ContextMenuItem> : null}
      <ContextMenuSub>
        <ContextMenuSubTrigger><MenuRow glyph="copy" label={text("Copy", "复制")} /></ContextMenuSubTrigger>
        <ContextMenuSubContent className="min-w-[160px]">
          <ContextMenuItem onSelect={() => copy(path)}><MenuRow glyph="copy" label={text("Absolute path", "绝对路径")} /></ContextMenuItem>
          <ContextMenuItem disabled={!relative} onSelect={() => { if (relative) copy(relative); }}><MenuRow glyph="copy" label={text("Relative path", "相对路径")} /></ContextMenuItem>
          <ContextMenuItem onSelect={() => copy(fileName(path))}><MenuRow glyph="file-text" label={text("Filename", "文件名")} /></ContextMenuItem>
        </ContextMenuSubContent>
      </ContextMenuSub>
      {canRevealFiles()
        ? <ContextMenuItem onSelect={() => void reveal()}><MenuRow glyph="folder-path-connect" label={revealLabel(text)} /></ContextMenuItem>
        : null}
      {canOpenInDefaultApp()
        ? <ContextMenuItem onSelect={() => void openDefault()}><MenuRow glyph="square-arrow-right-up" label={text("Open in default app", "用默认应用打开")} /></ContextMenuItem>
        : null}
      {sessionId ? <>
        <ContextMenuSeparator />
        <ContextMenuItem onSelect={() => appendToComposer(sessionId, fileMention(relative ?? path))}>
          <MenuRow glyph="paperclip" label={text("Attach as context", "作为上下文附加")} />
        </ContextMenuItem>
      </> : null}
    </ContextMenuContent>
  );
}

/** A file's own element (a card, a row) with the file menu on right-click. */
export function FilePathContextMenu({ children, ...menu }: Parameters<typeof FilePathMenuContent>[0] & { children: ReactElement }) {
  return (
    <ContextMenu modal={false}>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <FilePathMenuContent {...menu} />
    </ContextMenu>
  );
}
