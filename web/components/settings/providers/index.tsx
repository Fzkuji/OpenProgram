"use client";

/**
 * LLM Providers settings — port of /js/shared/settings-providers.js (521 lines).
 *
 * Two-pane layout: provider list (search + grouped by enabled/disabled)
 * on the left; detail pane on the right with enable toggle, API key
 * input (masked status and explicit replacement), base URL override,
 * connectivity check, and
 * model list (toggle / search / fetch remote / bulk enable / disable).
 *
 * Originally a single 800-line file (providers-section.tsx); now split
 * into one file per sub-component:
 *   - types.ts: Provider / Model / formatCtx
 *   - provider-item.tsx, detail.tsx, setup-hint.tsx
 *   - api-key.tsx (exported for /settings/search), base-url.tsx,
 *     connectivity.tsx, model-list.tsx
 */
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Detail } from "./detail";
import { ProviderItem } from "./provider-item";
import { AddCustomProvider } from "./add-custom-provider";
import type { Provider } from "./types";
import styles from "../settings-page.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { cachedFetch, invalidate } from "@/lib/prefs/settings-cache";
import { refreshAgentChip } from "./types";
import { useTranslation } from "@/lib/i18n";
import { pushPath } from "@/lib/shallow-nav";
import { PanelLeftCloseIcon, PanelLeftOpenIcon } from "../../animated-icons";
import { sidebarToggleClass } from "../../sidebar/nav-classes";

// Re-export ApiKey for search-providers-section.tsx (the only other
// consumer outside this subdirectory).
export { ApiKey } from "./api-key";
export type { Provider, Model } from "./types";

function readProviderListOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem("providerListOpen") !== "0";
  } catch {
    return true;
  }
}

export function ProvidersSection({ initialProviderId }: { initialProviderId?: string } = {}) {
  const { t, text } = useTranslation();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [providers, setProviders] = useState<Provider[]>([]);
  // Selection lives in the URL: /settings/providers/<id> selects <id>,
  // /settings/providers (no id) falls back to the first enabled provider.
  // ``initialProviderId`` is the route param so a hard refresh / shared
  // link lands on the right provider; clicking a row router.push()es the
  // new URL, which re-renders this with a new initialProviderId.
  const [selectedId, setSelectedId] = useState<string | null>(initialProviderId ?? null);
  const [search, setSearch] = useState("");
  const [listOpen, setListOpen] = useState(true);
  const sidebarRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => { setListOpen(readProviderListOpen()); }, []);

  function toggleList() {
    setListOpen((current) => {
      const next = !current;
      try { localStorage.setItem("providerListOpen", next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }

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
    // Only auto-pick a default when the URL named no provider. With a
    // route param the selection is the param (kept in sync below), so a
    // refresh on /settings/providers/<id> stays on <id>.
    if (!preserveSelection && !initialProviderId && list.length > 0) {
      const first = list.find((p) => p.enabled) || list[0];
      setSelectedId(first.id);
    }
  }, [initialProviderId]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Keep selection aligned with the route param (changes on navigation).
  useEffect(() => {
    if (initialProviderId) setSelectedId(initialProviderId);
  }, [initialProviderId]);

  // Navigate to /settings/providers/<id> instead of plain setState so the
  // URL is shareable and survives a refresh.
  const selectProvider = useCallback(
    (id: string) => {
      setSelectedId(id);
      pushPath(`/settings/providers/${encodeURIComponent(id)}`);
    },
    [router],
  );

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
    // Disabling/enabling a provider changes which models the chat picker
    // may show — drop the React-Query cache the composer + model badge
    // read so a disabled provider's models vanish from the top menu
    // without a page reload (the backend already excludes them).
    queryClient.invalidateQueries({ queryKey: ["models-enabled"] });
    // The top-bar agent chip reads agentSettings, not the query cache —
    // refetch it so disabling the current model's provider clears the chip.
    refreshAgentChip();
  }

  return (
    <div className={styles.page}>
      <div className={`${styles.pageBody} ${styles.pageBodyTwoPane}`}>
        <div className={styles.providersLayout + (!listOpen ? " " + styles.providerListCollapsed : "")}>
          <div className={styles.providersSidebar} ref={sidebarRef}>
            <div className={styles.providersStickyHeader}>
              <div className={styles.providersToolbar}>
                {listOpen && (
                  <div className={styles.providerSearch}>
                    <SearchInput
                      placeholder={text("Search providers...", "搜索 Provider...")}
                      value={search}
                      onChange={setSearch}
                    />
                  </div>
                )}
                <button
                  type="button"
                  className={sidebarToggleClass}
                  onClick={toggleList}
                  aria-expanded={listOpen}
                  aria-controls="provider-list-items"
                  aria-label={text(listOpen ? "Collapse provider list" : "Expand provider list", listOpen ? "收起 Provider 列表" : "展开 Provider 列表")}
                  title={text(listOpen ? "Collapse provider list" : "Expand provider list", listOpen ? "收起 Provider 列表" : "展开 Provider 列表")}
                >
                  {listOpen ? <PanelLeftCloseIcon size={20} /> : <PanelLeftOpenIcon size={20} />}
                </button>
              </div>
              {listOpen && (
                <AddCustomProvider
                  onCreated={(id) => {
                    invalidate("/api/providers/list");
                    reload(true).then(() => selectProvider(id));
                  }}
                />
              )}
            </div>
          <div id="provider-list-items" className={styles.providerListItems}>
          {enabled.filter(matches).length > 0 && (
            <>
              {listOpen && <div className={styles.providersGroupLabel}>{text("Enabled", "已启用")}</div>}
              {enabled.filter(matches).map((p) => (
                <ProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => selectProvider(p.id)}
                />
              ))}
            </>
          )}
          {disabled.filter(matches).length > 0 && (
            <>
              {listOpen && <div className={styles.providersGroupLabel}>{text("Not enabled", "未启用")}</div>}
              {disabled.filter(matches).map((p) => (
                <ProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => selectProvider(p.id)}
                />
              ))}
            </>
          )}
          </div>
        </div>
          <div className={styles.detail}>
            {!selected ? (
              <div className={styles.detailEmpty}>{text("Select a provider on the left", "选择左侧 Provider")}</div>
            ) : (
              <Detail
                key={selected.id}
                provider={selected}
                onToggle={(en) => toggleProvider(selected.id, en)}
                onChanged={() => reload(true)}
                onDeleted={() => {
                  invalidate("/api/providers/list");
                  setSelectedId(null);
                  queryClient.invalidateQueries({ queryKey: ["models-enabled"] });
                  refreshAgentChip();
                  router.push("/settings/providers");
                  reload();
                }}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
