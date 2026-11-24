"use client";

import { useCallback, useEffect, useState } from "react";

import { cachedFetch, refreshSettings, setCachedSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import { PROVIDER_STATUS_URL } from "@/lib/settings/settings-urls";
import { CREDENTIALS_CHANGED } from "../detail/credential-model";
import type {
  ProviderQuotaFrame,
  ProviderStatus,
  ProviderStatusPayload,
} from "@/lib/settings/provider-status.generated";

export const STATUS_URL = PROVIDER_STATUS_URL;

/** The window event a `provider_quota` WebSocket frame becomes
 *  (lib/net/ws/use-ws.ts): one key's new quota, applied in place. */
export const QUOTA_EVENT = "op:provider-quota";

/** How long the cards wait after a change for the rest of its burst. */
export const CHANGE_SETTLE_MS = 250;

/** One key's new quota folded into the payload, without a request. */
export function applyQuotaFrame(payload: ProviderStatusPayload, frame: ProviderQuotaFrame): ProviderStatusPayload {
  let changed = false;
  const providers = payload.providers.map((p) => {
    if (p.id !== frame.provider) return p;
    const current = p.quota?.account ?? null;
    // The credential's own row takes its new limits whichever key is in use.
    const keys = p.keys.map((k) => (k.credential_id === frame.account
      ? { ...k, quota: { ...k.quota, kind: frame.kind, account: frame.account, fetched_at: frame.fetched_at, stale: frame.stale, limits: frame.limits } }
      : k));
    const own = keys.some((k, i) => k !== p.keys[i]);
    if (current && frame.account && current !== frame.account) {
      if (!own) return p;
      changed = true;
      return { ...p, keys } as ProviderStatus;
    }
    changed = true;
    return {
      ...p,
      keys,
      quota: {
        ...(p.quota ?? { read_error: null, read_at: null }),
        kind: frame.kind,
        account: frame.account || current,
        fetched_at: frame.fetched_at,
        stale: frame.stale,
        limits: frame.limits,
      },
    } as ProviderStatus;
  });
  return changed ? { ...payload, providers } : payload;
}

/** The status of every connected provider: kept for the visit and read as
 *  Settings opens (a revisit shows the last answer at once and reads again
 *  behind it), kept current by quota frames, re-read when providers, models
 *  or a provider's credentials change (a burst of changes is read once), and
 *  read fresh from every quota endpoint by Refresh. A read that fails keeps
 *  the last answer and is retried quietly. */
export function useProviderStatus() {
  const payload = useSettingsData<ProviderStatusPayload>(STATUS_URL) ?? null;
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (fresh = false) => {
    await (fresh ? refreshSettings(STATUS_URL) : cachedFetch(STATUS_URL).catch(() => {}));
  }, []);

  useEffect(() => {
    const onQuota = (event: Event) => {
      const frame = (event as CustomEvent<ProviderQuotaFrame>).detail;
      if (!frame?.provider) return;
      setCachedSettings<ProviderStatusPayload>(STATUS_URL, (prev) => (prev ? applyQuotaFrame(prev, frame) : prev));
    };
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onChanged = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; void load(true); }, CHANGE_SETTLE_MS);
    };
    window.addEventListener(QUOTA_EVENT, onQuota);
    window.addEventListener("op:provider-models-changed", onChanged);
    window.addEventListener(CREDENTIALS_CHANGED, onChanged);
    return () => {
      if (timer) clearTimeout(timer);
      window.removeEventListener(QUOTA_EVENT, onQuota);
      window.removeEventListener("op:provider-models-changed", onChanged);
      window.removeEventListener(CREDENTIALS_CHANGED, onChanged);
    };
  }, [load]);

  /** Read every connected provider's quota endpoint now (the header's Refresh). */
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await fetch(`${STATUS_URL}/refresh`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (!r.ok) throw new Error(String(r.status));
      setCachedSettings(STATUS_URL, (await r.json()) as ProviderStatusPayload);
    } catch (err) {
      // The cards keep what they showed; their own read times say how old it is.
      console.warn("Providers: refreshing every status failed", err);
    } finally {
      setRefreshing(false);
    }
  }, []);

  /** Read one provider's quota endpoint now (the detail page's Refresh). */
  const refreshOne = useCallback(async (id: string) => {
    setRefreshing(true);
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(id)}/refresh`, { method: "POST" });
      if (!r.ok) return;
      const next = (await r.json()) as ProviderStatus;
      setCachedSettings<ProviderStatusPayload>(STATUS_URL, (prev) => prev ? {
        ...prev,
        providers: prev.providers.some((p) => p.id === id)
          ? prev.providers.map((p) => (p.id === id ? next : p))
          : [...prev.providers, next],
      } : prev);
    } catch (err) {
      console.warn(`Providers: refreshing ${id} failed`, err);
    } finally {
      setRefreshing(false);
    }
  }, []);

  return { payload, refreshing, reload: load, refresh, refreshOne };
}
