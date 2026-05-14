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

function readPersistedBool(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
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
  const [tools, setTools] = useState(false);
  const [webSearch, setWebSearch] = useState(false);

  useEffect(() => {
    setTools(readPersistedBool(TOOLS_KEY));
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
