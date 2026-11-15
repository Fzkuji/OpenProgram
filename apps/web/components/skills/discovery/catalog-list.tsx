"use client";

import { useMemo, useState } from "react";

import type { CatalogEntry } from "@/lib/abilities/skills-store";
import { useSkills } from "@/lib/abilities/skills-store";
import { useTranslation } from "@/lib/i18n";
import { XIcon } from "@/components/animated-icons";

import { fmtCount, hasStats, relTime } from "./helpers";
import type { Source, SortKey } from "./types";
import { SimpleSelect } from "@/components/ui/select";
import { SearchInput } from "@/components/ui/search-input";
import { Button } from "@/components/ui/button";
import { ManageCatalogCard, managePageStyles as shared } from "@/components/ui/manage-page";
import { confirmAction } from "@/components/ui/confirm-dialog";

// Pill-shaped action buttons. Design philosophy: every button looks
// the same in its idle state — a subtle ``bg-tertiary`` chip that sits
// one shade brighter than the card surface, with muted text. The
// button's *intent* (install / update / destroy / etc.) only emerges
// on hover, when the appropriate accent tints the fill and recolors
// the label. Idle UI stays calm; the user reads intent the moment
// they reach for a control. Applies equally to the per-card pills here
// and the source-row pills in ``index.tsx``.
export const pillBase =
  "inline-flex items-center justify-center h-(--ui-button-h-xs) rounded-md px-3 text-fs-xs font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none border border-transparent";
const pillIdle =
  "bg-bg-tertiary text-text-secondary";
export const pillPrimary =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,var(--accent-blue)_22%,transparent)] hover:text-accent-blue";
export const pillNeutral =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,var(--text-bright)_10%,var(--bg-tertiary))] hover:text-text-bright";
export const pillWarn =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,var(--pill-warn)_22%,transparent)] hover:text-[color:var(--pill-warn)]";
export const pillDanger =
  pillIdle +
  " hover:bg-[color-mix(in_srgb,var(--pill-danger)_18%,transparent)] hover:text-[color:var(--pill-danger)]";

export function CatalogList({
  entries, source, installedNames, outdatedNames, installingKey, onInstall,
  bulkBusy,
  externalFilter,
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
  externalFilter?: string;
}) {
  const { text, locale } = useTranslation();
  const { deleteSkill } = useSkills();
  const [filter, setFilter] = useState("");
  const hasMeta = useMemo(() => hasStats(entries), [entries]);
  const [sort, setSort] = useState<SortKey>(hasMeta ? "downloads" : "default");

  const filtered = useMemo(() => {
    const active = externalFilter !== undefined ? externalFilter : filter;
    if (!active.trim()) return entries;
    const q = active.toLowerCase();
    return entries.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        (e.description || "").toLowerCase().includes(q) ||
        (e.display_name || "").toLowerCase().includes(q),
    );
  }, [entries, filter, externalFilter]);

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
        {externalFilter === undefined && (
        <SearchInput
          className="flex-1"
          value={filter}
          onChange={setFilter}
          placeholder={text(
            `Search ${entries.length} skill${entries.length === 1 ? "" : "s"}...`,
            `搜索 ${entries.length} 个技能...`,
          )}
        />
        )}
        <SimpleSelect
          className="shrink-0"
          aria-label={text("Sort", "排序")}
          value={sort}
          onValueChange={setSort}
          options={[
            { value: "default" as SortKey, label: `${text("Sort", "排序")}：${hasMeta ? text("Trending", "热门") : text("Default", "默认")}` },
            { value: "name" as SortKey, label: text("Name", "名称") },
            ...(hasMeta ? [
              { value: "downloads" as SortKey, label: text("Most downloaded", "下载最多") },
              { value: "stars" as SortKey, label: text("Most starred", "收藏最多") },
              { value: "updated" as SortKey, label: text("Recently updated", "最近更新") },
            ] : []),
          ]}
        />
      </div>

      <div className={shared.catalogGrid}>
        {shown.map((e) => {
          const key = `${source.url}::${e.name}`;
          const fullName = source.slug ? `${source.slug}/${e.name}` : e.name;
          const installed = installedNames.has(fullName);
          const outdated = outdatedNames.has(fullName);
          const showStats = (e.stars || 0) > 0 || (e.downloads || 0) > 0;
          return (
            <ManageCatalogCard
              key={e.name}
              title={e.display_name || e.name}
              subtitle={e.display_name && e.display_name !== e.name ? e.name : e.version ? `v${e.version}` : undefined}
              description={e.description}
              status={<>
                {installed && !outdated && <span className={`${shared.badge} ${shared.badgeGreen}`}>{text("installed", "已安装")}</span>}
                {outdated && <span className={`${shared.badge} ${shared.badgeYellow}`}>{text("update", "可更新")}</span>}
              </>}
              meta={showStats ? <>
                {(e.stars || 0) > 0 && <span data-tip={text("stars", "星标")}>★ {fmtCount(e.stars)}</span>}
                {(e.downloads || 0) > 0 && <span data-tip={text("downloads", "下载")}>↓ {fmtCount(e.downloads)}</span>}
                {(e.updated_at || 0) > 0 && <span data-tip={text("last updated", "最近更新")}>{relTime(e.updated_at, locale)}</span>}
              </> : undefined}
              actions={<>
                  <Button
                    variant={installed ? "elevated" : "default"}
                    size="xs"
                    onClick={() => onInstall(source, e.name)}
                    disabled={installingKey === key || bulkBusy}
                  >
                    {installingKey === key
                      ? "…"
                      : outdated
                        ? text("Update", "更新")
                        : installed
                          ? text("Reinstall", "重新安装")
                          : text("Install", "安装")}
                  </Button>
                  {installed && (
                    <Button
                      variant="elevated"
                      size="xs"
                      title={text(`Uninstall ${fullName}`, `卸载 ${fullName}`)}
                      aria-label={text(`Uninstall ${fullName}`, `卸载 ${fullName}`)}
                      disabled={bulkBusy}
                      onClick={async () => {
                        if (await confirmAction({
                          title: text(`Delete skill "${fullName}"?`, `删除技能“${fullName}”？`),
                          confirmLabel: text("Delete", "删除"),
                          destructive: true,
                        })) {
                          void deleteSkill(fullName);
                        }
                      }}
                    >
                      {/* Animated delete glyph (X) — the app-standard delete
                          icon used across the branch / project menus. */}
                      <XIcon size={14} aria-hidden />
                      {text("Remove", "移除")}
                    </Button>
                  )}
              </>}
            />
          );
        })}
        {shown.length === 0 && (
          <div className="col-span-full text-fs-2xs text-text-secondary py-2">{text("No matches.", "没有匹配结果。")}</div>
        )}
      </div>
    </div>
  );
}
