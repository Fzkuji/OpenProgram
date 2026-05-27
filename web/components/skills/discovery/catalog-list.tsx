"use client";

import { useMemo, useState } from "react";

import type { CatalogEntry } from "@/lib/skills-store";
import { useSkills } from "@/lib/skills-store";

import { fmtCount, hasStats, relTime } from "./helpers";
import type { Source, SortKey } from "./types";

// Pill-shaped action buttons, sized + tinted to match the rest of the
// app (composer pills, /functions card buttons). Three intents:
//   - primary:    install — accent-tinted fill so it reads as the
//                 main action on the card
//   - neutral:    reinstall / minor refresh — bg-tertiary fill that
//                 sits one shade darker than the card so it doesn't
//                 vanish into the surface (the old `outline` Button
//                 variant rendered as just a thin border on the same
//                 bg, which is what the user flagged as ugly)
//   - danger:     uninstall — red-tinted fill, low-saturation so it's
//                 visibly destructive without screaming
const pillBase =
  "inline-flex items-center justify-center h-7 rounded-full px-3 text-[12px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none";
const pillPrimary =
  "bg-[color-mix(in_srgb,var(--accent-blue)_22%,transparent)] text-[var(--accent-blue)] hover:bg-[color-mix(in_srgb,var(--accent-blue)_32%,transparent)]";
const pillNeutral =
  "bg-[var(--bg-tertiary)] text-[var(--text-bright)] hover:bg-[color-mix(in_srgb,var(--text-bright)_10%,var(--bg-tertiary))]";
const pillWarn =
  "bg-[color-mix(in_srgb,#f59e0b_22%,transparent)] text-[#f59e0b] hover:bg-[color-mix(in_srgb,#f59e0b_32%,transparent)]";
const pillDanger =
  "bg-[color-mix(in_srgb,#ef4444_18%,transparent)] text-[#ef4444] hover:bg-[color-mix(in_srgb,#ef4444_28%,transparent)]";

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
  const { deleteSkill, installFromDiscovery } = useSkills();
  const [filter, setFilter] = useState("");
  const hasMeta = useMemo(() => hasStats(entries), [entries]);
  const [sort, setSort] = useState<SortKey>(hasMeta ? "downloads" : "default");

  // Bulk-install state. While ``bulk`` is non-null, the per-card
  // Install / Reinstall / Update / Delete buttons are disabled so the
  // user can't queue a second op on top of the running loop. The
  // counter is updated after each request — successes and failures both
  // tick ``done``, failures additionally bump ``failed`` — so the UI
  // reflects forward progress even when a few entries in the middle
  // 404 or 5xx.
  const [bulk, setBulk] = useState<
    { done: number; total: number; failed: number } | null
  >(null);

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

  // The "Install all" bulk action operates on what the user can see —
  // i.e. ``shown`` minus anything already installed in this source's
  // namespace. That way filtering / search narrows the bulk target,
  // which is what users intuitively expect (and is the only way to
  // "install just the ones matching X" without per-card checkboxes).
  const bulkTargets = useMemo(
    () =>
      shown.filter((e) => {
        const fullName = source.slug ? `${source.slug}/${e.name}` : e.name;
        return !installedNames.has(fullName);
      }),
    [shown, source.slug, installedNames],
  );

  async function handleBulkInstall() {
    if (bulk) return;
    const queue = bulkTargets.map((e) => e.name);
    if (queue.length === 0) return;
    setBulk({ done: 0, total: queue.length, failed: 0 });
    for (const name of queue) {
      try {
        await installFromDiscovery(source.url, name, source.slug);
        setBulk((prev) =>
          prev ? { ...prev, done: prev.done + 1 } : prev,
        );
      } catch {
        setBulk((prev) =>
          prev
            ? { ...prev, done: prev.done + 1, failed: prev.failed + 1 }
            : prev,
        );
      }
    }
    // Hold the final count visible for a moment so users see e.g.
    // "20/24 done (4 failed)" instead of it instantly flipping back to
    // the idle "Install all" label. 1.8s is long enough to read,
    // short enough not to feel sticky.
    window.setTimeout(() => setBulk(null), 1800);
  }

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
        {/* Bulk install — shown when there's more than one uninstalled
            entry in the current view. While the loop is running, the
            label flips to a live progress counter so users can tell at
            a glance that it's making forward progress (and where it
            stopped if a few entries fail). */}
        {(bulk || bulkTargets.length > 1) && (
          <button
            type="button"
            onClick={handleBulkInstall}
            disabled={!!bulk || bulkTargets.length === 0}
            title={
              bulk
                ? "Installing in sequence — leave this tab open"
                : `Install the ${bulkTargets.length} uninstalled skill${bulkTargets.length === 1 ? "" : "s"} matching the current filter`
            }
            className={pillBase + " shrink-0 px-4 " + pillPrimary}
          >
            {bulk
              ? `Installing ${bulk.done}/${bulk.total}` +
                (bulk.failed > 0 ? ` (${bulk.failed} failed)` : "") +
                (bulk.done < bulk.total ? "…" : "")
              : `Install all ${bulkTargets.length}`}
          </button>
        )}
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
              className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-3 hover:border-[var(--accent-blue)] transition-colors"
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
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    type="button"
                    onClick={() => onInstall(source, e.name)}
                    disabled={installingKey === key || !!bulk}
                    className={
                      pillBase + " " +
                      (outdated ? pillWarn : installed ? pillNeutral : pillPrimary)
                    }
                  >
                    {installingKey === key
                      ? "…"
                      : outdated
                        ? "Update"
                        : installed
                          ? "Reinstall"
                          : "Install"}
                  </button>
                  {installed && (
                    <button
                      type="button"
                      title={`Uninstall ${fullName}`}
                      aria-label={`Uninstall ${fullName}`}
                      disabled={!!bulk}
                      onClick={() => {
                        if (confirm(`Delete skill "${fullName}"?`)) {
                          void deleteSkill(fullName);
                        }
                      }}
                      className={pillBase + " w-7 px-0 " + pillDanger}
                    >
                      {/* trash icon */}
                      <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
                        <path d="M8 3h4a1 1 0 0 1 1 1v1h3.5a.75.75 0 0 1 0 1.5H16l-.83 9.13A2 2 0 0 1 13.18 17H6.82a2 2 0 0 1-1.99-1.87L4 6.5h-.5a.75.75 0 0 1 0-1.5H7V4a1 1 0 0 1 1-1Zm.5 2h3V4h-3v1ZM8 8.25a.75.75 0 0 1 .75.75v5a.75.75 0 0 1-1.5 0v-5A.75.75 0 0 1 8 8.25Zm4 0a.75.75 0 0 1 .75.75v5a.75.75 0 0 1-1.5 0v-5A.75.75 0 0 1 12 8.25Z"/>
                      </svg>
                    </button>
                  )}
                </div>
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

