/**
 * `@filename` mention state + behavior for the composer.
 *
 * Owns: the parsed `@` token under the caret, the debounced
 * /api/file-search query result, the keyboard-driven highlight index,
 * the popover position, and the picker that inserts the chosen path
 * back into the draft.
 *
 * Pulled out of composer/index.tsx so the main file isn't carrying
 * six pieces of state + two effects that only matter to one feature.
 * The composer wires the returned handlers into ``onKeyDown`` and
 * passes ``items / position / selectedIndex / onPick`` straight to
 * ``<FileMenu />``.
 */
"use client";

import React, { useCallback, useEffect, useLayoutEffect, useState } from "react";

import { findAtToken } from "./at-mention";
import type { FileMatch } from "./file-menu";

/** Debounce delay (ms) before firing /api/file-search after the
 *  partial token changes. 100 ms keeps typing-driven flicker low. */
const SEARCH_DEBOUNCE_MS = 100;
const SEARCH_LIMIT = 12;

interface UseFileMentionArgs {
  input: string;
  setInput: (s: string) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
}

export interface UseFileMentionResult {
  /** Token under the caret, or null when not in an @ context. */
  atToken: ReturnType<typeof findAtToken>;
  /** Current caret index in the textarea — kept in sync by the
   *  composer's keyup/click handlers. */
  caretPos: number;
  setCaretPos: (n: number) => void;
  /** Latest matches from the file-search endpoint. Empty when no
   *  query is active. */
  fileMatches: FileMatch[];
  /** Index of the keyboard-highlighted item in ``fileMatches``. */
  fileMenuIndex: number;
  setFileMenuIndex: (n: number | ((prev: number) => number)) => void;
  /** True while a debounced fetch is in flight. */
  fileMenuLoading: boolean;
  /** Where to render the popover (anchored to the textarea), or null
   *  when not shown. */
  fileMenuPos: { left: number; top: number } | null;
  /** Pick a match — replaces the `@partial` slice with `@path[/]`. */
  pickFile: (item: FileMatch) => void;
  /** Programmatically close the menu (Escape, blur, etc.). */
  closeMenu: () => void;
}

export function useFileMention({
  input,
  setInput,
  textareaRef,
}: UseFileMentionArgs): UseFileMentionResult {
  const [caretPos, setCaretPos] = useState(0);
  const atToken = React.useMemo(
    () => findAtToken(input, caretPos),
    [input, caretPos],
  );
  const [fileMatches, setFileMatches] = useState<FileMatch[]>([]);
  const [fileMenuIndex, setFileMenuIndex] = useState(0);
  const [fileMenuLoading, setFileMenuLoading] = useState(false);
  const [fileMenuPos, setFileMenuPos] =
    useState<{ left: number; top: number } | null>(null);

  // Debounced fetch. Each token change starts a fresh timer; the
  // cleanup cancels the prior one so a fast typer doesn't run N
  // overlapping requests. ``cancelled`` is a per-effect closure flag
  // so a late response from a stale token can't clobber a newer one.
  useEffect(() => {
    if (!atToken) {
      setFileMatches([]);
      setFileMenuPos(null);
      return;
    }
    setFileMenuLoading(true);
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const q = new URLSearchParams({
          q: atToken.partial,
          limit: String(SEARCH_LIMIT),
        });
        const r = await fetch(`/api/file-search?${q.toString()}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as { matches: FileMatch[] };
        if (!cancelled) {
          setFileMatches(data.matches || []);
          setFileMenuIndex(0);
        }
      } catch {
        if (!cancelled) setFileMatches([]);
      } finally {
        if (!cancelled) setFileMenuLoading(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [atToken?.partial, atToken?.start]);

  // Pin the popover to the textarea's top-left. Caret-precise
  // anchoring would be nicer but textarea has no native caret rect;
  // a slight offset off the textarea's left edge reads fine in v1.
  useLayoutEffect(() => {
    if (!atToken) return;
    const ta = textareaRef.current;
    if (!ta) return;
    const rect = ta.getBoundingClientRect();
    setFileMenuPos({
      left: rect.left + 8,
      top: Math.max(8, rect.top - 8),
    });
  }, [atToken, fileMatches.length, textareaRef]);

  const pickFile = useCallback(
    (item: FileMatch) => {
      if (!atToken) return;
      const insert = item.is_dir ? item.path + "/" : item.path + " ";
      const next =
        input.slice(0, atToken.start)
        + "@" + insert
        + input.slice(caretPos);
      setInput(next);
      const newCaret = atToken.start + 1 + insert.length;
      requestAnimationFrame(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.setSelectionRange(newCaret, newCaret);
        setCaretPos(newCaret);
      });
      setFileMatches([]);
      setFileMenuPos(null);
    },
    [atToken, caretPos, input, setInput, textareaRef],
  );

  const closeMenu = useCallback(() => {
    setFileMatches([]);
    setFileMenuPos(null);
  }, []);

  return {
    atToken,
    caretPos,
    setCaretPos,
    fileMatches,
    fileMenuIndex,
    setFileMenuIndex,
    fileMenuLoading,
    fileMenuPos,
    pickFile,
    closeMenu,
  };
}
