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
  pinned?: boolean;
  archived?: boolean;
  group?: string;
  status?: string;
  unread?: boolean;
}

/**
 * Cheap content signature of the conversations map over the fields the
 * sidebar actually renders. The legacy code mutates `window.conversations`
 * **in place** (same object ref) when the WS `sessions_list` populates it
 * or a title / pin / archive / group flips — so a pure ref / key-count
 * compare misses those updates (a freshly-populated list stays "empty",
 * a rename never shows). Hashing the rendered fields catches all of it
 * at a few µs per tick for a normal sidebar.
 */
function convsSignature(convs: Record<string, LegacyConv>): string {
  const ids = Object.keys(convs);
  let sig = String(ids.length);
  for (const id of ids) {
    const c = convs[id];
    sig +=
      "|" + id + ":" + (c.title || "") + ":" + (c.preview || "") +
      ":" + (c.pinned ? 1 : 0) + (c.archived ? 1 : 0) + ":" + (c.group || "") +
      ":" + (c.status || "") + (c.unread ? 1 : 0);
  }
  return sig;
}

interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}

interface WindowGlobalsState {
  conversations: Record<string, LegacyConv>;
  availableFunctions: AgenticFunction[];
  programsMeta: FunctionsMeta;
  sidebarOpen: boolean;
}

const EMPTY_META: FunctionsMeta = { favorites: [], folders: {}, icons: {} };
const EMPTY_FNS: AgenticFunction[] = [];
const EMPTY_CONVS: Record<string, LegacyConv> = {};

function capture(): WindowGlobalsState {
  const w = window as unknown as {
    conversations?: Record<string, LegacyConv>;
    availableFunctions?: AgenticFunction[];
    programsMeta?: FunctionsMeta;
    sidebarOpen?: boolean;
  };
  return {
    conversations: w.conversations ?? EMPTY_CONVS,
    availableFunctions: w.availableFunctions ?? EMPTY_FNS,
    programsMeta: w.programsMeta ?? EMPTY_META,
    sidebarOpen: w.sidebarOpen ?? true,
  };
}

export function useWindowGlobals(): WindowGlobalsState {
  const [snap, setSnap] = useState<WindowGlobalsState>(() =>
    typeof window === "undefined"
      ? {
          conversations: EMPTY_CONVS,
          availableFunctions: EMPTY_FNS,
          programsMeta: EMPTY_META,
          sidebarOpen: true,
        }
      : capture()
  );

  useEffect(() => {
    let prev = snap;
    let prevSig = convsSignature(prev.conversations);
    const id = setInterval(() => {
      const next = capture();
      // `availableFunctions` / `programsMeta` get swapped wholesale, so
      // a ref compare catches them. `conversations` is mutated in place
      // (same ref) on WS populate / rename / pin / archive / group, so
      // a ref or key-count compare misses those — hash the rendered
      // fields instead (see convsSignature).
      const nextSig = convsSignature(next.conversations);
      const cidNow =
        (window as unknown as { currentSessionId?: string | null })
          .currentSessionId ?? null;
      const cidPrev =
        (prev as unknown as { _cid?: string | null })._cid ?? null;
      if (
        nextSig !== prevSig ||
        next.availableFunctions !== prev.availableFunctions ||
        next.programsMeta !== prev.programsMeta ||
        next.sidebarOpen !== prev.sidebarOpen ||
        cidNow !== cidPrev
      ) {
        const stamped = next as WindowGlobalsState & { _cid?: string | null };
        stamped._cid = cidNow;
        prev = stamped;
        prevSig = nextSig;
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
