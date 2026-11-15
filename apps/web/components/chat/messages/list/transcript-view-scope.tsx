"use client";

import { createContext, useContext } from "react";
import { useSessionStore } from "@/lib/session-store";
import { useTranscriptView, type TranscriptView } from "@/lib/prefs/transcript-view";

/** The row list a transcript row is drawn in: the conversation it belongs
 *  to and the view holding its rows. A row takes its session from here,
 *  never from the focused one, so a split pane judges its rows against its
 *  own conversation. */
export interface TranscriptScope {
  sessionId: string | null;
  viewKey: string | null;
  /** The expanded sub-agent view, drawn inside its step row. */
  embedded: boolean;
}

export const TranscriptScopeContext = createContext<TranscriptScope | null>(null);

/** A conversation shown as a chat with a bot: messages only (bot-view.ts).
 *  ``breaks`` holds the rows that open with the time. */
export const BotViewContext = createContext<{ breaks: ReadonlyMap<string, number> } | null>(null);

/** The conversation a row belongs to: the caller's, else its row list's.
 *  Only a component drawn outside any row list falls back to the focused one. */
export function useRowSessionId(override?: string | null): string | null {
  const scope = useContext(TranscriptScopeContext);
  const focused = useSessionStore((s) => s.currentSessionId);
  return override ?? scope?.sessionId ?? focused;
}

export function useTranscriptViewHere(): TranscriptView {
  return useTranscriptView(useRowSessionId());
}
