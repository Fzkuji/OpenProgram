"use client";

/**
 * Hook that exposes functions and UI state to React components.
 *
 * `availableFunctions` and `programsMeta` are now read from the zustand
 * `useFunctions` store (pushed by the `functions_list` WS handler in
 * use-ws.ts). `sidebarOpen` lives on the shared `runtimeState` singleton,
 * which non-React modules mutate directly, so it is polled.
 */

import { useEffect, useState } from "react";
import type { AgenticFunction } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import { useFunctions } from "@/lib/state/functions-store";
import { runtimeState } from "@/lib/runtime-bridge/state";

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
      const cur = runtimeState.sidebarOpen ?? true;
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

/** Subscribe to just currentSessionId — reads from the React store, no
 *  polling. */
export function useCurrentSessionId(): string | null {
  return useSessionStore((s) => s.currentSessionId);
}
