"use client";

import { useMemo, useRef, useState } from "react";

import type { CatalogEntry } from "@/lib/skills-store";
import { useSkills } from "@/lib/skills-store";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  SearchIcon,
  XIcon,
} from "@/components/animated-icons";

import { fmtCount, hasStats, relTime } from "./helpers";
import type { Source, SortKey } from "./types";

// Pill-shaped action buttons. Design philosophy: every button looks
// the same in its idle state — a subtle ``bg-tertiary`` chip that sits
// one shade brighter than the card surface, with muted text. The
// button's *intent* (install / update / destroy / etc.) only emerges
// on hover, when the appropriate accent tints the fill and recolors
// the label. Idle UI stays calm; the user reads intent the moment
// they reach for a control. Applies equally to the per-card pills here
// and the source-row pills in ``index.tsx``.
export const pillBase =
  "inline-flex items-center justify-center h-7 rounded-full px-3 text-[12px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none border border-transparent";
const pillIdle =
  "bg-[var(--bg-tertiary)] text-[var(--text-secondary)]";
export const pillPrimary =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,var(--accent-blue)_22%,transparent)] hover:text-[var(--accent-blue)]";
export const pillNeutral =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,var(--text-bright)_10%,var(--bg-tertiary))] hover:text-[var(--text-bright)]";
export const pillWarn =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,#f59e0b_22%,transparent)] hover:text-[#f59e0b]";
export const pillDanger =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,#ef4444_18%,transparent)] hover:text-[#ef4444]";

export function CatalogList({
  entries, source, installedNames, outdatedNames, installingKey, onInstall,
  bulkBusy,
}: {
  entries: CatalogEntry[];
  source: Source;
  installedNames: Set<string>;
  outdatedNames: Set<string>;
  installingKey: string | null;
  onInstall: (source: Source, name: string) => void;
  // True while the parent (index.tsx) is running a bulk install /
  // bulk uninstall against this source. Per-card actions are disabled
  // so a user can't stack a Reinstall on top of a 200-skill batch
  // and confuse the queue.
  bulkBusy: boolean;
}) {
  const { text, locale } = useTranslation();
  const { deleteSkill } = useSkills();
  const searchIconRef = useRef<AnimatedNavIconHandle>(null);
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
        <div
          className="relative flex-1"
          onMouseEnter={() => searchIconRef.current?.startAnimation?.()}
          onMouseLeave={() => searchIconRef.current?.stopAnimation?.()}
        >
          <SearchIcon
            ref={searchIconRef}
            size={14}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]"
          />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={text(
              `Search ${entries.length} skill${entries.length === 1 ? "" : "s"}...`,
              `搜索 ${entries.length} 个技能...`,
            )}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-input)] py-2 pl-9 pr-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none focus:border-[var(--accent-blue)] focus:ring-2 focus:ring-[var(--accent-blue)]/20"
          />
        </div>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="shrink-0 rounded-md border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-blue)]"
        >
          <option value="default">{text("Sort", "排序")}：{hasMeta ? text("Trending", "热门") : text("Default", "默认")}</option>
          <option value="name">{text("Name", "名称")}</option>
          {hasMeta && <option value="downloads">{text("Most downloaded", "下载最多")}</option>}
          {hasMeta && <option value="stars">{text("Most starred", "收藏最多")}</option>}
          {hasMeta && <option value="updated">{text("Recently updated", "最近更新")}</option>}
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
                    <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-emerald-400">{text("in", "已装")}</span>
                  )}
                  {outdated && (
                    <span className="rounded border border-amber-500/40 bg-amber-500/15 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-amber-400" title={text("Upstream changed", "上游已变化")}>{text("old", "旧")}</span>
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
                      <span title={text("stars", "星标")}>★ {fmtCount(e.stars)}</span>
                    )}
                    {(e.downloads || 0) > 0 && (
                      <span title={text("downloads", "下载")}>↓ {fmtCount(e.downloads)}</span>
                    )}
                    {(e.updated_at || 0) > 0 && (
                      <span title={text("last updated", "最近更新")}>{relTime(e.updated_at, locale)}</span>
                    )}
                  </div>
                ) : (
                  <span />
                )}
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    type="button"
                    onClick={() => onInstall(source, e.name)}
                    disabled={installingKey === key || bulkBusy}
                    className={
                      pillBase + " " +
                      (outdated ? pillWarn : installed ? pillNeutral : pillPrimary)
                    }
                  >
                    {installingKey === key
                      ? "…"
                      : outdated
                        ? text("Update", "更新")
                        : installed
                          ? text("Reinstall", "重新安装")
                          : text("Install", "安装")}
                  </button>
                  {installed && (
                    <button
                      type="button"
                      title={text(`Uninstall ${fullName}`, `卸载 ${fullName}`)}
                      aria-label={text(`Uninstall ${fullName}`, `卸载 ${fullName}`)}
                      disabled={bulkBusy}
                      onClick={() => {
                        if (confirm(text(`Delete skill "${fullName}"?`, `删除技能“${fullName}”？`))) {
                          void deleteSkill(fullName);
                        }
                      }}
                      className={pillBase + " gap-1 " + pillDanger}
                    >
                      {/* Animated delete glyph (X) — the app-standard delete
                          icon used across the branch / project menus. */}
                      <XIcon size={14} aria-hidden />
                      Remove
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {shown.length === 0 && (
          <div className="col-span-full text-xs text-[var(--text-tertiary)] py-2">{text("No matches.", "没有匹配结果。")}</div>
        )}
      </div>
    </div>
  );
}
