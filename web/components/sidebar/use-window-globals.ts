"use client";

/**
 * Thin React hook that exposes legacy window globals to React components.
 *
 * The legacy chat init script writes:
 *   - `window.availableFunctions` — AgenticFunction[]
 *   - `window.programsMeta`      — { favorites: string[], folders: ... }
 *
 * Neither goes through the zustand store, so we poll at a low rate (250ms —
 * plenty for human-perceivable updates and cheap because each tick is just
 * an object-identity compare on a couple of refs). The conversation list
 * used to be polled here too; it now lives in the store (store.conversations,
 * fed by conv-store-mirror), so the sidebar subscribes to that directly and
 * this hook no longer touches `window.conversations`. As the remaining
 * globals migrate, drop them here as well.
 */

import { useEffect, useState } from "react";
import type { AgenticFunction } from "@/lib/session-store";

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
const EMPTY_FNS: AgenticFunction[] = [];

function capture(): WindowGlobalsState {
  const w = window as unknown as {
    availableFunctions?: AgenticFunction[];
    programsMeta?: FunctionsMeta;
    sidebarOpen?: boolean;
  };
  return {
    availableFunctions: w.availableFunctions ?? EMPTY_FNS,
    programsMeta: w.programsMeta ?? EMPTY_META,
    sidebarOpen: w.sidebarOpen ?? true,
  };
}

export function useWindowGlobals(): WindowGlobalsState {
  const [snap, setSnap] = useState<WindowGlobalsState>(() =>
    typeof window === "undefined"
      ? {
          availableFunctions: EMPTY_FNS,
          programsMeta: EMPTY_META,
          sidebarOpen: true,
        }
      : capture()
  );

  useEffect(() => {
    let prev = snap;
    const id = setInterval(() => {
      const next = capture();
      // `availableFunctions` / `programsMeta` get swapped wholesale, so
      // a ref compare catches them; `sidebarOpen` is a primitive.
      if (
        next.availableFunctions !== prev.availableFunctions ||
        next.programsMeta !== prev.programsMeta ||
        next.sidebarOpen !== prev.sidebarOpen
      ) {
        prev = next;
        setSnap(next);
      }
    }, 250);
    return () => clearInterval(id);
  }, []);

  return snap;
}

/** Subscribe to just `window.currentSessionId`. */
export function useCurrentSessionId(): string | null {
  const [id, setId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return (
      (window as unknown as { currentSessionId?: string | null })
        .currentSessionId ?? null
    );
  });
  useEffect(() => {
    const t = setInterval(() => {
      const cur =
        (window as unknown as { currentSessionId?: string | null })
          .currentSessionId ?? null;
      setId((prev) => (prev === cur ? prev : cur));
    }, 250);
    return () => clearInterval(t);
  }, []);
  return id;
}
