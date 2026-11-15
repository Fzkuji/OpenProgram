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

import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { findAtToken, mentionScopeParams, type MentionScope } from "./at-mention";
import type { FileMatch } from "./file-menu";
import { matchApps, matchTabs, mentionInsert, type FileMention, type MentionSources } from "./mention-sources";

/** Debounce delay (ms) before firing /api/file-search after the
 *  partial token changes. 100 ms keeps typing-driven flicker low. */
const SEARCH_DEBOUNCE_MS = 100;
const SEARCH_LIMIT = 12;

interface UseFileMentionArgs {
  input: string;
  setInput: (s: string) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  /** Open tabs and running apps listed above the files. */
  sources?: MentionSources;
  /** The chat whose working directory files are searched in. */
  scope?: MentionScope | null;
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
  fileMenuPos: { left: number; top: number; bottom?: number } | null;
  /** Pick a match — replaces the `@partial` slice with `@path[/]`. */
  pickFile: (item: FileMatch) => void;
  /** Programmatically close the menu (Escape, blur, etc.). */
  closeMenu: () => void;
}

export function useFileMention({
  input,
  setInput,
  textareaRef,
  sources,
  scope,
}: UseFileMentionArgs): UseFileMentionResult {
  const [caretPos, setCaretPos] = useState(0);
  const atToken = React.useMemo(
    () => findAtToken(input, caretPos),
    [input, caretPos],
  );
  const tokenKey = atToken ? JSON.stringify([atToken.start, atToken.partial]) : null;
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const activeToken = tokenKey !== null && tokenKey !== dismissedKey;
  const [result, setResult] = useState<{ key: string; matches: FileMatch[]; loading: boolean } | null>(null);
  const fileMatches = activeToken && result?.key === tokenKey ? result.matches : [];
  const fileMenuLoading = activeToken && (result?.key !== tokenKey || result.loading);
  const [fileMenuIndex, setFileMenuIndex] = useState(0);
  const [fileMenuPos, setFileMenuPos] =
    useState<{ left: number; top: number; bottom?: number } | null>(null);

  // Debounced fetch. Each token change starts a fresh timer; the
  // cleanup cancels the prior one so a fast typer doesn't run N
  // overlapping requests. ``cancelled`` is a per-effect closure flag
  // so a late response from a stale token can't clobber a newer one.
  const searchToken = useEffectEvent(() => {
    if (dismissedKey !== null && dismissedKey !== tokenKey) setDismissedKey(null);
    if (!atToken) {
      setDismissedKey(null);
      setFileMenuPos(null);
      return;
    }
    if (!activeToken || tokenKey === null) return;
    setResult({ key: tokenKey, matches: [], loading: true });
    let cancelled = false;
    const t = setTimeout(async () => {
      const searchFiles = async (): Promise<FileMention[]> => {
        try {
          const q = mentionScopeParams(scope, new URLSearchParams({
            q: atToken.partial,
            limit: String(SEARCH_LIMIT),
          }));
          const r = await fetch(`/api/file-search?${q.toString()}`);
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const data = (await r.json()) as { matches: FileMention[] };
          return data.matches || [];
        } catch {
          return [];
        }
      };
      const [files, apps] = await Promise.all([
        searchFiles(),
        sources ? sources.apps().catch(() => []) : Promise.resolve([]),
      ]);
      if (cancelled) return;
      const matches: FileMatch[] = [
        ...matchTabs(sources?.tabs() ?? [], atToken.partial),
        ...matchApps(apps, atToken.partial),
        ...files,
      ];
      setResult({ key: tokenKey, matches, loading: false });
      setFileMenuIndex(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  });
  useEffect(() => searchToken(), [tokenKey, activeToken]);

  // Open the popover upward from the field, starting at the "@" when the
  // field can report caret coordinates (the markdown editor can).
  useLayoutEffect(() => {
    if (!atToken || !activeToken) { setFileMenuPos(null); return; }
    const ta = textareaRef.current as (HTMLTextAreaElement & {
      coordsAt?: (pos: number) => { left: number; top: number } | null;
    }) | null;
    if (!ta) return;
    const rect = ta.getBoundingClientRect();
    const at = ta.coordsAt?.(atToken.start);
    setFileMenuPos({
      left: Math.max(8, Math.min(at?.left ?? rect.left + 8, window.innerWidth - 268)),
      top: 0,
      bottom: Math.max(8, window.innerHeight - rect.top + 6),
    });
  }, [atToken, activeToken, fileMatches.length, textareaRef]);

  const pickFile = useCallback(
    (item: FileMatch) => {
      if (!atToken || !activeToken || result?.key !== tokenKey || result.loading) return;
      const insert = mentionInsert(item);
      const next =
        input.slice(0, atToken.start)
        + insert
        + input.slice(caretPos);
      setInput(next);
      const newCaret = atToken.start + insert.length;
      requestAnimationFrame(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.setSelectionRange(newCaret, newCaret);
        setCaretPos(newCaret);
      });
      setDismissedKey(tokenKey);
      setFileMenuPos(null);
    },
    [atToken, activeToken, result, tokenKey, caretPos, input, setInput, textareaRef],
  );

  const closeMenu = useCallback(() => {
    setDismissedKey(tokenKey);
    setFileMenuPos(null);
  }, [tokenKey]);

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
