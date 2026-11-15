"use client";

import { useCallback, useEffect, useState } from "react";

import { cachedFetch, invalidate, readCachedSettings } from "@/lib/prefs/settings-cache";
import type {
  ProviderQuotaFrame,
  ProviderStatus,
  ProviderStatusPayload,
} from "@/lib/settings/provider-status.generated";

export const STATUS_URL = "/api/providers/status";

/** The window event a `provider_quota` WebSocket frame becomes
 *  (lib/net/ws/use-ws.ts): one key's new quota, applied in place. */
export const QUOTA_EVENT = "op:provider-quota";

/** One key's new quota folded into the payload, without a request. */
export function applyQuotaFrame(payload: ProviderStatusPayload, frame: ProviderQuotaFrame): ProviderStatusPayload {
  let changed = false;
  const providers = payload.providers.map((p) => {
    if (p.id !== frame.provider) return p;
    const current = p.quota?.account ?? null;
    if (current && frame.account && current !== frame.account) return p;
    changed = true;
    return {
      ...p,
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

/** The status of every connected provider: read once (cached 30 s for the
 *  next visit), kept current by quota frames, re-read when providers or
 *  models change, and read fresh from every quota endpoint by Refresh. */
export function useProviderStatus() {
  const [payload, setPayload] = useState<ProviderStatusPayload | null>(
    () => readCachedSettings<ProviderStatusPayload>(STATUS_URL) ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (fresh = false) => {
    if (fresh) invalidate(STATUS_URL);
    try {
      setPayload(await cachedFetch<ProviderStatusPayload>(STATUS_URL));
      setError(null);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const onQuota = (event: Event) => {
      const frame = (event as CustomEvent<ProviderQuotaFrame>).detail;
      if (!frame?.provider) return;
      setPayload((prev) => (prev ? applyQuotaFrame(prev, frame) : prev));
    };
    const onChanged = () => { void load(true); };
    window.addEventListener(QUOTA_EVENT, onQuota);
    window.addEventListener("op:provider-models-changed", onChanged);
    return () => {
      window.removeEventListener(QUOTA_EVENT, onQuota);
      window.removeEventListener("op:provider-models-changed", onChanged);
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
      const next = (await r.json()) as ProviderStatusPayload;
      invalidate(STATUS_URL);
      setPayload(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : String(err));
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
      invalidate(STATUS_URL);
      setPayload((prev) => prev ? {
        ...prev,
        providers: prev.providers.some((p) => p.id === id)
          ? prev.providers.map((p) => (p.id === id ? next : p))
          : [...prev.providers, next],
      } : prev);
    } finally {
      setRefreshing(false);
    }
  }, []);

  return { payload, error, refreshing, reload: load, refresh, refreshOne };
}
