"use client";

/**
 * Web-search provider settings — pick + configure backends like
 * Tavily, Exa, DuckDuckGo. Originally a 526-line single file;
 * split into one file per subcomponent for maintainability.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import styles from "../settings-page.module.css";
import { SearchProviderDetail } from "./detail";
import { SearchProviderItem } from "./item";
import type { SearchProvider } from "./types";

export function SearchProvidersSection() {
  const [providers, setProviders] = useState<SearchProvider[]>([]);
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/search-providers/list");
      const d = await r.json();
      const list: SearchProvider[] = d.providers || [];
      const def = d.default ?? null;
      setProviders(list);
      setDefaultId(def);
      // Initial selection: prefer the configured default, then the
      // first available backend, then the first row. Picking
      // ``list[0]`` blindly lands on Tavily (priority 100) which is
      // typically un-configured — confusing.
      setSelectedId((cur) => {
        if (cur) return cur;
        if (def && list.some((p) => p.id === def)) return def;
        const firstAvailable = list.find((p) => p.available);
        if (firstAvailable) return firstAvailable.id;
        return list[0]?.id ?? null;
      });
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setDefault = useCallback(async (id: string | null) => {
    setSaving(true);
    try {
      await fetch("/api/search-providers/default", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: id }),
      });
      setDefaultId(id);
      // Refresh `is_default` flags on every row.
      setProviders((prev) =>
        prev.map((p) => ({ ...p, is_default: p.id === id })),
      );
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
    }
  }, []);

  const matches = useCallback(
    (p: SearchProvider) =>
      !search.trim() ||
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.id.toLowerCase().includes(search.toLowerCase()),
    [search],
  );

  const { active, inactive } = useMemo(() => {
    const visible = providers.filter(matches).sort((a, b) => a.priority - b.priority);
    return {
      active: visible.filter((p) => p.available),
      inactive: visible.filter((p) => !p.available),
    };
  }, [providers, matches]);

  const selected = providers.find((p) => p.id === selectedId);

  if (loading) {
    return (
      <div className={styles.page}>
        <div className={styles.pageHeader}>
          <h2 className={styles.pageTitle}>Web Search</h2>
        </div>
        <div className={styles.pageBody} style={{ opacity: 0.6 }}>Loading…</div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>Web Search</h2>
        <p className={styles.pageMeta}>
          Pick which backend handles ``web_search`` calls. Tavily / Exa /
          Brave / Perplexity need an API key; DuckDuckGo and SearXNG work
          zero-config as fallbacks.
        </p>
      </div>
      <div className={`${styles.pageBody} ${styles.pageBodyTwoPane}`}>
        <div className={styles.providersLayout}>
          <div className={styles.providersSidebar}>
            <div className={styles.providersStickyHeader}>
              <div className={styles.providersSearch}>
                <input
                  type="search"
                  placeholder="Search backends…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            </div>
          {active.length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Available</div>
              {active.map((p) => (
                <SearchProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
          {inactive.length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Not configured</div>
              {inactive.map((p) => (
                <SearchProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
        </div>

          <div className={styles.detail}>
            {selected ? (
              <SearchProviderDetail
                provider={selected}
                defaultId={defaultId}
                saving={saving}
                onSetDefault={setDefault}
                onChanged={load}
              />
            ) : (
              <div className={styles.detailEmpty}>
                Select a search backend on the left
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

