"use client";

/**
 * Per-session state: one Zustand store instance per session, reached through
 * a React provider.
 *
 * The global `useSessionStore` holds what is genuinely shared — the session
 * list, every loaded message, WS status, the right dock. What it must NOT
 * hold is state that describes *one* session as a single value. A split view
 * renders two composers and one `composerInput` field cannot serve both. The
 * old shape mirrored the focused session's draft/settings/run-state into
 * global fields, and every consumer carried a
 * `bound === null ? global : keyed[bound]` ternary as the workaround.
 *
 * Here each session owns a store instance instead. A component asks for "my
 * draft" and the enclosing `SessionScopeProvider` decides whose. There is no
 * unbound path: the single-session shell wraps its composer too, so the
 * fallback branch does not exist and cannot be got wrong.
 *
 * Instances live in a module-level registry (`./session-scope-registry`) and
 * survive pane unmount — tabbing away and back keeps the draft you were
 * typing. Durable state still belongs to the global store's keyed maps, which
 * own localStorage and the tab-transfer wire format; scope writes go through
 * to them.
 */
import { createContext, useContext, useRef } from "react";
import { useStore, type StoreApi } from "zustand";

import { useSessionStore } from "./index";
import {
  DEFAULT_SCOPE_SETTINGS,
  getSessionStore,
  type SessionScopeState,
} from "./session-scope-registry";

export {
  dropSessionStore,
  getSessionStore,
  installScopeWriteThrough,
  pushToSessionStore,
  type SessionScopeState,
} from "./session-scope-registry";

const SessionScopeContext = createContext<StoreApi<SessionScopeState> | null>(
  null,
);

/** Declares "everything below belongs to session `sid`". */
export function SessionScopeProvider({
  sid,
  children,
}: {
  sid: string;
  children: React.ReactNode;
}) {
  // Keyed on sid: a provider whose session changes swaps stores rather than
  // keeping the previous one alive under a new label.
  const ref = useRef<{ sid: string; store: StoreApi<SessionScopeState> } | null>(
    null,
  );
  if (ref.current?.sid !== sid) {
    const global = useSessionStore.getState();
    ref.current = {
      sid,
      store: getSessionStore(sid, {
        draft: global.composerDrafts[sid] ?? "",
        settings: global.composerSettingsBySession[sid]
          ?? { ...DEFAULT_SCOPE_SETTINGS },
        running: global.runningTasks[sid] ?? null,
      }),
    };
  }
  return (
    <SessionScopeContext.Provider value={ref.current.store}>
      {children}
    </SessionScopeContext.Provider>
  );
}

/**
 * Subscribe to this subtree's session state.
 *
 * Throws when no provider encloses the component. That is deliberate: a
 * silent fallback to "the focused session" is the exact bug this layer exists
 * to remove, and it would surface only in a split view after a specific
 * interaction sequence. Failing on first render makes a missing wrap obvious
 * while it is still being written.
 */
export function useSessionScope<T>(selector: (s: SessionScopeState) => T): T {
  const store = useContext(SessionScopeContext);
  if (!store) {
    throw new Error(
      "useSessionScope must be used inside a <SessionScopeProvider>. "
        + "Wrap the pane (or the single-session shell) in one.",
    );
  }
  return useStore(store, selector);
}

/** This subtree's session id. */
export function useScopedSessionId(): string {
  return useSessionScope((s) => s.sid);
}
