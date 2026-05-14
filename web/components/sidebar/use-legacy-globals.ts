"use client";

/**
 * Thin React hook that exposes legacy window globals to React components.
 *
 * The legacy chat init script writes:
 *   - `window.conversations`     — id → { id, title, created_at, channel, ... }
 *   - `window.availableFunctions` — AgenticFunction[]
 *   - `window.programsMeta`      — { favorites: string[], folders: ... }
 *
 * None of these go through the zustand store, so we poll at a low rate
 * (250ms — plenty for human-perceivable updates and cheap because each
 * tick is just an object-identity compare on three refs). If those
 * globals later get rewritten through the store, switch this hook to
 * use `useSyncExternalStore` against the store instead.
 */

import { useEffect, useState } from "react";
import type { AgenticFunction } from "@/lib/session-store";

interface LegacyConv {
  id: string;
  title?: string;
  created_at?: number;
  channel?: string | null;
  account_id?: string | null;
  preview?: string | null;
  has_session?: boolean;
}

interface ProgramsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
}

interface LegacySnapshot {
  conversations: Record<string, LegacyConv>;
  availableFunctions: AgenticFunction[];
  programsMeta: ProgramsMeta;
  sidebarOpen: boolean;
}

const EMPTY_META: ProgramsMeta = { favorites: [], folders: {} };
const EMPTY_FNS: AgenticFunction[] = [];
const EMPTY_CONVS: Record<string, LegacyConv> = {};

function snapshot(): LegacySnapshot {
  const w = window as unknown as {
    conversations?: Record<string, LegacyConv>;
    availableFunctions?: AgenticFunction[];
    programsMeta?: ProgramsMeta;
    sidebarOpen?: boolean;
  };
  return {
    conversations: w.conversations ?? EMPTY_CONVS,
    availableFunctions: w.availableFunctions ?? EMPTY_FNS,
    programsMeta: w.programsMeta ?? EMPTY_META,
    sidebarOpen: w.sidebarOpen ?? true,
  };
}

export function useLegacyGlobals(): LegacySnapshot {
  const [snap, setSnap] = useState<LegacySnapshot>(() =>
    typeof window === "undefined"
      ? {
          conversations: EMPTY_CONVS,
          availableFunctions: EMPTY_FNS,
          programsMeta: EMPTY_META,
          sidebarOpen: true,
        }
      : snapshot()
  );

  useEffect(() => {
    let prev = snap;
    const id = setInterval(() => {
      const next = snapshot();
      // Reference equality is enough — legacy code replaces these
      // globals wholesale (`conversations[id] = ...` keeps the same
      // ref but `availableFunctions = await fetch(...)` swaps it).
      // For the conversations object the ref stays stable, so we also
      // compare the Object.keys length + currentSessionId to catch
      // adds / deletes / active-flip. Title changes inside an entry
      // do mutate the same object, which means a streaming title
      // update may not trigger a re-render until something else
      // changes — acceptable for this slice (title is set at
      // conversation creation, not per-token).
      const convKeys = Object.keys(next.conversations).length;
      const prevConvKeys = Object.keys(prev.conversations).length;
      const cidNow =
        (window as unknown as { currentSessionId?: string | null })
          .currentSessionId ?? null;
      const cidPrev =
        (prev as unknown as { _cid?: string | null })._cid ?? null;
      if (
        next.conversations !== prev.conversations ||
        next.availableFunctions !== prev.availableFunctions ||
        next.programsMeta !== prev.programsMeta ||
        next.sidebarOpen !== prev.sidebarOpen ||
        convKeys !== prevConvKeys ||
        cidNow !== cidPrev
      ) {
        const stamped = next as LegacySnapshot & { _cid?: string | null };
        stamped._cid = cidNow;
        prev = stamped;
        setSnap(stamped);
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
