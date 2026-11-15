"use client";

/**
 * Web Search toggle state for the composer's plus menu.
 *
 * Per-session: backed by the enclosing session scope's ``settings`` (keyed
 * by sessionId, persisted to localStorage), so each chat keeps its own
 * pick and it survives refresh + session switch. Which tools the agent
 * has comes from its Agent configuration, not from the composer.
 */

import { useCallback } from "react";

import {
  useBoundComposerSettings,
  useBoundSetComposerSettings,
} from "../../state/use-composer-settings";

export interface ToolsTogglesHook {
  webSearch: boolean;
  toggleWebSearch: () => void;
}

export function useToolsToggles(): ToolsTogglesHook {
  // Bound to this composer subtree's session — the focused one unless a
  // split-view pane provided its own session scope.
  const settings = useBoundComposerSettings();
  const setComposerSettings = useBoundSetComposerSettings();
  const { webSearch } = settings;

  const toggleWebSearch = useCallback(
    () => setComposerSettings({ webSearch: !webSearch }),
    [webSearch, setComposerSettings],
  );

  return { webSearch, toggleWebSearch };
}
