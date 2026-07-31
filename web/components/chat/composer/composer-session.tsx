"use client";

/**
 * Which session a Composer subtree is bound to.
 *
 * The Composer and its control hooks (thinking effort, tool toggles,
 * permission mode) historically read "the focused session" straight off the
 * store — `composerSettings` is the live slice for whatever chat is focused.
 * That's correct for the single-composer layout, but a split view renders
 * TWO composers side by side and each must read and write its own session.
 *
 * Rather than thread a `sessionId` prop through every hook, the Composer
 * publishes its target here and the hooks read it. An absent provider means
 * "the focused session", which is exactly the old behavior — so the
 * non-split path is unchanged.
 */
import { createContext, useContext } from "react";

import { useSessionStore } from "@/lib/session-store";

/** `null` = follow the focused session (default, pre-split behavior). */
const ComposerSessionContext = createContext<string | null>(null);

export const ComposerSessionProvider = ComposerSessionContext.Provider;

/** The chat key this composer subtree targets, or `null` to follow focus. */
export function useComposerSessionKey(): string | null {
  return useContext(ComposerSessionContext);
}

/**
 * This subtree's composer settings. Bound panes read their own session's
 * entry; unbound (focused) composers read the live slice exactly as before.
 */
export function useBoundComposerSettings() {
  const bound = useComposerSessionKey();
  return useSessionStore((s) =>
    bound === null
      ? s.composerSettings
      : (s.composerSettingsBySession[bound] ?? s.composerSettings),
  );
}

/**
 * `setComposerSettings` pre-bound to this subtree's session. Unbound
 * composers get the plain setter (targets the focused session).
 */
export function useBoundSetComposerSettings() {
  const bound = useComposerSessionKey();
  const set = useSessionStore((s) => s.setComposerSettings);
  return (patch: Parameters<typeof set>[0]) =>
    bound === null ? set(patch) : set(patch, bound);
}
