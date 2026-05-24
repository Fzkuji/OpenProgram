"use client";

/**
 * Slash-menu state machine for the composer.
 *
 * Triggered when the textarea value starts with `/` and contains no
 * space (e.g. user is typing `/he` to filter for `/help`). Filters
 * `SLASH_COMMANDS` by prefix. `runCommand` dispatches the matching
 * command's `run(rest, ctx)` if the input is a full slash command.
 *
 * The matching close animation is debounced by `ANIM_MS` so the
 * filter list can fade out before unmount; `openMenu` cancels any
 * pending close timer so re-typing while closing doesn't drop the
 * incoming filter.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";

import { useSessionStore } from "@/lib/session-store";

import { SLASH_COMMANDS, type SlashCommand, type SlashContext } from "./slash-commands";

const ANIM_MS = 380;

interface UseSlashMenuArgs {
  input: string;
  textareaRef: RefObject<HTMLTextAreaElement>;
  send: (payload: unknown) => boolean;
}

export interface SlashMenuHook {
  query: string | null;
  closing: boolean;
  matches: SlashCommand[];
  visible: boolean;
  /** Index of the keyboard-highlighted command in `matches`. */
  activeIndex: number;
  /** Move the highlight by `delta`, wrapping around the list. */
  move: (delta: number) => void;
  close: () => void;
  /** Tell the menu whether the composer textarea is focused — the menu
   *  is only shown while focused, and re-appears on re-focus if the
   *  input still holds a `/…` query. */
  setFocused: (focused: boolean) => void;
  runCommand: (text: string) => boolean;
}

export function useSlashMenu({ input, textareaRef, send }: UseSlashMenuArgs): SlashMenuHook {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const setComposerInput = useSessionStore((s) => s.setComposerInput);

  const [query, setQuery] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [focused, setFocused] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const open = useCallback((q: string) => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setClosing(false);
    setQuery(q);
    document.body.classList.add("slash-menu-open");
  }, []);

  const close = useCallback(() => {
    setClosing(true);
    document.body.classList.remove("slash-menu-open");
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setQuery(null);
      setClosing(false);
      closeTimerRef.current = null;
    }, ANIM_MS);
  }, []);

  // The menu is shown only while the textarea is focused AND the input
  // holds a bare `/…` query. Re-runs on focus changes too, so clicking
  // back into a textarea that still contains `/foo` re-opens it, and
  // clicking away closes it.
  useEffect(() => {
    const v = input.trim();
    if (focused && v.startsWith("/") && !v.includes(" ")) {
      open(v.toLowerCase());
    } else if (query !== null) {
      close();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, focused]);

  const matches = useMemo<SlashCommand[]>(() => {
    if (query === null) return [];
    return SLASH_COMMANDS.filter((c) => c.name.toLowerCase().startsWith(query));
  }, [query]);

  // Keyboard highlight — starts at -1 ("no highlight yet"). The first
  // ArrowDown / ArrowUp lights up an item; before that, nothing in
  // the menu is highlighted, so the user's eye isn't drawn to a
  // command they didn't pick. Filter changes also reset to -1 — the
  // previously-highlighted index would point at a different command
  // after the filter, which is confusing.
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  useEffect(() => {
    setActiveIndex(-1);
  }, [query]);

  const move = useCallback(
    (delta: number) => {
      setActiveIndex((i) => {
        const n = matches.length;
        if (n === 0) return -1;
        // First move from "no highlight" lands on either the top or
        // bottom item depending on direction, instead of wrapping
        // from -1 (which would be confusing).
        if (i < 0) return delta > 0 ? 0 : n - 1;
        return (i + delta + n) % n;
      });
    },
    [matches.length],
  );

  const slashContext = useMemo<SlashContext>(
    () => ({
      sessionId: currentSessionId,
      send,
      newConversation: () => {
        setCurrentConv(null);
        setComposerInput("");
      },
      setInput: (value, focus) => {
        setComposerInput(value);
        if (focus) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
      },
    }),
    [currentSessionId, send, setCurrentConv, setComposerInput, textareaRef],
  );

  const runCommand = useCallback(
    (text: string): boolean => {
      if (!text.startsWith("/")) return false;
      const space = text.indexOf(" ");
      const cmdName = space === -1 ? text : text.slice(0, space);
      const rest = space === -1 ? "" : text.slice(space + 1);
      const cmd = SLASH_COMMANDS.find((c) => c.name === cmdName);
      if (!cmd) return false;
      cmd.run(rest, slashContext);
      return true;
    },
    [slashContext],
  );

  return {
    query,
    closing,
    matches,
    visible: query !== null && matches.length > 0,
    activeIndex: Math.min(activeIndex, Math.max(0, matches.length - 1)),
    move,
    close,
    setFocused,
    runCommand,
  };
}
