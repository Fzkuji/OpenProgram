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

import { useSkills, type Skill } from "@/lib/skills-store";
import { useTranslation } from "@/lib/i18n";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

import {
  CatalogList,
  pillBase,
  pillPrimary,
  pillNeutral,
  pillWarn,
  pillDanger,
} from "./catalog-list";
import { hostname, slugFromUrl } from "./helpers";
import type { CatalogState, Source } from "./types";

export function DiscoverySources() {
  const { text } = useTranslation();
  const {
    skills,
    discoverySources, discoverySuggested,
    fetchDiscoverySources, fetchDiscoverySuggested,
    addDiscoverySource, removeDiscoverySource,
    browseDiscovery, installFromDiscovery,
    deleteSkill,
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
      setStatus(text(`Installed ${full}`, `已安装 ${full}`));
    } catch (e) {
      setStatus(text(`Failed: ${String(e)}`, `失败：${String(e)}`));
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
            ? text(
                `${source.label}: ${newOutdated.size} update${newOutdated.size === 1 ? "" : "s"} available`,
                `${source.label}：有 ${newOutdated.size} 个更新`,
              )
            : text(`${source.label}: already up to date`, `${source.label}：已是最新`),
        );
      } else {
        setStatus(text(`Check failed: HTTP ${r.status}`, `检查失败：HTTP ${r.status}`));
      }
    } catch (e) {
      setStatus(text(`Check failed: ${String(e)}`, `检查失败：${String(e)}`));
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
        text(
          `Updated ${ok}/${cat.outdated.size} outdated skill${ok === 1 ? "" : "s"}` +
            (fail ? ` (${fail} failed)` : ""),
          `已更新 ${ok}/${cat.outdated.size} 个过期技能` +
            (fail ? `（${fail} 个失败）` : ""),
        ),
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

  // -- Bulk install ----------------------------------------------------
  // Confirm + sequentially install every catalog entry not already in
  // the user's skills list. We hand the count to ``window.confirm``
  // explicitly because some catalogs are huge (Reza's Mega Pack ships
  // ~300 skills) and an accidental click would otherwise queue a
  // 30-minute background download with no undo affordance.
  async function handleBulkInstall(source: Source, toInstall: string[]) {
    if (toInstall.length === 0) return;
    const ok = window.confirm(
      `Install all ${toInstall.length} skill${toInstall.length === 1 ? "" : "s"} ` +
        `from ${source.label} into the “${source.slug}/” namespace?\n\n` +
        `Each entry is downloaded sequentially — large catalogs can take a while. ` +
        `You can cancel by closing the tab; partial installs are kept.`,
    );
    if (!ok) return;
    setBulkUrl(source.url);
    setStatus(`Installing 0/${toInstall.length} from ${source.label}…`);
    let done = 0;
    let fail = 0;
    try {
      for (const name of toInstall) {
        try {
          await installFromDiscovery(source.url, name, source.slug);
        } catch {
          fail += 1;
        }
        done += 1;
        setStatus(
          `Installing ${done}/${toInstall.length} from ${source.label}` +
            (fail ? ` (${fail} failed)` : "") + "…",
        );
      }
      setStatus(
        `Installed ${done - fail}/${toInstall.length} from ${source.label}` +
          (fail ? ` (${fail} failed)` : ""),
      );
    } finally {
      setBulkUrl(null);
    }
  }

  // -- Bulk uninstall --------------------------------------------------
  // Walk the live ``skills`` list (not the catalog) so we delete
  // every skill currently under the source's namespace, even ones
  // the catalog has since dropped. Same confirm + sequential loop +
  // status pattern as the install path; ``deleteSkill`` already
  // refreshes ``skills`` via the store so the green "N/N installed"
  // badge ticks down in real time.
  async function handleBulkUninstall(source: Source) {
    const prefix = source.slug + "/";
    const toRemove = (skills as Skill[])
      .filter((sk) => sk.name.startsWith(prefix))
      .map((sk) => sk.name);
    if (toRemove.length === 0) return;
    const ok = window.confirm(
      `Uninstall all ${toRemove.length} skill${toRemove.length === 1 ? "" : "s"} ` +
        `currently installed under “${source.slug}/”?\n\n` +
        `This deletes them from disk. You can reinstall any of them from ` +
        `this catalog later.`,
    );
    if (!ok) return;
    setBulkUrl(source.url);
    setStatus(`Uninstalling 0/${toRemove.length} from ${source.label}…`);
    let done = 0;
    let fail = 0;
    try {
      for (const fullName of toRemove) {
        try {
          await deleteSkill(fullName);
        } catch {
          fail += 1;
        }
        done += 1;
        setStatus(
          `Uninstalling ${done}/${toRemove.length} from ${source.label}` +
            (fail ? ` (${fail} failed)` : "") + "…",
        );
      }
      setStatus(
        `Uninstalled ${done - fail}/${toRemove.length} from ${source.label}` +
          (fail ? ` (${fail} failed)` : ""),
      );
    } finally {
      setBulkUrl(null);
    }
  }

  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-sm font-semibold text-[var(--text-bright)] mb-1">{text("Skill catalogs", "技能目录")}</h3>
        <p className="text-xs text-[var(--text-tertiary)] mb-3">
          {text("Discovery only finds and downloads skills. Installed skills appear in the ", "发现页只负责查找和下载技能。已安装技能会显示在")}
          <strong>{text("Browse", "浏览")}</strong>
          {text(" tab. Enable, disable or delete them there.", "标签页，可以在那里启用、禁用或删除。")}
        </p>
        <ul className="space-y-2">
          {sources.map((s) => {
            const open = expanded.has(s.url);
            const cat = catalogs[s.url];
            const installed = installedCounts[s.url] || 0;
            const catalogTotal = cat?.entries?.length;
            const outdatedCount = cat?.outdated?.size ?? 0;
            // Names in this catalog that aren't already installed
            // under the source's namespace. Drives the "Install all N"
            // button on the source row; empty list means we either
            // haven't loaded the catalog yet (no button) or the user
            // is fully installed (Update button shows instead).
            const uninstalledNames: string[] = cat?.entries
              ? cat.entries
                  .filter((e) => !installedNames.has(
                    s.slug ? `${s.slug}/${e.name}` : e.name,
                  ))
                  .map((e) => e.name)
              : [];
            const uninstalledCount = uninstalledNames.length;
            const isBulkBusy = bulkUrl === s.url;
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
                          title={text(`Installed under ${s.slug}/`, `安装在 ${s.slug}/ 下`)}
                        >
                          {catalogTotal !== undefined
                            ? text(`${installed}/${catalogTotal} installed`, `已安装 ${installed}/${catalogTotal}`)
                            : text(`${installed} installed`, `已安装 ${installed}`)}
                        </span>
                      )}
                      {outdatedCount > 0 && (
                        <span
                          className="rounded border border-amber-500/40 bg-amber-500/15 px-2 py-[1px] text-[10px] uppercase tracking-wide text-amber-400"
                          title={text("Local SKILL.md content differs from upstream", "本地 SKILL.md 与上游不同")}
                        >
                          {text(`${outdatedCount} outdated`, `${outdatedCount} 个过期`)}
                        </span>
                      )}
                      {s.origin === "custom" && (
                        <span className="rounded border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-[1px] text-[10px] uppercase tracking-wide text-[var(--text-dim)]">{text("custom", "自定义")}</span>
                      )}
                    </div>
                    {s.description && (
                      <p className="mt-1 text-xs text-[var(--text-secondary)] line-clamp-2">{s.description}</p>
                    )}
                    <p className="mt-1 text-[11px] font-mono text-[var(--text-tertiary)] truncate">{s.url}</p>
                  </div>
                  {(installed > 0 || uninstalledCount > 0 || s.origin === "custom") && (
                    <div
                      className="flex items-center gap-2 shrink-0 self-center"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {/* Source-row pills use the SAME idle-neutral /
                          colored-on-hover system as the per-card pills.
                          Intent (install / update / destroy) is signalled
                          by which accent the row reveals when the cursor
                          lands on it, not by a permanent paint job. Each
                          gets ``min-w-[110px]`` so a row with all four
                          chips stays grid-aligned. */}
                      {uninstalledCount > 0 && (
                        <button
                          type="button"
                          onClick={() => handleBulkInstall(s, uninstalledNames)}
                          disabled={isBulkBusy || checkingUrl === s.url}
                          title={`Install the ${uninstalledCount} skill${uninstalledCount === 1 ? "" : "s"} in this catalog not already present locally`}
                          className={pillBase + " min-w-[110px] " + pillPrimary}
                        >
                          {isBulkBusy ? "Working…" : `Install ${uninstalledCount}`}
                        </button>
                      )}
                      {installed > 0 && (
                        <button
                          type="button"
                          onClick={() => {
                            // Outdated > 0  → install the drifted skills.
                            // Outdated == 0 → re-check upstream (no destructive op).
                            if (outdatedCount > 0) handleUpdateOutdated(s);
                            else handleRefreshDiff(s);
                          }}
                          disabled={isBulkBusy || checkingUrl === s.url}
                          title={
                            outdatedCount > 0
                              ? text(
                                  `Re-pull ${outdatedCount} skill${outdatedCount === 1 ? "" : "s"} whose upstream SKILL.md changed`,
                                  `重新拉取 ${outdatedCount} 个上游 SKILL.md 已变化的技能`,
                                )
                              : text("Check upstream for new versions", "检查上游新版本")
                          }
                          className={
                            pillBase + " group min-w-[110px] " +
                            (outdatedCount > 0 ? pillWarn : pillNeutral)
                          }
                        >
                          {isBulkBusy ? (
                            text("Updating...", "更新中...")
                          ) : checkingUrl === s.url ? (
                            text("Checking...", "检查中...")
                          ) : outdatedCount > 0 ? (
                            <>
                              <span className="group-hover:hidden">{text(`Update ${outdatedCount}`, `更新 ${outdatedCount}`)}</span>
                              <span className="hidden group-hover:inline">↻ {text("Check again", "重新检查")}</span>
                            </>
                          ) : (
                            <>
                              <span className="group-hover:hidden">{text("Up to date", "已是最新")}</span>
                              <span className="hidden group-hover:inline">↻ {text("Check now", "立即检查")}</span>
                            </>
                          )}
                        </button>
                      )}
                      {installed > 0 && (
                        <button
                          type="button"
                          onClick={() => handleBulkUninstall(s)}
                          disabled={isBulkBusy}
                          title={`Delete the ${installed} skill${installed === 1 ? "" : "s"} installed from this catalog`}
                          className={pillBase + " min-w-[110px] " + pillDanger}
                        >
                          {isBulkBusy ? "Working…" : `Uninstall ${installed}`}
                        </button>
                      )}
                      {s.origin === "custom" && (
                        <button
                          type="button"
                          onClick={() => removeDiscoverySource(s.url)}
                          className={pillBase + " " + pillNeutral}
                        >
                          {text("Remove source", "移除来源")}
                        </button>
                      )}
                    </div>
                  )}
                </div>
                {open && (
                  <div className="border-t border-[var(--border)] p-3 bg-[var(--bg-secondary)]/40">
                    {cat?.loading && (
                      <div className="text-xs text-[var(--text-tertiary)]">{text("Loading catalog...", "正在加载目录...")}</div>
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
                        bulkBusy={isBulkBusy}
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
        <h3 className="text-sm font-semibold text-[var(--text-bright)] mb-2">{text("Add a source", "添加来源")}</h3>
        <p className="text-xs text-[var(--text-tertiary)] mb-2">
          {text("Paste a GitHub repo URL (e.g. ", "粘贴 GitHub 仓库 URL（例如 ")}
          <code>https://github.com/owner/repo</code>
          {text(") or a JSON index URL.", "）或 JSON 索引 URL。")}
        </p>
        <div className="flex gap-2">
          <Input
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
          />
          <Button onClick={handleAddCustom}>{text("Add", "添加")}</Button>
        </div>
      </section>

      {status && <div className="text-xs text-[var(--text-secondary)]">{status}</div>}
    </div>
  );
}
