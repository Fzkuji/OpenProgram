"use client";

/**
 * Tools / Web-Search toggle state for the composer's plus menu.
 *
 * Per-session now: backed by the store's ``composerSettings`` (keyed by
 * sessionId, persisted to localStorage), so each chat keeps its own
 * tool picks and they survive refresh + session switch. (Used to be two
 * global localStorage keys shared by every session.)
 */

import { useCallback } from "react";

import { useSessionStore } from "@/lib/session-store";

export interface ToolsTogglesHook {
  tools: boolean;
  webSearch: boolean;
  toggleTools: () => void;
  toggleWebSearch: () => void;
}

export function useToolsToggles(): ToolsTogglesHook {
  const tools = useSessionStore((s) => s.composerSettings.tools);
  const webSearch = useSessionStore((s) => s.composerSettings.webSearch);
  const setComposerSettings = useSessionStore((s) => s.setComposerSettings);

  const toggleTools = useCallback(
    () => setComposerSettings({ tools: !useSessionStore.getState().composerSettings.tools }),
    [setComposerSettings],
  );
  const toggleWebSearch = useCallback(
    () => setComposerSettings({ webSearch: !useSessionStore.getState().composerSettings.webSearch }),
    [setComposerSettings],
  );

  return { tools, webSearch, toggleTools, toggleWebSearch };
}
