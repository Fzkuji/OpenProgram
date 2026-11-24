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
import { createStore, useStore, type StoreApi } from "zustand";

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
 * With no provider around the component it reads an empty scope (no
 * session), never "the focused session": that fallback is the exact bug this
 * layer exists to remove. A missing wrap is reported once in the console
 * instead of throwing, so one misplaced component cannot take the page down.
 */
export function useSessionScope<T>(selector: (s: SessionScopeState) => T): T {
  const store = useContext(SessionScopeContext);
  if (!store) warnMissingScope();
  return useStore(store ?? EMPTY_SCOPE_STORE, selector);
}

const missingScopeWarning = { shown: false };
function warnMissingScope(): void {
  if (missingScopeWarning.shown) return;
  missingScopeWarning.shown = true;
  console.warn(
    "useSessionScope used outside a <SessionScopeProvider>; reading an empty scope. "
      + "Wrap the pane (or the single-session shell) in one.",
  );
}

/** This subtree's session id. */
export function useScopedSessionId(): string {
  return useSessionScope((s) => s.sid);
}

/**
 * This subtree's session id, or `null` when nothing scopes it.
 *
 * For chips that render both inside a per-session composer and (historically)
 * in the unscoped topbar: inside a provider they must follow their own pane,
 * outside one they fall back to the focused session. Everything with a single
 * call site should use `useScopedSessionId` and keep the missing-wrap error.
 */
export function useOptionalScopedSessionId(): string | null {
  const store = useContext(SessionScopeContext);
  return useStore(
    store ?? EMPTY_SCOPE_STORE,
    (s) => (store ? s.sid : null),
  );
}

// Hooks can't be called conditionally, so the no-provider case subscribes to
// this inert store instead of skipping `useStore`. Kept out of the registry:
// it stands for "no session", not for one named `__unscoped__`.
const EMPTY_SCOPE_STORE = createStore<SessionScopeState>(
  () => ({ sid: "" }) as SessionScopeState,
);
