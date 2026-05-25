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

const DEFAULT_THINKING: ThinkingEffort = "medium";
const FALLBACK_LEVELS = ["low", "medium", "high", "xhigh"] as const;
const STORAGE_KEY = "thinkingEffort";

/** Last picked effort, persisted so a page refresh keeps the choice. */
function readPersistedEffort(): ThinkingEffort | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writePersistedEffort(level: ThinkingEffort): void {
  try {
    localStorage.setItem(STORAGE_KEY, level);
  } catch {
    /* ignore */
  }
}

export type ThinkingEffort = string;

export interface ThinkingOption {
  value: string;
  desc?: string;
}

function readThinkingOptions(): ThinkingOption[] {
  const w = window as unknown as {
    _thinkingConfig?: { options?: ThinkingOption[] };
  };
  const opts = w._thinkingConfig?.options;
  if (Array.isArray(opts) && opts.length > 0) return opts;
  return FALLBACK_LEVELS.map((v) => ({ value: v }));
}

function readBackendDefault(): string | undefined {
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
  // `stored` is the user's raw last pick (seeded from localStorage so
  // a refresh keeps it). It is NOT sent as-is — the exposed `thinking`
  // below is always re-derived against the CURRENT model's options.
  const [stored, setStored] = useState<ThinkingEffort>(
    () => readPersistedEffort() ?? DEFAULT_THINKING,
  );
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
    setStored(level);
    writePersistedEffort(level);
  }, []);

  return { thinking, options, menuOpen, setMenuOpen, set };
}
