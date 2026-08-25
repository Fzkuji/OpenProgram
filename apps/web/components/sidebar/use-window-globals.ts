"use client";

/**
 * Hook that exposes functions and UI state to React components.
 *
 * `availableFunctions` and `programsMeta` are read from the zustand
 * `useFunctions` store (pushed by the `functions_list` WS handler in
 * use-ws.ts). Sidebar open/closed is React state in `<Sidebar />`.
 */

import type { AgenticFunction } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import { useFunctions } from "@/lib/state/functions-store";

interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}

interface WindowGlobalsState {
  availableFunctions: AgenticFunction[];
  programsMeta: FunctionsMeta;
}

const EMPTY_META: FunctionsMeta = { favorites: [], folders: {}, icons: {} };

export function useWindowGlobals(): WindowGlobalsState {
  const functions = useFunctions((s) => s.functions);
  const meta = useFunctions((s) => s.meta);

  return {
    availableFunctions: functions as AgenticFunction[],
    programsMeta: (meta as FunctionsMeta) ?? EMPTY_META,
  };
}

/** Subscribe to just currentSessionId — reads from the React store, no
 *  polling. */
export function useCurrentSessionId(): string | null {
  return useSessionStore((s) => s.currentSessionId);
}
