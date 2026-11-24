"use client";

/**
 * Web-search provider settings — pick + configure backends like
 * Tavily, Exa, DuckDuckGo. Originally a 526-line single file;
 * split into one file per subcomponent for maintainability.
 */
import { useCallback, useMemo, useState } from "react";

import { SettingsPage } from "../settings-ui";
import styles from "../providers/provider-pane.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { jsonFetch } from "@/lib/net/fetch-client";
import { invalidate, refreshSettings, setCachedSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import { useTranslation } from "@/lib/i18n";
import { SEARCH_PROVIDERS_URL } from "@/lib/settings/settings-urls";
import { SearchProviderDetail } from "./detail";
import { SearchProviderItem } from "./item";
import type { SearchProvider } from "./types";

type SearchProviderList = { providers?: SearchProvider[]; default?: string | null };

const NO_PROVIDERS: SearchProvider[] = [];

export function SearchProvidersSection() {
  const { t, text } = useTranslation();
  // Kept across visits and read as Settings opens: the list shows at once.
  const data = useSettingsData<SearchProviderList>(SEARCH_PROVIDERS_URL);
  const providers = data?.providers ?? NO_PROVIDERS;
  const defaultId = data?.default ?? null;
  const [picked, setPicked] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Until a row is picked: the configured default, then the first available
  // backend, then the first row. Picking ``list[0]`` blindly lands on Tavily
  // (priority 100) which is typically un-configured — confusing.
  const selectedId = picked ?? (
    defaultId && providers.some((p) => p.id === defaultId) ? defaultId
      : providers.find((p) => p.available)?.id ?? providers[0]?.id ?? null
  );
  const setSelectedId = setPicked;

  const load = useCallback(() => refreshSettings(SEARCH_PROVIDERS_URL).then(() => {}), []);

  const setDefault = useCallback(async (id: string | null) => {
    setSaving(true);
    setError(null);
    try {
      await jsonFetch("/api/search-providers/default", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: id }),
      });
      // Refresh `is_default` flags on every row, then read the list again.
      setCachedSettings<SearchProviderList>(SEARCH_PROVIDERS_URL, (prev) => prev && {
        ...prev,
        default: id,
        providers: (prev.providers ?? []).map((p) => ({ ...p, is_default: p.id === id })),
      });
      invalidate(SEARCH_PROVIDERS_URL);
    } catch {
      setError(text("Could not save the search provider. Retry after reconnecting.", "无法保存搜索后端，请在重新连接后重试。"));
    } finally {
      setSaving(false);
    }
  }, [text]);

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

  return (
    <SettingsPage
      title={t("settings.tab.search")}
      description={text(
        "Pick which backend handles web_search calls. Tavily / Exa / Brave / Perplexity need an API key; DuckDuckGo and SearXNG work zero-config as fallbacks.",
        "选择由哪个后端处理 web_search 调用。Tavily / Exa / Brave / Perplexity 需要 API key；DuckDuckGo 和 SearXNG 可作为零配置 fallback。",
      )}
      bodyClassName="gap-0 overflow-hidden p-0"
    >
      {error && <p role="alert">{error}</p>}
        {data ? <div className={styles.providersLayout}>
          <div className={styles.providersSidebar}>
            <div className="p-[0_0_8px]">
              <div className="flex items-center w-full min-w-0">
                <div className="w-full min-w-0">
                  <SearchInput
                    placeholder={text("Search backends...", "搜索后端...")}
                    value={search}
                    onChange={setSearch}
                  />
                </div>
              </div>
            </div>
            <div className={`${styles.providerListItems} flex flex-col gap-[1px]`}>
              {active.length > 0 && (
                <>
                  <div className={styles.providersGroupLabel}>{text("Available", "可用")}</div>
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
                  <div className={styles.providersGroupLabel}>{text("Not configured", "未配置")}</div>
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
              <div className={`${styles.detailSurface} flex flex-col gap-[16px] min-w-0 p-[16px_20px_20px]`}>
                <div className={styles.detailEmpty}>
                  {text("Select a search backend on the left", "选择左侧搜索后端")}
                </div>
              </div>
            )}
          </div>
        </div> : null}
    </SettingsPage>
  );
}
