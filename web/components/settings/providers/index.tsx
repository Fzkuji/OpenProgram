"use client";

/**
 * LLM Providers settings — port of /js/shared/settings-providers.js (521 lines).
 *
 * Two-pane layout: provider list (search + grouped by enabled/disabled)
 * on the left; detail pane on the right with enable toggle, API key
 * input (mask/reveal/save), base URL override, connectivity check, and
 * model list (toggle / search / fetch remote / bulk enable / disable).
 *
 * Originally a single 800-line file (providers-section.tsx); now split
 * into one file per sub-component:
 *   - types.ts: Provider / Model / formatCtx
 *   - provider-item.tsx, detail.tsx, setup-hint.tsx
 *   - api-key.tsx (exported for /settings/search), base-url.tsx,
 *     connectivity.tsx, model-list.tsx
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Detail } from "./detail";
import { ProviderItem } from "./provider-item";
import type { Provider } from "./types";
import styles from "../settings-page.module.css";
import { cachedFetch, invalidate } from "@/lib/settings-cache";
import { useTranslation } from "@/lib/i18n";

// Re-export ApiKey for search-providers-section.tsx (the only other
// consumer outside this subdirectory).
export { ApiKey } from "./api-key";
export type { Provider, Model } from "./types";

export function ProvidersSection() {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Sidebar scroll is intentionally CONTAINED — see the matching
  // `overscroll-behavior: contain` in settings-page.module.css. The
  // previous version forwarded wheel deltas from the sidebar onto the
  // page scroller once the list hit its boundary; that made the right
  // detail column drift up/down while the user was just trying to
  // scroll the provider list, which read as a bug.

  const reload = useCallback(async (preserveSelection?: boolean) => {
    let list: Provider[] = [];
    try {
      // ``cachedFetch`` shares in-flight promises and keeps successful
      // responses for 30s, so tab-switching back here within that
      // window is instant. Mutations call ``invalidate()`` below to
      // force the next read to hit the network.
      const d = await cachedFetch<{ providers?: Provider[] }>("/api/providers/list");
      list = d.providers || [];
    } catch {
      /* empty */
    }
    setProviders(list);
    if (!preserveSelection && list.length > 0) {
      const first = list.find((p) => p.enabled) || list[0];
      setSelectedId(first.id);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const enabled = providers.filter((p) => p.enabled);
  const disabled = providers.filter((p) => !p.enabled);
  const selected = providers.find((p) => p.id === selectedId) || null;

  function matches(p: Provider) {
    if (!search) return true;
    const q = search.toLowerCase();
    return p.label.toLowerCase().includes(q) || p.id.toLowerCase().includes(q);
  }

  async function toggleProvider(id: string, en: boolean) {
    try {
      await fetch(`/api/providers/${encodeURIComponent(id)}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: en }),
      });
    } catch {
      /* ignore */
    }
    invalidate("/api/providers/list");
    reload(true);
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>{t("settings.tab.providers")}</h2>
        <p className={styles.pageMeta}>
          Enable an LLM backend, paste an API key (or rely on local OAuth /
          subscription), and pick which models the chat composer exposes.
        </p>
      </div>
      <div className={`${styles.pageBody} ${styles.pageBodyTwoPane}`}>
        <div className={styles.providersLayout}>
          <div className={styles.providersSidebar} ref={sidebarRef}>
            <div className={styles.providersStickyHeader}>
              <div className={styles.providersSearch}>
                <input
                  type="search"
                  placeholder="Search providers…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            </div>
          {enabled.filter(matches).length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Enabled</div>
              {enabled.filter(matches).map((p) => (
                <ProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
          {disabled.filter(matches).length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Not enabled</div>
              {disabled.filter(matches).map((p) => (
                <ProviderItem
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
            {!selected ? (
              <div className={styles.detailEmpty}>Select a provider on the left</div>
            ) : (
              <Detail
                key={selected.id}
                provider={selected}
                onToggle={(en) => toggleProvider(selected.id, en)}
                onChanged={() => reload(true)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
