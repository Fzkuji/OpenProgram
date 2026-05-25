"use client";

import { useEffect, useMemo, useState } from "react";
import { useSkills, type CatalogEntry, type Skill } from "@/lib/skills-store";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

type CatalogState = {
  loading: boolean;
  entries: CatalogEntry[] | null;
  error: string | null;
};

type Source = {
  url: string;
  label: string;
  description: string;
  added: boolean;
  origin: "suggested" | "custom";
};

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
  const sources: Source[] = useMemo(() => {
    const suggested = discoverySuggested.map<Source>((s) => ({
      url: s.url,
      label: s.label,
      description: s.description,
      added: s.added,
      origin: "suggested",
    }));
    const suggestedUrls = new Set(suggested.map((s) => s.url));
    const custom = discoverySources
      .filter((u) => !suggestedUrls.has(u))
      .map<Source>((u) => ({
        url: u,
        label: hostname(u) || u,
        description: u,
        added: true,
        origin: "custom",
      }));
    return [...suggested, ...custom];
  }, [discoverySuggested, discoverySources]);

  // Skill names already installed (for "Installed" marker in catalog).
  const installedNames = useMemo(() => {
    const set = new Set<string>();
    for (const s of skills as Skill[]) set.add(s.name);
    return set;
  }, [skills]);

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
    setCatalogs((c) => ({ ...c, [url]: { loading: true, entries: null, error: null } }));
    try {
      const entries = await browseDiscovery(url);
      setCatalogs((c) => ({ ...c, [url]: { loading: false, entries, error: null } }));
    } catch (e) {
      setCatalogs((c) => ({ ...c, [url]: { loading: false, entries: null, error: String(e) } }));
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

  async function handleInstallOne(url: string, name: string) {
    const key = `${url}::${name}`;
    setInstallingKey(key);
    setStatus(null);
    try {
      await installFromDiscovery(url, name);
      setStatus(`Installed ${name}`);
    } catch (e) {
      setStatus(`Failed: ${String(e)}`);
    } finally {
      setInstallingKey(null);
    }
  }

  async function handlePullAll(url: string) {
    setBulkUrl(url);
    setStatus(null);
    try {
      const pulled = await pullDiscovery(url);
      setStatus(`Installed ${pulled.length} skill${pulled.length === 1 ? "" : "s"} from ${hostname(url) || url}`);
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
            return (
              <li key={s.url} className="rounded-md border border-[var(--border)]">
                <div className="flex items-start gap-3 p-3 hover:bg-bg-hover hover:text-nav-color-hover">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-bright)]">{s.label}</span>
                      {s.origin === "custom" && (
                        <span className="rounded border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-[1px] text-[10px] uppercase tracking-wide text-[var(--text-dim)]">custom</span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-[var(--text-secondary)] line-clamp-2">{s.description}</p>
                    <p className="mt-1 text-[11px] font-mono text-[var(--text-tertiary)] truncate">{s.url}</p>
                  </div>
                  <div className="flex flex-col gap-1 items-end">
                    <Button size="sm" onClick={() => toggleExpand(s.url)}>
                      {open ? "Hide catalog" : "Browse catalog"}
                    </Button>
                    <Button size="sm" variant="outline"
                      onClick={() => handlePullAll(s.url)} disabled={bulkUrl === s.url}>
                      {bulkUrl === s.url ? "Installing…" : "Install all"}
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
                        url={s.url}
                        installedNames={installedNames}
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
  entries, url, installedNames, installingKey, onInstall,
}: {
  entries: CatalogEntry[];
  url: string;
  installedNames: Set<string>;
  installingKey: string | null;
  onInstall: (url: string, name: string) => void;
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
          const key = `${url}::${e.name}`;
          const installed = installedNames.has(e.name);
          return (
            <li key={e.name}
              className="flex items-start gap-3 rounded px-2 py-2 hover:bg-bg-hover hover:text-nav-color-hover">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[12px] text-[var(--text-bright)]">{e.name}</span>
                  {installed && (
                    <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-[1px] text-[10px] uppercase tracking-wide text-emerald-400">installed</span>
                  )}
                </div>
                {e.description && (
                  <p className="mt-0.5 text-xs text-[var(--text-secondary)] line-clamp-2">{e.description}</p>
                )}
              </div>
              <Button size="sm" variant={installed ? "outline" : "default"}
                onClick={() => onInstall(url, e.name)}
                disabled={installingKey === key}>
                {installingKey === key ? "Installing…" : installed ? "Reinstall" : "Install"}
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
