"use client";

/**
 * Tools / Web-Search toggle state for the composer's plus menu.
 *
 * Persisted to localStorage so the user's per-turn picks survive page
 * reloads. The chat send envelope reads these via the `toggle…`
 * setters' returned bool — Composer passes the current values to
 * `wsSend`.
 */

import { useCallback, useEffect, useState } from "react";

const TOOLS_KEY = "agentic_tools_enabled";
const WEB_KEY = "agentic_web_search_enabled";

function readPersistedBool(key: string, fallback = false): boolean {
  try {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    return v === "1";
  } catch {
    return fallback;
  }
}

function persistBool(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export interface ToolsTogglesHook {
  tools: boolean;
  webSearch: boolean;
  toggleTools: () => void;
  toggleWebSearch: () => void;
}

export function useToolsToggles(): ToolsTogglesHook {
  // Tools default ON: a fresh install that never touched the wrench
  // toggle used to send `tools: false` on every turn, so new users got
  // a model with an empty tools array ("I can't access your files").
  const [tools, setTools] = useState(true);
  const [webSearch, setWebSearch] = useState(false);

  useEffect(() => {
    setTools(readPersistedBool(TOOLS_KEY, true));
    setWebSearch(readPersistedBool(WEB_KEY));
  }, []);

  const toggleTools = useCallback(() => {
    setTools((prev) => {
      const next = !prev;
      persistBool(TOOLS_KEY, next);
      return next;
    });
  }, []);

  const toggleWebSearch = useCallback(() => {
    setWebSearch((prev) => {
      const next = !prev;
      persistBool(WEB_KEY, next);
      return next;
    });
  }, []);

  return { tools, webSearch, toggleTools, toggleWebSearch };
}
