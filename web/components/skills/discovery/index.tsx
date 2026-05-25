"use client";

/**
 * Skills Discovery panel — pick + install skills from configured
 * catalog sources (ClawHub registry, custom repos, etc.). Originally
 * a single 588-line file; split into:
 *   - types.ts          CatalogState / Source / SortKey
 *   - helpers.ts        slugFromUrl / hasStats / fmtCount /
 *                       relTime / hostname
 *   - catalog-list.tsx  grid + search + sort of one source's entries
 *   - index.tsx         this file — the source list + install plumbing
 */
import { useEffect, useMemo, useState } from "react";

import { useSkills, type CatalogEntry, type Skill } from "@/lib/skills-store";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

import { CatalogList } from "./catalog-list";
import { hostname, slugFromUrl } from "./helpers";
import type { CatalogState, Source } from "./types";

export function DiscoverySources() {
  const {
    skills,
    discoverySources, discoverySuggested,
    fetchDiscoverySources, fetchDiscoverySuggested,
    addDiscoverySource, removeDiscoverySource,
    browseDiscovery, installFromDiscovery,
  } = useSkills();

  const [newUrl, setNewUrl] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [catalogs, setCatalogs] = useState<Record<string, CatalogState>>({});
  const [installingKey, setInstallingKey] = useState<string | null>(null);
  const [bulkUrl, setBulkUrl] = useState<string | null>(null);
  const [checkingUrl, setCheckingUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    fetchDiscoverySources();
    fetchDiscoverySuggested();
  }, [fetchDiscoverySources, fetchDiscoverySuggested]);

  // Merge suggested + custom into one list, suggested first.
  // Custom entries that share a slug with a suggested entry are dropped —
  // they install into the same namespace folder so they'd be redundant
  // cards (and were a recurring source of confusion).
  const sources: Source[] = useMemo(() => {
    const suggested = discoverySuggested.map<Source>((s) => ({
      url: s.url,
      label: s.label,
      slug: s.slug || slugFromUrl(s.url),
      description: s.description,
      added: s.added,
      origin: "suggested",
    }));
    const suggestedUrls = new Set(suggested.map((s) => s.url));
    const suggestedSlugs = new Set(suggested.map((s) => s.slug));
    const custom = discoverySources
      .filter((u) => !suggestedUrls.has(u))
      .map<Source>((u) => ({
        url: u,
        label: hostname(u) || u,
        slug: slugFromUrl(u),
        description: "",
        added: true,
        origin: "custom",
      }))
      .filter((c) => !suggestedSlugs.has(c.slug));
    return [...suggested, ...custom];
  }, [discoverySuggested, discoverySources]);

  // Names already installed (full path including namespace) — used to mark
  // "Installed" on catalog rows.
  const installedNames = useMemo(() => {
    const set = new Set<string>();
    for (const s of skills as Skill[]) set.add(s.name);
    return set;
  }, [skills]);

  // How many skills from each source are already installed in its namespace,
  // counted by matching the slug prefix against the global skills list.
  const installedCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const src of sources) {
      const prefix = src.slug + "/";
      counts[src.url] = (skills as Skill[]).filter((sk) => sk.name.startsWith(prefix)).length;
    }
    return counts;
  }, [sources, skills]);

  function toggleExpand(url: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(url)) {
        next.delete(url);
      } else {
        next.add(url);
        if (!catalogs[url]) loadCatalog(url);
      }
      return next;
    });
  }

  async function loadCatalog(url: string) {
    setCatalogs((c) => ({
      ...c,
      [url]: { loading: true, entries: null, error: null, outdated: new Set() },
    }));
    try {
      const entries = await browseDiscovery(url);
      // Best-effort outdated diff — runs server-side, ignore failure.
      let outdated = new Set<string>();
      try {
        const r = await fetch(`/api/skills/discovery/diff?url=${encodeURIComponent(url)}`);
        if (r.ok) {
          const d: { outdated?: string[] } = await r.json();
          outdated = new Set(d.outdated || []);
        }
      } catch { /* ignore */ }
      setCatalogs((c) => ({
        ...c,
        [url]: { loading: false, entries, error: null, outdated },
      }));
    } catch (e) {
      setCatalogs((c) => ({
        ...c,
        [url]: { loading: false, entries: null, error: String(e), outdated: new Set() },
      }));
    }
  }

  async function handleAddCustom() {
    const url = newUrl.trim();
    if (!url) return;
    await addDiscoverySource(url);
    setNewUrl("");
    setExpanded((p) => new Set(p).add(url));
    loadCatalog(url);
  }

  async function handleInstallOne(source: Source, name: string) {
    const key = `${source.url}::${name}`;
    setInstallingKey(key);
    setStatus(null);
    try {
      const full = await installFromDiscovery(source.url, name, source.slug);
      setStatus(`Installed ${full}`);
    } catch (e) {
      setStatus(`Failed: ${String(e)}`);
    } finally {
      setInstallingKey(null);
    }
  }

  async function handleRefreshDiff(source: Source) {
    setCheckingUrl(source.url);
    setStatus(null);
    try {
      const r = await fetch(
        `/api/skills/discovery/diff?url=${encodeURIComponent(source.url)}`,
      );
      if (r.ok) {
        const d: { outdated?: string[] } = await r.json();
        const newOutdated = new Set(d.outdated || []);
        setCatalogs((c) => {
          const prev = c[source.url] ?? {
            loading: false,
            entries: null,
            error: null,
            outdated: new Set<string>(),
          };
          return { ...c, [source.url]: { ...prev, outdated: newOutdated } };
        });
        setStatus(
          newOutdated.size > 0
            ? `${source.label}: ${newOutdated.size} update${newOutdated.size === 1 ? "" : "s"} available`
            : `${source.label}: already up to date`,
        );
      } else {
        setStatus(`Check failed: HTTP ${r.status}`);
      }
    } catch (e) {
      setStatus(`Check failed: ${String(e)}`);
    } finally {
      setCheckingUrl(null);
    }
  }

  async function handleUpdateOutdated(source: Source) {
    const cat = catalogs[source.url];
    if (!cat?.outdated || cat.outdated.size === 0) return;
    setBulkUrl(source.url);
    setStatus(null);
    let ok = 0;
    let fail = 0;
    try {
      for (const fullName of Array.from(cat.outdated)) {
        const short = fullName.includes("/")
          ? fullName.slice(fullName.indexOf("/") + 1)
          : fullName;
        try {
          await installFromDiscovery(source.url, short, source.slug);
          ok += 1;
        } catch {
          fail += 1;
        }
      }
      setStatus(
        `Updated ${ok}/${cat.outdated.size} outdated skill${ok === 1 ? "" : "s"}` +
          (fail ? ` (${fail} failed)` : ""),
      );
      // Refresh diff so the badge counts catch up.
      try {
        const r = await fetch(
          `/api/skills/discovery/diff?url=${encodeURIComponent(source.url)}`,
        );
        if (r.ok) {
          const d: { outdated?: string[] } = await r.json();
          setCatalogs((c) => ({
            ...c,
            [source.url]: { ...c[source.url], outdated: new Set(d.outdated || []) },
          }));
        }
      } catch {
        /* ignore */
      }
    } finally {
      setBulkUrl(null);
    }
  }

  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-sm font-semibold text-[var(--text-bright)] mb-1">Skill catalogs</h3>
        <p className="text-xs text-[var(--text-tertiary)] mb-3">
          Discovery only finds and downloads skills. Installed skills appear in the <strong>Browse</strong> tab — enable, disable or delete them there.
        </p>
        <ul className="space-y-2">
          {sources.map((s) => {
            const open = expanded.has(s.url);
            const cat = catalogs[s.url];
            const installed = installedCounts[s.url] || 0;
            const catalogTotal = cat?.entries?.length;
            const outdatedCount = cat?.outdated?.size ?? 0;
            return (
              <li key={s.url} className="rounded-md border border-[var(--border)] overflow-hidden">
                <div
                  role="button"
                  onClick={() => toggleExpand(s.url)}
                  className="flex items-start gap-3 p-3 cursor-pointer hover:bg-bg-hover hover:text-nav-color-hover select-none"
                >
                  <span
                    className="mt-[2px] text-[var(--text-tertiary)] shrink-0 w-3 text-center"
                    aria-hidden
                  >
                    {open ? "▾" : "▸"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-[var(--text-bright)]">{s.label}</span>
                      {installed > 0 && (
                        <span
                          className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-[1px] text-[10px] uppercase tracking-wide text-emerald-400"
                          title={`Installed under ${s.slug}/`}
                        >
                          {catalogTotal !== undefined
                            ? `${installed}/${catalogTotal} installed`
                            : `${installed} installed`}
                        </span>
                      )}
                      {outdatedCount > 0 && (
                        <span
                          className="rounded border border-amber-500/40 bg-amber-500/15 px-2 py-[1px] text-[10px] uppercase tracking-wide text-amber-400"
                          title="Local SKILL.md content differs from upstream"
                        >
                          {outdatedCount} outdated
                        </span>
                      )}
                      {s.origin === "custom" && (
                        <span className="rounded border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-[1px] text-[10px] uppercase tracking-wide text-[var(--text-dim)]">custom</span>
                      )}
                    </div>
                    {s.description && (
                      <p className="mt-1 text-xs text-[var(--text-secondary)] line-clamp-2">{s.description}</p>
                    )}
                    <p className="mt-1 text-[11px] font-mono text-[var(--text-tertiary)] truncate">{s.url}</p>
                  </div>
                  {(installed > 0 || s.origin === "custom") && (
                    <div
                      className="flex items-center gap-2 shrink-0 self-center"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {installed > 0 && (
                        <Button
                          size="sm"
                          variant={outdatedCount > 0 ? "default" : "outline"}
                          onClick={() => {
                            // Outdated > 0  → install the drifted skills.
                            // Outdated == 0 → re-check upstream (no destructive op).
                            if (outdatedCount > 0) handleUpdateOutdated(s);
                            else handleRefreshDiff(s);
                          }}
                          disabled={bulkUrl === s.url || checkingUrl === s.url}
                          title={
                            outdatedCount > 0
                              ? `Re-pull ${outdatedCount} skill${outdatedCount === 1 ? "" : "s"} whose upstream SKILL.md changed`
                              : "Check upstream for new versions"
                          }
                          className="group min-w-[110px]"
                        >
                          {bulkUrl === s.url ? (
                            "Updating…"
                          ) : checkingUrl === s.url ? (
                            "Checking…"
                          ) : outdatedCount > 0 ? (
                            <>
                              <span className="group-hover:hidden">{`Update ${outdatedCount}`}</span>
                              <span className="hidden group-hover:inline">↻ Check again</span>
                            </>
                          ) : (
                            <>
                              <span className="group-hover:hidden">Up to date</span>
                              <span className="hidden group-hover:inline">↻ Check now</span>
                            </>
                          )}
                        </Button>
                      )}
                      {s.origin === "custom" && (
                        <Button size="sm" variant="destructive"
                          onClick={() => removeDiscoverySource(s.url)}>Remove</Button>
                      )}
                    </div>
                  )}
                </div>
                {open && (
                  <div className="border-t border-[var(--border)] p-3 bg-[var(--bg-secondary)]/40">
                    {cat?.loading && (
                      <div className="text-xs text-[var(--text-tertiary)]">Loading catalog…</div>
                    )}
                    {cat?.error && (
                      <div className="text-xs text-[var(--accent-red,#ef4444)]">{cat.error}</div>
                    )}
                    {cat?.entries && (
                      <CatalogList
                        entries={cat.entries}
                        source={s}
                        installedNames={installedNames}
                        outdatedNames={cat.outdated}
                        installingKey={installingKey}
                        onInstall={handleInstallOne}
                      />
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-[var(--text-bright)] mb-2">Add a source</h3>
        <p className="text-xs text-[var(--text-tertiary)] mb-2">
          Paste a GitHub repo URL (e.g. <code>https://github.com/owner/repo</code>) or a JSON index URL.
        </p>
        <div className="flex gap-2">
          <Input
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
          />
          <Button onClick={handleAddCustom}>Add</Button>
        </div>
      </section>

      {status && <div className="text-xs text-[var(--text-secondary)]">{status}</div>}
    </div>
  );
}

