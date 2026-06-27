"use client";

/**
 * Hook that exposes functions and UI state to React components.
 *
 * `availableFunctions` and `programsMeta` are now read from the zustand
 * `useFunctions` store (pushed by the `functions_list` WS handler in
 * use-ws.ts). `sidebarOpen` is still read from `window` since it's
 * toggled by legacy UI code.
 */

import { useEffect, useState } from "react";
import type { AgenticFunction } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import { useFunctions } from "@/lib/functions-store";

interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}

interface WindowGlobalsState {
  availableFunctions: AgenticFunction[];
  programsMeta: FunctionsMeta;
  sidebarOpen: boolean;
}

const EMPTY_META: FunctionsMeta = { favorites: [], folders: {}, icons: {} };

export function useWindowGlobals(): WindowGlobalsState {
  const functions = useFunctions((s) => s.functions);
  const meta = useFunctions((s) => s.meta);

  const [sidebarOpen, setSidebarOpen] = useState(true);

  useEffect(() => {
    const id = setInterval(() => {
      const cur =
        (window as unknown as { sidebarOpen?: boolean }).sidebarOpen ?? true;
      setSidebarOpen((prev) => (prev === cur ? prev : cur));
    }, 250);
    return () => clearInterval(id);
  }, []);

  return {
    availableFunctions: functions as AgenticFunction[],
    programsMeta: (meta as FunctionsMeta) ?? EMPTY_META,
    sidebarOpen,
  };
}

/** Subscribe to just currentSessionId — now reads from the React store
 *  instead of polling window.currentSessionId at 250ms. */
export function useCurrentSessionId(): string | null {
  return useSessionStore((s) => s.currentSessionId);
}
