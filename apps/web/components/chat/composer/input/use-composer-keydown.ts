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
 *   Enter          → submit
 */
import React from "react";

import type { SlashCommand } from "../slash/slash-commands";
import type { useFileMention } from "../attach/use-file-mention";
import type { useHistoryRecall } from "./use-history-recall";
import type { useSlashMenu } from "../slash/use-slash-menu";

export interface ComposerKeyDownOptions {
  input: string;
  setInput(next: string): void;
  fileMention: ReturnType<typeof useFileMention>;
  historyRecall: ReturnType<typeof useHistoryRecall>;
  slash: ReturnType<typeof useSlashMenu>;
  selectSlashCommand(cmd: SlashCommand): void;
  submit(): void | Promise<void>;
}

export function useComposerKeyDown({
  input,
  setInput,
  fileMention,
  historyRecall,
  slash,
  selectSlashCommand,
  submit,
}: ComposerKeyDownOptions) {
  const {
    atToken,
    fileMatches,
    fileMenuIndex,
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
      if (e.key === "Escape") {
        e.preventDefault();
        closeFileMenu();
        return;
      }
    }
    // Fish-shell-style history recall. Mirrors the TUI's PromptInput
    // logic (cli/src/components/PromptInput/PromptInput.tsx). Only fires
    // when the slash menu isn't holding the arrows and the caret is on
    // the first / last visual line of the textarea, so multi-line
    // editing still works naturally.
    if (e.key === "ArrowUp" && !slash.visible && !e.shiftKey
        && !e.metaKey && !e.altKey) {
      if (recallPrevious(e, input, setInput)) return;
    }
    if (e.key === "ArrowDown" && !slash.visible && historyIndex >= 0
        && !e.shiftKey && !e.metaKey && !e.altKey) {
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
        // activeIndex starts at -1 (no kbd nav yet); fall back to the
        // first match so ``/sp<Enter>`` runs ``/spawn`` without the
        // user having to press ArrowDown first.
        const idx = slash.activeIndex >= 0 ? slash.activeIndex : 0;
        const cmd = slash.matches[idx];
        if (cmd) selectSlashCommand(cmd);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };
}
