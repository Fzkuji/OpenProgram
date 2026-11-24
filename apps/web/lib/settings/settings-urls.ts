/**
 * What each Settings page reads, named once so Settings opening can read
 * every page ahead (`prefetchSettingsPages`) and the page finds its answer
 * already kept when it is clicked. This module holds no page code: the
 * Settings shell imports it without pulling in any page.
 */
import { prefetchSettings } from "@/lib/prefs/settings-cache";

export const CHANNEL_ACCOUNTS_URL = "/api/channels/accounts";
export const CHANNEL_BINDINGS_URL = "/api/channels/bindings";
export const CHANNEL_ACCESS_URL = "/api/channels/access";
export const SEARCH_PROVIDERS_URL = "/api/search-providers/list";
export const MEMORY_SETTINGS_URL = "/api/settings?scope=memory";
export const MEMORY_STATUS_URL = "/api/memory/status?settings=true";
export const VOICE_STATUS_URL = "/api/voice/status";
export const IMPORT_CANDIDATES_URL = "/api/sessions/import/candidates";
export const SYSTEM_VERSION_URL = "/api/system/version";
export const PROVIDER_STATUS_URL = "/api/providers/status";
export const PROVIDERS_DETECTED_URL = "/api/providers/detected";
export const PROVIDERS_CATALOGUE_URL = "/api/providers/list";

export const providerModelsUrl = (id: string) => `/api/providers/${encodeURIComponent(id)}/models`;
/** One read serves both the usage panel and the models table's 30-day spend. */
export const providerUsageUrl = (id: string, days: number) =>
  `/api/providers/${encodeURIComponent(id)}/usage?days=${days}&group=day,model`;

export const USAGE_DAYS = 30;
const DAY_SECONDS = 86_400;

/** Token Usage covers 30 days. The window starts on a day boundary so the
 *  address stays the same all day and its answer stays kept. */
export function usageUrls(now = Date.now()) {
  const today = Math.floor(now / 1000 / DAY_SECONDS);
  const since = (today - USAGE_DAYS + 1) * DAY_SECONDS;
  const trend = (group: string) => `/api/usage/trend?bucket=day&days=${USAGE_DAYS}&group=${group}`;
  return {
    summary: `/api/usage/summary?since=${since}`,
    call_kind: trend("call_kind"),
    model_id: trend("model_id"),
    call_label: trend("call_label"),
  };
}

/** Every page's reads, started as Settings opens. Import is left out: it
 *  scans the disk for other agents' conversations, so it reads on its own
 *  page only. */
export function prefetchSettingsPages(): void {
  for (const url of [
    PROVIDER_STATUS_URL, PROVIDERS_DETECTED_URL, PROVIDERS_CATALOGUE_URL,
    MEMORY_SETTINGS_URL, MEMORY_STATUS_URL,
    SEARCH_PROVIDERS_URL, VOICE_STATUS_URL,
    CHANNEL_ACCOUNTS_URL, CHANNEL_BINDINGS_URL, CHANNEL_ACCESS_URL,
    SYSTEM_VERSION_URL,
    ...Object.values(usageUrls()),
  ]) prefetchSettings(url);
}
