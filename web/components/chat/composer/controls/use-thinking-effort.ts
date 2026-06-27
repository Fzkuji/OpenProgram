"use client";

/**
 * Thinking-effort selector state.
 *
 * The legacy `providers.js` writes `window._thinkingConfig = { options,
 * default }` whenever the chat-agent provider changes. We poll for that
 * (500ms is plenty since it only changes on agent switch) and re-pick a
 * default if the previously-selected effort isn't valid for the new
 * provider.
 */

import { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

const DEFAULT_THINKING: ThinkingEffort = "medium";
const FALLBACK_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const;

export type ThinkingEffort = string;

export interface ThinkingOption {
  value: string;
  desc?: string;
}

function readThinkingOptions(): ThinkingOption[] {
  // SSR / pre-hydration: ``window`` is undefined. Returning the
  // fallback (instead of throwing) lets the component render its
  // visible pill during the very first paint — the legacy
  // providers.js then writes _thinkingConfig client-side and the
  // 500ms tick refreshes us into the real provider-specific list.
  // Without this guard a ReferenceError ate the pill during SSR;
  // hydration had to retry the subtree before it appeared, which
  // showed up as "the effort pill sometimes doesn't display, or
  // takes ages".
  if (typeof window === "undefined") {
    return FALLBACK_LEVELS.map((v) => ({ value: v }));
  }
  const w = window as unknown as {
    _thinkingConfig?: { options?: ThinkingOption[] };
  };
  const opts = w._thinkingConfig?.options;
  if (Array.isArray(opts) && opts.length > 0) return opts;
  return FALLBACK_LEVELS.map((v) => ({ value: v }));
}

function readBackendDefault(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as { _thinkingConfig?: { default?: string } };
  return w._thinkingConfig?.default;
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
  // composerSettings.thinking, persisted + isolated per chat). "" means
  // "no explicit pick" → fall through to DEFAULT/backend default below.
  // It is NOT sent as-is — the exposed `thinking` is always re-derived
  // against the CURRENT model's options (clamp).
  const storedRaw = useSessionStore((s) => s.composerSettings.thinking);
  const setComposerSettings = useSessionStore((s) => s.setComposerSettings);
  const stored: ThinkingEffort = storedRaw || DEFAULT_THINKING;
  const [menuOpen, setMenuOpen] = useState(false);
  // `_thinkingConfig` is mutated in place by legacy providers.js when
  // the agent/model changes. A 500ms poll just bumps a counter to
  // force a re-render so `options` / `thinking` below pick up the
  // new config — they're read live, not cached in state.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((t) => t + 1), 500);
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
