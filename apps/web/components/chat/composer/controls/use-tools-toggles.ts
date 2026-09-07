"use client";

/**
 * Tools / Web-Search toggle state for the composer's plus menu.
 *
 * Per-session now: backed by the enclosing session scope's ``settings`` (keyed by
 * sessionId, persisted to localStorage), so each chat keeps its own
 * tool picks and they survive refresh + session switch. (Used to be two
 * global localStorage keys shared by every session.)
 */

import { useCallback } from "react";

import {
  useBoundComposerSettings,
  useBoundSetComposerSettings,
} from "../state/use-composer-settings";

export interface ToolsTogglesHook {
  tools: boolean;
  webSearch: boolean;
  toggleTools: () => void;
  toggleWebSearch: () => void;
}

export function useToolsToggles(): ToolsTogglesHook {
  // Bound to this composer subtree's session — the focused one unless a
  // split-view pane provided its own session scope.
  const settings = useBoundComposerSettings();
  const setComposerSettings = useBoundSetComposerSettings();
  const { tools, webSearch } = settings;

  const toggleTools = useCallback(
    () => setComposerSettings({ tools: !tools }),
    [tools, setComposerSettings],
  );
  const toggleWebSearch = useCallback(
    () => setComposerSettings({ webSearch: !webSearch }),
    [webSearch, setComposerSettings],
  );

  return { tools, webSearch, toggleTools, toggleWebSearch };
}
