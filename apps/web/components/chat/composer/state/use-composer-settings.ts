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
import { useCallback } from "react";
import { draftTouchedPatch, persistBranchSettings } from "@/lib/agents/branch-settings";
import { useSessionScope } from "@/lib/session-store/session-scope";
import type { ComposerSettings } from "@/lib/session-store";

/** This subtree's composer settings. */
export function useBoundComposerSettings(): ComposerSettings {
  return useSessionScope((s) => s.settings);
}

/** Patch this subtree's composer settings. A conversation the server has
 *  stores the change on its branch at once; a draft remembers which
 *  controls the person changed, so its first message carries only those. */
export function useBoundSetComposerSettings() {
  const sid = useSessionScope((s) => s.sid);
  const patchSettings = useSessionScope((s) => s.patchSettings);
  const touched = useSessionScope((s) => s.settings.draftTouched);
  return useCallback((patch: Partial<ComposerSettings>) => {
    patchSettings({ ...patch, ...draftTouchedPatch(sid, touched, patch) });
    persistBranchSettings(sid, patch);
  }, [sid, patchSettings, touched]);
}
