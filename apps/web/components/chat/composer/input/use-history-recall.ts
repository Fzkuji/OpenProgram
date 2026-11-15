"use client";

/**
 * Fish-shell-style ↑/↓ history recall over this session's user messages.
 *
 * Mirrors the TUI's PromptInput semantics (cli/src/components/PromptInput):
 * the list is oldest-first, ↑ steps backwards starting at the newest and ↓
 * steps forward toward the live draft. Recall only ENGAGES when the draft
 * is empty or the caret is on its first visual line (a wrapped paragraph
 * has several), so moving up through a draft still works; once engaged
 * (`index >= 0`) the arrows keep stepping regardless of caret. A recalled
 * message comes back as the text that was typed: the attachment markers
 * the send added are left out.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { selectViewRows, useSessionStore } from "@/lib/session-store";
import { extractAttachmentMentions } from "../../../../lib/chat/send/attachment-marker.ts";

/** Don't recall a user message longer than this through ↑/↓ history
 *  cycling. Long messages (full pasted code, expanded tokens, etc.)
 *  are not useful to step through and bloat the persisted draft on
 *  every keystroke once recalled. The user can still scroll back to
 *  the original message in the chat transcript to re-use it. */
export const HISTORY_RECALL_MAX = 5000;

/** Stable empty result so a session with no transcript doesn't hand the
 *  shallow compare a fresh array every read. */
const EMPTY_HISTORY: string[] = [];

export function useHistoryRecall(
  bound: string | null,
  currentSessionId: string | null,
  input: string,
) {
  // History recall — user messages from the active session, ordered
  // oldest-first to match TUI semantics. Built from
  // ``messageOrder[currentSessionId]`` filtered to user role. Resets
  // automatically whenever the session changes via the useEffect below.
  // Derived INSIDE the selector, compared shallowly: subscribing to the
  // whole row map re-rendered the composer on every single
  // streaming token, because each delta writes a fresh map
  // object. User turns don't change while the assistant
  // streams, so the shallow compare makes those deltas free here.
  const history = useSessionStore(
    useShallow((s): string[] => {
      const sid = bound ?? s.currentSessionId;
      const order = sid ? s.messageOrder[sid] : undefined;
      if (!order) return EMPTY_HISTORY;
      const out: string[] = [];
      const rows = selectViewRows(sid)(s);
      for (const id of order) {
        const m = rows[id];
        if (!m || m.role !== "user" || typeof m.content !== "string") continue;
        const typed = recallText(m.content);
        // Skip giant messages — recalling them into the textarea
        // would bloat the persisted draft (the per-keystroke write
        // to ``composerDrafts``) and is rarely useful: long messages
        // are typically expanded pastes, not commands the user wants
        // to step back through with ↑/↓.
        if (typed && typed.length <= HISTORY_RECALL_MAX) out.push(typed);
      }
      return out;
    }),
  );
  const owner = bound ?? currentSessionId;
  const [recall, setRecall] = useState({ owner, index: -1, draft: "", value: "" });
  const historyIndex = recall.owner === owner && recall.value === input ? recall.index : -1;
  const setHistoryIndex = useCallback((index: number) => {
    setRecall(previous => ({ ...previous, owner, index }));
  }, [owner]);
  useEffect(() => {
    if (recall.owner === owner && recall.index >= 0 && recall.value !== input) {
      setRecall(previous => ({ ...previous, index: -1, draft: "" }));
    }
  }, [owner, input, recall]);
  const current = useRef({ owner, input });
  current.current = { owner, input };
  const caretFrame = useRef<number | null>(null);
  useEffect(() => {
    setRecall({ owner, index: -1, draft: "", value: "" });
    return () => {
      if (caretFrame.current !== null) cancelAnimationFrame(caretFrame.current);
    };
  }, [owner]);

  /** ↑ handler. Returns true when it consumed the key. */
  function recallPrevious(
    e: React.KeyboardEvent<HTMLTextAreaElement>,
    input: string,
    setInput: (next: string) => void,
  ) {
    const ta = e.currentTarget;
    // Enter recall mode when the draft is empty, or the caret is on the
    // first visual line and nothing is selected. Once recall mode is active
    // (historyIndex >= 0) ↑ keeps stepping back regardless of caret position.
    const onFirstLine = !input || caretOnFirstVisualLine(ta, input);
    if (ta.selectionStart !== ta.selectionEnd || history.length === 0 || (historyIndex < 0 && !onFirstLine)) return false;
    e.preventDefault();
    const next = historyIndex < 0
      ? history.length - 1
      : Math.max(0, historyIndex - 1);
    setRecall({ owner, index: next, draft: historyIndex < 0 ? input : recall.draft, value: history[next] ?? "" });
    setInput(history[next] ?? "");
    // Move caret to end so the next ↑ keeps recalling instead of
    // moving inside the freshly-loaded text.
    if (caretFrame.current !== null) cancelAnimationFrame(caretFrame.current);
    caretFrame.current = requestAnimationFrame(() => {
      caretFrame.current = null;
      const v = history[next] ?? "";
      if (current.current.owner === owner && current.current.input === v) {
        ta.setSelectionRange(v.length, v.length);
      }
    });
    return true;
  }

  /** ↓ handler — only meaningful once recall mode is engaged. */
  function recallNext(setInput: (next: string) => void) {
    const next = historyIndex + 1;
    if (next >= history.length) {
      setHistoryIndex(-1);
      setInput(recall.draft);
    } else {
      setRecall({ ...recall, index: next, value: history[next] ?? "" });
      setInput(history[next] ?? "");
    }
  }

  return { history, historyIndex, setHistoryIndex, recallPrevious, recallNext };
}

/** A sent user message as it was typed: the attachment markers prepended at
 *  send are dropped (the files are not attached again by recalling it). */
export function recallText(content: string): string {
  if (!content.includes("[attach")) return content.trim();
  return extractAttachmentMentions(content).text.trim();
}

/** The caret is on the first line the user sees: before the first newline,
 *  and, when the field can report coordinates, on the same row as the start
 *  of the text (a long paragraph wraps onto several rows). */
export function caretOnFirstVisualLine(
  field: Pick<HTMLTextAreaElement, "selectionStart"> & {
    coordsAt?: (pos: number) => { top: number; bottom: number } | null;
  },
  value: string,
): boolean {
  const caret = field.selectionStart;
  const firstNewline = value.indexOf("\n");
  if (caret > (firstNewline < 0 ? value.length : firstNewline)) return false;
  const start = field.coordsAt?.(0);
  const at = field.coordsAt?.(caret);
  if (!start || !at) return true;
  // Same row: the caret's top is above the first row's bottom.
  return at.top < start.bottom - 1;
}
