"use client";

/**
 * Fish-shell-style ↑/↓ history recall over this session's user messages.
 *
 * Mirrors the TUI's PromptInput semantics (cli/src/components/PromptInput):
 * the list is oldest-first, ↑ steps backwards starting at the newest and ↓
 * steps forward toward the live draft. Recall only ENGAGES when the caret
 * is on the first (or last) visual line so multi-line editing still works;
 * once engaged (`index >= 0`) the arrows keep stepping regardless of caret.
 */
import React, { useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

/** Don't recall a user message longer than this through ↑/↓ history
 *  cycling. Long messages (full pasted code, expanded tokens, etc.)
 *  are not useful to step through and bloat the persisted draft on
 *  every keystroke once recalled. The user can still scroll back to
 *  the original message in the chat transcript to re-use it. */
export const HISTORY_RECALL_MAX = 5000;

export function useHistoryRecall(
  bound: string | null,
  currentSessionId: string | null,
) {
  // History recall — user messages from the active session, ordered
  // oldest-first to match TUI semantics. Built from
  // ``messageOrder[currentSessionId]`` filtered to user role. Resets
  // automatically whenever the session changes via the useEffect below.
  const messagesById = useSessionStore((s) => s.messagesById);
  const messageOrder = useSessionStore((s) => {
    const sid = bound ?? s.currentSessionId;
    return sid ? s.messageOrder[sid] : undefined;
  });
  const history = React.useMemo<string[]>(() => {
    if (!messageOrder) return [];
    const out: string[] = [];
    for (const id of messageOrder) {
      const m = messagesById[id];
      if (m && m.role === "user" && typeof m.content === "string"
          && m.content.trim()
          // Skip giant messages — recalling them into the textarea
          // would bloat the persisted draft (the per-keystroke write
          // to ``composerDrafts``) and is rarely useful: long messages
          // are typically expanded pastes, not commands the user wants
          // to step back through with ↑/↓.
          && m.content.length <= HISTORY_RECALL_MAX) {
        out.push(m.content);
      }
    }
    return out;
  }, [messageOrder, messagesById]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  // Reset history index when the session switches.
  useEffect(() => {
    setHistoryIndex(-1);
  }, [currentSessionId]);

  /** ↑ handler. Returns true when it consumed the key. */
  function recallPrevious(
    e: React.KeyboardEvent<HTMLTextAreaElement>,
    input: string,
    setInput: (next: string) => void,
  ) {
    const ta = e.currentTarget;
    // Enter recall mode when caret is on the first visual line and
    // nothing is selected. Once recall mode is active (historyIndex
    // >= 0) ↑ keeps stepping back regardless of caret position.
    const firstNewline = input.indexOf("\n");
    const onFirstLine = ta.selectionStart === ta.selectionEnd
      && ta.selectionStart <= (firstNewline < 0 ? input.length : firstNewline);
    if (history.length === 0 || (historyIndex < 0 && !onFirstLine)) return false;
    e.preventDefault();
    const next = historyIndex < 0
      ? history.length - 1
      : Math.max(0, historyIndex - 1);
    setHistoryIndex(next);
    setInput(history[next] ?? "");
    // Move caret to end so the next ↑ keeps recalling instead of
    // moving inside the freshly-loaded text.
    requestAnimationFrame(() => {
      const v = history[next] ?? "";
      ta.setSelectionRange(v.length, v.length);
    });
    return true;
  }

  /** ↓ handler — only meaningful once recall mode is engaged. */
  function recallNext(setInput: (next: string) => void) {
    const next = historyIndex + 1;
    if (next >= history.length) {
      setHistoryIndex(-1);
      setInput("");
    } else {
      setHistoryIndex(next);
      setInput(history[next] ?? "");
    }
  }

  return { history, historyIndex, setHistoryIndex, recallPrevious, recallNext };
}
