"use client";

/**
 * Thinking-effort selector state.
 *
 * `lib/runtime-bridge/providers.ts` writes `runtimeState._thinkingConfig
 * = { options, default }` whenever the chat-agent provider changes and
 * sends THINKING_CONFIG_CHANGED; we redraw then and re-pick a default if
 * the previously-selected effort isn't valid for the new provider.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { harnessOf } from "@/lib/models/harnesses";
import { runtimeState, THINKING_CONFIG_CHANGED } from "@/lib/runtime-bridge/state";

import {
  useBoundComposerSettings,
  useBoundSetComposerSettings,
} from "../../state/use-composer-settings";
import { useSessionAgentSettings } from "../../state/use-session-agent-settings";

const DEFAULT_THINKING: ThinkingEffort = "medium";
const FALLBACK_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const;

export type ThinkingEffort = string;

export interface ThinkingOption {
  value: string;
  desc?: string;
  /** The level this model falls back to when the user has not picked one
   *  (the backend's `default`, an agent invocation's `defaultThinking`, or
   *  `medium` for the pre-hydration fallback list). The effort card hangs
   *  its "Recommended" caption under this option. */
  recommended?: boolean;
}

function markRecommended(
  options: ThinkingOption[],
  value: string | undefined,
): ThinkingOption[] {
  if (!value || !options.some((o) => o.value === value)) return options;
  return options.map((o) => (o.value === value ? { ...o, recommended: true } : o));
}

type ThinkingConfigView = { options?: ThinkingOption[]; default?: string } | null | undefined;

function readThinkingOptions(own: ThinkingConfigView): ThinkingOption[] {
  // SSR / pre-hydration: `_thinkingConfig` is still null, so the
  // fallback renders the visible pill during the very first paint —
  // providers.ts fills it in client-side and the 500ms tick refreshes
  // us into the real provider-specific list.
  const opts = (own ?? runtimeState._thinkingConfig)?.options;
  if (Array.isArray(opts) && opts.length > 0) return opts;
  return FALLBACK_LEVELS.map((v) => ({ value: v }));
}

function readBackendDefault(own: ThinkingConfigView): string | undefined {
  return (own ?? runtimeState._thinkingConfig)?.default;
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
  const settings = useBoundComposerSettings();
  const storedRaw = settings.thinking;
  const invocation = settings.agentInvocation;
  const setComposerSettings = useBoundSetComposerSettings();
  const stored: ThinkingEffort = storedRaw || DEFAULT_THINKING;
  // This conversation's model levels; the focused one's global config
  // answers until they arrive.
  const chat = useSessionAgentSettings().chat;
  const own = chat?.thinking;
  // An outside agent's model that lists no levels takes none: no list to pick from.
  const outside = !!(chat?.harness ?? harnessOf(chat?.provider));
  const noLevels = outside && Array.isArray(own?.options) && own.options.length === 0
    && !(own as { pending?: boolean } | undefined)?.pending;
  // Its levels are not read yet: no control until they are, rather than levels it may not take.
  const waiting = outside && !!(own as { pending?: boolean } | undefined)?.pending;
  const [menuOpen, setMenuOpen] = useState(false);
  // `_thinkingConfig` is replaced by providers.ts when the agent/model
  // changes, which then sends THINKING_CONFIG_CHANGED. This re-renders
  // ONLY when the config actually changed: this hook lives inside the
  // Composer, and a blind re-render redraws that whole subtree.
  const [, forceTick] = useState(0);
  useEffect(() => {
    let seen = configFingerprint();
    const changed = () => {
      const now = configFingerprint();
      if (now === seen) return;
      seen = now;
      forceTick((t) => t + 1);
    };
    // It may have changed between the first render and this effect.
    changed();
    window.addEventListener(THINKING_CONFIG_CHANGED, changed);
    return () => window.removeEventListener(THINKING_CONFIG_CHANGED, changed);
  }, []);

  // Read live every render so a model switch is reflected immediately
  // on the next render (no stale-state window).
  // `recommended` marks the model's default so the card can label it; the
  // server always sends `default` alongside a non-empty option list, so
  // only the pre-hydration fallback list leans on DEFAULT_THINKING.
  // One list per config: the same levels keep their array, so the controls
  // drawn from it are not redrawn by every composer render.
  const liveConfig = own ?? runtimeState._thinkingConfig;
  const liveOptions = liveConfig?.options;
  const liveDefault = liveConfig?.default;
  const liveFingerprint = configFingerprint();
  const options = useMemo(
    () => invocation
      ? markRecommended(
          (invocation.thinkingLevels ?? []).map((value) => ({ value })),
          invocation.defaultThinking,
        )
      : noLevels || waiting ? []
      : markRecommended(readThinkingOptions(own), readBackendDefault(own) ?? DEFAULT_THINKING),
    // The live config is mutated in place; the last three name its content.
    [invocation, own, noLevels, waiting, liveOptions, liveDefault, liveFingerprint],
  );

  // CLAMP: the value actually exposed (and sent with chat turns) must
  // be one the current model supports. If the stored pick isn't in
  // the current option list — e.g. `minimal` persisted, then the
  // agent switched to gpt-5.5 which only does none/low/medium/high/
  // xhigh — fall back to the backend default (or the first option).
  // Without this clamp the stale pick reached the API and 400'd:
  // "'minimal' is not supported with the 'gpt-5.5' model".
  const thinking = invocation
    ? storedRaw || invocation.defaultThinking || ""
    : noLevels ? ""
    // Not read yet: the person's own pick goes as chosen, and the engine takes the model's nearest level.
    : waiting ? storedRaw || ""
    : options.some((o) => o.value === stored) ? stored : readBackendDefault(own) ?? options[0]?.value ?? stored;

  const set = useCallback((level: ThinkingEffort) => {
    setComposerSettings({ thinking: level, ...(invocation ? { agentInvocation: { ...invocation, thinkingEffort: level } } : {}) });
  }, [setComposerSettings, invocation]);

  return { thinking, options, menuOpen, setMenuOpen, set };
}
