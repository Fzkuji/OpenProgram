"use client";

import { useCallback } from "react";

import { refreshSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import { PROVIDERS_CATALOGUE_URL, PROVIDERS_DETECTED_URL } from "@/lib/settings/settings-urls";
import type {
  AuthKind,
  DetectedEnvKey,
  DetectedPayload,
  KeyCheck,
  ProviderChoice,
  SuggestedModel,
} from "@/lib/settings/provider-status.generated";

import type { Model, Provider } from "../types";

export const DETECTED_URL = PROVIDERS_DETECTED_URL;
export const CATALOGUE_URL = PROVIDERS_CATALOGUE_URL;

/** A provider Add provider can connect: a popular tile, a search result,
 *  an environment key's provider, or a local server. */
export type Choice = {
  id: string;
  label: string;
  auth: AuthKind;
  console_url: string | null;
  /** A key for it found in the Worker's environment. */
  env_var: string | null;
  /** Other ways to connect the same vendor (sign-in beside API key, another region, a plan). */
  variants: ProviderChoice[];
};

/** Anthropic connects with an API key only, whatever the catalogue lists. */
export function authOf(p: Pick<Provider, "id" | "local" | "api_key_env" | "login_methods" | "kind">): AuthKind {
  if (p.id === "anthropic") return "api_key";
  if (p.local) return "local";
  if ((p.login_methods?.length ?? 0) > 0 && !p.api_key_env) return "sign_in";
  return "api_key";
}

export function choiceOf(p: Provider, detected?: DetectedPayload | null): Choice {
  const popular = detected?.popular.find((c) => c.id === p.id)
    ?? detected?.popular.flatMap((c) => c.variants).find((c) => c.id === p.id);
  if (popular) return { variants: [], ...popular, auth: p.id === "anthropic" ? "api_key" : popular.auth };
  const env = detected?.env.find((e) => e.providers.includes(p.id));
  return { id: p.id, label: p.label, auth: authOf(p), console_url: null, env_var: env?.env_var ?? null, variants: [] };
}

/** Keys in the Worker's environment that `providerId` reads and that are not
 *  stored for it yet: step 2 offers them before the paste field. */
export function envKeysFor(detected: DetectedPayload | null | undefined, providerId: string): DetectedEnvKey[] {
  return (detected?.env ?? []).filter((row) =>
    row.providers.includes(providerId) && !(row.connected && row.provider === providerId));
}

const NO_PROVIDERS: Provider[] = [];

/** What Add provider offers before anything is typed, and the catalogue it
 *  searches: kept for the visit and read as Settings opens, so both are
 *  usually here before the page is. A read that fails keeps the last answer
 *  and is retried quietly. */
export function useAddData() {
  const detected = useSettingsData<DetectedPayload>(DETECTED_URL) ?? null;
  const catalogue = useSettingsData<{ providers?: Provider[] }>(CATALOGUE_URL)?.providers ?? NO_PROVIDERS;
  const load = useCallback(async (fresh = false) => {
    if (!fresh) return;
    await Promise.all([refreshSettings(DETECTED_URL), refreshSettings(CATALOGUE_URL)]);
  }, []);
  return { detected, catalogue, reload: load };
}

/** The result of storing a key: the account it became and its one-line check. */
export type Stored = { name: string; check: KeyCheck | null };

/** Models for step 3 when no key check named them (a sign-in, a local
 *  server): the ones already on first, then those with tool use. */
export function suggestFromModels(models: Model[], limit = 3): { top: SuggestedModel[]; count: number; enabled: string[] } {
  const ranked = [...models].sort((a, b) => Number(b.enabled) - Number(a.enabled) || Number(!!b.tools) - Number(!!a.tools));
  const top = ranked.slice(0, limit).map((m) => ({
    id: m.id,
    name: m.name || m.id,
    context_window: m.context_window ?? null,
    input_cost: m.input_cost ?? null,
    output_cost: m.output_cost ?? null,
    reasoning: !!m.reasoning,
    vision: !!m.vision,
    tools: !!m.tools,
    enabled: m.enabled,
  }));
  return { top, count: models.length, enabled: models.filter((m) => m.enabled).map((m) => m.id) };
}

/** The host a key check talks to, for "Can't reach api.deepseek.com". */
export function hostOf(p: Pick<Provider, "base_url" | "default_base_url"> | undefined): string {
  const url = p?.base_url || p?.default_base_url;
  if (!url) return "";
  try { return new URL(url).host; } catch { return ""; }
}
