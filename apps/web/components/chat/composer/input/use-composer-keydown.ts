"use client";

/**
 * Composer textarea key handling, in strict precedence order:
 *
 *   IME composing  → never hijacked (a Chinese/Japanese/Korean candidate
 *                    confirmation is not a send)
 *   @file mention  → its arrows / Enter / Tab / Esc steer the file menu
 *   ↑ / ↓ recall   → fish-shell history, only when the slash menu isn't
 *                    holding the arrows
 *   slash menu     → arrows move the highlight, Enter picks, Esc closes
 *   Esc            → stops the running turn, whatever the draft holds
 *                    (Claude Code / Codex); the draft is kept
 *   Enter          → submit; during a run Enter adds the message to the
 *                    current turn (steer) and ⌘/Ctrl+Enter queues it for
 *                    the next turn
 */
import React from "react";

import type { SlashCommand } from "../slash/slash-commands";
import type { useFileMention } from "../attach/mention/use-file-mention";
import type { useHistoryRecall } from "./use-history-recall";
import type { useSlashMenu } from "../slash/use-slash-menu";
import type { RunningSendMode } from "../submit/use-chat-submit";

export interface ComposerKeyDownOptions {
  input: string;
  setInput(next: string): void;
  fileMention: ReturnType<typeof useFileMention>;
  historyRecall: ReturnType<typeof useHistoryRecall>;
  slash: ReturnType<typeof useSlashMenu>;
  selectSlashCommand(cmd: SlashCommand): void;
  submit(mode?: RunningSendMode): void | Promise<void>;
  /** A turn is running and can be stopped from here. */
  canStop?: boolean;
  stop?(): void;
}

export function useComposerKeyDown({
  input,
  setInput,
  fileMention,
  historyRecall,
  slash,
  selectSlashCommand,
  submit,
  canStop = false,
  stop,
}: ComposerKeyDownOptions) {
  const {
    atToken,
    fileMatches,
    fileMenuIndex,
    fileMenuLoading,
    fileMenuPos,
    setFileMenuIndex,
    pickFile,
    closeMenu: closeFileMenu,
  } = fileMention;
  const { historyIndex, setHistoryIndex, recallPrevious, recallNext } =
    historyRecall;

  return function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Don't hijack Enter while an IME is composing — the user is
    // confirming a Chinese / Japanese / Korean candidate, not
    // sending. ``isComposing`` is set during the IME session;
    // Chromium also reports keyCode 229 for the same window.
    // Reading off ``nativeEvent`` because React's synthetic event
    // type doesn't include the flag yet.
    const native = e.nativeEvent as KeyboardEvent;
    if (native.isComposing || native.keyCode === 229) {
      return;
    }
    // @file mention menu takes precedence — its arrows / enter / esc /
    // tab steer the menu, never fall through to history-recall or
    // the slash menu.
    if (atToken && e.key === "Escape"
        && (fileMenuLoading || fileMatches.length > 0 || fileMenuPos)) {
      e.preventDefault();
      closeFileMenu();
      return;
    }
    if (atToken && fileMenuLoading) {
      if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") {
        e.preventDefault();
        return;
      }
    }
    if (atToken && fileMatches.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFileMenuIndex((i) => Math.min(fileMatches.length - 1, i + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setFileMenuIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const picked = fileMatches[fileMenuIndex] ?? fileMatches[0];
        if (picked) pickFile(picked);
        return;
      }
    }
    // Fish-shell-style history recall. Mirrors the TUI's PromptInput
    // logic (cli/src/components/PromptInput/PromptInput.tsx). Only fires
    // when the slash menu isn't holding the arrows and the caret is on
    // the first / last visual line of the textarea, so multi-line
    // editing still works naturally.
    if (e.key === "ArrowUp" && !slash.visible && !e.shiftKey
        && !e.metaKey && !e.altKey && !e.ctrlKey) {
      if (recallPrevious(e, input, setInput)) return;
    }
    if (e.key === "ArrowDown" && !slash.visible && historyIndex >= 0
        && !e.shiftKey && !e.metaKey && !e.altKey && !e.ctrlKey
        && e.currentTarget.selectionStart === e.currentTarget.selectionEnd) {
      e.preventDefault();
      recallNext(setInput);
      return;
    }
    // Any typing (non-arrow key) drops out of history-recall mode so
    // editing a recalled entry doesn't re-snap when the user hits ↑
    // again.
    if (historyIndex >= 0 && e.key.length === 1) {
      setHistoryIndex(-1);
    }
    // While the slash menu is open it captures the arrow keys (move the
    // highlight), Enter (pick the highlighted command) and Escape.
    // `closing` is the 380ms fade-out: visible stays true so the list
    // can animate, but Enter must fall through to submit → runCommand.
    if (slash.visible && !slash.closing) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        slash.move(1);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        slash.move(-1);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        slash.close();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        // activeIndex starts at -1 (no kbd nav yet); a fully typed name
        // runs that command, otherwise the first match, so ``/sp<Enter>``
        // runs ``/spawn`` without the user having to press ArrowDown first.
        const typed = input.trim().toLowerCase();
        const cmd = slash.activeIndex >= 0
          ? slash.matches[slash.activeIndex]
          : slash.matches.find((c) => c.name.toLowerCase() === typed) ?? slash.matches[0];
        if (cmd) selectSlashCommand(cmd);
        return;
      }
    }
    if (e.key === "Escape" && canStop && stop
        && !e.shiftKey && !e.metaKey && !e.altKey && !e.ctrlKey) {
      e.preventDefault();
      e.stopPropagation();
      stop();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit(e.metaKey || e.ctrlKey ? "queue" : "steer");
    }
  };
}
