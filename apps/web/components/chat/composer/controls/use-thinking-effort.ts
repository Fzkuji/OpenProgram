"use client";

/**
 * Thinking-effort selector state.
 *
 * `lib/runtime-bridge/providers.ts` writes `runtimeState._thinkingConfig
 * = { options, default }` whenever the chat-agent provider changes. We
 * poll for that (500ms is plenty since it only changes on agent switch)
 * and re-pick a default if the previously-selected effort isn't valid
 * for the new provider.
 */

import { useCallback, useEffect, useState } from "react";

import { runtimeState } from "@/lib/runtime-bridge/state";

import {
  useBoundComposerSettings,
  useBoundSetComposerSettings,
} from "../state/use-composer-settings";

const DEFAULT_THINKING: ThinkingEffort = "medium";
const FALLBACK_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const;

export type ThinkingEffort = string;

export interface ThinkingOption {
  value: string;
  desc?: string;
}

function readThinkingOptions(): ThinkingOption[] {
  // SSR / pre-hydration: `_thinkingConfig` is still null, so the
  // fallback renders the visible pill during the very first paint —
  // providers.ts fills it in client-side and the 500ms tick refreshes
  // us into the real provider-specific list.
  const opts = runtimeState._thinkingConfig?.options;
  if (Array.isArray(opts) && opts.length > 0) return opts;
  return FALLBACK_LEVELS.map((v) => ({ value: v }));
}

function readBackendDefault(): string | undefined {
  return runtimeState._thinkingConfig?.default;
}

/** Cheap identity of the current thinking config — the poll below
 *  compares this instead of re-rendering blind. The config is a short
 *  option list written once per agent/model switch, so stringifying it
 *  every 500ms costs nothing next to the render it replaces. */
function configFingerprint(): string {
  const cfg = runtimeState._thinkingConfig;
  if (!cfg) return "";
  return `${cfg.default ?? ""}|${(cfg.options ?? [])
    .map((o) => o.value)
    .join(",")}`;
}

export interface ThinkingEffortHook {
  thinking: ThinkingEffort;
  options: ThinkingOption[];
  menuOpen: boolean;
  setMenuOpen: (v: boolean | ((prev: boolean) => boolean)) => void;
  /** Update the selection without closing the popover (slider drag). */
  set: (level: ThinkingEffort) => void;
}

export function useThinkingEffort(): ThinkingEffortHook {
  // `stored` is the user's raw last pick — now per-session (store's
  // scope's settings.thinking, persisted + isolated per chat). "" means
  // "no explicit pick" → fall through to DEFAULT/backend default below.
  // It is NOT sent as-is — the exposed `thinking` is always re-derived
  // against the CURRENT model's options (clamp).
  // Bound to this composer subtree's session scope.
  const storedRaw = useBoundComposerSettings().thinking;
  const setComposerSettings = useBoundSetComposerSettings();
  const stored: ThinkingEffort = storedRaw || DEFAULT_THINKING;
  const [menuOpen, setMenuOpen] = useState(false);
  // `_thinkingConfig` is mutated in place by providers.ts when the
  // agent/model changes, so it has to be polled. The poll re-renders
  // ONLY when the config actually changed: this hook lives inside the
  // Composer, and an unconditional tick re-rendered that whole subtree
  // twice a second for the life of the page.
  const [, forceTick] = useState(0);
  useEffect(() => {
    let seen = configFingerprint();
    const id = setInterval(() => {
      const now = configFingerprint();
      if (now === seen) return;
      seen = now;
      forceTick((t) => t + 1);
    }, 500);
    return () => clearInterval(id);
  }, []);

  // Read live every render so a model switch is reflected immediately
  // on the next render (no stale-state window).
  const options = readThinkingOptions();

  // CLAMP: the value actually exposed (and sent with chat turns) must
  // be one the current model supports. If the stored pick isn't in
  // the current option list — e.g. `minimal` persisted, then the
  // agent switched to gpt-5.5 which only does none/low/medium/high/
  // xhigh — fall back to the backend default (or the first option).
  // Without this clamp the stale pick reached the API and 400'd:
  // "'minimal' is not supported with the 'gpt-5.5' model".
  const thinking = options.some((o) => o.value === stored)
    ? stored
    : readBackendDefault() ?? options[0]?.value ?? stored;

  const set = useCallback((level: ThinkingEffort) => {
    setComposerSettings({ thinking: level });
  }, [setComposerSettings]);

  return { thinking, options, menuOpen, setMenuOpen, set };
}
