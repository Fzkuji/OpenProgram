"use client";

import { useEffect, useMemo, useState } from "react";
import { useSkills, type CatalogEntry, type Skill } from "@/lib/skills-store";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

type CatalogState = {
  loading: boolean;
  entries: CatalogEntry[] | null;
  error: string | null;
  outdated: Set<string>;  // namespaced names whose local SKILL.md hash drifted
};

type Source = {
  url: string;
  label: string;
  slug: string;          // namespace folder used when installing from this source
  description: string;
  added: boolean;
  origin: "suggested" | "custom";
};

function slugFromUrl(url: string): string {
  // Mirror backend _default_namespace logic so the "installed" badge in the
  // catalog matches what the install endpoint will write.
  const gh = url.match(
    /^(?:https?:\/\/github\.com\/|github:\/\/)[^/]+\/([^/@]+?)(?:\.git)?(?:\/|@|$)/,
  );
  if (gh) return gh[1].toLowerCase();
  try {
    const h = new URL(url).hostname;
    return h.replace(/\./g, "-").toLowerCase() || "remote";
  } catch {
    return "remote";
  }
}

export function DiscoverySources() {
  const {
    skills,
    discoverySources, discoverySuggested,
    fetchDiscoverySources, fetchDiscoverySuggested,
    addDiscoverySource, removeDiscoverySource,
    browseDiscovery, installFromDiscovery, pullDiscovery,
  } = useSkills();

  const [newUrl, setNewUrl] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [catalogs, setCatalogs] = useState<Record<string, CatalogState>>({});
  const [installingKey, setInstallingKey] = useState<string | null>(null);
  const [bulkUrl, setBulkUrl] = useState<string | null>(null);
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

  async function handlePullAll(source: Source) {
    setBulkUrl(source.url);
    setStatus(null);
    try {
      const pulled = await pullDiscovery(source.url, source.slug);
      setStatus(`Installed ${pulled.length} skill${pulled.length === 1 ? "" : "s"} into ${source.slug}/`);
    } catch (e) {
      setStatus(`Failed: ${String(e)}`);
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
            const allInstalled =
              catalogTotal !== undefined && installed >= catalogTotal && catalogTotal > 0;
            const outdatedCount = cat?.outdated?.size ?? 0;
            return (
              <li key={s.url} className="rounded-md border border-[var(--border)]">
                <div className="flex items-start gap-3 p-3 hover:bg-bg-hover hover:text-nav-color-hover">
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
                          title="Local SKILL.md content differs from upstream — click Reinstall all to sync."
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
                  <div className="flex flex-col gap-1 items-end">
                    <Button size="sm" onClick={() => toggleExpand(s.url)}>
                      {open ? "Hide catalog" : "Browse catalog"}
                    </Button>
                    <Button size="sm" variant={installed > 0 ? "outline" : "default"}
                      onClick={() => handlePullAll(s)} disabled={bulkUrl === s.url}>
                      {bulkUrl === s.url
                        ? "Installing…"
                        : allInstalled
                          ? "Reinstall all"
                          : catalogTotal !== undefined && installed < catalogTotal
                            ? `Install ${catalogTotal - installed} missing`
                            : installed > 0
                              ? "Reinstall"
                              : "Install all"}
                    </Button>
                    {s.origin === "custom" && (
                      <Button size="sm" variant="destructive"
                        onClick={() => removeDiscoverySource(s.url)}>Remove source</Button>
                    )}
                  </div>
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

function CatalogList({
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
  const shown = useMemo(() => {
    if (!filter.trim()) return entries;
    const q = filter.toLowerCase();
    return entries.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q),
    );
  }, [entries, filter]);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <Input value={filter} onChange={(e) => setFilter(e.target.value)}
          placeholder={`Filter ${entries.length} skill${entries.length === 1 ? "" : "s"}…`} />
      </div>
      <ul className="space-y-1">
        {shown.map((e) => {
          const key = `${source.url}::${e.name}`;
          // Resolve the installed-path the same way the backend writes it.
          const fullName = source.slug ? `${source.slug}/${e.name}` : e.name;
          const installed = installedNames.has(fullName);
          const outdated = outdatedNames.has(fullName);
          return (
            <li key={e.name}
              className="flex items-start gap-3 rounded px-2 py-2 hover:bg-bg-hover hover:text-nav-color-hover">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[12px] text-[var(--text-bright)]">{e.name}</span>
                  {installed && !outdated && (
                    <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-[1px] text-[10px] uppercase tracking-wide text-emerald-400">installed</span>
                  )}
                  {outdated && (
                    <span className="rounded border border-amber-500/40 bg-amber-500/15 px-2 py-[1px] text-[10px] uppercase tracking-wide text-amber-400" title="Upstream SKILL.md differs from the local copy">outdated</span>
                  )}
                </div>
                {e.description && (
                  <p className="mt-0.5 text-xs text-[var(--text-secondary)] line-clamp-2">{e.description}</p>
                )}
              </div>
              <Button size="sm" variant={outdated ? "default" : installed ? "outline" : "default"}
                onClick={() => onInstall(source, e.name)}
                disabled={installingKey === key}>
                {installingKey === key ? "Installing…" : outdated ? "Update" : installed ? "Reinstall" : "Install"}
              </Button>
            </li>
          );
        })}
        {shown.length === 0 && (
          <li className="text-xs text-[var(--text-tertiary)] py-2">No matches.</li>
        )}
      </ul>
    </div>
  );
}

function hostname(u: string): string {
  try {
    return new URL(u).hostname;
  } catch {
    return "";
  }
}
