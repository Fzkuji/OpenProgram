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

// Re-export ApiKey for search-providers-section.tsx (the only other
// consumer outside this subdirectory).
export { ApiKey } from "./api-key";
export type { Provider, Model } from "./types";

export function ProvidersSection() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Forward wheel events to the page scroll once the sidebar's own
  // scroll is at top/bottom. Browsers' default scroll-chaining only
  // kicks in on the NEXT wheel event after the boundary is hit, so a
  // user trying to scroll past the end of a long provider list sees
  // their small wheel ticks get consumed without page movement until
  // they "force" a larger gesture. We rAF-accumulate deltas so the
  // forwarded scroll feels as smooth as the browser's own.
  useEffect(() => {
    const sb = sidebarRef.current;
    if (!sb) return;
    let pendingDelta = 0;
    let rafId = 0;
    let scrollTarget: HTMLElement | null = null;
    function flush() {
      rafId = 0;
      if (!scrollTarget || pendingDelta === 0) return;
      scrollTarget.scrollTop += pendingDelta;
      pendingDelta = 0;
    }
    function onWheel(e: WheelEvent) {
      const el = sidebarRef.current;
      if (!el) return;
      const atTop = el.scrollTop === 0;
      const atBottom =
        Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight;
      if ((e.deltaY < 0 && atTop) || (e.deltaY > 0 && atBottom)) {
        // Find the closest scrollable ancestor once and cache it.
        if (!scrollTarget) {
          let p: HTMLElement | null = el.parentElement;
          while (p) {
            const cs = getComputedStyle(p);
            if (
              (cs.overflowY === "auto" || cs.overflowY === "scroll") &&
              p.scrollHeight > p.clientHeight
            ) {
              scrollTarget = p;
              break;
            }
            p = p.parentElement;
          }
        }
        if (!scrollTarget) return;
        e.preventDefault();
        pendingDelta += e.deltaY;
        if (!rafId) rafId = requestAnimationFrame(flush);
      }
    }
    sb.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      sb.removeEventListener("wheel", onWheel);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  const reload = useCallback(async (preserveSelection?: boolean) => {
    let list: Provider[] = [];
    try {
      const r = await fetch("/api/providers/list");
      const d = await r.json();
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
    reload(true);
  }

  return (
    <div className={styles.section}>
      <div className={styles.providersLayout}>
        <div className={styles.providersSidebar} ref={sidebarRef}>
          <div className={styles.providersStickyHeader}>
            <h2 className={styles.sectionTitle}>AI Providers</h2>
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
  );
}
