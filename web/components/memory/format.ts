/**
 * Small formatting + grouping helpers for the Memory page.
 */
import type { TimelineDay, TimelineYearGroup, TopicPage } from "./types";

/** Human-readable byte size: 512 B / 12.3 KB / 1.4 MB. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Relative timestamp: "just now" / "5m ago" / "3h ago" / "2d ago" /
 *  locale-formatted date for anything older than a week. */
export function formatDate(mtime: number, locale: "en" | "zh" = "en"): string {
  const d = new Date(mtime * 1000);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (locale === "zh") {
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
    if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)} 天前`;
    return d.toLocaleDateString("zh-CN");
  }
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)}d ago`;
  return d.toLocaleDateString();
}

/** Group wiki pages by their top-level folder prefix (e.g.
 *  ``concepts/foo.md`` → "concepts"). Pages at the root land
 *  under the empty-string bucket so the caller can render them
 *  ungrouped. */
export function groupByFolder(pages: TopicPage[]): Map<string, TopicPage[]> {
  const groups = new Map<string, TopicPage[]>();
  for (const p of pages) {
    const parts = p.path.split("/");
    const folder = parts.length > 1 ? parts[0] : "";
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder)!.push(p);
  }
  return groups;
}

/** Group derived Timeline files by calendar year and month. The filename date
 * is the semantic event date; the file mtime is only the last rebuild time. */
export function groupTimelineDays(
  days: TimelineDay[],
  locale: "en" | "zh" = "en",
): TimelineYearGroup[] {
  const language = locale === "zh" ? "zh-CN" : "en-US";
  const years = new Map<string, Map<string, TimelineYearGroup["months"][number]>>();

  for (const day of [...days].sort((a, b) => b.date.localeCompare(a.date))) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day.date);
    if (!match) continue;
    const [, year, month, dayLabel] = match;
    const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(dayLabel)));
    const monthKey = `${year}-${month}`;
    const months = years.get(year) ?? new Map();
    const group = months.get(monthKey) ?? {
      key: monthKey,
      label: new Intl.DateTimeFormat(language, { month: "long", timeZone: "UTC" }).format(date),
      days: [],
    };
    group.days.push({
      ...day,
      dayLabel,
      weekday: new Intl.DateTimeFormat(language, { weekday: "long", timeZone: "UTC" }).format(date),
    });
    months.set(monthKey, group);
    years.set(year, months);
  }

  return Array.from(years, ([year, months]) => {
    const groupedMonths = Array.from(months.values());
    return {
      year,
      dayCount: groupedMonths.reduce((total, month) => total + month.days.length, 0),
      months: groupedMonths,
    };
  });
}
