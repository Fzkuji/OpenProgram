"use client";

/**
 * Composer-facing view of the enclosing session scope.
 *
 * The Composer and its control hooks (thinking effort, tool toggles,
 * permission mode) each need "this composer's settings" — which used to mean
 * "the focused session's, unless a split pane overrode it". That conditional
 * is gone: every composer now renders inside a `SessionScopeProvider`
 * (`app-shell` wraps the single-session one, `PeerSessionPane` wraps each
 * split pane), so there is exactly one answer and no fallback to get wrong.
 *
 * These two hooks stay as named helpers because the control hooks read like
 * prose with them, not because they hide any branching.
 */
import { useSessionScope } from "@/lib/session-store/session-scope";
import type { ComposerSettings } from "@/lib/session-store";

/** This subtree's composer settings. */
export function useBoundComposerSettings(): ComposerSettings {
  return useSessionScope((s) => s.settings);
}

/** Patch this subtree's composer settings. */
export function useBoundSetComposerSettings() {
  return useSessionScope((s) => s.patchSettings);
}
