"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { CatalogEntry } from "@/lib/skills-store";

import { fmtCount, hasStats, relTime } from "./helpers";
import type { Source, SortKey } from "./types";

export function CatalogList({
  entries, source, installedNames, outdatedNames, installingKey, onInstall,
}: {
  entries: CatalogEntry[];
  source: Source;
  installedNames: Set<string>;
  outdatedNames: Set<string>;
  installingKey: string | null;
  onInstall: (source: Source, name: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const hasMeta = useMemo(() => hasStats(entries), [entries]);
  const [sort, setSort] = useState<SortKey>(hasMeta ? "downloads" : "default");

  const filtered = useMemo(() => {
    if (!filter.trim()) return entries;
    const q = filter.toLowerCase();
    return entries.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        (e.description || "").toLowerCase().includes(q) ||
        (e.display_name || "").toLowerCase().includes(q),
    );
  }, [entries, filter]);

  const shown = useMemo(() => {
    const arr = [...filtered];
    switch (sort) {
      case "name":
        arr.sort((a, b) => a.name.localeCompare(b.name));
        break;
      case "stars":
        arr.sort((a, b) => (b.stars || 0) - (a.stars || 0));
        break;
      case "downloads":
        arr.sort((a, b) => (b.downloads || 0) - (a.downloads || 0));
        break;
      case "updated":
        arr.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
        break;
      case "default":
      default:
        break;
    }
    return arr;
  }, [filtered, sort]);

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex-1">
          <svg
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]"
            width="14" height="14" viewBox="0 0 20 20" fill="currentColor"
          >
            <path fillRule="evenodd" clipRule="evenodd"
              d="M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11ZM2 9a7 7 0 1 1 12.452 4.391l3.328 3.329a.75.75 0 1 1-1.06 1.06l-3.329-3.328A7 7 0 0 1 2 9Z" />
          </svg>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={`Search ${entries.length} skill${entries.length === 1 ? "" : "s"}…`}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-input)] py-2 pl-9 pr-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none focus:border-[var(--accent-blue)] focus:ring-2 focus:ring-[var(--accent-blue)]/20"
          />
        </div>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="shrink-0 rounded-md border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-blue)]"
        >
          <option value="default">Sort: {hasMeta ? "Trending" : "Default"}</option>
          <option value="name">Name</option>
          {hasMeta && <option value="downloads">Most downloaded</option>}
          {hasMeta && <option value="stars">Most starred</option>}
          {hasMeta && <option value="updated">Recently updated</option>}
        </select>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((e) => {
          const key = `${source.url}::${e.name}`;
          const fullName = source.slug ? `${source.slug}/${e.name}` : e.name;
          const installed = installedNames.has(fullName);
          const outdated = outdatedNames.has(fullName);
          const showStats = (e.stars || 0) > 0 || (e.downloads || 0) > 0;
          return (
            <div
              key={e.name}
              className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-secondary)]/40 p-3 hover:border-[var(--text-dim)] transition-colors"
            >
              <div className="flex items-start justify-between gap-2 min-w-0">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap min-w-0">
                    <span className="font-mono text-[12px] text-nav-color-hover truncate">
                      {e.display_name || e.name}
                    </span>
                    {e.version && (
                      <span className="text-[10px] text-[var(--text-tertiary)]">v{e.version}</span>
                    )}
                  </div>
                  {(e.display_name && e.display_name !== e.name) && (
                    <div className="text-[10px] font-mono text-[var(--text-tertiary)] truncate">
                      {e.name}
                    </div>
                  )}
                </div>
                <div className="flex gap-1 shrink-0">
                  {installed && !outdated && (
                    <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-emerald-400">in</span>
                  )}
                  {outdated && (
                    <span className="rounded border border-amber-500/40 bg-amber-500/15 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-amber-400" title="Upstream changed">old</span>
                  )}
                </div>
              </div>

              {e.description && (
                <p className="text-xs text-[var(--text-secondary)] line-clamp-2">{e.description}</p>
              )}

              <div className="mt-auto flex items-center justify-between gap-2">
                {showStats ? (
                  <div className="flex items-center gap-3 text-[11px] text-[var(--text-tertiary)]">
                    {(e.stars || 0) > 0 && (
                      <span title="stars">★ {fmtCount(e.stars)}</span>
                    )}
                    {(e.downloads || 0) > 0 && (
                      <span title="downloads">↓ {fmtCount(e.downloads)}</span>
                    )}
                    {(e.updated_at || 0) > 0 && (
                      <span title="last updated">{relTime(e.updated_at)}</span>
                    )}
                  </div>
                ) : (
                  <span />
                )}
                <Button
                  size="sm"
                  variant={outdated ? "default" : installed ? "outline" : "default"}
                  onClick={() => onInstall(source, e.name)}
                  disabled={installingKey === key}
                >
                  {installingKey === key ? "…" : outdated ? "Update" : installed ? "Reinstall" : "Install"}
                </Button>
              </div>
            </div>
          );
        })}
        {shown.length === 0 && (
          <div className="col-span-full text-xs text-[var(--text-tertiary)] py-2">No matches.</div>
        )}
      </div>
    </div>
  );
}

